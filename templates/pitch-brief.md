<!-- mv:pitch -->
I want a music video for the song in this folder, and before anyone builds it I'm holding a pitch round: {{n}} directors pitch it at the same time, each from a different angle. You are one of them, **pitch {{k}} of {{n}}**, and your angle is **{{angle_name}}**: {{angle_text}}

The others pitch from {{others}}. You can't see their work and they can't see yours. When the round is over I put the pitches side by side, the studio's critic compares them, and the one I pick becomes the video: a director with a full budget builds it from your pitch and goes further. So be unmistakably different from what those angles would make, and as bold as can be made well. A pitch that could have come from one of the other angles has already lost; so has a safe one. The pitches are ranked by ambition first, then by how well the idea fits this song, then by whether it can be built well in a director's budget on this machine, then by craft. The safe option never wins.

What I want from the video: a full music video like a real official music video, with a lot of dance or some story, made as motion graphics only (no filmed footage, photos or downloaded artwork), 2D, 3D or both. What matters to me is that it's creative and unique. My notes on the song (below, if any) are a starting point, not a spec: take them as inspiration and go further, and the less I say, the more is up to you. Of my notes, only what I ask you to avoid is a hard rule.
{{direction}}

## Your angle, pushed all the way
Push your angle further than anyone would on a first idea. Find the one thing in this song (a line of the lyric, the shape of its form, a sound, its biggest ★★★ moment) that your angle can turn into a video only this song could have, and build everything on it: one visual language, committed to, with one deliberate rupture at the biggest moment. Read {{anti}} before you choose: the first picture an AI reaches for (a visualiser, particles that pulse to the kick, a neon grid, centred lyrics on a gradient) is what every other pitch might fall back on; yours doesn't. If your first idea feels safe, it is: throw it away and take the second or third one.

## What you deliver: all of it in {{dir}}/ (your pitch folder: write nothing anywhere else)
1. **pitch.md**, in this shape (the critic and I read it; at most ~80 lines):
   ```
   # <the title>
   **The idea:** two sentences: what the video is.
   **Why this song:** what in this song makes this its video and no other's.
   ## The visual language
   marks / shapes / figures, the palette as hex, the type (fonts from assets/fonts), the motion rules, the camera (or
   none), the one deliberate rupture
   ## Signature moments
   3-5, each on a ★★★ moment: bar:beat, the time, what changes there
   ## Section plan
   | time | bars | music | picture |
   ## Unlike the defaults
   which first-idea pictures it refuses, and what it does instead
   ## How it gets made
   the renderers, the final render time on this machine (the numbers below), what the director builds first
   ## Risks
   what could fail, honestly, and how the director would find out early
   ```
