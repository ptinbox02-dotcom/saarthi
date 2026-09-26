#!/usr/bin/env python3
"""Eval gates for the Saarthi factory.

Everything here is deterministic and re-runnable: parse the artifact, measure it,
return a score. The one exception is the LLM-judge rubric (step 6 / step 9 final QA),
which is *cached* in factory/judgements/<topic>.<step>.json — see llm_judge().
Provenance of every judgement is recorded so qa_report.md can say who scored it.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
from pathlib import Path

from common import EvalResult, ROOT, sh, words

JUDGE_DIR = ROOT / "factory" / "judgements"


# ==========================================================================
# fact sheet
# ==========================================================================
FACT_ROW = re.compile(r"^\|\s*(F\d+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*$", re.M)


def parse_fact_sheet(path: Path) -> dict:
    txt = path.read_text()
    facts = {m.group(1): {"claim": m.group(2), "detail": m.group(3),
                          "confidence": m.group(4)} for m in FACT_ROW.finditer(txt)}
    sources = []
    msrc = re.search(r"##\s*Sources.*?\n(.*?)(?=\n##\s|\Z)", txt, re.S)
    if msrc:
        sources = [l.strip("- ").strip() for l in msrc.group(1).splitlines()
                   if l.strip().startswith("-")]
    in_scope = _bullets(txt, "What is IN scope")
    out_scope = _bullets(txt, "OUT of scope")
    exact = _bullets(txt, "Numbers to get exactly right on screen")
    anchors = _bullets(txt, "Indian-context anchors")
    return {"facts": facts, "sources": sources, "in_scope": in_scope,
            "out_scope": out_scope, "exact_numbers": exact, "india_anchors": anchors,
            "raw": txt}


def _bullets(txt: str, heading: str) -> list[str]:
    m = re.search(rf"##\s*{re.escape(heading)}.*?\n(.*?)(?=\n##\s|\Z)", txt, re.S)
    if not m:
        return []
    return [re.sub(r"^[-*]\s*", "", l).strip() for l in m.group(1).splitlines()
            if l.strip().startswith(("-", "*"))]


# ---- step 1: numbers / dates / sourcing ----------------------------------
CANON = {
    "e/m": [r"1\.758", r"10\s*[¹\^]*\s*11|10¹¹"],
    "thomson_year": [r"\b1897\b"],
    "mass_ratio": [r"\b1836\b"],
    "electron_mass": [r"9\.1[01]\d*\s*×\s*10⁻³¹|9\.109"],
    "electron_charge": [r"1\.602\s*×\s*10⁻¹⁹|1\.602"],
    "stoney": [r"Stoney.*?1891|1891.*?Stoney"],
    "goldstein": [r"Goldstein.*?1876|1876.*?Goldstein"],
    "millikan": [r"Millikan"],
}


def eval_step1_facts(fact_path: Path, min_sources: int = 2) -> EvalResult:
    if not fact_path.exists():
        return EvalResult("1-research", False, 0.0, "fact_sheet.md missing")
    fs = parse_fact_sheet(fact_path)
    txt = fs["raw"]
    missing, checked = [], []
    for name, pats in CANON.items():
        ok = all(re.search(p, txt, re.I | re.S) for p in pats)
        checked.append((name, ok))
        if not ok:
            missing.append(name)

    # every fact row must carry a confidence tag
    unrated = [k for k, v in fs["facts"].items()
               if v["confidence"].strip().lower() not in ("standard", "verified")]

    n_src = len(fs["sources"])
    score = (len(checked) - len(missing)) / max(1, len(checked))
    passed = not missing and not unrated and n_src >= min_sources and len(fs["facts"]) >= 10
    fb = []
    if missing:
        fb.append(f"canonical values not found verbatim: {missing}")
    if unrated:
        fb.append(f"fact rows without a confidence rating: {unrated}")
    if n_src < min_sources:
        fb.append(f"only {n_src} sources listed, need >= {min_sources}")
    return EvalResult(
        "1-research", passed, score, "; ".join(fb) or
        f"{len(fs['facts'])} facts, {n_src} sources, all canonical numbers/dates exact",
        {"facts": len(fs["facts"]), "sources": n_src,
         "canonical_checked": dict(checked)},
    )


# ---- step 2: outline coverage --------------------------------------------
def eval_step2_outline(outline_path: Path, fact_path: Path, beats) -> EvalResult:
    if not outline_path.exists():
        return EvalResult("2-refine", False, 0.0, "outline.md missing")
    fs = parse_fact_sheet(fact_path)
    otxt = outline_path.read_text().lower()
    covered, missing = [], []
    for item in fs["in_scope"]:
        keys = [w for w in words(item) if len(w) > 4][:4]
        hit = sum(1 for k in keys if k in otxt)
        (covered if hit >= max(1, len(keys) // 2) else missing).append(item)

    # nothing derived from the OUT-of-scope list
    offscope = []
    for item in fs["out_scope"]:
        head = words(item)[:3]
        if head and all(w in otxt for w in head) and "out of scope" not in otxt:
            offscope.append(item)

    all_beats_listed = all(f"beat {b.index}" in otxt for b in beats)
    score = len(covered) / max(1, len(fs["in_scope"]))
    passed = not missing and all_beats_listed
    fb = []
    if missing:
        fb.append(f"in-scope items not covered: {missing}")
    if not all_beats_listed:
        fb.append("outline does not list every beat")
    return EvalResult("2-refine", passed, score, "; ".join(fb) or
                      f"{len(covered)}/{len(fs['in_scope'])} in-scope items covered, "
                      f"{len(beats)} beats, nothing off-scope",
                      {"covered": covered, "offscope_derived": offscope})


# ---- step 3: length / hook / readability ----------------------------------
def _band(x: float, best: float, worst: float) -> float:
    """100 at `best`, 0 at `worst`, linear in between."""
    if worst == best:
        return 100.0
    return max(0.0, min(100.0, 100.0 * (worst - x) / (worst - best)))


def hinglish_readability(text: str) -> tuple[float, dict]:
    """A 0-100 ease score for spoken Hinglish, built from measurable structure.

    Flesch is deliberately not used: its syllable coefficient is calibrated on English,
    and transliterated Hindi carries far more vowel groups per word for the same
    difficulty, so Flesch calls any Hinglish narration 'hard' regardless of how simple
    it is. These three components are the ones that actually govern whether a listener
    keeps up, and each is a direct measurement with a stated scale.
    """
    # spoken breaks: the script uses … and — as real pauses, not just . ! ?
    sents = [s.strip() for s in re.split(r"[.!?…—:;]+", text) if s.strip()]
    ws = text.split()
    if not sents or not ws:
        return 0.0, {}
    lens = sorted(len(s.split()) for s in sents)
    median = lens[len(lens) // 2]
    long_share = sum(1 for l in lens if l > 25) / len(lens)
    cpw = sum(len(w) for w in ws) / len(ws)
    parts = {
        "median_words_per_sentence": median,
        "share_sentences_over_25_words": round(long_share, 3),
        "mean_chars_per_word": round(cpw, 2),
    }
    score = (_band(median, 12, 30) + _band(long_share, 0.0, 0.30) + _band(cpw, 5.0, 9.0)) / 3
    parts["components"] = [round(_band(median, 12, 30), 1),
                           round(_band(long_share, 0.0, 0.30), 1),
                           round(_band(cpw, 5.0, 9.0), 1)]
    return score, parts


def eval_step3_script(beats, cfg) -> EvalResult:
    v = cfg["video"]["full"]
    total_words = sum(len(b.narration_plain.split()) for b in beats)
    planned = max(b.t_end for b in beats)
    est = total_words / 150.0 * 60.0            # ~150 wpm Hinglish
    text = " ".join(b.narration_plain for b in beats)
    ease, ease_parts = hinglish_readability(text)

    hook = beats[0].narration_plain if beats else ""
    hook_words = hook.split()[:9]               # ~3 s of speech
    hook_ok = bool(hook_words) and any(
        k in " ".join(hook_words).lower() for k in ("tube light", "tubelight", "tumhare ghar"))

    dur_ok = v["min_seconds"] <= planned <= v["max_seconds"]
    ease_ok = ease >= cfg["evals"]["script_reading_ease_min"]
    passed = dur_ok and hook_ok and ease_ok
    fb = []
    if not dur_ok:
        fb.append(f"planned runtime {planned:.0f}s outside {v['min_seconds']}-{v['max_seconds']}s")
    if not hook_ok:
        fb.append("no concrete Indian hook object in the first ~3s of narration")
    if not ease_ok:
        fb.append(f"readability proxy {ease:.0f} < {cfg['evals']['script_reading_ease_min']}")
    return EvalResult("3-script", passed, ease / 100,
                      "; ".join(fb) or
                      f"{total_words} words, planned {planned:.0f}s (est {est:.0f}s @150wpm), "
                      f"readability {ease:.0f}/100 (median {ease_parts['median_words_per_sentence']} "
                      f"words/sentence, {ease_parts['mean_chars_per_word']} chars/word), "
                      f"hook = tube light",
                      {"words": total_words, "planned_seconds": planned,
                       "estimated_seconds": round(est), "readability": round(ease, 1),
                       **ease_parts})


# ---- step 4: India analogies + jargon unpacked -----------------------------
JARGON = {
    "cathode": ["negative terminal", "minus"],
    "anode": ["positive", "plus"],
    "discharge tube": ["kaanch", "band tube", "sealed", "gas"],
    "cathode rays": ["cathode se", "kirn"],
    "momentum": ["mass", "takra"],
    "e/m": ["charge-to-mass", "charge divided by mass", "charge to mass"],
    "subatomic": ["atom ke", "atom se bhi chhota", "andar"],
    "electron": ["particle"],
}


def eval_step4_examples(beats, fact_path: Path, cfg) -> EvalResult:
    aud = cfg["audience"]
    text = " ".join(b.narration_plain for b in beats)
    low = text.lower()

    banned = [b for b in aud.get("banned_references", [])
              if re.search(rf"\b{re.escape(str(b).lower())}\b", low)]

    fs = parse_fact_sheet(fact_path)
    anchors = []
    for a in fs["india_anchors"]:
        head = re.sub(r"\*\*", "", a.split("=")[0]).strip().lower()
        head = re.split(r"[/(]", head)[0].strip()
        anchors.append(head)
    used = [a for a in anchors if a and a.split()[0] in low]

    # Every analogy must lean on an approved Indian anchor. An analogy often spans two
    # sentences ("Jaise hawa … Bilkul waise."), so the anchor is looked for in a window
    # around the marker rather than in the marker's own sentence.
    analogy_markers = ("jaise", "bilkul waise", "ki tarah", "wali tarah")
    domain = set(words(fs["raw"]))          # everything the fact sheet already covers
    sents = [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]
    unanchored = []
    for i, s in enumerate(sents):
        low = s.lower()
        hit = next((m for m in analogy_markers if m in low), None)
        if not hit:
            continue
        # "jaise tungsten" names an in-domain example, not an analogy to everyday life;
        # only a comparison to something OUTSIDE the topic needs an Indian anchor.
        after = words(low.split(hit, 1)[1])[:4]
        if any(w in domain for w in after):
            continue
        window = " ".join(sents[max(0, i - 1):i + 2]).lower()
        if not any(a and a.split()[0] in window for a in anchors):
            unanchored.append(s)

    missing_jargon = []
    for term, unpackers in JARGON.items():
        if term in low and not any(u in low for u in unpackers):
            missing_jargon.append(term)

    passed = not banned and not missing_jargon and len(used) >= 2 and not unanchored
    score = 1.0 - (0.2 * len(banned) + 0.15 * len(missing_jargon) + 0.1 * len(unanchored))
    fb = []
    if banned:
        fb.append(f"banned non-Indian reference used: {banned}")
    if missing_jargon:
        fb.append(f"jargon used without unpacking: {missing_jargon}")
    if unanchored:
        fb.append(f"analogy not tied to an approved Indian anchor: {unanchored[:2]}")
    if len(used) < 2:
        fb.append(f"only {len(used)} approved India anchors used")
    return EvalResult("4-examples", passed, max(0.0, min(1.0, score)),
                      "; ".join(fb) or
                      f"India anchors used: {used}; every jargon term unpacked; "
                      f"no Western references",
                      {"anchors_used": used, "jargon_checked": list(JARGON)})


# ---- step 5: Q&A / trap coverage -------------------------------------------
def eval_step5_qa(script_path: Path, beats) -> EvalResult:
    txt = script_path.read_text()
    m = re.search(r"##\s*Step-5 coverage check.*?\n(.*?)(?=\n##\s|\Z)", txt, re.S)
    if not m:
        return EvalResult("5-qa", False, 0.0, "no step-5 coverage checklist in script.md")
    rows = [l for l in m.group(1).splitlines() if l.strip().startswith("-")]
    unticked = [r for r in rows if "✅" not in r]

    # each row must point at a beat that actually exists and mentions the answer
    bad_refs = []
    idxs = {b.index: b for b in beats}
    for r in rows:
        for n in re.findall(r"Beat\s+(\d+)", r):
            if int(n) not in idxs:
                bad_refs.append(r.strip())

    trap_row = [r for r in rows if "trap" in r.lower()]
    trap_in_script = bool(re.search(r"e ya m\s+alag-alag nahi|NOT e", txt, re.I))

    passed = rows and not unticked and not bad_refs and trap_row and trap_in_script
    return EvalResult("5-qa", bool(passed), (len(rows) - len(unticked)) / max(1, len(rows)),
                      ("; ".join(filter(None, [
                          f"unticked coverage rows: {len(unticked)}" if unticked else "",
                          f"rows referencing a missing beat: {bad_refs}" if bad_refs else "",
                          "" if trap_row else "the e/m trap is not in the coverage checklist",
                          "" if trap_in_script else "the e/m trap is not stated in narration",
                      ])) or
                       f"{len(rows)} student questions covered, all mapped to real beats, "
                       f"e/m trap present"),
                      {"rows": len(rows)})


# ---- step 6: hallucination trace -------------------------------------------
NUM_RE = re.compile(r"\d[\d,.]*\s*(?:×\s*10[⁻\d¹²³⁰-⁹^\-]*)?")


def eval_step6_hallucination(beats, fact_path: Path, cfg) -> EvalResult:
    fs = parse_fact_sheet(fact_path)
    ftxt = fs["raw"]
    fnorm = _numnorm(ftxt)

    unknown_tags, untraced_numbers, untraced_names = [], [], []
    for b in beats:
        for tag in b.fact_tags:
            if tag.strip("[]") not in fs["facts"]:
                unknown_tags.append((b.index, tag))
        # every number spoken must exist in the fact sheet
        for raw in NUM_RE.findall(b.narration_plain):
            n = _numnorm(raw.strip(" .,;:"))        # sentence punctuation is not part of the number
            if not n or len(n) < 3:
                continue
            if n not in fnorm:
                untraced_numbers.append((b.index, raw.strip()))
        # Every proper name spoken must exist in the fact sheet. Only mid-sentence
        # capitals count as names — Hinglish narration capitalises ordinary words at
        # the start of a clause ("Lekin…", "Magnet paas lao"), and the script uses
        # — : ; … as clause breaks just like full stops.
        for clause in re.split(r"[.!?…—:;]+", b.narration_plain):
            # a clause may open with a stray quote mark left by the sentence split,
            # so strip punctuation first and only then treat token 0 as clause-initial
            toks = [t for t in (w.strip(",'\"()") for w in clause.split()) if t]
            for name in toks[1:]:
                if not re.fullmatch(r"[A-Z][a-z]{2,}", name):
                    continue
                if name not in ftxt and name.lower() not in ftxt.lower():
                    untraced_names.append((b.index, name))

    # tagged claims must be numerically consistent with the sheet they cite
    inconsistencies = []
    for b in beats:
        if "F10" in [t.strip("[]") for t in b.fact_tags]:
            if not re.search(r"1\.7[56]", b.narration_plain):
                inconsistencies.append((b.index, "F10 cited but e/m value not stated"))
        if "F12" in [t.strip("[]") for t in b.fact_tags]:
            if "1836" not in b.narration_plain:
                inconsistencies.append((b.index, "F12 cited but 1836x not stated"))

    problems = unknown_tags + untraced_numbers + untraced_names + inconsistencies
    total_claims = sum(len(b.fact_tags) for b in beats)
    accuracy = 1.0 if not problems else max(0.0, 1 - len(problems) / max(1, total_claims))
    passed = not problems and accuracy >= cfg["evals"]["factual_accuracy_gate"]
    fb = []
    if unknown_tags:
        fb.append(f"[Fx] tags with no fact-sheet row: {unknown_tags}")
    if untraced_numbers:
        fb.append(f"numbers spoken that are not in fact_sheet.md: {untraced_numbers}")
    if untraced_names:
        fb.append(f"names spoken that are not in fact_sheet.md: {untraced_names}")
    if inconsistencies:
        fb.append(f"cited fact not actually stated: {inconsistencies}")
    return EvalResult("6-finalize", passed, accuracy, "; ".join(fb) or
                      f"{total_claims} tagged claims across {len(beats)} beats — every number, "
                      f"date and name traces to fact_sheet.md",
                      {"tagged_claims": total_claims, "problems": len(problems)})


def _numnorm(s: str) -> str:
    sup = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻", "0123456789-")
    return re.sub(r"[\s,]", "", s.translate(sup))


# ---- LLM-judge (cached, with provenance) -----------------------------------
def llm_judge(topic_id: str, step: str, min_score: float) -> EvalResult:
    """Rubric score for things no regex can measure (flow, engagement, mentor voice).

    Judgements live in factory/judgements/<topic>.<step>.json and record who produced
    them, so the QA report never passes an agent opinion off as a machine measurement.
    """
    p = JUDGE_DIR / f"{topic_id}.{step}.json"
    if not p.exists():
        return EvalResult(f"{step}-judge", False, 0.0,
                          f"no judgement on file at {p.relative_to(ROOT)} — re-running the "
                          f"step cannot produce one; write it and re-run",
                          retryable=False)
    j = json.loads(p.read_text())
    scores = j["scores"]
    mean = sum(scores.values()) / len(scores)
    lows = {k: v for k, v in scores.items() if v < min_score}
    return EvalResult(f"{step}-judge", not lows and mean >= min_score, mean,
                      (f"below threshold: {lows}" if lows else
                       f"rubric mean {mean:.2f}/5 ({j.get('judge','?')}) — " + j.get("summary", "")),
                      {"scores": scores, "judge": j.get("judge"), "when": j.get("when")})


# ==========================================================================
# step 7 — render sanity + frame OCR
# ==========================================================================
def grab_frame(video: Path, at: float, tmp: Path) -> Path | None:
    """One frame, upright grey-on-white — good for both pixel stats and OCR."""
    tmp.parent.mkdir(parents=True, exist_ok=True)
    sh(["ffmpeg", "-v", "error", "-ss", f"{at}", "-i", str(video), "-frames:v", "1",
        "-vf", "scale=1280:-1", "-y", str(tmp)], check=False)
    return tmp if tmp.exists() else None


def frame_spread(png: Path) -> float:
    """Luma standard deviation. A blank render sits at ~0."""
    try:
        import numpy as np
        from PIL import Image
        return float(np.asarray(Image.open(png).convert("L"), dtype="float32").std())
    except Exception:
        return -1.0


def ocr_frame(png: Path) -> str:
    """OCR a slide frame, both polarities, with local thresholding.

    Tesseract binarises globally, which on this dark theme drops any text sitting on a
    mid-tone panel — the white-on-red TRAP banner disappeared entirely. Subtracting a
    blurred copy gives a local threshold, and running it at both polarities catches
    light-on-dark body text and dark-on-light panel text in the same frame.
    """
    try:
        import numpy as np
        import pytesseract
        from PIL import Image, ImageFilter, ImageOps
    except Exception:
        return sh(["tesseract", str(png), "stdout"], check=False).stdout

    g = Image.open(png).convert("L")
    outs = [pytesseract.image_to_string(ImageOps.invert(g))]
    bg = g.filter(ImageFilter.GaussianBlur(radius=20))
    d = np.asarray(g, dtype=int) - np.asarray(bg, dtype=int)
    for pol in (1, -1):
        mask = (d * pol) > 10
        img = Image.fromarray(np.uint8(255 - mask * 255))
        outs.append(pytesseract.image_to_string(img, config="--psm 11"))
    return "\n".join(outs)


def _fuzzy_in(needle: str, hay: str, cutoff=0.82) -> bool:
    n = re.sub(r"\s+", "", needle.lower())
    h = re.sub(r"\s+", "", hay.lower())
    if n in h:
        return True
    # OCR noise tolerance: slide a window of the needle's length
    for i in range(0, max(1, len(h) - len(n) + 1)):
        if difflib.SequenceMatcher(None, n, h[i:i + len(n)]).ratio() >= cutoff:
            return True
    return False


def eval_step7_render(clips: list[dict], beats, cfg, tmpdir: Path) -> EvalResult:
    """Non-blank + duration + OCR of the labels each beat's brief promises."""
    expect = {
        0: ["tubelight", "electron"],
        1: ["discharge tube", "10,000"],
        2: ["cathode", "anode", "cathode rays"],
        3: ["shadow", "momentum", "negative"],
        4: ["same", "particle"],
        5: ["1897", "1.758", "not e", "1836"],
        6: ["thomson", "rutherford", "bohr"],
        7: ["particle", "ionise", "x-rays", "1836", "tungsten", "hydrogen"],
        8: ["saarthi", "e/m", "tubelight"],
    }
    problems, details = [], []
    for c in clips:
        b = next(x for x in beats if x.slug == c["slug"])
        v = Path(c["path"])
        dur = c["duration"]
        # a clip is sized to its narration when audio exists, otherwise to its slot
        want_len = float(c.get("target") or b.planned_seconds)
        tol = max(1.0, 0.05 * want_len)
        if abs(dur - want_len) > tol:
            problems.append(f"{b.slug}: clip {dur:.1f}s vs target {want_len:.1f}s "
                            f"(tol {tol:.1f}s)")

        # sample roughly every 8s: a long beat holds several distinct cards, and a
        # fixed sample count can skip one entirely (that is how the X-ray card's
        # "tungsten" label went unseen on a 127s beat).
        n = max(9, min(24, int(dur / 8) + 1))
        samples = [round(0.06 + 0.90 * i / (n - 1), 4) for i in range(n)]
        pngs, stds, texts = [], [], []
        for f in samples:
            p = grab_frame(v, dur * f, tmpdir / f"{b.slug}_{int(f*100)}.png")
            if p is None:
                continue
            pngs.append(p)
            stds.append(round(frame_spread(p), 2))
            texts.append(ocr_frame(p))
        live = sum(1 for s in stds if s > 3.0)
        if live < len(samples) - 1:
            problems.append(f"{b.slug}: {len(samples)-live}/{len(samples)} sampled frames "
                            f"look blank (luma sd {stds})")

        text = " ".join(texts)
        want = expect.get(b.index, [])
        miss = [w for w in want if not _fuzzy_in(w, text)]
        if miss:
            problems.append(f"{b.slug}: OCR could not find {miss}")
        details.append({"slug": b.slug, "duration": dur, "target": want_len,
                        "luma_sd": stds, "engine": c.get("engine"),
                        "labels_found": [w for w in want if w not in miss],
                        "labels_missing": miss})

    score = 1.0 - min(1.0, len(problems) / max(1, len(clips) * 3))
    return EvalResult("7-animate", not problems, score,
                      "; ".join(problems) or
                      f"{len(clips)} clips render non-blank at 1920x1080, durations match "
                      f"their beats, and every promised on-screen label OCRs back",
                      {"clips": details})


