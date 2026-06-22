"""CPU vs GPU inference benchmark for WhisperX."""
import sys, time
sys.path.insert(0, "backend")
from app.services.transcription_engine import TranscriptionEngine
import whisperx

SHORT = "test_audio_dialogue.wav"

def bench(device, compute, batch):
    print(f"\n{'='*50}", flush=True)
    print(f"Device: {device} | Compute: {compute} | Batch: {batch}", flush=True)
    print(f"{'='*50}", flush=True)

    print(f"[{time.strftime('%H:%M:%S')}] Loading model...", flush=True)
    t0 = time.time()
    engine = TranscriptionEngine(device=device, compute_type=compute, batch_size=batch)
    engine._ensure_model_loaded()
    print(f"[{time.strftime('%H:%M:%S')}] Model loaded in {time.time()-t0:.1f}s", flush=True)

    audio = whisperx.load_audio(SHORT)
    duration = len(audio) / 16000

    print(f"[{time.strftime('%H:%M:%S')}] Transcribing {duration:.1f}s audio...", flush=True)
    t1 = time.time()
    result = engine._model.transcribe(audio, batch_size=batch)
    t2 = time.time()
    print(f"[{time.strftime('%H:%M:%S')}] ASR done: {t2-t1:.1f}s | {len(result['segments'])} segments", flush=True)

    lang = result.get("language", "en")
    align_model, meta = whisperx.load_align_model(language_code=lang, device=device)
    t3 = time.time()
    result = whisperx.align(result["segments"], align_model, meta, audio, device, return_char_alignments=False)
    t4 = time.time()
    print(f"[{time.strftime('%H:%M:%S')}] Alignment done: {t4-t3:.1f}s", flush=True)

    total = t4 - t1
    words = sum(len(s.get("words", [])) for s in result["segments"])
    print(f"\n[RESULT] Audio: {duration:.1f}s | Inference: {total:.1f}s | RTF: {duration/total:.2f}x | Words: {words}", flush=True)
    return duration, total

print("WhisperX Benchmark: CPU vs GPU (36s audio)\n")

d, t_cpu = bench("cpu", "int8", 4)
_, t_gpu = bench("cuda", "float16", 32)

print(f"\n{'='*50}")
print(f"SUMMARY")
print(f"{'='*50}")
print(f"Audio duration:  {d:.1f}s")
print(f"CPU (int8, b=4): {t_cpu:.1f}s  ({d/t_cpu:.2f}x real-time)")
print(f"GPU (fp16, b=32):{t_gpu:.1f}s  ({d/t_gpu:.1f}x real-time)")
print(f"GPU speedup:     {t_cpu/t_gpu:.1f}x faster than CPU")
