# Continue in Claude Code — ML 1.1 HYBRID (Manim + Gemini Omni B-roll + Sarvam)

> Paste into Claude Code opened at `/Users/padminitripathi/Documents/edtech/ml-gemini-hybrid`.
> This is a ONE-VIDEO experiment: is cinematic Gemini-Omni B-roll worth adding to the pure-Manim lesson?

You have full filesystem + OS access. Build one hybrid ML 1.1 video and a short comparison report.

READ FIRST:
- README.md, PLAN.md, shotlist.md, omni_prompts.md, assemble.sh (this folder)
- REUSE (do not rewrite): ../micro-lectures/lessons/ML1.1/script.md (script+storyboard),
  ../micro-lectures/lessons/ML1.1/fact_sheet.md (facts), ../micro-lectures/factory/config.yaml
  (brand/voice/eval), ../micro-lectures/.env (SARVAM_API_KEY)

HARD RULE: generated video NEVER carries a fact. Every label, number, formula, vector stays in
Manim or a burned ffmpeg overlay. Gemini Omni supplies wordless atmosphere only.

DO, IN ORDER:
1. AUDIO (Sarvam Bulbul): per-beat wavs → audio/ from the NARRATION in script.md; honour
   [PAUSE]/… and config's pronunciation map. EVAL: STT round-trip WER < 0.15; must_hear terms present.
2. MANIM beats (shotlist = 1,2,3,4,6,7 + Beat-0 morph): render silent clips → manim/ using the
   ON-SCREEN briefs. Reuse ../micro-lectures scenes if present. EVAL: non-blank, duration ≈ beat,
   multimodal frame check (labels/values correct).
3. GEMINI OMNI B-roll: generate b1,b2,b4 (b3 optional) from omni_prompts.md → broll/. Request 16:9,
   NO on-screen text, dark/cinematic, audio off. If you have Omni API access, automate; else generate
   manually and drop files in broll/. EVAL (Gemini judge): right shot, contains NO readable text,
   dark cinematic look → regenerate on fail. Missing clip → assembler falls back to Manim.
4. ASSEMBLE: run `bash assemble.sh` (fits visuals to audio, strips B-roll audio, burns overlays,
   concats with fallback). Then add 2s intro + 3s outro + burned captions; export
   build/ML1.1_hybrid.mp4 and a <90s build/ML1.1_hybrid_reel.mp4. EVAL: A/V sync ±0.2s; final
   multimodal QA; factual gate still 100%.
5. COMPARISON: write build/COMPARISON.md per PLAN.md's protocol — perceived quality (side-by-side
   stills of Beat 0 & Beat 5, hybrid vs pure-Manim in ../micro-lectures), accuracy (0 regressions),
   cost (clips × price + manual minutes), automation impact, and a clear VERDICT: is the lift worth
   it? If yes, propose adding an optional per-beat `engine: manim|omni` layer to the main factory.

Deliver: build/ML1.1_hybrid.mp4, build/ML1.1_hybrid_reel.mp4, build/COMPARISON.md.
