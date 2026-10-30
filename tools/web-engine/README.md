# web-engine: HTML / JS scenes rendered frame by frame

Headless Chromium (driven by Python Playwright: Node isn't needed) renders a scene one frame at a time. The runtime
(`runtime/mv.js`) owns time and chance, so the same frame always gives the same pixels: in any order, on any worker,
after a crash.

```
python tools/studio.py run <p> web-engine/render.py work/scenes/intro/scene.js --out work/renders/intro.mp4 --section intro --preview
python tools/studio.py run <p> web-engine/render.py work/scenes/type.html --out work/renders/type --transparent        # PNG frames with alpha
python tools/studio.py run <p> web-engine/render.py work/scenes/intro/scene.js --still 12.5,40.2 --out work/style-frames
```
Options: `--from/--to` (seconds) or `--frames a:b` or `--section "chorus 1"` (default: the whole song) · `--preview`
(half size, faster capture and encode, same fps) or `--scale 0.25` · `--transparent` (a folder of PNGs, or `.mov` =
ProRes 4444) · `--workers N` (one browser each; default half the cores) · `--gpu auto|on|off` · `--seed N` ·
`--fresh` · `--chunk 2.0` (seconds per chunk) · `--lossless` (PNG capture instead of JPEG q98).

## A scene
A JS module (`scene.js`; the engine provides the page, the fonts, the import map) or an HTML page (`scene.html`; the
engine injects the runtime, fonts and import map into its `<head>`; call `MV.scene({...})`).
```js
import * as THREE from "three";                       // also "three/addons/...", "animejs", "simplex-noise"; d3 is global
let ctx;
export default {
  seed: 7,                    // optional: Math.random / MV.rand / MV.noise are seeded from it
  stateful: false,            // true = render(f) depends on earlier frames (a simulation): the engine pre-rolls from 0
  preroll: null,              // with stateful: seconds to pre-roll before a chunk instead of from 0
  transparent: false,         // true = this scene is a layer (keep the page background transparent)
  async setup(mv) {           // once: canvases, geometry, layouts, data
    ctx = mv.canvas().getContext("2d");   // a full-size canvas on #stage (WebGL: mv.canvas() -> THREE.WebGLRenderer({canvas, preserveDrawingBuffer: true}))
  },
  render(f) {                 // one frame; may be async. Depend ONLY on f (and what setup built)
    ctx.clearRect(0, 0, f.w, f.h);
  },
};
```
`f` = `{frame, t, time, fps, w, h, dt, progress, bar, beat, beatPhase, barPhase, section, sectionIndex, sectionPhase,
chord, line, lineProgress, word, c}`:
- `f.t` is the **end of the frame's time slot** (`(frame + 0.5) / fps`): whatever happens in a frame's slot (a downbeat,
  a kick, a sung word) shows in that frame, the studio's rule `frame = round(t × fps)`. `f.time = frame / fps`.
- `f.c.<curve>` = this frame's value from `song/frames.json` (`loud`, `level`, `vocals`, `drums`, `bass`, `other`,
  `sub`, `low`, `lowmid`, `highmid`, `high`, `transient`, `bright`, `width`, `voice_midi`, `bass_midi`, `lufs`, `centroid_hz`).
- `f.bar` (−1 outside the beat grid), `f.beat` (position in the bar, 2.5 = the 8th after beat 2), phases 0..1.
- `f.section` (`{name, label, t0, t1, bar0, bar1, ...}`), `f.line` (the lyric line being sung, `{text, t, end, words}`), `f.word`.

## MV (window.MV)
- Song data: `MV.song` (song-map.json), `MV.frames`, `MV.hitsData`, `MV.duration`, `MV.nFrames`, `MV.width`, `MV.height`,
  `MV.fps`, `MV.s` (= min(w, h) / 1080: a scale for sizes), `MV.transparent`.
- Time: `MV.t(bar, beat)` → seconds; `MV.pos(t)` → `[bar, beat]`; `MV.frame(t)`; `MV.cue(t)` (the beat or 8th at or just
  before t: where lyric text for a word sung at t appears); `MV.snap(t, "beat"|"8th"|"bar")`.