# ==========================================================================
# step 8 — STT round-trip
# ==========================================================================
_DEV_MAP = None


def romanize(s: str) -> str:
    global _DEV_MAP
    s = s.replace("।", " ").replace("॥", " ")     # danda / double danda
    if not re.search(r"[ऀ-ॿ]", s):
        return s
    try:
        from indic_transliteration import sanscript
        from indic_transliteration.sanscript import transliterate
        # IAST, not HK: HK writes anusvara as an uppercase "M", which lowercases into
        # the preceding "m" and makes momentum ("momeMTam") fold to "mtm" instead of
        # "mntm". IAST's ṃ maps cleanly to n in the skeleton table below.
        return transliterate(s, sanscript.DEVANAGARI, sanscript.IAST)
    except Exception:
        return s


_SKEL_DROP = set("aeiouāīūṛṝḷeoaiau'`~^")


def phon(word: str) -> str:
    """Consonant skeleton — the comparable core of a Hinglish word.

    Roman Hinglish spelling is ad-hoc ('hoon'/'hun', 'saarthi'/'sarthi') and Whisper
    returns Devanagari, so raw WER between the two measures orthography, not speech.
    Folding both sides to aspirate-collapsed consonants compares what was *said*.
    """
    w = word.lower()
    w = re.sub(r"[^a-zāīūṛṝḷḥṃṅñṭḍṇśṣ]", "", w)
    for a, b in (("chh", "c"), ("ch", "c"), ("kh", "k"), ("gh", "g"), ("th", "t"),
                 ("dh", "d"), ("ph", "f"), ("bh", "b"), ("jh", "j"), ("sh", "s"),
                 ("ṭh", "t"), ("ḍh", "d"), ("ṣ", "s"), ("ś", "s"), ("ṭ", "t"),
                 ("ḍ", "d"), ("ṇ", "n"), ("ṅ", "n"), ("ñ", "n"), ("ṃ", "n"),
                 ("ḥ", ""), ("q", "k"), ("x", "ks"), ("z", "j"), ("v", "w"),
                 ("y", ""), ("c", "k")):
        w = w.replace(a, b)
    w = "".join(ch for ch in w if ch not in _SKEL_DROP)
    w = re.sub(r"(.)\1+", r"\1", w)
    return w


