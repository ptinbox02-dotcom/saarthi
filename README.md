# ML 1.1 — Hybrid experiment (Manim + Gemini Omni B-roll + Sarvam)

**Question this folder answers:** does adding a few cinematic Gemini-Omni B-roll shots to the
pure-Manim lesson lift perceived quality / retention enough to justify the cost + a small manual step?

**One video only.** We rebuild ML 1.1 (Discovery of the Electron) as a hybrid, then compare it
head-to-head with the pure-Manim version produced by `../micro-lectures`.

## The division of labour (the whole point)
- **Manim** owns every shot with a **label, formula, vector, graph, or exact motion** — i.e. all
  the *concept* animation. This is the only thing that can pass the 100% factual-accuracy gate.
- **Gemini Omni** owns **atmospheric / emotional B-roll with NO critical on-screen text** — the
  hook, the human story, establishing shots. Text/labels are added later in ffmpeg, never baked
  into the generated clip (Omni/Veo garble in-video text).
- **Sarvam (Bulbul)** = audio, same as the main factory. **Gemini (LLM)** = eval judge.

## Files
```
README.md            # this file
PLAN.md              # step-by-step build + the A/B comparison protocol
shotlist.md          # every ML1.1 beat tagged MANIM or GEMINI-BROLL, with durations
omni_prompts.md      # ready-to-paste Gemini Omni prompts for the B-roll shots (+ overlay text)
assemble.sh          # ffmpeg: fit clips to Sarvam audio, burn overlays/captions, concat
broll/               # drop the generated Omni .mp4 clips here (b1_tubelight.mp4, ...)
manim/               # Manim scenes for the technical beats (reuse ../micro-lectures scenes)
audio/               # Sarvam per-beat wavs
build/               # intermediate + final ML1.1_hybrid.mp4
```

## Source of truth (reused — do NOT rewrite)
- Script + storyboard: `../micro-lectures/lessons/ML1.1/script.md`
- Verified facts: `../micro-lectures/lessons/ML1.1/fact_sheet.md`
- Brand/voice/eval config: `../micro-lectures/factory/config.yaml`

## Access to confirm before the B-roll step
Gemini Omni ships mainly via the Gemini app / Google Flow / YouTube. If you have **API** access,
B-roll can be automated; if not, generate the 2–3 clips manually from the prompts in
`omni_prompts.md` and drop them into `broll/`. Everything else stays fully automated.
