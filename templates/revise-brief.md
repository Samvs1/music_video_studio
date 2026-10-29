This is a review round for the music video in this folder. I watched version v{{version}} (final/music-video_v{{version}}.mp4, also final/music-video.mp4 right now) and left notes at moments in it. Make the next version.

My notes on v{{version}} (song time · bar:beat · section · what is sung there · my note):
{{notes}}
{{direction}}

- Address every note. Read your own report.md and work/log.md first: they say what you made and why; keep what works.{{refs}}
- Fix the film, not the frame: when a note changes timing (something stays longer, moves to another bar), re-think what comes after it against the music as a whole instead of squeezing the fix in locally. Small fixes stay small.
- The song is song/{{file}}, all of it ({{duration}} seconds), audio unchanged. Deliver final/music-video.mp4 again, {{width}}×{{height}}, {{fps}} fps, H.264 yuv420p with AAC audio, the same length as the song file. v{{version}} is kept as final/music-video_v{{version}}.mp4: you can't overwrite a cut I liked.
- You have about {{budget}}; check the clock. Leave enough time for the final render and the checks.
- Work on your own; don't stop to ask me. Keep all your work inside this project folder. You can't hear the audio or watch the video: when you say something is right, it's because you looked at frames.
- No rapid bright flashing. Before you finish, check that the file plays to the end and that the visuals are in sync with the song.
- In review/notes.json mark each note you addressed: "done": true and "fix": "what changed" (one line, I read it under my note).
- Update report.md: add a section "Round {{round}}" at the top: each note, what you changed, and the timestamps I should check (by ear too).

---
Notes from the studio (how this machine works):
- This folder is the project `{{project}}`. The studio's tools are in {{tools}}/ (don't edit them). Run them as `{{python}} {{tools}}/studio.py run {{project}} <tool> ...`.
- The clock: you started at {{started}}; the budget ends at **{{deadline}}** (`{{python}} {{tools}}/studio.py clock {{project}}`). The studio messages you as time runs down and stops the session a few minutes after the deadline.
- The workflow and craft rules: {{skill}} (section "Review round"). When done: `{{python}} {{tools}}/studio.py check {{project}}` must pass.{{critic}}