def phonetic_wer(ref: str, hyp: str, match_cutoff=0.7) -> dict:
    """Levenshtein WER where two words match if their skeletons are close."""
    R = [phon(w) for w in words(romanize(ref))]
    H = [phon(w) for w in words(romanize(hyp))]
    R = [w for w in R if w]
    H = [w for w in H if w]
    n, m = len(R), len(H)
    if n == 0:
        return {"wer": 1.0, "ref_words": 0, "hyp_words": m, "sub": 0, "ins": m, "dele": 0}

    def same(a, b):
        return a == b or difflib.SequenceMatcher(None, a, b).ratio() >= match_cutoff

    # classic DP, with the fuzzy equality above
    prev = list(range(m + 1))
    ops_prev = [(0, i, 0) for i in range(m + 1)]        # (sub, ins, del)
    for i in range(1, n + 1):
        cur = [i]
        ops_cur = [(0, 0, i)]
        for j in range(1, m + 1):
            if same(R[i - 1], H[j - 1]):
                cost, op = prev[j - 1], ops_prev[j - 1]
            else:
                cands = [(prev[j - 1] + 1, _add(ops_prev[j - 1], 1, 0, 0)),
                         (cur[j - 1] + 1, _add(ops_cur[j - 1], 0, 1, 0)),
                         (prev[j] + 1, _add(ops_prev[j], 0, 0, 1))]
                cost, op = min(cands, key=lambda x: x[0])
            cur.append(cost)
            ops_cur.append(op)
        prev, ops_prev = cur, ops_cur
    s, ins, dele = ops_prev[m]
    return {"wer": prev[m] / n, "ref_words": n, "hyp_words": m,
            "sub": s, "ins": ins, "dele": dele}


