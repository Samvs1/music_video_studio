// Live-preview test scene (Canvas 2D, stateful): a mark that walks a step every frame and leaves a fading trail, so
// the picture at a frame depends on every frame before it (a jump needs the pre-roll from 0, as in a render). It
// declares its counter with MV.text every frame (the live mode must clear the per-frame text list). Deterministic.
let ctx, x = 0, y = 0;
export default {
  stateful: true,
  setup(mv) {
    ctx = mv.canvas().getContext("2d");
    ctx.fillStyle = "#0b0d10"; ctx.fillRect(0, 0, mv.width, mv.height);
  },
  render(f) {
    const W = f.w, H = f.h, s = Math.min(W, H) / 1080;
    x = (x + (5 + 9 * MV.random()) * s * 4) % W;
    y = (y + (2 + 6 * f.c.loud) * s * 4) % H;
    ctx.fillStyle = "rgba(11,13,16,0.06)"; ctx.fillRect(0, 0, W, H);
    ctx.fillStyle = f.beatPhase < 0.25 ? "#f4e2b8" : "#7fb2ff";
    ctx.fillRect(x, y, 24 * s, 24 * s);
    MV.text(`frame ${f.frame}`, {x: 10, y: 10, w: 120, h: 24, size: 20, role: "deco"});
  },
};
