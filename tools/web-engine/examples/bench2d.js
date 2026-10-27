// Benchmark scene (Canvas 2D): ~2400 shapes, gradients and a line of text per frame. Not a look; a load.
let ctx;
export default {
  setup(mv) { ctx = mv.canvas().getContext("2d"); },
  render(f) {
    const W = f.w, H = f.h, s = Math.min(W, H) / 1080;
    const g = ctx.createLinearGradient(0, 0, W, H);
    g.addColorStop(0, `hsl(${(f.t * 20) % 360},40%,12%)`); g.addColorStop(1, "#050507");
    ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
    for (let i = 0; i < 2400; i++) {
      const a = MV.rand(i) * 6.283 + f.t * (0.2 + MV.rand(i, "v")), r = (80 + 900 * MV.rand(i, "r")) * s;
      ctx.fillStyle = `hsla(${(i * 7) % 360},70%,${50 + 20 * f.c.drums}%,0.6)`;
      ctx.beginPath(); ctx.arc(W / 2 + Math.cos(a) * r, H / 2 + Math.sin(a) * r * 0.6, (2 + 6 * MV.rand(i, "s")) * s, 0, 6.283); ctx.fill();
    }
    ctx.font = `700 ${Math.round(96 * s)}px Anton`; ctx.fillStyle = "#fff"; ctx.textAlign = "center";
    ctx.fillText(`BAR ${f.bar}`, W / 2, H * 0.9);
  },
};
