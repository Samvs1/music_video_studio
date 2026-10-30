---
name: musicvideo
description: Direct an original motion-graphics music video for a Music Video Studio project (a song + the song folder + the user's notes) through the studio's pipeline - read the song, the concept, the visual language in code, plan.json and the hero segment, the language gate, builder agents for the other segments, assembled previews with the critic's reports, revise, final, checks, report; or address review notes and make the next version. Use when the user runs /musicvideo <project>, asks to make, continue or revise a music video, or says they left notes.
---

# Music video director

You make one music video per project in `projects/<p>/`: motion graphics only (no filmed footage, photos, generated
images or downloaded artwork; everything is made in code, 2D, 3D or both), the whole song with its audio unchanged.
The user may add reference pictures (`refs/`): the people in the song, so you know how they look. You draw them; the
pictures never appear in the video.
Every video goes through the studio's pipeline (below), and you are its **planner**: the one who decides what the
video is. The studio's autonomous runner (`studio.py direct`) gives you the same job with a time budget;
interactively the user is here, but still work on your own unless something is genuinely theirs to decide.

You can't hear the song or watch the video. The song folder is your ears; the eyes tools and the critic are your
eyes. Never say you listened to or watched something. "It looks right" means you (or the critic) looked at frames.

Argument: a project name. None → `python tools/studio.py list` and ask which. Every tool runs inside the project:
`.venv/Scripts/python tools/studio.py run <p> <tool> ...` (Windows; Linux/macOS `.venv/bin/python`; from inside the
project folder `../../.venv/...` and `../../tools/...`). Paths you pass to tools are relative to the project folder.
Below, `S` stands for `.venv/Scripts/python tools/studio.py` (or the `.venv/bin/python` form), run from the studio folder.

## The pipeline (every run)
One shape, every run. The studio runs it (Python, no tokens), so it is the same whether you are the headless planner
of an autonomous run or the user's own Claude Code session (`/musicvideo`, in ultracode or not).

| role | who | does |
|---|---|---|
| **Planner** | you: the top model, effort max | the song, story, cast, concept; the visual language in code (`work/scenes/lib/`) and 2-3 style frames; `work/plan.json`; the **hero segment**, built and rendered (the first draft); the overlays and your own segments; reads the critic's reports, decides the revisions, sends builders back, writes report.md |
| **Builders** | one headless session per `"owner": "builder"` segment (your model by default, effort high) | a narrow brief: the concept, the lib, the style frames, the plan's `rules`, its segment and its neighbours' joins, the song over its range as text, the craft rules. Writes only `work/scenes/<id>/` and `work/segments/<id>/`, renders its segment preview, gets one critic round, revises once. Can't change the lib: it writes requests to `work/segments/<id>/requests.md` |
| **Critic** | a cheaper model (Sonnet by default), fresh eyes | at every gate: opens the pictures itself and reports in text to whoever must act, ambition first, then craft |
| **The studio** | Python | validates, dispatches, queues the renders (one video render at a time per machine), assembles from plan.json, runs the checks and the eyes, keeps the clock, the costs and `work/pipeline/state.json` (the web app's Direct page shows it) |

```
1 PLAN      you: the song -> story, cast, concept -> the lib + style frames -> plan.json -> the hero, rendered
GATE 1      plan.json validated; the critic on the hero + the style frames -> you revise once -> the language is locked
2 BUILD     builders on their segments, in parallel: render -> critic -> one revision   ||   you: your segments, the overlays
3 ASSEMBLE  a half-size preview of the whole film, the preview checks, the critic's report on the whole film -> you
4 REVISE    you fix things, and/or send builders back (work/pipeline/tasks.json) -> 3 again;  or "stage": "final" -> 5
5 FINAL     full size, every check, the critic -> you fix any FAIL, write report.md -> delivered as vN
```

Why it has this shape (work with it, not around it):
- **You set how bold the whole video is.** Builders work inside the language you lock, and the critic can only push
  what is there: nobody downstream can make the film bolder than your concept and your hero. Commit to the boldest
  concept you can make well, lock it in code specifically enough that nobody can fall back on a default, and make the
  hero the bar every segment must clear. Don't pick the concept that is easiest to split up: split up the one you want.
- **Lock the language before fanning out.** Gate 1 is the last cheap moment to change it. After it the lib only grows
  (add to it, don't change it: a change re-renders every segment that imports it).
- **Parallel agents speed up thinking and code, not rendering.** Renders queue one at a time on this machine; the
  final render is the floor no number of builders lowers (`S pipeline status <p>` prints its estimate here).
- **The cheap model checks; it never builds.** The scene code is the video. Builders get small contexts (no song
  folder, no images but the style frames); all the looking at frames goes to the critic.
- **Solo** (`--mode solo`, `--builders 0`, a plan that never validated, or a budget the pipeline's stages don't fit:
  never under 45 min, and on a slow machine much more, since the plan, the gate, a builder round and a preview must
  all come before the final render has to start: `S direct <p> --budget 90m --dry-run` says which mode a budget gets
  and why): you are the whole team in one session. The same steps, without builders or gates; the studio's critic looks at each
  `work/previews/*.mp4` and the final.

### work/plan.json, the lib, the hero
The full rules are in `tools/README.md` ("pipeline.py"); `S pipeline validate <p>` says what to change, with the numbers.
- **Segments** in song order, following on with no gap, the first `from` 0, the last `to` the song's end, cut on
  downbeats (boundaries become frames: `round(t × fps)`). Each scene in its **own folder** `work/scenes/<id>/`
  (the render cache follows a scene's folder and its imports; a builder writes only there). Builder segments are
  ones a strong brief can carry on its own: a section or two, 15-40 s. Keep for yourself what the arc turns on: the
  hero, the rupture, the ending if you can.
- **The hero** (`"hero"`, `"owner": "planner"`): the segment where the language shows what it can do, not the easiest
  one. Build it fully: it is the first draft of the video and every builder's bar.
- **Briefs** (`brief`, `enter`, `exit`, `moments`): what happens on which bars, what changes at its ★★★ moments, the
  lyric plan, how bold, what it must not settle for; the picture's state at its first and last frame (the joins).
  A brief that describes something safe gets something safe back.
- **`rules`**: one paragraph every segment keeps (builders get it word for word). **`lib`**: `work/scenes/lib/`:
  the palette, the type, the motifs as functions, the motion rules on the beat grid, the helpers the segments share
  (`import { ... } from "../lib/look.js"`). Everything a segment needs to look like this film, nothing that makes
  every segment look the same.
- **`status`**: todo → drafted (you rendered a draft) → built. You set your own; the studio sets the builders'.
  **`stage`**: planning → planned (your hero is done) → final (you want the final). **Overlays** (the type, a grain):
  transparent passes layered over the joined segments.

### Autonomous runs (`studio.py direct`, the web app)
The studio runs the stages. You work, then **end your turn**: it validates and gates the plan, starts the builders,
assembles, renders the final, and messages you (the brief lists each stage's deadline; its messages may join a turn
you are in). Don't run `pipeline build`, `pipeline gate` or `pipeline assemble` yourself there: the studio does, and
each costs a critique. `pipeline validate`, `pipeline render --only <id>`, `pipeline status`, `clock` and a second
look with `S critique <p> <video> --focus "..."` are yours. Solo runs: mux full previews into
`work/previews/preview_N.mp4` (the runner's critic looks at each); `pipeline assemble --preview --no-critic` is fine
too (without `--no-critic` it would be critiqued twice).

### Interactive runs: you run the stages
The same commands the runner uses; the web app's Direct page shows the same state (the builders' cards, the critic's
reports, the costs). Run anything over about two minutes **in the background** (Bash `run_in_background`) and keep
working: you're told when it ends. Don't poll it with sleep loops.

| stage | command | takes | what comes back |
|---|---|---|---|
| 1 | `S pipeline validate <p>` | seconds | the plan's table, or errors that say what to change (with the numbers to set); exit 1 |
| 1 | `S pipeline render <p> --preview --only <hero>` | a few min (it waits for the render queue) | per segment rendered / unchanged (from the cache) / failed, with its log; `work/segments/<id>/<id>_preview.mp4` |
| gate 1 | `S pipeline gate <p>` | 2-5 min | the critic's report on the hero and the style frames, with scores: revise the language now if it's right, then lock it |
| 2 | `S pipeline build <p> [--builders 3] [--budget 30m]` | 10-40 min: **background** | blocks until every builder and its critic round is done, then one line per segment (state, scores, cost) and the builders' requests for the lib; build your own segments meanwhile |
| any | `S pipeline status <p>` | seconds | the plan, each segment's renders (current or stale), sessions, gates, costs, the final's estimate, the render queue |
| 3 | `S pipeline assemble <p> --preview` | what changed + 3-6 min | `work/previews/preview_N.mp4`, the preview checks, the critic's report on the whole film (read it first) |
| 4 | `work/pipeline/tasks.json`, then `S pipeline build <p>` | like build | builders on those segments, each with your note, its scene as it is and its last critique |
| 5 | `"stage": "final"`, then `S pipeline assemble <p> --final` | the final's estimate + ~5 min | `final/music-video.mp4`, every check, the critic on the final |
| 5 | `S deliver <p> --critic` | a minute | the cut archived as vN with its checks and the critic's notes |

`pipeline build --only s1,s3` builds those (any owner); `--builder-model sonnet` is an option, but the scene code is
the video. `S stop <p>` stops a running build (the builders wrap up). `pipeline build` refuses while an autonomous run
is going on in the project.

## 0. Where are we? (read little)
- `project.json` (the user's notes: `about`, `mood`, `ideas`, `avoid`, `format`; `refs`: their reference pictures in
  `refs/`, each with who it is and a note; `versions`), `work/log.md` (your own log, if any), `review/notes.json` (the
  user's notes per version), `ls final/`, and `S pipeline status <p>` (the plan, the segments' renders, the last run's
  stages, gates and costs).
- No `song/song-map.json` → `studio.py ingest <p>` first (a few minutes; progress in `cache/status.json`).
- Open notes (`done: false`) on the newest version → **Review round** (below).
- Otherwise continue from the first unfinished step; say in one line where you pick up.

**Efficiency (usage is the user's money):** read each big file once, in order, and write your own digest
(`work/song-notes.md`); afterwards use the digest and `songq.py` (`summary`, `at 83.2`, `section "chorus 2"`,
`moments`, `bars 16 24`, `hits kick 30 40`, `lines`) instead of re-reading. Never print whole JSON files or frames.json.
Let the critic look at full previews; you open only the pictures you need. Render segments, not the whole song, while
iterating. Keep notes short.

## 1. Read the song (once)
In this order: `song/README.md` → `song/report.json` (`warnings`, `confidence`, `signals`: how far to trust each part:
estimated stems, a fallback beat tracker or estimated word times change what you can time tightly) →
`song/song-map.md` (at a glance, sections, **moments**, bars) → `song/overview.png` → `song/lyrics.md` →
`song/song-story.md`. Open `song/pages/bars_*.png` only for a stretch you will time tightly.
Write `work/song-notes.md` (≤ 60 lines): tempo/meter/key, length and where the music really starts and ends, the
section table (name, bars, times, energy), the ★★★ moments with bar:beat, the hook lines with times, stops and
tempo changes, what the confidence says, and anything you doubt. The critic reads it, and the builders never read the
song folder: your digest and plan.json are what they know of the song (the studio gives each its range as text).

## 2. The story
If `song/song-story.md` is still the skeleton (it has "_2–4 sentences_"), write it now from the lyric's meaning, the
song map and the user's notes in project.json (their associations win): what it's about, the arc per section (feeling +
what the video could do), lines that could carry a moment, where it peaks and breathes, what the user brings. If the
user edited or drafted it, it is their brief: build on it, don't rewrite it.

## 3. Concept (read `anti-defaults.md` next to this file first)
**Freedom**: the user's notes (and the story) are a starting point, not a spec. They want the director to have a lot
of freedom whether they wrote much or little, and the less they wrote, the more is yours. Of their notes only `avoid`
is a hard rule (the craft rules below never bend either). Pick the boldest concept you can make well in the time, not
the safest: you set how bold the whole video is, and the pipeline only carries it out.

**The cast first**, if there are reference pictures (`refs`): look at each one once (Read shows images) and write
`work/cast.md`: per person (or thing), what makes them recognisable at the level of detail your video draws (hair
shape and colour, skin tone, glasses, beard, build, the clothes they're known for, a few colours as hex). The pictures
fix who is who, not the style: likeness comes from a few of those features, drawn in whatever language the concept
picks (a few lines, a silhouette, a colour pair, a puppet). Never put the pictures, or images made from their pixels
(filtered, cut out, auto-traced), in the video. Afterwards use `work/cast.md`, not the pictures.

Think of three genuinely different concepts (one from the story, one from the song's structure or sound, one wild),
then commit to one. Write `work/concept.md` (every builder reads it word for word, the critic judges against it):
- the idea in two sentences, and why it belongs to *this* song
- the visual language: marks / shapes / figures, palette, typography (fonts in `assets/fonts`, all OFL / Apache, licences
  there), motion rules, camera (or none); one deliberate rupture
- "dance or story": who or what moves with intent, and how (choreography on the beat grid, a character arc per section);
  the people from `work/cast.md`, if any, and how this visual language draws them
- 3-5 **signature moments** on the song's ★★★ moments, each with bar:beat and what changes there
- a section plan: | time | bars | music | picture | (and, in the pipeline, which segment and who builds it)
- renderers and a time plan: frames = duration × fps; per-frame cost from `tools/speeds.json` (× 1.5 safety) for
  previews and the final; choose heavy 3D only if the final render fits in a third of the time left (renders queue one
  at a time: builders don't make the final faster)
- what the user asked to avoid, and the lyric text plan (which lines appear, how, or none)

## 4. The language in code, and style frames
- **The lib** `work/scenes/lib/`: the concept as code the segments import (palette, type, the motifs as functions, how
  things move on this song's beats, the camera or its absence). Specific enough that a builder can't fall back on a
  default; open enough that each segment can still surprise.
- **Style frames**: 2-4 stills at the signature moments at full size (`web-engine/render.py <scene> --still
  61.2,83.0 --out work/style-frames/a.png` or `blender/run.py <scene> --still ...`), into `work/style-frames/`
  (listed in plan.json `style_frames`: the builders' only pictures). Look at each. Ask: is this a default? would any
  song have produced it? is the type readable (contrast ≥ 3:1, ≥ 34 px at 1080p)? Redo weak ones before building.

## 5. The plan and the hero
Write `work/plan.json` (above) and `S pipeline validate <p>` until it is valid. Build the hero in
`work/scenes/<hero>/`, render it (`S pipeline render <p> --preview --only <hero>`, only its frames), look at what you
need (one `eyes.py strip` or `beats` on `work/segments/<hero>/<hero>_preview.mp4`), make it as strong as you can, set
`"stage": "planned"`. Then **gate 1**: autonomous runs: end your turn; interactive: `S pipeline gate <p>` (background).
Take what's right in the critic's report on the language, push harder where it says you played safe, and change the
lib and the style frames now if you're going to: after this they are what every builder copies.

## 6. Build
- Builders: autonomous runs: the studio starts them when you end your turn after gate 1; interactive:
  `S pipeline build <p>` in the background. Meanwhile build your own segments and the overlays. A builder that fails
  (crashed, out of time) isn't redone by the studio: take it over, give its time to a neighbour, or send a builder
  again. Read `work/segments/<id>/requests.md`: add what's right to the lib (add, don't change).
- Scenes (yours and theirs): the web engine (`tools/web-engine/README.md`) gives every scene the song: `f.t`, `f.bar`,
  `f.beat`, `f.beatPhase`, `f.c.<curve>` (frames.json), `MV.t(bar, beat)`, `MV.pulse('kick', t)`, `MV.hits('snare')`,
  `MV.lineAt(t)`, `MV.wordAt(t)`, `MV.cue(t)`, seeded `MV.rand(key)` and `MV.noise(x, y, z)`. Blender scenes get the
  same through `mv.song` (`tools/blender/README.md`). A segment's scene renders only its own frames, in song time.
- **Deterministic**: render(f) depends only on f. Simulations set `stateful: true` (pre-rolled). No wall-clock time.
- Text: mark DOM text `data-mv-text="lyric|lyric-word|title|credit|label|deco"`; canvas/WebGL text calls
  `MV.text(str, {x, y, w, h, color, size, role})` every frame it shows. Black or frozen on purpose: `MV.intent('black',
  t0, t1, 'why')` (or `work/intent.json`). Otherwise the checks flag them.
- Layers: a 3D pass and a transparent 2D/type pass are separate items (an overlay in plan.json, or `--transparent`
  and `compose.py layer` by hand); `pipeline assemble` joins the segments frame-exact and adds the song. Solo without a
  plan: `compose.py concat`, `compose.py layer`, `compose.py mux` yourself.
- Render only what changed: `pipeline render` skips a segment whose scene, lib imports and settings are unchanged.

## 7. Assemble → look → revise (most of the time goes here)
- Previews: the whole film at half size (same fps: sync checks still hold): `S pipeline assemble <p> --preview`
  (autonomous pipeline runs: the studio assembles whenever you end your turn in stage 4) → `work/previews/preview_N.mp4`.
  Solo without a plan: render at `--preview`, then `compose.py mux <picture> -o work/previews/preview_N.mp4` (that
  folder and name: the studio's critic looks for them).
- **The critic does the looking** (cheap model, fresh eyes, 2-4 min): the assemble runs it on the whole film (the arc,
  the joins, the language held, the signature moments, the ending) and prints its report; elsewhere `S critique <p>
  <video>` (add `--focus "the chorus 2 type"` to aim it). It runs the eyes, opens the pictures itself, judges them
  against `work/concept.md`, the song and the user's notes, ambition first, then the craft rules, and prints ≤ 60
  lines: scores, the worst problems with times and a direction to push, what works, what it can't tell, and the
  pictures it opened. It never knows your reasoning, so it can be wrong: take what's right, push harder where it says
  you played safe, ignore what's wrong, and log what you took. In autonomous runs every report comes to you as a
  message: don't run it twice.
- **Your own looking is targeted, not a second full pass**: after a full preview, don't run `eyes.py look` on it or
  open its sheets yourself (the critic did; its pictures are in the video's eyes folder: `work/eyes/<name>/`, a
  segment's in `work/segments/<id>/eyes/<name>/`). Open only the pictures its report names that you need to judge
  yourself, and ask the eyes one question at a time: `eyes.py beats <video> --section "chorus 2"` (does each downbeat
  land?), `eyes.py strip <video> --at 61.2` (a signature moment frame by frame), `eyes.py sheet <video> --section
  ...`, `eyes.py motion` (does the picture's energy follow the song's?), `eyes.py sync` (text: cuts and hits within
  one frame of the music, drift per section). `checks.py text` and `checks.py flash` work on previews too. Segment
  renders while building: one strip or beat sheet on what you changed. Without the critic (it's off, or can't run):
  `eyes.py look <video>` → read its `index.md`, open its pictures.
- **Revise the film, not the segment**: fix what you own yourself (re-render only what you change: `S pipeline render
  <p> --preview --only <id>`), and send builders back with `work/pipeline/tasks.json` `[{"segment": "s3", "what":
  "the note, and what you want"}]` (autonomous: end your turn; interactive: `S pipeline build <p>`). A join that
  doesn't hold may mean moving a boundary in plan.json. Then assemble again. Log every pass in `work/log.md`: what you
  rendered, what the critic said, what you took and why.

## 8. Final, checks, report
- Leave the final its time: its estimated render time (`S pipeline status <p>`) plus the checks and a fix. In
  autonomous runs set `"stage": "final"` and end your turn (the studio also starts the final by itself when the time
  left is what it needs); interactive: `S pipeline assemble <p> --final` (background) → `final/music-video.mp4`
  (every segment at full size, joined, layered, the song muxed). Solo without a plan: full-size passes, compose,
  `compose.py mux <picture>` → `final/music-video.mp4`.
- The checks (`studio.py check <p>`; the final assemble runs them): spec (size, fps, H.264 yuv420p + AAC, the song's
  length to the frame, plays to the end, the audio identical to the song file and in sync, no undeclared black/frozen
  stretches), photosensitivity, text, sync. Fix every FAIL (re-render only the segment you change). Look at
  `work/checks/report.md`.
- The critic on the final (the final assemble and the runner do it; else `S critique <p> final/music-video.mp4`). If
  it finds a high-severity problem you can fix in the time left, fix it and render again.
- `report.md` (project folder): what you made and the idea behind it; how you made it (renderers, segments and who
  built each, passes); what you checked (the numbers); what you're unsure about, with **the timestamps a human should
  check by ear** (QA can't hear: phrase-level musical awkwardness, a moment that may land a beat late in feel, the
  ending, the joins between segments). The studio appends the run settings (models, efforts, budget, the stages, wall
  time, costs by role) itself. Improving a cut within the same run: rewrite the report so it describes the cut you
  deliver (a short "Changes since the first cut" list is fine); "Round N" sections are only for the user's review
  rounds.
- Interactive runs: `S deliver <p> --critic` archives the cut as `final/music-video_vN.mp4`, records it and keeps the
  critic's notes on it as `final/critic_vN.md` (reused when it already looked at this exact cut; the autonomous runner
  does all this for you).

## Review round
`review/notes.json`: `[{id, version: "v2", t, text, done, fix}]`. For each open note on the newest version:
`songq.py at <t>` (what happens there), the segment at that time in plan.json, look at `eyes.py strip
final/music-video.mp4 --at <t>`, find the root cause. **Fix the film, not the frame**: a timing change moves
everything after it against the music; re-think the downstream sections as a whole instead of squeezing the fix in
(a note can move a boundary in plan.json). A video made with a plan starts at stage 4: fix it yourself, or send a
builder back with `work/pipeline/tasks.json`; assemble, then the final. Keep what the user liked. Then render, check,
mark each note `done: true` with a one-line `fix`, add a "Round N" section at the top of report.md, deliver (the new
cut becomes vN+1; earlier versions stay in final/).

## Working with workflows (ultracode)
Ambition without the token burn. **The pipeline is the fan-out**: builders are the parallel agents that build, each
with a narrow brief, its own folders, a critic round and a cost the studio counts and shows. So:
- **Never build segments with workflow agents or subagents**: segments are built by `S pipeline build` (in the
  background), which gives each builder exactly what it needs, keeps it out of the lib and the other segments, queues
  its renders and runs its critic round. An agent you start yourself gets none of that.
- **Use workflows only for your own thinking**, where ideas multiply, not tokens: three to five genuinely different
  concepts from different angles before you commit, variants of a signature moment, alternative style frames or lib
  motifs before gate 1. Not: several agents reading the same song folder, looking at the same render, or babysitting
  the same command.
- **Every subagent gets `work/song-notes.md` + its task**, never "read the song folder": write the digest (section 1)
  first. Concept agents also get the user's notes and their own angle (that's where they must differ); once you've
  committed, every agent also gets `work/concept.md`. A subagent that needs a fact asks `songq.py`.
- **Looking at renders → the studio's critic** (the gates do it; `S critique <p> <video> [--focus "..."]` for a
  second look at one part), not reviewer agents on the top model: it opens the pictures itself on a cheaper model and
  returns text. One critique per cut is enough.
- **Mechanical stages are commands, not thinking**: rendering, assembling, the checks and the eyes run as plain Bash
  in the background. If a workflow agent must drive one, give it `model: 'sonnet'` (or `'haiku'`) and `effort: 'low'`.
- **Keep the top model for** the concept, the lib, the hero and your own segments, the builders' briefs, and deciding
  what to do with the critic's reports.
- The point of fanning out is a bolder video, not a safer consensus: never let agents average an idea down. When
  variants compete, pick the most ambitious one that can be made well in the time, and graft the best of the others.

## Craft rules (from real review rounds; they never bend, the look always can)
1. **Everything lands on a musical event**: every cut, flash, hit, reveal, camera move start and type entrance on a beat,
   8th, downbeat, a stop or a sung word (from hits.json / song-map.json), within one frame (`round(t × fps)`).
2. **Lyric text appears at the start of its sung phrase**: on the beat or 8th at or just before the aligned onset
   (`MV.cue(t)` / `song.cue(t)`), never after it. If word times are estimated (report.json), time lyric text to
   line starts only.
3. **Reading time**: text stays fully visible ≥ 0.8 s + 55 ms per character (1.4-3 s) before it goes or the shot
   cuts; word-by-word kinetic type is fine as long as the line as a whole can be read. Text never clips off screen,
   stays in the title-safe area, and keeps ≥ 3:1 contrast with what's behind it (plates, outlines, or a design that
   makes room).
4. **Nothing is revealed in a shot's last beat.** Fewer ideas with room beat crammed ones.
5. **One committed visual language per video**, with one deliberate rupture at the biggest moment.
6. **Structure follows the song's energy**: quiet intros get space, builds accelerate, drops get the boldest idea,
   the ending lands (the final hit, the ring-out, silence after the music ends).
7. **Fix the film, not the frame** (see Review round).
8. **No rapid bright flashing**: no more than 3 flashes a second over any large area, no saturated red flashes;
   `checks.py flash` must pass.
9. **QA can't hear**: say which timestamps need a human ear.
10. Respect the user's "avoid" absolutely.
