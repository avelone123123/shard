import json
import math
import subprocess
import tempfile
import wave
from pathlib import Path
import imageio_ffmpeg
import kokoro_onnx
import numpy as np
import requests
from scipy.signal import butter, filtfilt, lfilter, resample_poly
SR = 48000
FPS = 30
VOICE = "am_michael"
SPEED = 1.12
MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
MODEL_DIR = Path.home() / ".cache" / "shard-video" / "kokoro"
VIDEO_DIR = Path(__file__).resolve().parent.parent
AUDIO_OUT = VIDEO_DIR / "public" / "audio" / "master_audio.mp3"
TIMING_OUT = VIDEO_DIR / "src" / "timing.json"
SCENES = [
    {
        "id": "hook",
        "title": "01 // CLOUD LOCK-IN",
        "min_dur": 3.6,
        "pad_after": 0.4,
        "beats": [
            {"tts": "AI shouldn't be locked inside the cloud.", "sub": "AI shouldn't be locked inside the cloud."},
        ],
    },
    {
        "id": "intro",
        "title": "02 // MEET SHARD",
        "min_dur": 5.0,
        "pad_after": 0.4,
        "beats": [
            {"tts": "Meet Shard.", "sub": "Meet SHARD."},
            {"tts": "Your local AI infrastructure, built for developers who own their compute.", "sub": "Your local AI infrastructure. Built for full compute ownership."},
        ],
    },
    {
        "id": "local",
        "title": "03 // PRE-FLIGHT FIT & DASHBOARD",
        "min_dur": 7.4,
        "pad_after": 0.4,
        "beats": [
            {"tts": "Inspect quantization and vee ram fit before downloading weights.", "sub": "Inspect quantization and VRAM fit before downloading."},
            {"tts": "Launch models directly from the Modern Slate dashboard.", "sub": "Launch models straight from the Modern Slate Workstation UI."},
        ],
    },
    {
        "id": "dev",
        "title": "04 // CLI, API & KERNELS",
        "min_dur": 8.4,
        "pad_after": 0.4,
        "beats": [
            {"tts": "Control everything through one CLI and one FastAPI server.", "sub": "One CLI · One FastAPI Server (:8000)"},
            {"tts": "G-G-U-F via llama C-P-P, PyTorch, and C-plus-plus-20 A-V-X-2 kernels,", "sub": "GGUF (llama.cpp) · PyTorch · C++20 / MASM AVX2"},
            {"tts": "with OpenAI and Anthropic compatible endpoints.", "sub": "OpenAI /v1/chat/completions & Anthropic /v1/messages"},
        ],
    },
    {
        "id": "distributed",
        "title": "05 // LAN POOLING & EXPERIMENTAL P2P",
        "min_dur": 8.0,
        "pad_after": 0.4,
        "beats": [
            {"tts": "Pool spare vee ram across your local network via UDP discovery and RPC workers.", "sub": "Pool LAN VRAM via UDP :8001 + ggml-rpc workers"},
            {"tts": "Plus experimental peer-to-peer and encrypted blind relay tunnels.", "sub": "Experimental: Ed25519 P2P NAT & ChaCha20 Blind Relays"},
        ],
    },
    {
        "id": "soup",
        "title": "06 // SOUP FINE-TUNING",
        "min_dur": 7.0,
        "pad_after": 0.4,
        "beats": [
            {"tts": "Turn consented feedback into redacted SFT and DPO datasets,", "sub": "Consent feedback -> Secret/PII redaction -> SFT / DPO"},
            {"tts": "and run single-node Soup fine-tuning with one-click rollback.", "sub": "Single-node SOUP fine-tuning with eval & 1-click rollback"},
        ],
    },
    {
        "id": "cta",
        "title": "07 // EXPLORE SHARD",
        "min_dur": 6.0,
        "pad_after": 1.3,
        "beats": [
            {"tts": "Your models. Your hardware. Your infrastructure.", "sub": "Your models. Your hardware. Your infrastructure."},
            {"tts": "Explore Shard on GitHub today.", "sub": "Explore SHARD on GitHub"},
        ],
    },
]
def ensure_kokoro():
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    for fn in ("kokoro-v1.0.fp16.onnx", "voices-v1.0.bin"):
        p = MODEL_DIR / fn
        if not p.exists():
            p.write_bytes(requests.get(MODEL_URL + fn, timeout=180).content)
    return kokoro_onnx.Kokoro(str(MODEL_DIR / "kokoro-v1.0.fp16.onnx"), str(MODEL_DIR / "voices-v1.0.bin"))