- What happens: `MV.sectionAt(t)`, `MV.section(name)`, `MV.moments(stars)`, `MV.lineAt(t)`, `MV.wordAt(t)`, `MV.chordAt(t)`.
- Hits (`song/hits.json`): `MV.hits(kind, minStrength)` (kinds: `beats`, `downbeats`, `kick`, `snare`, `hat`, `crash`,
  `bass`, `other`, `vocal`, `onset`, `words`, `lines`, `chords`, `sections`, `moments`, `notes`),
  `MV.hitsBetween(kind, t0, t1)`, `MV.pulse(kind, t, decay = 0.15)` (0..1, jumps on a hit and decays),
  `MV.since(kind, t)` (seconds since the last one); `MV.curve(name, frame)`.
- Chance and shape: `MV.random()` (seeded per frame), `MV.rand(key, salt)` (the same number for the same key on every
  frame), `MV.noise(x, y, z)` (seeded Perlin), `MV.lerp`, `MV.clamp`, `MV.smoothstep`, `MV.ease.*`.
- For the checks: DOM text marked `data-mv-text="role"` is recorded by itself (its glyphs' box, colour, size, opacity);
  canvas / WebGL text calls `MV.text(str, {x, y, w, h, color, size, role})` every frame it is on screen. Roles: `text`,
  `title`, `credit`, `lyric`, `lyric-word`, `label` (counters), `deco` (type as texture, not checked).
  `MV.intent("black"|"frozen", t0, t1, "why")` declares a deliberate black or frozen stretch. `MV.log(...)`.

## Determinism
`Date`, `performance.now`, `requestAnimationFrame`, `setTimeout`/`setInterval` run on the frame clock (in the live
preview too: the live loop uses the browser's own `requestAnimationFrame`, saved before the runtime replaces it); CSS animations,
transitions and `element.animate()` are paused and set to the frame's time; `Math.random` is re-seeded every frame.
anime.js: create timelines with `autoplay: false` and `.seek(f.t * 1000)` in render (or let them run: they follow the
frame clock). Don't fetch anything from the network: the page can only load the studio's runtime, vendor files, fonts,
the song folder and the project folder.

## Output, speed, resume
- `.mp4`: chunks on a global grid (2 s), each encoded without B-frames, joined frame-exact by stream copy. A re-run
  with the same scene version reuses finished chunks (a crash, a section render followed by the whole song); any change in
  the scene's folder, or in a file it reaches by a relative `import` (static or dynamic, followed through the files it
  imports, outside its folder too: a shared `work/scenes/lib/`), renders them again; a sibling scene in its own folder
  doesn't. `--fresh` starts over. Keep each scene in its own folder: a scene loose in `work/scenes/` hashes everything
  below it.
- One render at a time per machine: a video render waits for the render queue (`waiting for the render queue: <who>
  since <time>`; see `tools/README.md`). `--still` doesn't wait.
- `--transparent`: PNG frames `frame_000123.png` numbered by song frame (alpha kept), or a ProRes 4444 `.mov`.
- Every output gets `<out>.mv.json`: the frame range, the browser and WebGL renderer used, and every text on screen.
- Capture: JPEG q98 for opaque passes (Chrome's PNG encoder is ~7× slower on busy frames; invisible after H.264),
  PNG for transparent passes and `--lossless`. Speeds on this machine: `tools/speeds.json`.
- GPU: with `--gpu auto` the engine checks the WebGL renderer and falls back to SwiftShader (software) when there is no
  GPU. Browser: `MV_CHROME`, Playwright's Chromium, Chrome, Edge.

## Live preview (the web app plays scenes with the song, no render)
The Watch page's **▶ live** view plays a project's scenes in the user's browser with the song, so the one person who
can hear gets their ears in early, while the director still works. It is the same runtime in **live mode**
(`__MV_CONFIG.live`, set only by the live shell; a render never has it, and without it nothing in `mv.js` behaves any
differently: the smoke test renders frames with and without the live block and compares them pixel for pixel):
- The page the live server sends is the renderer's own (`/_mv/live.html?scene=<path from the project>&scale=0.5`: the
  same shell, fonts, import map and URL space; an HTML scene is served at its own URL with the same head), at the
  delivery size × the size the user picks (¼, ½, full; default ½).
- The web app owns the clock (the song's `<audio>`): it posts `{type: "mv-clock", t, playing, rate, seek}` on every
  frame while it plays and on a seek, a pause or a rate change. The page shows frame `round(t × fps)` of that clock as it
  is now, on the browser's real `requestAnimationFrame`, and renders only when that frame changed and the last render
  is done: **a scene slower than real time drops frames, the timing stays true** (what you see is where the song is).
- Stateful scenes step on one frame at a time; a jump pre-rolls exactly as a render does (from 0, or `preroll` seconds
  before), in slices that let the browser breathe ("catching up 40%"), and a new seek cancels a catch-up under way. A
  jump *back* in a stateful scene without `preroll` reloads the page first (its state must start from `setup()` again).
  So a live frame is the renderer's frame, pixel for pixel (checked by the smoke test, stateful scenes included). A step
  back that isn't a seek (the clock correcting itself: a pause, a play, a new sound delay, a switch of stems) holds the
  frame until the clock passes it, so pausing never restarts a simulation.
- While it plays, a stateful scene that can't render every frame as fast as the clock runs (a catch-up that doesn't
  gain on the clock) skips to the clock's frame instead, like a stateless one: it stays on time, and its state is then
  close to the render's, not exact (`approx`, said in the live view), until a jump back starts it again. Paused, a
  catch-up is always exact.
- The page posts `{type: "mv-live", frame, fps (achieved), ms (per render), need, ready, catching_up, approx, errors}`
  to the web app ~4 times a second (errors at once); the live view shows it per layer: "24 of 30 fps · slower than real
  time here", "catching up 40%", "skipping frames to keep time", the scene's error.
- The per-frame text list is cleared every frame (no leak); texts aren't collected live (the checks read renders).

**What plays live**: web scenes (JS modules and HTML pages) as they are on disk (the live server sends `no-store`: a
scene that changed reloads within 5 s, its imports and the lib included), stateful ones too; transparent overlays over
them, with the plan's blend mode (`mix-blend-mode`) and opacity; rendered passes as muted videos in their slots (a
Blender segment's preview render, a `{"video": ...}` layer). **What doesn't**: a Blender scene itself (its preview render
plays, or a labelled slate), a transparent PNG-folder pass (a Blender overlay), compose's own work (the joins are the
slots; the blend is the browser's), anything not in a scene. The final render stays the reference; the live view is
there to hear the picture early.

**Make scenes that play near real time at half size**, so the user can judge them with the music: do the heavy work in
`setup()` (geometry, layouts, text measurement, textures, precomputed curves), keep `render(f)` to drawing, prefer
`preroll` (a few seconds) over a full history for simulations, and say in `work/log.md` when a scene can't (the live
view will show it slower than real time anyway; the render is unaffected).

