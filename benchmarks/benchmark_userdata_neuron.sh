#!/bin/bash
set -ex
exec > /var/log/whisperx-benchmark.log 2>&1

echo "=== WhisperX Neuron Benchmark Setup ==="
echo "Instance type: $(curl -s http://169.254.169.254/latest/meta-data/instance-type)"
echo "Start time: $(date -u)"

# Neuron SDK should be pre-installed on the DLAMI
# Activate the neuron venv
source /opt/aws_neuronx_venv_pytorch_2_9/bin/activate 2>/dev/null || true

pip install whisperx 2>/dev/null || echo "whisperx install may need adjustments for neuron"

# Install system deps
dnf install -y ffmpeg git 2>/dev/null || yum install -y ffmpeg git 2>/dev/null || true

# Clone repo
cd /tmp
git clone https://github.com/Ziyang-Liao/WhisperX.git whisperx_bench 2>/dev/null || true
cd whisperx_bench

# Neuron benchmark - test if we can run whisperx on neuron
cat > /tmp/bench_neuron.py << 'PYEOF'
import sys, time, json, subprocess

instance_type = subprocess.check_output(
    ["curl", "-s", "http://169.254.169.254/latest/meta-data/instance-type"]
).decode().strip()

print(f"Instance: {instance_type}")

# Check neuron devices
import torch
print(f"PyTorch version: {torch.__version__}")
print(f"CUDA available: {torch.cuda.is_available()}")

try:
    import torch_neuronx
    print(f"Neuron SDK available: True")
    print(f"torch_neuronx version: {torch_neuronx.__version__}")
except ImportError:
    print("Neuron SDK: not found in this env")

# Try to run whisperx with CPU fallback on neuron instances
# (Neuron doesn't support CUDA, whisperx will fall back to CPU with int8)
try:
    sys.path.insert(0, "backend")
    from app.services.transcription_engine import TranscriptionEngine
    import whisperx

    audio_path = "test_audio_dialogue.wav"
    if not __import__("os").path.exists(audio_path):
        print("Test audio not found, skipping")
        sys.exit(0)

    audio = whisperx.load_audio(audio_path)
    duration = len(audio) / 16000

    # Force CPU since neuron doesn't support CUDA whisperx
    engine = TranscriptionEngine(device="cpu", compute_type="int8", batch_size=4)

    print(f"\nWarming up CPU model on {instance_type}...")
    _ = engine.transcribe(audio_path, enable_diarization=False)
    print("Warm-up done.")

    print(f"\n=== 36s audio — CPU Transcription on {instance_type} ===")
    times = []
    for i in range(3):
        t0 = time.time()
        r = engine.transcribe(audio_path, enable_diarization=False)
        elapsed = time.time() - t0
        times.append(elapsed)
        print(f"  Run {i+1}: {elapsed:.2f}s")

    avg = sum(times) / len(times)
    d = r.model_dump()
    print(f"  Avg: {avg:.2f}s | RTF: {duration/avg:.2f}x")

    results = {
        "instance_type": instance_type,
        "device": "cpu (neuron instance)",
        "36s_transcribe": {"duration": duration, "avg_time": round(avg, 2), "rtf": round(duration/avg, 2)},
        "note": "WhisperX does not natively support Neuron/Inferentia. Running CPU fallback."
    }

    with open(f"/tmp/benchmark_{instance_type.replace('.', '_')}.json", "w") as f:
        json.dump(results, f, indent=2)

    print(f"\n=== BENCHMARK COMPLETE ===")
    print(json.dumps(results, indent=2))

except Exception as e:
    print(f"Benchmark failed: {e}")
    import traceback
    traceback.print_exc()

PYEOF

python3 -u /tmp/bench_neuron.py || python3.9 -u /tmp/bench_neuron.py

echo "=== Benchmark finished at $(date -u) ==="
