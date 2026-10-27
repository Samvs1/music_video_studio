// Smoke-test scene (Canvas 2D): a field of marks whose arrangement changes on every downbeat (hard visual events
// for the sync check) and whose density follows the song's loudness. Deterministic: depends only on f.
let ctx;
const PAL = [["#101418", "#e9e4d8"], ["#1b1020", "#f2d5c4"], ["#0e1a14", "#d8efe0"], ["#1a1408", "#f4e2b8"]];
export default {
  setup(mv) { ctx = mv.canvas().getContext("2d"); },
  render(f) {
    const W = f.w, H = f.h, s = Math.min(W, H) / 1080;
    const bar = Math.max(0, f.bar), [bg, fg] = PAL[(bar + Math.max(0, f.sectionIndex)) % PAL.length];
    ctx.fillStyle = bg; ctx.fillRect(0, 0, W, H);
    const n = 40 + Math.round(160 * f.c.loud), kick = MV.pulse("kick", f.t, 0.12);
    ctx.fillStyle = fg;
    for (let i = 0; i < n; i++) {
      const x = MV.rand(i, "x" + bar) * W, y = MV.rand(i, "y" + bar) * H * 0.62;
      const r = (6 + 18 * MV.rand(i, "r")) * s * (1 + 0.35 * kick);
      ctx.globalAlpha = 0.25 + 0.6 * MV.rand(i, "a");
      ctx.fillRect(x - r / 2, y - r / 2, r, r * (0.2 + f.beatPhase));
    }
    ctx.globalAlpha = 1;
    const band = MV.rand(bar, "band") * 0.7 + 0.15;               // a band that jumps to a new place on every downbeat
    ctx.fillStyle = fg; ctx.globalAlpha = 0.18 + 0.25 * f.c.drums;
    ctx.fillRect(band * W - 0.06 * W, H * 0.66, 0.12 * W, H * 0.34);
    ctx.globalAlpha = 1;
  },
};