**work/live.json** (optional: the director's say over what plays, an override of the plan or the renders):
```json
{"layers": [{"scene": "work/scenes/world.js", "from": 0, "to": 61.2},
            {"scene": "work/scenes/stage.js", "section": "chorus 2"},
            {"video": "work/renders/stage.mp4"},
            {"scene": "work/scenes/type.html", "transparent": true}]}
```
In order, bottom to top; `from` / `to` in song seconds, or `section` (a name or a list of names: those sections only,
each its own slot); default the whole song. A layer the live view can't use (a section that isn't in the song, a scene
that isn't there) is left out with a warning in the live view; the others play.
A `video` is a rendered pass shown in its slot from its `.mv.json` range (or `from` / `to`), muted, synced to the song.
Also `mode` (normal, screen, add, multiply, lighten, darken, difference), `opacity`, `seed`, `id`. Without it the live view
plays `work/plan.json` (the plan tools' segments in their slots, the overlays on top), else the newest renders in
`work/renders/` (each web pass's scene live over its range, other passes as their video; opaque passes by range, the
newest wins; transparent ones on top). Any scene can also be played alone over the whole song.

## Vendored (no network at render time; licences in vendor/LICENSES)
three.js 0.186.1 (+ addons: postprocessing, geometries, lines, loaders for fonts / SVG / TTF, ...), anime.js 4.5.0,
d3 7.9.0, simplex-noise 4.0.3, opentype.js 1.3.4 (for three's TTFLoader). Fonts: `assets/fonts` as CSS families
(Inter, Space Mono, IBM Plex Mono, Anton, Bebas Neue, Archivo Narrow, Space Grotesk, Syne, Unbounded, Fraunces,
Instrument Serif, Playfair Display, Caveat, Permanent Marker, Special Elite, Noto Sans, Noto Sans Georgian).

Examples (mechanics, not looks): `examples/canvas-pulse.js`, `examples/dom-type.html`; the benchmark loads
`examples/bench2d.js` and `examples/bench3d.js`.
