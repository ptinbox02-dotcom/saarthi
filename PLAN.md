# Build plan + A/B protocol — ML 1.1 hybrid

Run this in Claude Code (full OS access). It reuses the script/fact-sheet/config from
`../micro-lectures`. Goal: ONE hybrid video, then compare to the pure-Manim version.

## Prereqs
- `../micro-lectures/.env` already has `SARVAM_API_KEY` (reused).
- Manim + ffmpeg installed (same as main factory). faster-whisper for the audio eval.
- Gemini Omni access confirmed (API → automate; else manual generate per `omni_prompts.md`).

## Steps
1. **Audio (Sarvam).** Generate per-beat wavs into `audio/` from the NARRATION in
   `../micro-lectures/lessons/ML1.1/script.md`, honouring [PAUSE]/… and the pronunciation map in
   `../micro-lectures/factory/config.yaml`. (Same code as main factory step 8 — import it.)
   EVAL: STT round-trip WER < 0.15; `must_hear` terms present.

2. **Manim beats.** Render the MANIM-tagged beats from `shotlist.md` (beats 1,2,3,4,6,7 + the
   Beat-0 morph). Reuse `../micro-lectures/lessons/ML1.1/scenes/*.py` once built; here they can be
   symlinked or imported. Output silent clips into `manim/`. EVAL: non-blank, duration ≈ beat,
   multimodal frame check (labels/values correct).

3. **B-roll (Gemini Omni).** Generate b1,b2,b4 (b3 optional) per `omni_prompts.md`. Save to
   `broll/`. EVAL (Gemini judge): clip matches the intended shot, **contains no readable text**,
   dark/cinematic look; regenerate on fail. If a clip is missing, the assembler falls back to the
   Manim shot for that beat.

4. **Assemble (ffmpeg).** `bash assemble.sh`:
   - For each beat: pick the clip (B-roll where tagged, else Manim), trim/pad to that beat's
     Sarvam audio length, strip any B-roll audio.
   - Burn the overlay text from `omni_prompts.md` onto B-roll shots (brand font/colour).
   - Concatenate all beats in order; add 2s intro + 3s outro; burn captions (SRT from audio).
   - Output `build/ML1.1_hybrid.mp4` + a <90s `build/ML1.1_hybrid_reel.mp4`.
   EVAL: A/V sync ±0.2s; final multimodal QA; **factual gate still 100%** (labels come from Manim
   + burned overlays, never from generated video).

## A/B comparison protocol (the actual experiment)
Produce a short `build/COMPARISON.md` answering:
1. **Perceived quality** — side-by-side stills: hook (Beat 0) and Thomson (Beat 5), hybrid vs
   pure-Manim. Does the B-roll read as more premium?
2. **Accuracy** — confirm 0 factual regressions (all labels still Manim/overlay, not generative).
3. **Cost** — Omni clips generated × price/clip; minutes of manual work (if no API).
4. **Effort/automation** — did B-roll require a human step? how many regenerations to get
   text-free, on-look clips?
5. **Verdict** — is the lift worth the cost + manual step? If yes → add an optional `broll:` layer
   to the main `../micro-lectures` factory (per-beat: engine = manim | omni). If no → stay pure-Manim.

## Guardrail
The hybrid must never let generated video carry a fact. Every label, number, formula, and vector
stays in Manim or in a burned ffmpeg overlay. Gemini Omni only ever supplies wordless atmosphere.