2. **style/**: 2-3 style frames at full size on signature moments, at least one inside the proof window.
3. **scenes/**: your code. Shared code (the palette, the type, the motifs, a rig) in scenes/lib/ when you have any,
   imported with relative paths (`../lib/look.js`; a Blender scene just `import look`: the lib next to its folder is on
   its path) so it still works when your scenes move to work/scenes/; the proof scene in its own folder (scenes/proof/,
   or scenes/<id>/).
4. **proof.mp4**: the proof window (below) at preview size. No audio needed: the studio adds the song's.
5. Optional, **plan.json**: a draft scene list for the whole song in the plan tools' format ({{tools}}/README.md,
   plan.py), when your idea is easier to hand on that way. Name each scene where it will live once your pitch is
   picked (`work/scenes/<id>/scene.js`) and keep its file in your scenes/<id>/: the live view plays it from there.

## The proof: {{proof_from}} to {{proof_to}} ({{proof_len}} s of the song)
Every pitch proves itself on the same window: {{proof_why}}. It runs from {{proof_from}} (bar:beat {{proof_pos0}}) to {{proof_to}} (bar:beat {{proof_pos1}}): song frames `--frames {{a}}:{{b}}` at {{fps}} fps{{proof_sections}}. Show your idea there at full strength, as the finished video would have it: the signature moment itself, not a sketch of it and not an easier part of your idea. No trailer, no title card, no effects only the proof would have. This is what I'll see and hear first.
- Render it (a web scene): `{{python}} {{tools}}/studio.py run {{project}} web-engine/render.py {{dir}}/scenes/proof/scene.js --frames {{a}}:{{b}} --preview --out {{dir}}/proof.mp4`. A Blender scene: `blender/run.py <scene.py> --frames {{a}}:{{b}} --preview --out {{dir}}/proof.mp4`. Passes layered with compose.py layer are fine; the result is {{dir}}/proof.mp4 over exactly those frames.
- Renders queue one at a time on this machine and {{n}} pitches share the queue: start the proof render by the half-way point ({{halfway}}) at the latest. On its own it takes about {{proof_est}} here at preview size; longer while other pitches' renders go first.
- Style frames don't wait for the queue: `{{python}} {{tools}}/studio.py run {{project}} web-engine/render.py <scene> --still <t1>,<t2> --out {{dir}}/style` (Blender: `blender/run.py <scene.py> --still <t> --out {{dir}}/style`). Look at each one: is this a default? would any song have produced it? Redo a weak one before you render the proof.

## Be honest about what it takes
The whole song is {{frames}} frames at {{width}}×{{height}}. On this machine (tools/speeds.json) its final render alone takes about: {{speeds}}. The director who builds the winner has about {{direct_budget}}, and the final render plus the checks should fit in a third of that. Pick the renderer your idea needs and say in pitch.md what it costs; an idea that needs a slow renderer can still win if it is the boldest, as long as the pitch says how it gets made in time.

## How to work (about {{budget}}: the deadline is {{deadline_hm}})
- Read the song once, fast: song/README.md, song/song-map.md (the "At a glance", the sections and the moments; the bar-by-bar only around the proof window), song/lyrics.md and song/song-story.md if it's written. Keep your notes in your pitch folder (notes.md); don't re-read the big files.
- Start from the song and my notes, not from an earlier director's work in work/ (a concept, scenes, renders): your pitch is a fresh start from your angle.
- {{skill}} is the director's workflow and craft rules: read its "Craft rules" and "Build" parts. They hold for the proof too: every cut and hit on a musical event within a frame, lyric text on the beat or 8th at or just before its sung word, readable (≥ 3:1 contrast, never clipped), nothing revealed in a shot's last beat, no rapid bright flashing.
- You can't hear the song or watch the video. Don't say you heard or watched something: when you say something works, it's because you looked at frames (`eyes.py strip {{dir}}/proof.mp4 --at <t>`, `eyes.py beats ...`: one question at a time; their pictures go in {{dir}}/eyes/).
- You have full reign over your pitch: build it yourself, or deploy agents where that makes it bolder in the time (the Agent tool, in the background with `run_in_background: true`; give each its `model` and a narrow brief: your notes, your angle, its task, and only your pitch folder to write). Their cost is your pitch's.
- After the round the studio's critic (a cheaper model with fresh eyes) looks at your proof against your pitch.md, and a verdict compares the pitches: you don't need to call the critic yourself. Spend your time on the idea and the proof.
- When pitch.md, the style frames and proof.mp4 are there, end your turn. The studio messages you as time runs down and ends the session a few minutes after the deadline.

---
Notes from the studio (how this machine works):
- This folder is the project `{{project}}`. The studio's tools are in {{tools}}/ (read them, don't edit them); run any tool as `{{python}} {{tools}}/studio.py run {{project}} <tool> ...` with paths relative to this folder. {{tools}}/README.md lists them; plain `python` may not have the libraries.
- Write only inside {{dir}}/. The project's own files (work/concept.md, work/scenes/, work/plan.json, final/, report.md, project.json, song/, review/) and the other pitches' folders are not yours: the studio refuses those writes.
- The clock: you started at {{started}}; the budget ends at **{{deadline}}**. `{{python}} {{tools}}/studio.py clock {{project}}` prints the time left.
- No network: everything you need is installed. Fonts only from assets/fonts (open licences, kept there).{{refs}}
