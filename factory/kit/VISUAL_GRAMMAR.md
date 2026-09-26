# Visual grammar — how every micro-lecture must be *directed*

Feedback round 1 (teacher review, ML1.1) said the animation was factually fine but **visually
monotonous**: every frame looked the same, one small thing moved inside a static frame, and the
eye was never told where to look. This file is the fix. It's a factory-level spec — step 7
(`animate.py` / `scenes/beatNN.py`) must follow it for **every** beat, and an eval should check it.

## 1. Camera / emphasis (the biggest lever)
Stop rendering flat, static wide shots. Give each beat a **shot list** with deliberate camera moves.

- **Push-in zoom on the point that matters.** When the narration says "look here" (a label, a
  value, a change), the camera zooms to it. Example (ML1.1 Beat 4): when the cathode metal changes
  Fe → Cu, **zoom into the electrode** so the viewer sees the swap, then pull back to show the beam
  is unchanged. That single move carries the whole "gas/metal independent" idea.
- **Reframe between sub-points.** Within a beat, cut/pan between framings (wide → detail → wide) so
  consecutive seconds don't look identical. No shot should hold the same framing for more than ~4–5s
  without a move, reveal, or highlight.
- **Highlight + dim.** Spotlight the active element; dim/desaturate the rest. The eye follows contrast.
- **Motion with meaning.** Every movement should encode information (a field turning on, a value
  updating), never decorative drift.
- **Match cut to the narration beat.** The emphasis move fires on the word it illustrates (use the
  audio chunk timings the pipeline already produces).

Implementation: add a small camera/emphasis helper to `manimkit.py` (e.g. `push_in(mobject)`,
`spotlight(mobject)`, `reframe(...)`), and have each `beatNN.py` emit at least N emphasis moves
(scaled to beat length). Reuse the existing `*.cues.json` mechanism to log them.

## 2. 3D animations (depth, not flat 2D)
Teacher wants dimensional visuals, not only flat diagrams. Two tracks — keep them separate:

- **Pre-rendered 3D for the VIDEO (this is what we ship).** The discharge tube, the deflecting
  beam, the crossed-field setup look far better in 3D. Options, cheapest first:
  - **Manim `ThreeDScene`** with `move_camera` / ambient camera rotation — no new dependency,
    stays inside the current pipeline. Start here.
  - **Three.js rendered headless to frames** (or Blender `bpy`) for the hero apparatus, composited
    by ffmpeg — only if Manim 3D isn't enough. Renders to mp4 like any other beat.
  - Rule unchanged: 3D carries **no critical text** — labels/values are added as flat overlays so
    they stay crisp (same reason we don't let generative video hold facts).
- **Interactive 3D is NOT for the video.** The click-to-explore WebGL model (electron moves on
  click) can't become a linear video and doesn't serve the Instagram/YouTube GTM. **Park it for the
  in-app interactive layer**, not the content factory.

## 3. Avatar (presence + eye contact)
Teacher priority: a presenter avatar, and it must make **eye contact** with the viewer (raised
earlier as the "eye contact funda"). Purpose is human connection / retention, not decoration.

- Add an **avatar layer** composited into the frame (corner presence or side-by-side with the
  board), lip-synced to the Sarvam narration.
- **Eye contact:** the avatar looks into the camera on direct-address lines ("beta", "socho zara",
  CTA) and can glance to the board when pointing at it.
- Needs an **API** (won't run locally) — so make it an **optional per-lesson layer**
  (`avatar: on|off` in config), rendered after the beats and composited in step 9. The lesson must
  still build fully with `avatar: off` so the pipeline never blocks on the API.
- This is the same avatar backbone the product will reuse for the live voice-tutor later.

## 4. New evals to add (step 7 / step 9)
- **Anti-monotony:** sample frames across a beat; if consecutive framings are near-identical for
  > ~5s (low visual change + no emphasis cue fired), fail → the scene must add a camera move.
- **Emphasis coverage:** each beat emits ≥1 push-in/spotlight per key on-screen label; fail if a
  promised label never gets emphasised.
- **Avatar (when on):** avatar present, lip-sync within tolerance, and eye-contact frames present on
  direct-address lines.

## Priority order (do in this sequence)
1. **Camera/emphasis** — biggest quality jump, no new dependency. Ship first.
2. **Manim 3D** for the hero apparatus (discharge tube, deflection).
3. **Avatar** layer (optional, API-gated).
Everything here is additive and per-beat — it layers onto the existing Manim pipeline, it does not
replace it.
