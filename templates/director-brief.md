Make a music video for the song in this folder.

What I want: a full music video like a real official music video, with a lot of dance or some story, but made as motion graphics only. No filmed footage. It can be 2D, 3D or both. What matters to me is that it's creative and unique.
{{direction}}

- The song is song/{{file}}. Use all of it ({{duration}} seconds), with its audio unchanged.
- Read song/README.md first. The folder has what the whole song is about, the lyrics, and measurements of the music: beats, bars, notes, chords, drum hits, loud and quiet parts, and each instrument separately.
- {{tools}}/README.md lists ready-made tools and what is installed on this machine; {{tools}}/speeds.json says how fast each renderer is here. Using the tools is optional, but check the render speeds before choosing heavy 3D.
- Deliver one finished video: final/music-video.mp4, {{width}}×{{height}}, {{fps}} fps, H.264 yuv420p with AAC audio, the same length as the song file.
- You have about {{budget}}; check the clock. Spend it making the video better, not just finishing: plan, make style frames, render low-res previews, look at them with the eyes tools, revise. Leave enough time for the final render and checks.
- Work on your own from start to finish. Don't stop to ask me for approval; deliver the finished video.
- Keep all your work inside this project folder. Use only free tools already installed. Don't download footage or artwork. Fonts must be open-licensed (for example OFL), with the licence file kept.
- You can't hear the audio. Don't say you listened to or watched something you didn't. When you say something looks right, it's because you looked at frames.
- No rapid bright flashing that could affect photosensitive viewers.
- Before you finish, check that the file plays to the end and that the visuals are in sync with the song file.
- Then write report.md: what you made and the idea behind it, how you made it, what you checked, and what you're unsure about, including timestamps a human should check by ear.

---
Notes from the studio (how this machine works):
- This folder is the project `{{project}}`. The studio's own tools are in {{tools}}/ (read them, don't edit them; copy what you want to change into work/). Run any tool as `{{python}} {{tools}}/studio.py run {{project}} <tool> ...` with paths relative to this folder; plain `python` may not have the libraries.
- The clock: you started at {{started}}; the budget ends at **{{deadline}}**. `{{python}} {{tools}}/studio.py clock {{project}}` prints the time left. The studio will also message you as time runs down, and stops the session a few minutes after the deadline: have the final rendered before then.
- How to work: {{skill}} is the director's workflow and craft rules (the same as the interactive /musicvideo skill). Read it once, early. Its anti-defaults note ({{anti}}) matters: no first-idea visualiser look.
- Keep your own notes and a log in work/ (work/log.md: plan, decisions, every render pass with what you looked at). Read the big song files once; afterwards use your notes and `songq.py`.
- When the video is done: `{{python}} {{tools}}/studio.py check {{project}}` must pass, then write report.md.
{{revise_note}}