def _add(t, a, b, c):
    return (t[0] + a, t[1] + b, t[2] + c)


# Numbers are spoken in English ("eighteen ninety seven") but Whisper writes them back
# as digits ("1897"), so leaving them in the word stream measures notation, not speech.
# They are stripped here and checked separately by number_check().
NUMWORD = set(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen twenty thirty forty fifty "
    "sixty seventy eighty ninety hundred thousand million point power into oh".split()
)


def skeleton_stream(s: str) -> str:
    out = []
    for w in words(romanize(s)):
        if w.isdigit() or w in NUMWORD:
            continue
        p = phon(w)
        if p:
            out.append(p)
    return "".join(out)


def _num_forms(token: str) -> list[str]:
    """Every way a spoken number may come back from STT: digits or English words."""
    from audio import int_to_english, year_to_english
    forms = [token, token.replace(",", "")]
    if re.fullmatch(r"\d+", token):
        n = int(token)
        forms.append(int_to_english(n))
        if 1500 <= n <= 2099:
            forms.append(year_to_english(n))
    elif re.fullmatch(r"\d+\.\d+", token):
        whole, frac = token.split(".")
        forms.append(int_to_english(int(whole)) + " point "
                     + " ".join(int_to_english(int(d)) for d in frac))
    return forms


