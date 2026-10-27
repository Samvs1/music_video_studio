"""Fetch time-synced lyrics candidates from LRCLIB (free, no key) for the project's song.

  python tools/studio.py run <project> fetch_lyrics.py "<artist>" "<title>"

Writes cache/lyrics/search.json. analyze_audio.py then picks the candidate that best matches the vocal stem and
aligns it to this exact file (offset + per-word times). Needs lrclib.net (the studio carries on without lyrics when
it can't be reached).
"""
import json, os, sys, urllib.parse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mv.common import CACHE, need_project  # noqa: E402


def main():
    need_project()
    artist, title = sys.argv[1], sys.argv[2]
    q = urllib.parse.urlencode({"artist_name": artist, "track_name": title})
    req = urllib.request.Request(f"https://lrclib.net/api/search?{q}", headers={"User-Agent": "music-video-studio/1.0"})
    data = json.loads(urllib.request.urlopen(req, timeout=30).read().decode("utf-8"))
    out = os.path.join(CACHE, "lyrics", "search.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(data, open(out, "w", encoding="utf-8"), ensure_ascii=False)
    synced = [d for d in data if d.get("syncedLyrics")]
    print(f"{len(data)} results, {len(synced)} with synced lyrics -> {out}")


if __name__ == "__main__":
    main()
