#!/usr/bin/env python3
"""
Saarthi micro-lecture factory — orchestrator.

Turns ONE JEE topic into a finished 5–7 min lesson video with NO human editing and an
EVAL GATE after every step (auto-retry on fail, hard 100% factual-accuracy gate).

    python factory/pipeline.py --topic ML1.1            # end-to-end
    python factory/pipeline.py --topic ML1.1 --from 7   # resume at animation
    python factory/pipeline.py --all                    # batch topics.yaml

Contract:
  * step_N(ctx, feedback) writes artifacts under lessons/<id>/
  * eval_N(ctx) -> EvalResult(passed, score, feedback)
  * on fail the step re-runs with the eval's feedback, up to evals.max_retries;
    if it still fails, qa_report.md is written and the run STOPS. Nothing ships red.
  * steps whose artifact already exists are VERIFIED, not regenerated, unless --force.
    ML1.1's steps 1–6 were authored in a previous session, so for that topic steps 1–6
    are pure verify passes.

Steps and their gates:
  1 research -> fact_sheet.md   numbers/dates exact; >=2 sources; every row rated
  2 refine   -> outline.md      syllabus coverage; every beat listed; nothing off-scope
  3 script   -> script.draft.md word-count -> 5-7 min; Indian hook in ~3s; readability
  4 examples -> (in script)     India-analogy check; every jargon term unpacked
  5 qa_weave -> (in script)     student-question coverage; the e/m trap is present
  6 finalize -> script.md       hallucination trace to fact_sheet + LLM-judge rubric
  7 animate  -> clips/*.mp4     non-blank, duration match, OCR of promised labels
  8 audio    -> audio/*.wav     STT round-trip (phonetic CER) + pronunciation + length
  9 assemble -> <id>.mp4 + reel A/V sync +-0.2s; caption alignment; reel spec
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import animate
import assemble as asm
import audio as tts_mod
import evals
from common import (CFG_PATH, ROOT, TOPICS_PATH, Ctx, EvalResult, ffprobe_duration,
                    load_yaml, mmss)
from scriptparse import parse_script


# ==========================================================================
# steps 1–6 — for ML1.1 these are verify passes over artifacts already on disk
# ==========================================================================
def _beats(ctx):
    if "beats" not in ctx.state:
        ctx.state["beats"] = parse_script(ctx.script_path)
    return ctx.state["beats"]


def step_1(ctx, fb):
    if ctx.fact_path.exists() and not ctx.force:
        return
    sys.exit("[1-research] fact_sheet.md is missing. Research output must be authored "
             "before the pipeline can verify it — see START_HERE_CLAUDE_CODE.md.")


def eval_1(ctx):
    return evals.eval_step1_facts(ctx.fact_path)


def step_2(ctx, fb):
    """Derive outline.md from the locked script + fact sheet (never rewrites content)."""
    if ctx.outline_path.exists() and not ctx.force and not fb:
        return
    fs = evals.parse_fact_sheet(ctx.fact_path)
    bs = _beats(ctx)
    lines = [f"# {ctx.topic_id} — outline (derived from script.md + fact_sheet.md)\n",
             f"**{ctx.topic['title']}** · {ctx.topic['subject']} · {ctx.topic['chapter']}",
             f"\nIndia hook: {ctx.topic.get('india_hook','')}\n",
             "## Beats\n"]
    for b in bs:
        lines.append(f"- **Beat {b.index} — {b.title}** "
                     f"({mmss(b.t_start)}–{mmss(b.t_end)}, {b.planned_seconds:.0f}s) "
                     f"· facts {', '.join(b.fact_tags) or '—'}")
        lines.append(f"  - on-screen: {b.onscreen.splitlines()[0][:150]}")
    lines.append("\n## In scope (from fact_sheet.md)\n")
    lines += [f"- {s}" for s in fs["in_scope"]]
    lines.append("\n## Out of scope — named in one line only, never derived\n")
    lines += [f"- {s}" for s in fs["out_scope"]]
    ctx.outline_path.write_text("\n".join(lines) + "\n")


def eval_2(ctx):
    return evals.eval_step2_outline(ctx.outline_path, ctx.fact_path, _beats(ctx))


def step_3(ctx, fb):
    if ctx.script_path.exists() and not ctx.force:
        return
    sys.exit("[3-script] script.md is missing and this pipeline verifies rather than "
             "writes scripts. Author it first.")


def eval_3(ctx):
    return evals.eval_step3_script(_beats(ctx), ctx.cfg)


def step_4(ctx, fb):
    return          # examples live inside the locked script


def eval_4(ctx):
    return evals.eval_step4_examples(_beats(ctx), ctx.fact_path, ctx.cfg)


def step_5(ctx, fb):
    return          # Q&A is woven into the locked script


def eval_5(ctx):
    return evals.eval_step5_qa(ctx.script_path, _beats(ctx))


def step_6(ctx, fb):
    return          # script.md is the finalised artifact


def eval_6(ctx):
    hall = evals.eval_step6_hallucination(_beats(ctx), ctx.fact_path, ctx.cfg)
    judge = evals.llm_judge(ctx.topic_id, "step6", ctx.cfg["evals"]["llm_judge_min"])
    ctx.reports.append(judge)
    if not hall.passed:
        return hall
    if not judge.passed:
        return EvalResult("6-finalize", False, judge.score,
                          f"hallucination trace clean, but rubric failed: {judge.feedback}")
    hall.feedback += f" | rubric mean {judge.score:.2f}/5"
    return hall


# ==========================================================================
# step 7 — animate
# ==========================================================================
def step_7(ctx, fb):
    ctx.state["clips"] = animate.animate(ctx, _beats(ctx), fb)
    ctx.state["brand_cards"] = animate.render_brand_cards(ctx)


def eval_7(ctx):
    return evals.eval_step7_render(ctx.state["clips"], _beats(ctx), ctx.cfg,
                                   ctx.build_dir / "frames")


# ==========================================================================
# step 8 — audio
# ==========================================================================
def step_8(ctx, fb):
    cfg = ctx.cfg
    ctx.ensure_dirs()
    pace = float(ctx.state.get("pace", cfg["voice"]["pace"]))
    hints = ctx.state.setdefault("pron_hints", [])

    # the eval feeds back either a pacing problem or a specific mis-heard chunk
    if fb:
        if "outside the" in fb and "window" in fb:
            pace = round(pace * (1.08 if "narration total" in fb else 0.94), 2)
            ctx.state["pace"] = pace
            print(f"  [8] adjusting pace -> {pace}")
        for term in _terms_from_feedback(fb, cfg):
            hints.append(term)
            print(f"  [8] adding phonetic hint: {term}")

    tts = tts_mod.SarvamTTS(cfg, ctx.audio_dir / "chunks")
    metas = []
    for b in _beats(ctx):
        m = tts_mod.synth_beat(tts, b, cfg, ctx.audio_dir, pace, hints)
        m["wav_path"] = str(ctx.audio_dir / m["wav"])
        metas.append(m)
        print(f"  [8] {b.slug}: {m['duration']:.1f}s, {len(m['chunks'])} chunks")
    ctx.state["audio"] = metas
    print(f"  [8] narration total {sum(m['duration'] for m in metas):.1f}s "
          f"({tts.calls} new Sarvam calls)")


SPELL_OUT = {"e/m": "e-by-m", "e-by-m": "e bai em", "cathode": "kaithode",
             "anode": "aanode", "electron": "ilectron", "momentum": "momentam",
             "subatomic": "sub-atomic", "negative": "negetive", "thomson": "Tomson"}


def _terms_from_feedback(fb: str, cfg) -> list[dict]:
    out = []
    for term in cfg["voice"].get("must_hear", []):
        if term.lower() in fb.lower() and "not heard back" in fb:
            repl = SPELL_OUT.get(term.lower())
            if repl:
                out.append({"find": term, "replace": repl})
    return out


def eval_8(ctx):
    return evals.eval_step8_audio(ctx.state["audio"], _beats(ctx), ctx.cfg,
                                  ctx.build_dir / "stt")


# ==========================================================================
# step 9 — assemble
# ==========================================================================
def step_9(ctx, fb):
    def refit(beat, seconds):
        """Re-render one beat's animation at the narration's exact length."""
        c = animate.animate_beat(ctx, beat, seconds)
        for i, old in enumerate(ctx.state["clips"]):
            if old["slug"] == beat.slug:
                ctx.state["clips"][i] = c
        return c

    out = asm.assemble(ctx, _beats(ctx), ctx.state["clips"], ctx.state["audio"],
                       ctx.state["brand_cards"], refit=refit)
    ctx.state["assembly"] = out
    (ctx.clips_dir / "clips.json").write_text(json.dumps(ctx.state["clips"], indent=2))


def eval_9(ctx):
    mech = evals.eval_step9_assemble(ctx, ctx.state["assembly"]["timeline"], ctx.cfg)
    if not mech.passed:
        return mech
    judge = evals.llm_judge(ctx.topic_id, "step9", ctx.cfg["evals"]["llm_judge_min"])
    ctx.reports.append(judge)
    if not judge.passed:
        return EvalResult("9-assemble", False, judge.score,
                          f"mechanical checks clean, but the final multimodal QA rubric "
                          f"did not pass: {judge.feedback}", retryable=judge.retryable)
    mech.feedback += f" | final QA rubric {judge.score:.2f}/5"
    return mech


# ==========================================================================
# state rehydration so --from N works without re-running earlier steps
# ==========================================================================
def rehydrate(ctx, from_step: int):
    if from_step > 7:
        p = ctx.clips_dir / "clips.json"
        if not p.exists():
            sys.exit("--from 8+ needs step 7's clips.json; run --from 7 first.")
        ctx.state["clips"] = json.loads(p.read_text())
        ctx.state["brand_cards"] = {
            "intro": str(ctx.build_dir / "intro.mp4"),
            "outro": str(ctx.build_dir / "outro.mp4"),
        }
        for k, v in ctx.state["brand_cards"].items():
            if not Path(v).exists():
                ctx.state["brand_cards"] = animate.render_brand_cards(ctx)
                break
    if from_step > 8:
        metas = []
        for b in _beats(ctx):
            p = ctx.audio_dir / f"{b.slug}.chunks.json"
            if not p.exists():
                sys.exit("--from 9 needs step 8's audio manifests; run --from 8 first.")
            m = json.loads(p.read_text())
            m["wav_path"] = str(ctx.audio_dir / m["wav"])
            metas.append(m)
        ctx.state["audio"] = metas


# ==========================================================================
# gate runner + report
# ==========================================================================
def run_with_gate(name, step_fn, eval_fn, ctx, max_retries):
    feedback = ""
    for attempt in range(1, max_retries + 1):
        t0 = time.time()
        step_fn(ctx, feedback)
        res: EvalResult = eval_fn(ctx)
        res.details["attempts"] = attempt
        res.details["seconds"] = round(time.time() - t0, 1)
        ctx.reports.append(res)
        print(f"[{name}] attempt {attempt}: {'PASS' if res.passed else 'FAIL'} "
              f"score={res.score:.2f} ({res.details['seconds']}s)")
        print(f"         {res.feedback[:400]}")
        if res.passed:
            return res
        if not res.retryable:
            write_qa_report(ctx, failed=name)
            sys.exit(f"[{name}] BLOCKED (not retryable): {res.feedback}")
        if res.feedback == feedback:
            # the step re-ran and produced the identical complaint, so it has no lever
            # left to pull on this input; burning the remaining attempts proves nothing
            write_qa_report(ctx, failed=name)
            sys.exit(f"[{name}] STUCK — retry changed nothing: {res.feedback}")
        feedback = res.feedback
        print(f"[{name}] retrying with feedback")
    write_qa_report(ctx, failed=name)
    sys.exit(f"[{name}] FAILED after {max_retries} attempts. See qa_report.md. Not shipping.")


STEP_TITLES = {
    "1-research": "Research → fact_sheet.md",
    "2-refine": "Refine → outline.md",
    "3-script": "Script → timing / hook / readability",
    "4-examples": "Examples → India-analogy + jargon unpacked",
    "5-qa": "Q&A weave → student-question / trap coverage",
    "6-finalize": "Finalize → hallucination trace to fact_sheet.md",
    "step6-judge": "Finalize → LLM-judge rubric",
    "7-animate": "Animate → Manim clips",
    "8-audio": "Audio → Sarvam TTS + STT round-trip",
    "9-assemble": "Assemble → mp4 + captions + reel",
    "step9-judge": "Final multimodal QA rubric",
}


def write_qa_report(ctx: Ctx, failed: str | None = None):
    out = ctx.lesson_dir / ctx.cfg["output"]["qa_report"]
    fact = next((r for r in reversed(ctx.reports) if r.step == "6-finalize"), None)
    accuracy = fact.score if fact else 0.0
    gate = ctx.cfg["evals"]["factual_accuracy_gate"]
    last = {}
    for r in ctx.reports:
        last[r.step] = r
    publish = (not failed) and accuracy >= gate and all(r.passed for r in last.values())

    L = [f"# QA report — {ctx.topic_id}",
         f"*{ctx.topic['title']}*",
         "",
         f"- Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
         f"- Voice: Sarvam `{ctx.cfg['voice']['model_id']}` / speaker "
         f"`{ctx.cfg['voice']['speaker_id']}` · {ctx.cfg['voice']['language']}",
         f"- Render: Manim {ctx.cfg['video']['full']['width']}×"
         f"{ctx.cfg['video']['full']['height']}@{ctx.cfg['video']['full']['fps']}",
         "",
         f"## Verdict: {'PUBLISH-READY' if publish else 'NOT PUBLISH-READY'}",
         "",
         f"**Factual accuracy gate: {accuracy*100:.0f}% "
         f"(required {gate*100:.0f}%) — {'PASS' if accuracy >= gate else 'FAIL'}**",
         ""]
    if failed:
        L.append(f"> Stopped at **{failed}** after exhausting retries.\n")

    # a retried step appears once per attempt; report the final state and the count
    final, order = {}, []
    for r in ctx.reports:
        if r.step not in final:
            order.append(r.step)
        final[r.step] = r
    tries = {s: sum(1 for r in ctx.reports if r.step == s) for s in order}

    L += ["## Step gates", "",
          "| Step | Gate | Result | Score | Tries | Detail |",
          "|---|---|---|---|---|---|"]
    for step in order:
        r = final[step]
        detail = r.feedback.replace("|", "／").replace("\n", " ")
        L.append(f"| `{r.step}` | {STEP_TITLES.get(r.step, '')} | "
                 f"{'✅ PASS' if r.passed else '❌ FAIL'} | {r.score:.2f} | "
                 f"{tries[step]} | {detail} |")

    # ---- artifacts
    L += ["", "## Artifacts", ""]
    for p, label in ((ctx.final_mp4, "full lesson"), (ctx.reel_mp4, "reel teaser"),
                     (ctx.build_dir / f"{ctx.topic_id}.srt", "captions"),
                     (ctx.script_path, "script/storyboard"),
                     (ctx.fact_path, "fact sheet"), (ctx.outline_path, "outline")):
        if p.exists():
            extra = ""
            if p.suffix == ".mp4":
                extra = f" · {ffprobe_duration(p):.1f}s"
            L.append(f"- `{p.relative_to(ROOT)}`{extra}")

    # ---- per-beat table
    clips = ctx.state.get("clips") or []
    au = {m["slug"]: m for m in (ctx.state.get("audio") or [])}
    tl = {t["slug"]: t for t in (ctx.state.get("assembly", {}).get("timeline") or [])}
    if clips:
        L += ["", "## Per-beat", "",
              "| Beat | Planned | Narration | Clip | Engine | PTS fit | Captions |",
              "|---|---|---|---|---|---|---|"]
        for b in _beats(ctx):
            c = next((x for x in clips if x["slug"] == b.slug), {})
            a = au.get(b.slug, {})
            t = tl.get(b.slug, {})
            L.append(f"| {b.index} {b.title} | {b.planned_seconds:.0f}s | "
                     f"{a.get('duration', 0):.1f}s | {c.get('duration', 0):.1f}s | "
                     f"{c.get('engine','—')} | {t.get('pts_applied','—')} | "
                     f"{len(t.get('caption_cues', []))} |")

    # ---- STT detail
    a8 = next((r for r in ctx.reports if r.step == "8-audio"), None)
    if a8 and a8.details.get("beats"):
        L += ["", "### STT round-trip (Whisper large-v3, per spoken chunk)", "",
              "| Beat | phonetic CER (gate) | phonetic WER | raw WER | flagged chunks |",
              "|---|---|---|---|---|"]
        for d in a8.details["beats"]:
            L.append(f"| {d['slug']} | {d['phonetic_cer']:.3f} | {d['phonetic_wer']:.3f} | "
                     f"{d['raw_wer']:.3f} | {len(d['bad_chunks'])} |")
        L += ["", "Whisper returns Devanagari while the script is Roman Hinglish, so raw WER "
                  "mostly measures transliteration spelling, not speech. The gate metric is "
                  "the character error rate over an aspirate-collapsed consonant skeleton of "
                  "both sides, which compares what was *said*. All three are reported."]

    # ---- reel
    reel = ctx.state.get("assembly", {}).get("reel") or {}
    if reel:
        L += ["", "### Reel cut", "",
              f"- {reel.get('seconds')}s, {ctx.cfg['video']['reel']['width']}×"
              f"{ctx.cfg['video']['reel']['height']}, {reel.get('cues')} caption cues"]
        for p in reel.get("picks", []):
            L.append(f"  - `{p['slug']}` {p.get('from','')}→{p.get('to','')} "
                     f"({p['seconds']}s)")

    # ---- constraints
    L += ["", "## Audience constraints (config-enforced)", "",
          "| Constraint | Enforced by | Result |", "|---|---|---|"]
    e4 = next((r for r in ctx.reports if r.step == "4-examples"), None)
    L += [f"| India-only examples, no Western references | `eval_4` banned-reference scan + "
          f"approved-anchor match | {'✅' if e4 and e4.passed else '❌'} |",
          f"| Every jargon term unpacked on first use | `eval_4` jargon table | "
          f"{'✅' if e4 and e4.passed else '❌'} |",
          f"| Warm Hinglish mentor voice | `step6` rubric (voice axis) | "
          f"{'✅' if any(r.step=='step6-judge' and r.passed for r in ctx.reports) else '❌'} |",
          f"| All skill levels | `step6` rubric (accessibility axis) | "
          f"{'✅' if any(r.step=='step6-judge' and r.passed for r in ctx.reports) else '❌'} |"]

    v = ctx.cfg["voice"]
    L += ["", "## Voice settings in force", "",
          f"- Speaker `{v['speaker_id']}` · `{v['model_id']}` · temperature "
          f"`{v.get('temperature')}` · pace `{v.get('pace')}`",
          f"- Synthesis unit: **{v.get('synthesis_unit', 'sentence')}** — one request per "
          f"pause-delimited run, so intonation carries across a thought instead of "
          f"resetting at every full stop",
          f"- Numbers in English: **{v.get('numbers_in_english')}** — years, decimals and "
          f"quantities are spoken as English words, not read in Hindi",
          f"- {len(v.get('devanagari_terms') or {})} technical terms respelled in Devanagari "
          f"for TTS only, so English words land in Indian English "
          f"(`script.md`, on-screen text and captions stay in Latin)",
          "- Voice chosen from the audition pack in `lessons/_voice_samples/` "
          "(`factory/voicecheck.py`); accent and warmth are not machine-measurable, so that "
          "choice is a listening decision, not a gate.",
          "",
          "## Notes", "",
          "- LLM-judge rows are cached rubric verdicts in `factory/judgements/` with the "
          "judge recorded; every other row is a machine measurement recomputed on each run.",
          "- No human has listened to the narration in this run. The audio evidence is the "
          "STT round-trip above plus the technical-term and number checks.",
          "- BasicTeX was not installed (its pkg installer needs interactive sudo), so the "
          "scenes are LaTeX-free — all symbols are Unicode text or hand-built shapes. "
          "This ffmpeg build also lacks libass and freetype, so captions are rendered with "
          "PIL into an alpha track and overlaid, rather than burned by `subtitles`.",
          ""]
    out.write_text("\n".join(L))
    print(f"\nwrote {out}")
    return publish


# ==========================================================================
STEPS = [
    ("1-research", step_1, eval_1),
    ("2-refine",   step_2, eval_2),
    ("3-script",   step_3, eval_3),
    ("4-examples", step_4, eval_4),
    ("5-qa",       step_5, eval_5),
    ("6-finalize", step_6, eval_6),
    ("7-animate",  step_7, eval_7),
    ("8-audio",    step_8, eval_8),
    ("9-assemble", step_9, eval_9),
]


def run_topic(topic_id: str, cfg: dict, topics: dict, from_step: int = 1,
              force: bool = False):
    topic = next((t for t in topics["topics"] if t["id"] == topic_id), None)
    if not topic:
        sys.exit(f"unknown topic '{topic_id}' — add it to topics.yaml")
    lesson_dir = ROOT / cfg["output"]["publish_dir"] / topic_id
    lesson_dir.mkdir(parents=True, exist_ok=True)
    ctx = Ctx(topic_id, topic, cfg, lesson_dir, force=force)
    ctx.ensure_dirs()

    if not ctx.script_path.exists() or not ctx.fact_path.exists():
        sys.exit(f"[{topic_id}] needs fact_sheet.md and script.md (status "
                 f"'{topic.get('status')}'). Steps 1–6 are verify passes in this build.")

    rehydrate(ctx, from_step)
    print(f"\n=== {topic_id} — {topic['title']} (from step {from_step}) ===")
    for i, (name, step_fn, eval_fn) in enumerate(STEPS, start=1):
        if i < from_step:
            continue
        run_with_gate(name, step_fn, eval_fn, ctx, cfg["evals"]["max_retries"])

    publish = write_qa_report(ctx)
    print(f"\n[{topic_id}] {'PUBLISH-READY' if publish else 'NOT publish-ready'}")
    for p in (ctx.final_mp4, ctx.reel_mp4):
        if p.exists():
            print(f"  {p}  ({ffprobe_duration(p):.1f}s)")
    return publish


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--from", dest="from_step", type=int, default=1)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    cfg = load_yaml(CFG_PATH)
    topics = load_yaml(TOPICS_PATH)

    if args.all:
        ok = True
        for t in topics["topics"]:
            ld = ROOT / cfg["output"]["publish_dir"] / t["id"]
            if not (ld / "script.md").exists():
                print(f"[{t['id']}] skipped — no script.md yet (status "
                      f"'{t.get('status')}')")
                continue
            ok &= bool(run_topic(t["id"], cfg, topics, args.from_step, args.force))
        sys.exit(0 if ok else 1)
    elif args.topic:
        sys.exit(0 if run_topic(args.topic, cfg, topics, args.from_step, args.force) else 1)
    else:
        sys.exit("usage: pipeline.py --topic ML1.1 | --all")


if __name__ == "__main__":
    main()