def number_check(expected: list[str], transcript: str) -> list[str]:
    """Which of the lesson's must-get-right numbers are missing from the transcript."""
    hay = re.sub(r"[\s,]", "", transcript.lower())
    missing = []
    for tok in expected:
        if not any(re.sub(r"[\s,]", "", f.lower()) in hay for f in _num_forms(tok)):
            missing.append(tok)
    return missing


def phonetic_cer(ref: str, hyp: str) -> float:
    """Error rate over the concatenated consonant skeleton — the gate metric.

    Word-level WER is unusable here: Whisper returns Devanagari, which merges and
    splits words differently from Roman Hinglish ('e-by-m' comes back as one token
    'ebAema', 'naapa' as two). Those are orthography differences, not speech errors.
    Dropping word boundaries and comparing the phonetic skeleton character-by-character
    measures what was actually said.
    """
    R, H = skeleton_stream(ref), skeleton_stream(hyp)
    if not R:
        return 1.0
    prev = list(range(len(H) + 1))
    for i, rc in enumerate(R, 1):
        cur = [i]
        for j, hc in enumerate(H, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rc != hc)))
        prev = cur
    return prev[len(H)] / len(R)


def raw_wer(ref: str, hyp: str) -> float:
    try:
        import jiwer
        return float(jiwer.wer(" ".join(words(romanize(ref))), " ".join(words(romanize(hyp)))))
    except Exception:
        return float("nan")


