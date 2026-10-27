/* Music Video Studio web-engine runtime (window.MV).
 *
 * Loaded before any scene code. It owns time and chance, so a scene renders the same pixels for the same frame no
 * matter which worker renders it, in which order, or how fast:
 *   - Date, performance.now, requestAnimationFrame, setTimeout/setInterval run on the frame clock (frame / fps)
 *   - CSS animations, transitions and element.animate() are paused and set to the frame time
 *   - Math.random is re-seeded for every frame (same frame = same numbers); MV.rand(key) is stable across frames
 *   - render(f) must depend only on f (and on what setup built). Scenes that simulate (state carried from frame to
 *     frame) set `stateful: true`: the engine then pre-rolls every frame from 0 before a chunk it renders.
 *
 * A scene module (scene.js) exports default {setup?, render, seed?, stateful?, transparent?}:
 *     export default {
 *       async setup(mv) { ... build things once; mv.canvas(), mv.song, mv.width, ... },
 *       render(f) { ... draw frame f: f.t, f.frame, f.bar, f.beat, f.beatPhase, f.c.drums, f.section, f.word ... }
 *     }
 *   f.t is the end of the frame's time slot ((frame + 0.5) / fps): whatever happens in a frame's slot shows in that
 *   frame, the same rule as hits.json (frame = round(t * fps)). Use f.t for everything (MV.pulse(kind, f.t), ...).
 * An HTML scene calls MV.scene({...}) itself. See tools/web-engine/README.md.
 */
