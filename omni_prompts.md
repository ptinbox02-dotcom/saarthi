# Gemini Omni — B-roll prompts (ready to paste)

Generate each clip, then save it into `broll/` with the exact filename shown.
**Rules that keep quality high and accuracy safe:**
- **No readable on-screen text** in the generated clip (Omni/Veo garble text). We burn all labels
  later with ffmpeg. If Omni adds incidental text, regenerate.
- Vertical or 16:9? Master lesson is **16:9 (1920×1080)** → request **16:9**.
- Keep each clip a few seconds; we trim to the beat length in `assemble.sh`.
- Consistent look to match the Manim dark theme: warm, slightly cinematic, shallow depth of field,
  dark/moody background so it cuts cleanly against `#0e1116`.
- **Generate audio OFF / ignore** — Sarvam is our only audio source; we strip B-roll audio in ffmpeg.

---

## b1_tubelight.mp4  (~6s) — Beat 0 hook
> A modest middle-class Indian home at dusk, warm and lived-in. A ceiling fluorescent **tube
> light** flickers a couple of times with that familiar tink-tink start, then settles into a
> steady cool-white glow that fills the room. Slow push-in toward the glowing tube. Cinematic,
> shallow depth of field, gentle film grain, moody dark surroundings. No text, no people's faces
> in focus. 16:9.

**Overlay text (burned later):** "Ye tubelight… ek discovery hai."

---

## b2_tubelight_closeup.mp4  (~4s) — Beat 2 cutaway
> Extreme macro close-up of a glowing fluorescent tube light: the soft blue-white mercury glow
> along the glass, subtle shimmer, dust motes in the light. Very shallow focus, dark background.
> Slow drift along the tube. No text. 16:9.

**Overlay text (burned later):** "Same cheez — low-pressure gas + voltage."

---

## b3_wind_leaves.mp4  (~3s, OPTIONAL) — Beat 3 metaphor ("you infer the invisible")
> A quiet Indian courtyard: a gust of wind moves green peepal leaves and a hanging dupatta; you
> cannot see the wind, only its effect on the things it moves. Warm afternoon light, cinematic,
> shallow depth of field, dark shaded background. No text. 16:9.

**Overlay text (burned later):** "Hawa dikhti nahi — asar se pata chalti hai."

---

## b4_thomson_lab.mp4  (~5s) — Beat 5 establishing
> A late-19th-century physics laboratory, Cambridge-era: dark wooden benches, brass and glass
> apparatus, a glass vacuum tube apparatus faintly glowing, warm lamplight, motes of dust in
> shafts of light. Slow dolly across the bench. Atmospheric, moody, cinematic, no readable text,
> no modern objects. 16:9.

**Overlay text (burned later):** "1897 · J.J. Thomson"

---

## After generating
Drop the files in `broll/` with the exact names above, then run `bash assemble.sh` (it fits each
clip to the Sarvam audio, strips B-roll audio, burns the overlay text, and concatenates with the
Manim beats). If you generated only some B-roll, `assemble.sh` falls back to the Manim shot for
any missing clip so the build never breaks.
