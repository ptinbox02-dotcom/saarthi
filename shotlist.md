# ML 1.1 hybrid shot-list — MANIM vs GEMINI-BROLL

Rule: anything with a label / formula / vector / graph / exact motion → **MANIM**.
Anything atmospheric with **no critical on-screen text** → **GEMINI-BROLL** (text added later in ffmpeg).
Beats + narration are the source of truth in `../micro-lectures/lessons/ML1.1/script.md`.

| Beat | Time | Content | Engine | Why |
|------|------|---------|--------|-----|
| 0 Hook | 0:00–0:20 | Tube light flickers on in an Indian home, glow → morph to discharge tube | **GEMINI-BROLL** (b1) + Manim morph | Emotional hook, photoreal home; the morph/label is Manim |
| 1 Puzzle | 0:20–0:55 | Bare wires no-spark ❌; sealed tube; pressure gauge drops; gas glows; "Discharge tube" label | **MANIM** | Labels, gauge, exact setup |
| 2 Rays | 0:55–1:45 | Cathode/anode labels, pressure stages, beam → wall fluoresces; cut to real tube light | **MANIM** + **GEMINI-BROLL** (b2 tube-light close-up) | Diagram is Manim; the "real tube light" cutaway is B-roll |
| 3 Detective | 1:45–3:35 | Shadow, paddle-wheel spin, +plate deflection, magnet; wind/leaves aside | **MANIM** (+ optional b3 wind/leaves B-roll, 2s) | All proofs need exact motion/labels; only the wind metaphor is B-roll |
| 4 Universal | 3:35–4:20 | Gas/metal swap carousel; same beam; zoom to "same particle in everything" | **MANIM** | Labels + controlled swap |
| 5 Thomson e/m | 4:20–5:25 | Thomson-era lab establishing shot; then crossed-field tube, formula card, red TRAP | **GEMINI-BROLL** (b4 lab, ~4s) + **MANIM** | Lab atmosphere is B-roll; the physics + formula + TRAP are Manim |
| 6 Why it mattered | 5:25–6:05 | Solid atom cracks open → electrons; timeline Thomson→Rutherford→Bohr | **MANIM** | Timeline text + controlled reveal |
| 7 Recap + CTA | 6:05–6:40 | Recap chips, 3 exam pointers, @saarthi outro, CTA | **MANIM** | All text |

## B-roll shots to generate (only these — keep it minimal)
- **b1_tubelight.mp4** (~6s) — a tube light flickering on in a modest Indian home at dusk. Hook.
- **b2_tubelight_closeup.mp4** (~4s) — macro of a glowing fluorescent tube, the mercury glow. Beat 2 cutaway.
- **b3_wind_leaves.mp4** (~3s, OPTIONAL) — wind moving leaves/a dupatta; "you infer the invisible." Beat 3 metaphor.
- **b4_thomson_lab.mp4** (~5s) — a 19th-century physics lab, glass apparatus, warm lamplight, no readable text. Beat 5.

Total generated B-roll ≈ 15–18s across the 6:40 video. Everything technical stays Manim.
All on-screen text/labels for these shots are burned in later (see omni_prompts.md → overlay text).