(function () {
  "use strict";
  const CFG = window.__MV_CONFIG || {};
  const W = CFG.w || 1920, H = CFG.h || 1080, FPS = CFG.fps || 30;
  const SEED = (CFG.seed == null ? 1 : CFG.seed) >>> 0;
  const real = {setTimeout: window.setTimeout.bind(window), Date: window.Date, now: performance.now.bind(performance)};
  const errors = [];
  window.addEventListener("error", e => errors.push(String(e.message || e.error) + (e.filename ? ` (${e.filename}:${e.lineno})` : "")));
  window.addEventListener("unhandledrejection", e => errors.push("unhandled promise rejection: " + String(e.reason && (e.reason.stack || e.reason))));

  // ------------------------------------------------------------------ the frame clock
  let NOW = 0;                                   // ms of song time
  const EPOCH = 946684800000;                    // Date.now() at song time 0 (2000-01-01, fixed)
  class VDate extends real.Date {
    constructor(...a) { if (a.length === 0) super(EPOCH + NOW); else super(...a); }
    static now() { return EPOCH + NOW; }
  }
  window.Date = VDate;
  try { Object.defineProperty(performance, "now", {value: () => NOW, configurable: true}); } catch (e) { performance.now = () => NOW; }
  let rafQ = new Map(), rafId = 0;
  window.requestAnimationFrame = cb => { rafQ.set(++rafId, cb); return rafId; };
  window.cancelAnimationFrame = id => { rafQ.delete(id); };
  const timers = new Map(); let timerId = 0;
  window.setTimeout = (cb, ms, ...args) => { timers.set(++timerId, {due: NOW + Math.max(0, +ms || 0), cb, args, every: 0}); return timerId; };
  window.setInterval = (cb, ms, ...args) => { const e = Math.max(1, +ms || 0); timers.set(++timerId, {due: NOW + e, cb, args, every: e}); return timerId; };
  window.clearTimeout = window.clearInterval = id => { timers.delete(id); };
  function fireTimers() {
    for (let guard = 0; guard < 10000; guard++) {
      let best = null;
      for (const [id, tm] of timers) if (tm.due <= NOW && (!best || tm.due < best[1].due)) best = [id, tm];
      if (!best) return;
      const [id, tm] = best;
      if (tm.every) tm.due += tm.every; else timers.delete(id);
      try { typeof tm.cb === "function" ? tm.cb(...tm.args) : (0, eval)(String(tm.cb)); } catch (e) { errors.push("timer: " + (e.stack || e)); }
    }
  }
  function flushRaf() {
    const q = rafQ; rafQ = new Map();
    for (const cb of q.values()) { try { cb(NOW); } catch (e) { errors.push("requestAnimationFrame: " + (e.stack || e)); } }
  }
  const seen = new WeakMap();
  function syncAnimations() {
    if (!document.getAnimations) return;
    for (const a of document.getAnimations()) {
      if (!seen.has(a)) { seen.set(a, NOW); try { a.pause(); } catch (e) { /* finished */ } }
      try { a.currentTime = Math.max(0, NOW - seen.get(a)); } catch (e) { /* ignore */ }
    }
  }

  // ------------------------------------------------------------------ chance
  function hash32(str) {
    let h = 1779033703 ^ str.length;
    for (let i = 0; i < str.length; i++) { h = Math.imul(h ^ str.charCodeAt(i), 3432918353); h = (h << 13) | (h >>> 19); }
    h = Math.imul(h ^ (h >>> 16), 2246822507); h = Math.imul(h ^ (h >>> 13), 3266489909);
    return (h ^= h >>> 16) >>> 0;
  }
  function mulberry32(a) {
    return function () { a |= 0; a = (a + 0x6D2B79F5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
  }
  let rng = mulberry32(hash32(SEED + ":setup"));
  Math.random = () => rng();
  function reseed(frame) { rng = mulberry32(hash32(SEED + ":frame:" + frame)); }
  // 3D gradient noise (Perlin, seeded by the scene seed)
  const P = new Uint8Array(512);
  { const r = mulberry32(hash32(SEED + ":noise")), p = [...Array(256).keys()];
    for (let i = 255; i > 0; i--) { const j = Math.floor(r() * (i + 1)); [p[i], p[j]] = [p[j], p[i]]; }
    for (let i = 0; i < 512; i++) P[i] = p[i & 255]; }
  const fade = t => t * t * t * (t * (t * 6 - 15) + 10), lerp = (a, b, t) => a + (b - a) * t;
  function grad(h, x, y, z) { const u = h < 8 ? x : y, v = h < 4 ? y : (h === 12 || h === 14 ? x : z); return ((h & 1) ? -u : u) + ((h & 2) ? -v : v); }
  function noise3(x, y = 0, z = 0) {
    const X = Math.floor(x) & 255, Y = Math.floor(y) & 255, Z = Math.floor(z) & 255;
    x -= Math.floor(x); y -= Math.floor(y); z -= Math.floor(z);
    const u = fade(x), v = fade(y), w = fade(z), A = P[X] + Y, AA = P[A] + Z, AB = P[A + 1] + Z, B = P[X + 1] + Y, BA = P[B] + Z, BB = P[B + 1] + Z;
    return lerp(lerp(lerp(grad(P[AA] & 15, x, y, z), grad(P[BA] & 15, x - 1, y, z), u),
                     lerp(grad(P[AB] & 15, x, y - 1, z), grad(P[BB] & 15, x - 1, y - 1, z), u), v),
                lerp(lerp(grad(P[AA + 1] & 15, x, y, z - 1), grad(P[BA + 1] & 15, x - 1, y, z - 1), u),
                     lerp(grad(P[AB + 1] & 15, x, y - 1, z - 1), grad(P[BB + 1] & 15, x - 1, y - 1, z - 1), u), v), w);
  }

  // ------------------------------------------------------------------ song data
  const D = {map: null, frames: null, hits: null};
  const bsearch = (arr, t) => { let lo = 0, hi = arr.length; while (lo < hi) { const m = (lo + hi) >> 1; if (arr[m] <= t) lo = m + 1; else hi = m; } return lo; };
  let beats = [], downbeats = [], dbi = [], beatS = 0.5, eighths = [], wordT = [], wordTimes = [];
  const hitCache = new Map(), hitTimes = new Map();
  function index() {
    const m = D.map;
    beats = m.beats; beatS = m.tempo.beat_s;
    downbeats = m.bars.map(b => b.t);
    const near = t => { const i = bsearch(beats, t); return i > 0 && (i >= beats.length || t - beats[i - 1] < beats[i] - t) ? i - 1 : i; };
    dbi = downbeats.map(near);
    eighths = [];
    beats.forEach((b, i) => { eighths.push(b); eighths.push((b + (i + 1 < beats.length ? beats[i + 1] : b + beatS)) / 2); });
    wordT = [];
    m.lyrics.forEach(x => x.words.forEach(w => wordT.push([w.t, w.w, x.i])));
    wordT.sort((a, b) => a[0] - b[0]);
    wordTimes = wordT.map(w => w[0]);
  }
  function beatTime(x) {
    if (x <= 0) return beats[0] + x * beatS;
    if (x >= beats.length - 1) return beats[beats.length - 1] + (x - beats.length + 1) * beatS;
    const i = Math.floor(x); return beats[i] + (x - i) * (beats[i + 1] - beats[i]);
  }
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

  const MV = {
    width: W, height: H, fps: FPS, seed: SEED, errors, transparent: !!CFG.transparent, s: Math.min(W, H) / 1080,
    get song() { return D.map; }, get frames() { return D.frames; }, get hitsData() { return D.hits; },
    get duration() { return D.map ? D.map.duration : 0; },
    get nFrames() { return D.frames ? D.frames.frames : Math.round(MV.duration * FPS); },
    // --- time <-> bar:beat
    t(bar, beat = 0) {
      if (bar < 0 || bar >= dbi.length) return beatTime((bar < 0 ? dbi[0] + bar * 4 : dbi[dbi.length - 1] + (bar - dbi.length + 1) * 4) + beat);
      return beatTime(dbi[bar] + beat);
    },
    pos(t) {
      if (!beats.length || t < beats[0] - 2 * beatS || t > beats[beats.length - 1] + 4 * beatS) return null;
      const bar = Math.max(0, bsearch(downbeats, t + 1e-6) - 1);
      let j = bsearch(beats, t + 1e-6) - 1, frac;
      if (j < 0) { frac = (t - beats[0]) / beatS; j = 0; } else frac = (t - beats[j]) / ((j + 1 < beats.length ? beats[j + 1] : beats[j] + beatS) - beats[j]);
      return [bar, j - dbi[bar] + frac];
    },
    frame(t) { return Math.round(t * FPS); },
    cue(t) { const i = bsearch(eighths, t + 1e-4) - 1; return i >= 0 ? eighths[i] : t; },
    snap(t, step = "beat") {
      const arr = step === "bar" ? downbeats : step === "8th" ? eighths : beats, i = bsearch(arr, t);
      const c = [arr[i - 1], arr[i]].filter(x => x !== undefined); return c.reduce((a, b) => Math.abs(b - t) < Math.abs(a - t) ? b : a, c[0] ?? t);
    },
    section(name) { return D.map.sections.find(s => s.name === name); },
    sectionAt(t) { return D.map.sections.find(s => t >= s.t0 && t < s.t1) || null; },
    moments(stars = 1) { return D.map.moments.filter(m => m.stars >= stars); },
    lineAt(t, hold = 0.3) { return D.map.lyrics.find(x => t >= x.t - 0.05 && t <= x.end + hold) || null; },
    wordAt(t) { const i = bsearch(wordTimes, t + 1e-4) - 1; const ln = MV.lineAt(t); return i >= 0 && ln && ln.i === wordT[i][2] ? wordT[i][1] : null; },
    chordAt(t) { const c = D.map.chords.find(c => t >= c.t && t < (c.end ?? 1e9)); return c && c.chord !== "N" ? c.chord : null; },
    // --- per-frame curves (frames.json) and hits (hits.json)
    curve(name, frame) {
      const fr = D.frames, arr = fr.curves[name] || fr.grid[name];
      if (!arr) throw new Error(`no curve '${name}': ${Object.keys(fr.curves).concat(Object.keys(fr.grid)).join(", ")}`);
      const k = fr.fps === FPS ? frame : Math.round(frame / FPS * fr.fps);
      return arr[clamp(k, 0, arr.length - 1)];
    },
    hits(kind, minS = 0) {
      const key = kind + "|" + minS;
      if (!hitCache.has(key)) {
        const h = D.hits[kind];
        if (h === undefined) throw new Error(`no hits '${kind}': ${Object.keys(D.hits).join(", ")}`);
        const list = Array.isArray(h) ? h.filter(x => (x.s ?? 1) >= minS) : h;
        hitCache.set(key, list); if (Array.isArray(list)) hitTimes.set(list, list.map(x => x.t));
      }
      return hitCache.get(key);
    },
    hitsBetween(kind, t0, t1, minS = 0) { return MV.hits(kind, minS).filter(h => h.t >= t0 && h.t < t1); },
    pulse(kind, t, decay = 0.15, minS = 0.2) {
      const hs = MV.hits(kind, minS), i = bsearch(hitTimes.get(hs), t + 1e-6); let v = 0;
      for (let k = Math.max(0, i - 8); k < i; k++) v += (hs[k].s ?? 1) * Math.exp(-(t - hs[k].t) / decay);
      return Math.min(1, v);
    },
    since(kind, t, minS = 0.2) { const hs = MV.hits(kind, minS), i = bsearch(hitTimes.get(hs), t + 1e-6) - 1; return i >= 0 ? t - hs[i].t : Infinity; },
    // --- chance and shape
    random: () => rng(),
    rand(key, salt = "") { return mulberry32(hash32(SEED + ":" + key + ":" + salt))(); },
    noise: noise3,
    clamp, lerp, mix: lerp,
    smoothstep: (a, b, x) => { const t = clamp((x - a) / (b - a), 0, 1); return t * t * (3 - 2 * t); },
    ease: {
      linear: t => t, inQuad: t => t * t, outQuad: t => t * (2 - t), inOutQuad: t => t < .5 ? 2 * t * t : -1 + (4 - 2 * t) * t,
      inCubic: t => t * t * t, outCubic: t => (--t) * t * t + 1, inOutCubic: t => t < .5 ? 4 * t * t * t : (t - 1) * (2 * t - 2) * (2 * t - 2) + 1,
      outExpo: t => t === 1 ? 1 : 1 - Math.pow(2, -10 * t), inExpo: t => t === 0 ? 0 : Math.pow(2, 10 * t - 10),
      outBack: t => { const c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2); },
      outElastic: t => t === 0 || t === 1 ? t : Math.pow(2, -10 * t) * Math.sin((t * 10 - 0.75) * (2 * Math.PI / 3)) + 1,
    },
    // --- drawing surfaces
    stage: null,
    canvas(opts = {}) {
      const c = document.createElement("canvas");
      c.width = opts.width || W; c.height = opts.height || H;
      Object.assign(c.style, {position: "absolute", left: "0", top: "0", width: W + "px", height: H + "px"});
      (opts.parent || MV.stage).appendChild(c);
      return c;
    },
    // text on screen, for the checks: DOM text marked data-mv-text="role" is found by itself; canvas / WebGL text
    // calls MV.text(string, {x, y, w, h, color, size, role}) in render() every frame it is visible
    text(str, o = {}) { frameTexts.push({text: String(str), x: o.x || 0, y: o.y || 0, w: o.w || 0, h: o.h || 0, color: o.color || null,
                                         size: o.size || null, role: o.role || "text", opacity: o.opacity ?? 1, overflow: !!o.clipped}); },
    // intended black / frozen stretches (song seconds) for the spec check: MV.intent('black', 0, 1.5, 'fade in')
    intent(kind, t0, t1, why = "") { intents.push({kind, t0, t1, why}); },
    log(...a) { logs.push(a.map(String).join(" ")); },
  };
  const frameTexts = [], intents = [], logs = [];

  // ------------------------------------------------------------------ the frame
  // A frame k shows song time [(k - 0.5) / fps, (k + 0.5) / fps): everything that happens in that slot (a downbeat, a
  // kick, a word) is in this frame. So f.t is the END of the slot; f.time = k / fps is its middle.
  const slot = i => (i + 0.5) / FPS - 1e-6;
  function info(i) {
    const t = slot(i), fr = D.frames, same = fr && fr.fps === FPS, k = same ? clamp(i, 0, fr.frames - 1) : null;
    const p = MV.pos(t), sec = MV.sectionAt(t), ln = MV.lineAt(t);
    const c = {};
    if (fr) for (const name of Object.keys(fr.curves)) Object.defineProperty(c, name, {get: () => MV.curve(name, i), enumerable: true});
    let beatPhase = 0, barPhase = 0;
    if (p) {
      beatPhase = p[1] - Math.floor(p[1]);
      const b = p[0], end = b + 1 < downbeats.length ? downbeats[b + 1] : downbeats[b] + 4 * beatS;
      barPhase = clamp((t - downbeats[b]) / (end - downbeats[b]), 0, 0.9999);
    }
    return {frame: i, t, time: i / FPS, fps: FPS, w: W, h: H, dt: 1 / FPS, progress: t / (MV.duration || 1),
            bar: p ? p[0] : -1, beat: p ? p[1] : 0, beatPhase, barPhase,
            section: sec, sectionIndex: sec ? D.map.sections.indexOf(sec) : -1,
            sectionPhase: sec ? (t - sec.t0) / (sec.t1 - sec.t0) : 0,
            chord: MV.chordAt(t), line: ln, lineProgress: ln ? clamp((t - ln.t) / Math.max(0.1, ln.end - ln.t), 0, 1) : 0,
            word: MV.wordAt(t), c, k};
  }
  function collectTexts() {
    const out = frameTexts.splice(0);
    document.querySelectorAll("[data-mv-text]").forEach(el => {
      const rg = document.createRange();                 // the glyphs' own box, not the element's (a block spans the stage)
      rg.selectNodeContents(el);
      let r = rg.getBoundingClientRect();
      if (r.width < 1 || r.height < 1) r = el.getBoundingClientRect();
      if (r.width < 1 || r.height < 1) return;
      const cs = getComputedStyle(el);
      if (cs.visibility === "hidden" || cs.display === "none") return;
      let op = 1;
      for (let e = el; e && e.nodeType === 1; e = e.parentElement) op *= parseFloat(getComputedStyle(e).opacity || "1");
      if (op < 0.5) return;
      const txt = (el.innerText || el.textContent || "").trim();
      if (!txt) return;
      const clips = /hidden|clip/.test(cs.overflow + cs.overflowX + cs.overflowY) && el.clientWidth > 0 &&
                    (el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1);
      out.push({text: txt, x: r.left, y: r.top, w: r.width, h: r.height, color: cs.color, size: parseFloat(cs.fontSize),
                role: el.dataset.mvText || "text", opacity: op, overflow: clips});
    });
    return out;
  }
  let SCENE = null;
  async function step(i, collect) {
    NOW = slot(i) * 1000;
    reseed(i);
    fireTimers();
    syncAnimations();
    flushRaf();
    const f = info(i);
    if (SCENE && SCENE.render) await SCENE.render(f);
    syncAnimations();
    return collect ? collectTexts() : null;
  }

  // ------------------------------------------------------------------ the engine's entry points
  window.__mvReady = false;
  window.__mvErrors = errors;
  MV.scene = function (scene) { SCENE = scene; return scene; };
  MV._frame = i => step(i, true);
  MV._preroll = async (a, b) => { for (let i = a; i < b; i++) { await step(i, false); frameTexts.length = 0; } return true; };
  MV._intents = () => intents;
  MV._logs = () => logs.splice(0);
  MV._start = async function () {
    const base = CFG.songBase || "/_mv/song/";
    const get = async n => { const r = await fetch(base + n); if (!r.ok) throw new Error(`song/${n}: ${r.status} (run ingest first)`); return r.json(); };
    try {
      [D.map, D.frames, D.hits] = await Promise.all([get("song-map.json"), get("frames.json"), get("hits.json")]);
      index();
      if (D.frames.fps !== FPS) MV.log(`frames.json is at ${D.frames.fps} fps but this render is at ${FPS}: curves use the nearest frame (run studio.py curves --fps ${FPS})`);
    } catch (e) { errors.push(String(e)); throw e; }
    MV.stage = document.getElementById("stage") || document.body;
    if (document.fonts) await document.fonts.ready;
    for (let n = 0; !SCENE && n < 400; n++) await new Promise(r => real.setTimeout(r, 5));
    if (!SCENE) throw new Error("the scene never registered: export default {render(f) {...}} or call MV.scene({...})");
    NOW = 0;
    if (SCENE.setup) {
      let done = false, fail = null;
      Promise.resolve(SCENE.setup(MV)).then(() => { done = true; }, e => { fail = e; done = true; });
      for (let n = 0; !done && n < 24000; n++) {                // up to ~2 min of real time for setup
        await new Promise(r => real.setTimeout(r, 5));
        fireTimers();
        if (!done && timers.size) { let next = Infinity; for (const tm of timers.values()) next = Math.min(next, tm.due); if (next > NOW && next < Infinity) NOW = next; }
      }
      if (fail) throw fail;
      if (!done) throw new Error("setup() did not finish within 2 minutes");
    }
    if (document.fonts) await document.fonts.ready;
    NOW = 0;
    window.__mvReady = true;
    return {w: W, h: H, fps: FPS, frames: MV.nFrames, stateful: !!SCENE.stateful, transparent: !!SCENE.transparent,
            preroll: SCENE.preroll ?? null};
  };
  window.MV = MV;
})();
