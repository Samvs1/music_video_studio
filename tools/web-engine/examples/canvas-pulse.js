// Minimal example (Canvas 2D): what the runtime gives a scene. A template for the mechanics, not a look to copy.
// Render: python tools/studio.py run <project> web-engine/render.py ../../tools/web-engine/examples/canvas-pulse.js --out work/renders/pulse.mp4 --preview
let ctx;
export default {
  seed: 7,
  setup(mv) { ctx = mv.canvas().getContext("2d"); },      // build once: canvases, geometry, precomputed layouts
  render(f) {                                             // draw frame f; depend only on f
    const W = f.w, H = f.h, s = Math.min(W, H) / 1080;
    ctx.fillStyle = "#0b0b0d"; ctx.fillRect(0, 0, W, H);
    const kick = MV.pulse("kick", f.t, 0.16);              // 0..1, jumps on each kick, decays
    const r = (120 + 220 * f.c.loud + 90 * kick) * s;      // f.c.<curve>: this frame's value from song/frames.json
    ctx.strokeStyle = "#e8e2d0"; ctx.lineWidth = (2 + 10 * f.c.bass) * s;
    ctx.beginPath(); ctx.arc(W / 2, H / 2, r, 0, Math.PI * 2 * (0.1 + 0.9 * f.barPhase)); ctx.stroke();
    if (f.line) {                                          // the lyric line being sung, appearing at its cue
      ctx.font = `600 ${Math.round(54 * s)}px "Space Grotesk"`; ctx.fillStyle = "#e8e2d0"; ctx.textAlign = "center";
      const y = H * 0.85, w = ctx.measureText(f.line.text).width;
      ctx.fillText(f.line.text, W / 2, y);
      MV.text(f.line.text, {x: W / 2 - w / 2, y: y - 54 * s, w, h: 66 * s, color: "#e8e2d0", size: 54 * s, role: "lyric"});
    }
  },
};
