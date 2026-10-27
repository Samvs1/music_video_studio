"""The song-analysis models: what is downloaded, and fetching them before the first ingest (setup asks first).

  python tools/models.py status                 which models are cached on this machine
  python tools/models.py fetch [--all]          Demucs (htdemucs + htdemucs_ft, ~400 MB), beat_this (~80 MB),
                                                the MMS lyric aligner (~1.2 GB); --all adds Whisper large-v3-turbo
                                                (~1.6 GB) and CLAP (~0.8 GB), which are only needed for songs
                                                without lyrics online / with a long non-music intro
Every model also downloads by itself the first time ingest needs it. Sources: dl.fbaipublicfiles.com (Demucs, MMS),
cloud.cp.jku.at (beat_this), huggingface.co (Whisper, CLAP). Without them, ingest falls back to estimated stems and
beats (and says so in song/report.json).
"""
import argparse, glob, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def status():
    import torch
    hub = torch.hub.get_dir()
    ck = os.path.join(hub, "checkpoints")
    files = sorted(os.listdir(ck)) if os.path.isdir(ck) else []
    print(f"torch hub: {ck}")
    print(f"  Demucs:   {[f for f in files if f.endswith('.th')] or 'not downloaded'}")
    print(f"  MMS:      {[f for f in files if 'model.pt' in f or 'mms' in f.lower()] or 'not downloaded'}")
    bt = glob.glob(os.path.join(ck, "*final0*")) + glob.glob(os.path.expanduser("~/.cache/**/final0*"), recursive=True)
    print(f"  beat_this: {'cached' if bt else 'not downloaded'}")
    hf = os.path.expanduser("~/.cache/huggingface/hub")
    print(f"  Hugging Face: {sorted(os.listdir(hf)) if os.path.isdir(hf) else 'nothing cached'}")


def fetch(all_=False):
    ok = True
    steps = [("Demucs htdemucs (CPU stems, ~80 MB)", lambda: __import__("demucs.pretrained", fromlist=["get_model"]).get_model("htdemucs")),
             ("Demucs htdemucs_ft (GPU stems, ~320 MB)", lambda: __import__("demucs.pretrained", fromlist=["get_model"]).get_model("htdemucs_ft")),
             ("beat_this final0 (~80 MB)", lambda: __import__("beat_this.inference", fromlist=["Audio2Beats"]).Audio2Beats(checkpoint_path="final0", device="cpu", dbn=False)),
             ("MMS lyric aligner (~1.2 GB)", lambda: __import__("torchaudio").pipelines.MMS_FA.get_model(with_star=True))]
    if all_:
        steps += [("Whisper large-v3-turbo (~1.6 GB)", lambda: __import__("transformers").pipeline("automatic-speech-recognition", model="openai/whisper-large-v3-turbo")),
                  ("CLAP music+speech (~0.8 GB)", lambda: __import__("transformers").ClapModel.from_pretrained("laion/larger_clap_music_and_speech"))]
    for name, fn in steps:
        print(f"== {name}", flush=True)
        try:
            fn()
            print("   ok", flush=True)
        except Exception as ex:
            ok = False
            print(f"   FAILED: {type(ex).__name__}: {str(ex)[:200]}", flush=True)
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["status", "fetch"])
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    if a.cmd == "status":
        status()
    else:
        sys.exit(0 if fetch(a.all) else 1)


if __name__ == "__main__":
    main()