_WHISPER = None


def transcribe(wav: Path, language="hi", model_size="large-v3") -> str:
    global _WHISPER
    from faster_whisper import WhisperModel
    if _WHISPER is None:
        _WHISPER = WhisperModel(model_size, device="cpu", compute_type="int8")
    segs, _ = _WHISPER.transcribe(str(wav), language=language, beam_size=5,
                                  vad_filter=False, condition_on_previous_text=False)
    return " ".join(s.text for s in segs).strip()


def transcribe_chunk(wav: Path, start: float, end: float, tmp: Path) -> str:
    """Transcribe one spoken chunk in isolation.

    Whisper silently drops whole regions of a 60 s narration; on a single sentence it
    cannot, and a bad chunk is then pinpointed for a targeted phonetic hint.

    Results are cached by audio content, so re-running the gate on unchanged narration
    is seconds rather than a quarter of an hour — the eval stays exact and idempotent.
    """
    tmp.parent.mkdir(parents=True, exist_ok=True)
    sh(["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
        "-i", str(wav), "-c", "copy", "-y", str(tmp)], check=False)
    if not tmp.exists():
        return ""
    key = hashlib.sha1(tmp.read_bytes()).hexdigest()
    cache_path = tmp.parent / "transcripts.json"
    cache = {}
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text())
        except Exception:
            cache = {}
    if key in cache:
        return cache[key]
    text = transcribe(tmp)
    cache[key] = text
    cache_path.write_text(json.dumps(cache, indent=1, ensure_ascii=False))
    return text


STT_SEGMENT_MAX_SECONDS = 24.0


def speech_runs(chunks: list[dict], budget: float = STT_SEGMENT_MAX_SECONDS) -> list[dict]:
    """Group the sentence-level manifest into clips sized for one Whisper window.

    Two failure modes bracket this. Transcribing a whole beat lets Whisper silently drop
    an entire region. Transcribing sentence by sentence is accurate but wasteful: every
    pass pays for a full 30s encoder window however short the clip. And grouping by
    synthesis run is worse still -- a run can be 60s, i.e. several windows plus a long
    sequential decode.

    So: merge consecutive sentences up to just under one window, and always break at a
    silence. Few passes, each one cheap, and no clip long enough to lose a region.
    """
    runs, cur = [], None
    for c in chunks:
        if c["kind"] != "speech":
            cur = None
            continue
        if cur is not None and c["end"] - cur["start"] > budget:
            cur = None
        # compare against what was *said*, not the caption spelling
        said = c.get("spoken") or c["text"]
        if cur is None:
            cur = {"i": len(runs), "start": c["start"], "end": c["end"], "text": said}
            runs.append(cur)
        else:
            cur["end"] = c["end"]
            cur["text"] += " " + said
    return runs