def trim_and_resample(samples: np.ndarray, src_sr: int, thresh=0.008, pad_s=0.04):
    arr = np.asarray(samples, dtype=np.float32)
    idx = np.where(np.abs(arr) > thresh)[0]
    if len(idx) > 0:
        pad = int(pad_s * src_sr)
        arr = arr[max(0, int(idx[0]) - pad):min(len(arr), int(idx[-1]) + pad)]
    g = math.gcd(src_sr, SR)
    up = SR // g
    down = src_sr // g
    if up != down:
        arr = resample_poly(arr, up, down).astype(np.float32)
    peak = float(np.max(np.abs(arr))) if len(arr) else 0.0
    if peak > 0.01:
        arr = arr * min(0.88 / peak, 2.0)
    return np.stack([arr, arr], axis=1)
def write_wav_int16(path: Path, stereo: np.ndarray):
    clipped = np.clip(stereo, -0.99, 0.99)
    pcm = (clipped * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(pcm.tobytes())
def build_music_bed(total_samples: int, cue_times: list[float]):
    t = np.arange(total_samples, dtype=np.float64) / SR
    beat_hz = 112.0 / 60.0
    chords = [
        (55.00, 110.00, 164.81, 220.00, 261.63),
        (43.65, 87.31, 130.81, 174.61, 220.00),
        (65.41, 130.81, 196.00, 261.63, 329.63),
        (49.00, 98.00, 146.83, 196.00, 246.94),
    ]
    bar_s = 4.0 / beat_hz
    chord_idx = (t / bar_s).astype(np.int32) % len(chords)
    bass_sig = np.zeros(total_samples, dtype=np.float64)
    pad_l = np.zeros(total_samples, dtype=np.float64)
    pad_r = np.zeros(total_samples, dtype=np.float64)
    for idx, freqs in enumerate(chords):
        mask = chord_idx == idx
        if not np.any(mask):
            continue
        tm = t[mask]
        f0 = freqs[0]
        bass_sig[mask] = 0.72 * np.sin(2 * np.pi * f0 * tm) + 0.28 * np.sin(2 * np.pi * (f0 * 2.0) * tm)
        pl = np.zeros_like(tm)
        pr = np.zeros_like(tm)
        for fi, fq in enumerate(freqs[1:]):
            det = 1.0 + (0.0022 * (fi + 1))
            pl += np.sin(2 * np.pi * fq * tm)
            pr += np.sin(2 * np.pi * (fq * det) * tm)
        pad_l[mask] = pl / len(freqs[1:])
        pad_r[mask] = pr / len(freqs[1:])
    beat_phase = (t * beat_hz) % 1.0
    pump = 0.56 + 0.44 * np.power(np.sin(np.pi * beat_phase), 0.7)
    b_low, a_low = butter(3, 210.0 / (0.5 * SR), btype="low")
    bass_sig = filtfilt(b_low, a_low, bass_sig) * pump * 0.25
    b_pad, a_pad = butter(2, 660.0 / (0.5 * SR), btype="low")
    pad_l = filtfilt(b_pad, a_pad, pad_l) * (0.14 + 0.04 * np.sin(2 * np.pi * 0.16 * t))
    pad_r = filtfilt(b_pad, a_pad, pad_r) * (0.14 + 0.04 * np.cos(2 * np.pi * 0.16 * t))
    tick_env = np.exp(-beat_phase * 42.0) * (beat_phase < 0.2)
    rng = np.random.default_rng(42)
    noise = rng.normal(0.0, 1.0, total_samples)
    b_hi, a_hi = butter(2, 5600.0 / (0.5 * SR), btype="high")
    hihat = lfilter(b_hi, a_hi, noise) * tick_env * 0.032
    sub_tick = np.sin(2 * np.pi * (68.0 * np.exp(-beat_phase * 8.0) + 42.0) * beat_phase) * np.exp(-beat_phase * 14.0) * 0.13
    sfx_l = np.zeros(total_samples, dtype=np.float64)
    sfx_r = np.zeros(total_samples, dtype=np.float64)
    for cs in cue_times:
        c0 = int(cs * SR)
        hit_len = int(0.45 * SR)
        if c0 + hit_len < total_samples:
            ht = np.arange(hit_len, dtype=np.float64) / SR
            sweep = np.sin(2 * np.pi * (145.0 * np.exp(-ht * 9.0) + 48.0) * ht) * np.exp(-ht * 8.5)
            ping = np.sin(2 * np.pi * 880.0 * ht) * np.exp(-ht * 24.0) * 0.18
            hit = sweep * 0.34 + ping
            sfx_l[c0:c0 + hit_len] += hit
            sfx_r[c0:c0 + hit_len] += hit
    left = bass_sig + pad_l + hihat + sub_tick + sfx_l
    right = bass_sig + pad_r + hihat + sub_tick + sfx_r
    fade_in = np.minimum(1.0, t / 0.8)
    fade_out = np.minimum(1.0, np.maximum(0.0, (total_samples / SR - t) / 1.4))
    env = fade_in * fade_out
    return np.stack([left * env, right * env], axis=1).astype(np.float32)
def main():
    kokoro = ensure_kokoro()
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    AUDIO_OUT.parent.mkdir(parents=True, exist_ok=True)
    TIMING_OUT.parent.mkdir(parents=True, exist_ok=True)
    cursor_s = 0.25
    voice_placements = []
    manifest_scenes = []
    for idx, sc in enumerate(SCENES):
        start_s = cursor_s
        start_frame = int(round(start_s * FPS))
        beat_cursor_s = start_s + (0.30 if idx == 0 else 0.20)
        beat_entries = []
        for b_idx, b in enumerate(sc["beats"]):
            samples, src_sr = kokoro.create(b["tts"], voice=VOICE, speed=SPEED, lang="en-us")
            arr = trim_and_resample(samples, src_sr)
            dur_s = len(arr) / SR
            b_start_s = beat_cursor_s
            b_end_s = b_start_s + dur_s
            voice_placements.append((b_start_s, arr))
            beat_entries.append({
                "text": b["sub"],
                "startFrame": int(round(b_start_s * FPS)),
                "endFrame": int(round(b_end_s * FPS)),
                "startSec": round(b_start_s, 3),
                "endSec": round(b_end_s, 3),
            })
            beat_cursor_s = b_end_s + 0.16
        speech_end_s = beat_cursor_s - 0.16
        natural_dur = (speech_end_s - start_s) + sc["pad_after"]
        slot_dur = max(sc["min_dur"], natural_dur)
        slot_frames = int(math.ceil(slot_dur * FPS))
        slot_dur = slot_frames / FPS
        if beat_entries:
            beat_entries[-1]["endFrame"] = max(beat_entries[-1]["endFrame"], start_frame + slot_frames - 3)
        manifest_scenes.append({
            "id": sc["id"],
            "title": sc["title"],
            "startFrame": start_frame,
            "durationFrames": slot_frames,
            "startSec": round(start_s, 3),
            "durationSec": round(slot_dur, 3),
            "beats": beat_entries,
        })
        cursor_s = start_s + slot_dur
    total_frames = int(math.ceil(cursor_s * FPS))
    total_samples = int(math.ceil((total_frames / FPS) * SR))
    voice_mix = np.zeros((total_samples, 2), dtype=np.float32)
    for v_start, arr in voice_placements:
        s0 = int(round(v_start * SR))
        s1 = min(total_samples, s0 + len(arr))
        if s1 > s0:
            voice_mix[s0:s1] += arr[:s1 - s0]
    speech_mag = np.max(np.abs(voice_mix), axis=1)
    win = int(0.18 * SR)
    smooth_speech = np.convolve(speech_mag, np.ones(win, dtype=np.float32) / win, mode="same")
    duck = np.where(smooth_speech > 0.015, 0.34, 0.72).astype(np.float32)
    b_d, a_d = butter(2, 4.0 / (0.5 * SR), btype="low")
    duck_smooth = filtfilt(b_d, a_d, duck).astype(np.float32)
    cue_times = [s["startSec"] for s in manifest_scenes]
    music = build_music_bed(total_samples, cue_times)
    music_ducked = music * duck_smooth[:, None]
    with tempfile.TemporaryDirectory() as tmpd:
        td = Path(tmpd)
        raw_voice_wav = td / "voice.wav"
        raw_music_wav = td / "music.wav"
        write_wav_int16(raw_voice_wav, voice_mix)
        write_wav_int16(raw_music_wav, music_ducked)
        subprocess.run(
            [
                ffmpeg, "-y",
                "-i", str(raw_voice_wav),
                "-i", str(raw_music_wav),
                "-filter_complex",
                "[0:a]highpass=f=85,acompressor=threshold=-16dB:ratio=2.4:attack=8:release=120,volume=1.18[v];"
                "[1:a]volume=0.82[m];"
                "[v][m]amix=inputs=2:duration=longest:dropout_transition=2,alimiter=limit=0.95[out]",
                "-map", "[out]",
                "-c:a", "libmp3lame",
                "-b:a", "256k",
                "-ar", str(SR),
                "-ac", "2",
                str(AUDIO_OUT),
            ],
            check=True,
            capture_output=True,
        )
    manifest = {
        "fps": FPS,
        "width": 1080,
        "height": 1920,
        "totalFrames": total_frames,
        "totalSeconds": round(total_frames / FPS, 3),
        "voice": f"kokoro-v1.0.fp16:{VOICE}",
        "scenes": manifest_scenes,
    }
    TIMING_OUT.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Built {AUDIO_OUT.name} ({total_frames / FPS:.2f}s / {total_frames} frames) and src/timing.json")
if __name__ == "__main__":
    main()
