# The song folder: how to read it

You can't hear the song. These files are what a good editor hears, written down, plus the exact numbers a motion
designer animates with. Made by `tools/song_kit.py` from the song file, its stems and the aligned lyrics
(`studio.py song <project>` remakes it; `studio.py curves <project> --fps N` remakes the per-frame files for another fps).

**Video time = song time.** The video uses the whole song from 0, unchanged, so every time below is also the video's.

## Read in this order (once; keep your own digest in `work/`)
1. this README, then `report.json` → `warnings` and `confidence` (how far to trust each part)
2. `song-map.md`: at a glance → sections → **moments** (★★★ = the song's own peaks and turns) → bar by bar
3. `overview.png` (the whole song in lanes); `pages/bars_*.png` for any stretch you will time tightly
4. `lyrics.md`: when each line is sung, the hook, the melody (peaks, held words)
5. `song-story.md`: what the song is about and how its arc can carry the video (write it first if it is a skeleton)

## The files
| file | what |
|---|---|
| `song-map.md` | at a glance → sections → moments → every bar (chord, loudness, voice/drums/bass/other, sung words, events) |
| `lyrics.md` | the lyric by section: when each line is sung, `bar:beat`, the hook, melody (contour, peak note, held words) |
| `song-story.md` | what the song is about and how its arc can carry the video |
| `overview.png`, `pages/` | the song in lanes, the way an editor sees a timeline; 16 bars per page |
| `song-map.json` | everything as data: `beats`, `bars`, `sections`, `moments`, `lyrics` (words with sung time, note, held), `notes` (vocal + bass), `chords` |
| `frames.json` | **one value per video frame** at 30 fps: curves (`loud`, `level`, `vocals`, `drums`, `bass`, `other`, `sub`, `low`, `lowmid`, `highmid`, `high`, `transient`, `bright`, `width`, `voice_midi`, `bass_midi`, `lufs`, `centroid_hz`) and grid (`bar`, `beat`, `beat_phase`, `bar_phase`, `section`); `info` says what each is and how sure it is |
| `hits.json` | every hit with time `t`, frame `f` and strength `s`: `beats`, `downbeats`, `kick`, `snare`, `hat`, `crash`, `bass`, `other`, `vocal`, `onset`, `words`, `lines`, `notes`, `chords`, `sections`, `moments` |
| `envelopes.json` | 10 Hz curves for plots (the web app's song page) |
| `report.json` | models, device, timings, warnings, confidence, and `signals`: every curve and hit list with its confidence |
| `stems/` | `vocals.wav`, `drums.wav`, `bass.wav`, `other.wav` (+ `model.txt`): analysis only, never in the video |

## Timing rules (the same in every tool)
- An event at song time `t` belongs to frame `round(t * fps)`. Frame `k` covers `[(k-0.5)/fps, (k+0.5)/fps)`; a curve's
  value for frame k is the mean over that slot (`transient`: the max, so a hit shows at full strength in its frame).
- `bar:beat`: `17:2.5` = bar 17 (bar 0 = the first downbeat), 2.5 beats after its downbeat (0 = the downbeat; .5 = the
  8th after a beat). Negative beats = pickups before bar 0. Python: `song.t(17, 2.5)`; web engine: `MV.t(17, 2.5)`.
- Lyric text appears on the beat or 8th at or just before the word's sung onset (`song.cue(t)` / `MV.cue(t)`), never after.
- Helpers: `tools/mv/song.py` (Python, also inside Blender) and the web engine's `MV` runtime read these files for you;
  `python tools/songq.py at 83.2` (or `section "chorus 2"`, `moments`, `bars 16 24`) answers small questions without
  re-reading the big files.

## Notation in song-map.md
- **LUFS**: loudness as ears hear it (EBU R128). −8 is very loud, −14 normal, −25 quiet. Differences matter more than
  values: +3 LU is clearly louder, +6 LU is a big lift.
- **V D B O** (0-9): **V** = how much of the bar is sung (9 = all through); **D / B / O** = how loud the drums, bass and
  *other* (guitars, keys, synths, strings) are against their own loudest bars (0 = not playing, 9 = full).
- **Sections**: names come from repetition (the *sound* letter: sections with the same letter share chords and texture)
  and the lyric (lines that come back = chorus). `AB` = merged from two classes.
- **Moments**: ★ (cut on it if it's handy) → ★★★ (the song's own peaks and turns: the video's big ideas go here).

## Caveats
- Section names are inferred: if the bars say otherwise, trust your reading and say so in `song-story.md`.
- Chords are major/minor triads from the *other* + *bass* stems (no 7ths/sus): good for where harmony changes,
  approximate for spelling. Key: C major (fit 0.81 vs A minor 0.67).
- Notes come from pitch tracking (CREPE) on the vocal / bass stems: reliable for contour, highest notes and holds;
  backing vocals and bleed can add stray notes.
- Genre, mood and instrument tags were tested in aftermovie-studio and left out: too often wrong. The song's
  character is yours to read from the measurements, the lyric, the story and the user's notes.
- Stems: fallback (librosa HPSS + bands + stereo centre: estimated, not a real separation). Beats: fallback (librosa dynamic-tempo tracker + downbeat phase vote). Made 2026-10-27 18:18 on the CPU.
- **Warning:** the stems were ESTIMATED without Demucs (fallback): the voice / drums / bass / other lanes, the notes, the chords and the sections are rough. Re-run ingest with the Demucs weights for reliable ones.
- **Warning:** the beats come from the fallback tracker (beat_this unavailable): check bar 0 and the downbeats against the song before cutting tightly