def eval_step8_audio(metas: list[dict], beats, cfg, tmpdir: Path) -> EvalResult:
    thr = cfg["evals"]["audio_wer_max"]
    must = [t.lower() for t in cfg["voice"].get("must_hear", [])]
    v = cfg["video"]["full"]
    problems, details = [], []
    all_hyp = []
    total_audio = sum(m["duration"] for m in metas)

    for meta in metas:
        b = next(x for x in beats if x.slug == meta["slug"])
        wav = Path(meta["wav_path"])
        pieces, worst = [], []
        for g in speech_runs(meta["chunks"]):
            h = transcribe_chunk(wav, g["start"], g["end"],
                                 tmpdir / f"{b.slug}_{g['i']:03d}.wav")
            pieces.append(h)
            ccer = phonetic_cer(g["text"], h)
            if ccer > 0.30:                 # this run needs a hint, not an average
                worst.append({"text": g["text"][:70], "heard": h[:70],
                              "cer": round(ccer, 2)})
        hyp = " ".join(pieces)
        all_hyp.append(hyp)
        # reference = what was actually voiced, i.e. the spoken chunks only.
        # meta["spoken_text"] still carries the [PAUSE] markers, which are directions
        # to the splicer, not words, and would count as errors against the transcript.
        ref = " ".join(c.get("spoken") or c["text"]
                       for c in meta["chunks"] if c["kind"] == "speech")
        cer = phonetic_cer(ref, hyp)
        pw = phonetic_wer(ref, hyp)
        rw = raw_wer(ref, hyp)
        if cer > thr:
            problems.append(f"{b.slug}: phonetic CER {cer:.3f} > {thr}"
                            + (f"; worst chunks {worst}" if worst else ""))
        # beat slots in script.md are storyboard estimates; what must hold is that no
        # beat is wildly off its slot and the lesson total lands in the 5-7 min window
        tol = max(8.0, 0.40 * b.planned_seconds)
        if abs(meta["duration"] - b.planned_seconds) > tol:
            problems.append(f"{b.slug}: audio {meta['duration']:.1f}s vs planned "
                            f"{b.planned_seconds:.0f}s (tol {tol:.0f}s)")
        details.append({"slug": b.slug, "phonetic_cer": round(cer, 3),
                        "phonetic_wer": round(pw["wer"], 3),
                        "raw_wer": round(rw, 3), "duration": meta["duration"],
                        "bad_chunks": worst, "transcript": hyp})

    if not (v["min_seconds"] - 10 <= total_audio + 5 <= v["max_seconds"]):
        problems.append(f"narration total {total_audio:.0f}s (+5s brand cards) falls "
                        f"outside the {v['min_seconds']}-{v['max_seconds']}s window — "
                        f"adjust voice.pace")

    # pronunciation gate on the technical terms, over the whole lesson
    stream = skeleton_stream(" ".join(all_hyp))
    missed_terms = [t for t in must if phon(t) and phon(t) not in stream]
    if missed_terms:
        problems.append(f"technical terms not heard back in the STT round-trip: {missed_terms}")

    mean_cer = sum(d["phonetic_cer"] for d in details) / max(1, len(details))
    mean_wer = sum(d["phonetic_wer"] for d in details) / max(1, len(details))
    return EvalResult("8-audio", not problems, max(0.0, 1 - mean_cer),
                      "; ".join(problems) or
                      f"mean phonetic CER {mean_cer:.3f} (gate {thr}) over {len(details)} beats "
                      f"[word-level phonetic WER {mean_wer:.3f}]; every technical term heard "
                      f"back; narration total {total_audio:.0f}s",
                      {"beats": details, "mean_phonetic_cer": round(mean_cer, 3),
                       "mean_phonetic_wer": round(mean_wer, 3),
                       "total_narration_seconds": round(total_audio, 1),
                       "missed_terms": missed_terms})


# ==========================================================================
# step 9 — A/V sync, captions, final QA
# ==========================================================================
def parse_srt(p: Path) -> list[dict]:
    out = []
    for block in re.split(r"\n\s*\n", p.read_text().strip()):
        lines = [l for l in block.splitlines() if l.strip()]
        if len(lines) < 3:
            continue
        m = re.match(r"(\d\d):(\d\d):(\d\d),(\d\d\d)\s*-->\s*(\d\d):(\d\d):(\d\d),(\d\d\d)",
                     lines[1])
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        out.append({"start": g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000,
                    "end": g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000,
                    "text": " ".join(lines[2:])})
    return out


def eval_step9_assemble(ctx, timeline: list[dict], cfg) -> EvalResult:
    from common import ffprobe_streams, ffprobe_duration
    tol = cfg["evals"]["av_sync_tolerance_seconds"]
    v = cfg["video"]["full"]
    problems, details = [], []

    final = ctx.final_mp4
    if not final.exists():
        return EvalResult("9-assemble", False, 0.0, f"{final.name} was not produced")
    info = ffprobe_streams(final)
    vs = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    as_ = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    if not vs or not as_:
        return EvalResult("9-assemble", False, 0.0, "final mp4 is missing a video or audio stream")

    vdur = float(vs.get("duration") or ffprobe_duration(final))
    adur = float(as_.get("duration") or vdur)
    if abs(vdur - adur) > tol:
        problems.append(f"A/V length drift {abs(vdur-adur):.3f}s > {tol}s")
    if not (v["min_seconds"] <= vdur <= v["max_seconds"]):
        problems.append(f"runtime {vdur:.1f}s outside {v['min_seconds']}-{v['max_seconds']}s")
    if (vs["width"], vs["height"]) != (v["width"], v["height"]):
        problems.append(f"final is {vs['width']}x{vs['height']}, want {v['width']}x{v['height']}")

    # per-beat: the animation segment must equal its narration to within tolerance
    for t in timeline:
        if abs(t["video_seconds"] - t["audio_seconds"]) > tol:
            problems.append(f"{t['slug']}: clip {t['video_seconds']:.3f}s vs audio "
                            f"{t['audio_seconds']:.3f}s")

    # captions: every cue must sit inside the segment its chunk came from
    srt = ctx.build_dir / f"{ctx.topic_id}.srt"
    if not srt.exists():
        problems.append("no SRT generated")
    else:
        cues = parse_srt(srt)
        expected = [c for t in timeline for c in t["caption_cues"]]
        if len(cues) != len(expected):
            problems.append(f"SRT has {len(cues)} cues, timeline expects {len(expected)}")
        else:
            drift = max(abs(c["start"] - e["start"]) for c, e in zip(cues, expected))
            if drift > tol:
                problems.append(f"caption drift {drift:.3f}s > {tol}s")
            details.append({"caption_cues": len(cues), "max_caption_drift": round(drift, 3)})

    # the reel
    reel = ctx.reel_mp4
    rcfg = cfg["video"]["reel"]
    if cfg["output"].get("make_reel_teaser", True):
        if not reel.exists():
            problems.append("reel was not produced")
        else:
            rinfo = ffprobe_streams(reel)
            rv = next(s for s in rinfo["streams"] if s["codec_type"] == "video")
            rdur = ffprobe_duration(reel)
            if rdur > rcfg["max_seconds"]:
                problems.append(f"reel {rdur:.1f}s > {rcfg['max_seconds']}s")
            if (rv["width"], rv["height"]) != (rcfg["width"], rcfg["height"]):
                problems.append(f"reel is {rv['width']}x{rv['height']}, "
                                f"want {rcfg['width']}x{rcfg['height']}")
            details.append({"reel_seconds": round(rdur, 2)})

    # keyframes + transcript for the final multimodal QA pass
    kf = export_keyframes(final, ctx.build_dir / "keyframes", timeline)
    details.append({"keyframes": len(kf), "keyframe_dir": str(
        (ctx.build_dir / "keyframes").relative_to(ROOT))})

    details.append({"final_seconds": round(vdur, 2), "audio_seconds": round(adur, 2),
                    "resolution": f"{vs['width']}x{vs['height']}"})
    return EvalResult("9-assemble", not problems, 1.0 if not problems else 0.5,
                      "; ".join(problems) or
                      f"{vdur:.1f}s at {vs['width']}x{vs['height']}, A/V drift "
                      f"{abs(vdur-adur)*1000:.0f}ms, captions aligned, reel OK",
                      {"checks": details})


