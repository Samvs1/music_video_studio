"""Split the project's song into vocals / drums / bass / other stems with Demucs.

  python tools/studio.py run <project> separate.py [--force] [--fallback]

On an NVIDIA GPU this uses htdemucs_ft (the fine-tuned bag of four models: cleaner vocals, less synth bleed; ~1.5 GB
VRAM in 8 s segments, ~30 s per song on an RTX 4060); on the CPU the single htdemucs model (a few minutes).
Stems are for analysis and for soloing in the web app; the video always uses the original song file.
Writes song/stems/<stem>.wav (44.1 kHz stereo) + model.txt (which model made them).

Offline fallback (new in Music Video Studio): when the Demucs weights can't be loaded (first use needs
dl.fbaipublicfiles.com) or --fallback is given, the stems are *estimated* with librosa: harmonic/percussive
separation, a low band for the bass and the centre of the stereo image for the voice. They are rough (bleed
everywhere) but keep the pipeline running; model.txt says "fallback" and the song kit lowers its confidence.
While the stems are estimates, every run (analysing again) tries Demucs again; real stems are kept (--force remakes).
"""
import argparse, os, sys
import numpy as np
import soundfile as sf
import librosa

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mv.common import STEMS, song_file, need_project  # noqa: E402
from mv import device as D  # noqa: E402

NAMES = ("vocals", "drums", "bass", "other")


def demucs_stems(path):
    """{name: (2, n) float32} at 44.1 kHz with Demucs, and the model label."""
    import torch
    from demucs.pretrained import get_model
    from demucs.apply import apply_model
    dev = D.pick(2.0)
    name = "htdemucs_ft" if dev == "cuda" else "htdemucs"
    torch.set_num_threads(max(1, (os.cpu_count() or 2) - 1))
    print(f"   loading {name} (first use downloads the weights: htdemucs ~80 MB, htdemucs_ft ~320 MB)", flush=True)
    model = get_model(name)
    model.eval()
    sr = model.samplerate
    y, _ = librosa.load(path, sr=sr, mono=False)
    if y.ndim == 1:
        y = np.stack([y, y])
    wav = torch.tensor(y, dtype=torch.float32)
    ref = wav.mean(0)
    wav = (wav - ref.mean()) / (ref.std() + 1e-8)
    with torch.no_grad():
        src = apply_model(model, wav[None], device=dev, shifts=1, split=True, overlap=0.25, progress=False)[0]
    src = src.cpu() * ref.std() + ref.mean()
    out = {s: src[i].numpy() for i, s in enumerate(model.sources)}
    label = f"{name} ({dev})"
    if dev == "cuda":
        label += f", peak VRAM {D.peak_gb()} GB"
    del model, src, wav
    D.release()
    return out, sr, label


def fallback_stems(path):
    """Rough stems without a model: HPSS + bands + the stereo centre. Returns ({name: (2, n)}, sr, label)."""
    sr = 44100
    y, _ = librosa.load(path, sr=sr, mono=False)
    if y.ndim == 1:
        y = np.stack([y, y])
    n = y.shape[1]
    mid, side = (y[0] + y[1]) / 2, (y[0] - y[1]) / 2
    nfft, hop = 4096, 1024
    M = librosa.stft(mid, n_fft=nfft, hop_length=hop)
    Sd = librosa.stft(side, n_fft=nfft, hop_length=hop)
    mh, mp = librosa.decompose.hpss(np.abs(M), mask=True, margin=(1.0, 1.5))
    f = librosa.fft_frequencies(sr=sr, n_fft=nfft)[:, None]
    low = 1 / (1 + (f / 180.0) ** 6)                                     # soft low-pass: the bass
    band = (1 / (1 + (150.0 / np.maximum(f, 1)) ** 6)) * (1 / (1 + (f / 6000.0) ** 4))   # the voice's band
    centre = np.clip(1 - np.abs(Sd) / (np.abs(M) + 1e-6), 0, 1) ** 2      # what sits in the middle of the image
    masks = {"drums": mp, "bass": mh * low}
    masks["vocals"] = mh * (1 - low) * band * centre
    masks["other"] = np.clip(mh - masks["bass"] - masks["vocals"], 0, 1)
    out = {}
    for k, m in masks.items():
        x = librosa.istft(M * m, hop_length=hop, length=n)
        if k == "other":                                                   # keep the stereo spread of the band
            xs = librosa.istft(Sd * mh * (1 - low), hop_length=hop, length=n)
            out[k] = np.stack([x + xs, x - xs]).astype(np.float32)
        else:
            out[k] = np.stack([x, x]).astype(np.float32)
    return out, sr, "fallback (librosa HPSS + bands + stereo centre: estimated, not a real separation)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--fallback", action="store_true", help="skip Demucs and estimate the stems")
    a = ap.parse_args()
    need_project()
    path = song_file()
    if not path:
        sys.exit("no song in song/ yet")
    have = os.path.exists(os.path.join(STEMS, "vocals.wav"))
    mt = os.path.join(STEMS, "model.txt")
    estimated = have and os.path.exists(mt) and open(mt).read().startswith("fallback")
    if have and not a.force and (a.fallback or not estimated):
        print("stems exist, skipping (--force to remake)")
        return
    if estimated and not a.force:                    # the model wasn't there last time: a real separation if it is now
        print("   the stems are estimates (no Demucs last time): trying Demucs again", flush=True)
    os.makedirs(STEMS, exist_ok=True)
    stems = None
    if not a.fallback:
        try:
            stems, sr, label = demucs_stems(path)
        except Exception as ex:
            print(f"   Demucs unavailable ({type(ex).__name__}: {str(ex)[:160]})", flush=True)
            print("   WARNING: estimating the stems without a model; the stem lanes, notes and chords will be rough", flush=True)
    if stems is None:
        stems, sr, label = fallback_stems(path)
    for k in NAMES:
        sf.write(os.path.join(STEMS, f"{k}.wav"), stems[k].T, sr, subtype="PCM_16")
    open(os.path.join(STEMS, "model.txt"), "w").write(label + "\n")
    print(f"wrote {', '.join(NAMES)} with {label}")


if __name__ == "__main__":
    main()
