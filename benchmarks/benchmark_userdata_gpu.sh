#!/bin/bash
set -ex
exec > /var/log/whisperx-benchmark.log 2>&1

echo "=== WhisperX GPU Benchmark Setup ==="
echo "Instance type: $(curl -s http://169.254.169.254/latest/meta-data/instance-type)"
echo "Start time: $(date -u)"

# Install system deps
dnf install -y python3.9 python3.9-pip ffmpeg git

# Install PyTorch + WhisperX
pip3.9 install torch torchaudio --index-url https://download.pytorch.org/whl/cu128
pip3.9 install whisperx fastapi sqlalchemy pydantic apscheduler

# Clone repo
cd /tmp
git clone https://github.com/Ziyang-Liao/WhisperX.git whisperx_bench
cd whisperx_bench

# Generate test audio (~5min by repeating test file)
if [ -f test_audio_dialogue.wav ]; then
  for i in $(seq 1 9); do echo "file 'test_audio_dialogue.wav'"; done > /tmp/concat.txt
  ffmpeg -y -f concat -safe 0 -i /tmp/concat.txt -c copy /tmp/test_5min.wav
fi

# Run benchmark
cat > /tmp/bench.py << 'PYEOF'
import sys, time, json, subprocess
sys.path.insert(0, "backend")

instance_type = subprocess.check_output(
    ["curl", "-s", "http://169.254.169.254/latest/meta-data/instance-type"]
).decode().strip()

import torch
print(f"Instance: {instance_type}")
print(f"CUDA available: {torch.cuda.is_available()}")
if torch.cuda.is_available():
    print(f"GPU: {torch.cuda.get_device_name(0)}")
    print(f"VRAM: {torch.cuda.get_device_properties(0).total_mem / 1024**3:.1f} GB")

from app.services.transcription_engine import TranscriptionEngine
import whisperx

results = {}

for label, audio_path in [("36s", "test_audio_dialogue.wav"), ("5min", "/tmp/test_5min.wav")]:
    if not __import__("os").path.exists(audio_path):
        print(f"Skipping {label}: file not found")
        continue

    audio = whisperx.load_audio(audio_path)
    duration = len(audio) / 16000

    engine = TranscriptionEngine()
    # Warm up
    if label == "36s":
        print(f"\nWarming up model...")
        _ = engine.transcribe("test_audio_dialogue.wav", enable_diarization=False)
        print("Warm-up done.")

    # Transcription only
    print(f"\n=== {label} audio ({duration:.1f}s) — Transcription only ===")
    times = []
    for i in range(3 if label == "36s" else 1):
        t0 = time.time()
        r = engine.transcribe(audio_path, enable_diarization=False)
        elapsed = time.time() - t0
        times.append(elapsed)
        print(f"  Run {i+1}: {elapsed:.2f}s")

    avg = sum(times) / len(times)
    d = r.model_dump()
    print(f"  Avg: {avg:.2f}s | RTF: {duration/avg:.1f}x | Segments: {len(d['segments'])} | Words: {sum(len(s.get('words',[])) for s in d['segments'])}")

    results[f"{label}_transcribe"] = {"duration": duration, "avg_time": round(avg, 2), "rtf": round(duration/avg, 1)}

    # With diarization (36s only)
    if label == "36s":
        print(f"\n=== {label} audio — Transcription + Diarization ===")
        t0 = time.time()
        r2 = engine.transcribe(audio_path, enable_diarization=True)
        elapsed = time.time() - t0
        d2 = r2.model_dump()
        speakers = set(s['speaker'] for s in d2['segments'] if s.get('speaker'))
        print(f"  Time: {elapsed:.2f}s | RTF: {duration/elapsed:.1f}x | Speakers: {len(speakers)}")
        results["36s_diarize"] = {"duration": duration, "time": round(elapsed, 2), "rtf": round(duration/elapsed, 1), "speakers": len(speakers)}

results["instance_type"] = instance_type
results["gpu"] = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "N/A"

with open(f"/tmp/benchmark_{instance_type.replace('.', '_')}.json", "w") as f:
    json.dump(results, f, indent=2)

print(f"\n=== BENCHMARK COMPLETE ===")
print(json.dumps(results, indent=2))
PYEOF

cd /tmp/whisperx_bench
python3.9 -u /tmp/bench.py

echo "=== Benchmark finished at $(date -u) ==="