def export_keyframes(video: Path, outdir: Path, timeline: list[dict]) -> list[Path]:
    """One frame per beat mid-point plus a few evenly-spaced — the input to the
    final multimodal QA rubric (keyframes + transcript -> accuracy/quality)."""
    # wipe first: a previous build's frames sitting alongside the current ones is a
    # trap for whoever reviews them (they are named by timeline position, and the
    # timeline moves between runs)
    outdir.mkdir(parents=True, exist_ok=True)
    for old in outdir.glob("*.png"):
        old.unlink()
    marks = []
    for t in timeline:
        if t.get("video_seconds", 0) > 2:
            marks.append((t["slug"], t["start"] + t["video_seconds"] * 0.45))
            marks.append((t["slug"] + "b", t["start"] + t["video_seconds"] * 0.85))
    out = []
    for name, at in marks:
        p = outdir / f"{name}_{int(at):04d}s.png"
        sh(["ffmpeg", "-v", "error", "-ss", f"{at:.2f}", "-i", str(video),
            "-frames:v", "1", "-vf", "scale=960:-1", "-y", str(p)], check=False)
        if p.exists():
            out.append(p)
    return out


# ===== VISUAL_GRAMMAR.md — anti-monotony + emphasis coverage ================
def _frame_signatures(clip: Path, fps: float = 2.0, width: int = 96) -> list:
    """Downscaled greyscale frames, ~fps per second, as float arrays in 0..1.

    Deliberately small: the question is "did the framing change", not "did a pixel
    change", so a 96px luma thumbnail is both sufficient and cheap.
    """
    import tempfile

    import numpy as np
    from PIL import Image

    with tempfile.TemporaryDirectory() as td:
        sh(["ffmpeg", "-v", "error", "-i", str(clip),
            "-vf", f"fps={fps},scale={width}:-1,format=gray",
            str(Path(td) / "f_%05d.png")], check=False)
        return [np.asarray(Image.open(f)).astype("float32") / 255.0
                for f in sorted(Path(td).glob("f_*.png"))]


def eval_visual_grammar(clips: list, cfg: dict, cues_dir: Path) -> EvalResult:
    """Fail a beat that holds one framing, or that never directs the eye.

    Two independent checks, because they catch different failures:
      * anti-monotony - consecutive sampled frames barely differ for longer than
        direction.max_static_seconds AND no emphasis move was logged inside that
        window. A beat passes by moving the camera OR by having something genuinely
        change on screen; either counts as directing attention.
      * emphasis coverage - the scene logged at least the required number of
        push-in / spotlight / reframe moves for its length.
    """
    import numpy as np

    d = cfg.get("direction", {})
    max_static = float(d.get("max_static_seconds", 5.0))
    min_per_beat = int(d.get("min_emphasis_per_beat", 1))
    per_seconds = float(d.get("emphasis_per_seconds", 20.0))
    fps = 2.0
    # Relative, not absolute. These scenes are dark by design (near-black canvas,
    # dim cards), so a fixed luma-difference threshold reads a busy dark beat as static
    # and a bright one as active. Comparing the change against the frame's own
    # brightness makes the test behave the same on beat 8 as on beat 2.
    quiet_rel = 0.02

    problems, details = [], []
    for c in clips:
        slug = c["slug"]
        clip = Path(c["path"])
        dur = float(c.get("duration") or ffprobe_duration(clip))
        cues_path = cues_dir / f"{slug}.cues.json"
        cues = json.loads(cues_path.read_text()) if cues_path.exists() else {}
        events = (cues.get("emphasis") or {}).get("events", [])
        ev_t = [e["t"] for e in events]

        frames = _frame_signatures(clip, fps=fps)
        diffs = []
        for i in range(1, len(frames)):
            change = float(np.abs(frames[i] - frames[i - 1]).mean())
            level = max(float(frames[i].mean()), 1e-4)
            diffs.append(change / level)

        worst_run, worst_at, run, run_start = 0.0, 0.0, 0, 0
        for i, v in enumerate(diffs):
            if v < quiet_rel:
                if run == 0:
                    run_start = i
                run += 1
            else:
                run = 0
            if run:
                t0, t1 = run_start / fps, (i + 1) / fps
                if not any(t0 <= t <= t1 for t in ev_t) and (t1 - t0) > worst_run:
                    worst_run, worst_at = t1 - t0, t0

        need = max(min_per_beat, int(round(dur / per_seconds)))
        got = len(events)

        if worst_run > max_static:
            problems.append(f"{slug}: {worst_run:.1f}s static at {worst_at:.1f}s "
                            f"(limit {max_static:.0f}s) - needs a move or a reveal")
        if got < need:
            problems.append(f"{slug}: {got} emphasis moves, needs {need} for {dur:.0f}s")
        details.append({"slug": slug, "duration": round(dur, 1),
                        "emphasis": got, "emphasis_needed": need,
                        "longest_static_seconds": round(worst_run, 1),
                        "static_at": round(worst_at, 1),
                        "by_kind": (cues.get("emphasis") or {}).get("by_kind", {})})

    passed = not problems
    return EvalResult(
        step="visual_grammar", passed=passed,
        score=1.0 if passed else round(1 - len(problems) / max(1, 2 * len(clips)), 2),
        feedback=("every beat directs the eye and no framing is held too long"
                  if passed else "; ".join(problems)),
        details={"beats": details},
    )
