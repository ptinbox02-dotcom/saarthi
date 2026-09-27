"""The deterministic half of the knowledge layer.

Everything here runs without a model, a key or a network — which is the point. If
notation reconstruction regresses, the corpus fills with chemistry that is confidently
wrong and nothing downstream can tell. These cases are the ones that were actually
broken before the reconstruction existed.

    python3 -m pytest knowledge/tests -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "knowledge"), str(ROOT / "knowledge" / "extract")]

import pdf          # noqa: E402
import questions     # noqa: E402
from store import Store   # noqa: E402

SHEETS = ROOT / "sources" / "unpacked" / "sheets"
NCERT = ROOT / "sources" / "unpacked" / "ncert-chemistry"
have_sources = pytest.mark.skipif(not SHEETS.exists(),
                                  reason="source PDFs not unpacked")


# --- notation, the thing plain text extraction destroys -------------------------
@have_sources
def test_subscripts_and_charges_survive():
    """pypdf yielded zero sub/superscripts across 143 sampled pages of these files."""
    pages = pdf.read(str(SHEETS / "Electrochemistry.pdf"))
    text = "\n".join(p["text"] for p in pages)
    assert "O₂" in text, "a diatomic subscript did not survive"
    assert "H⁺" in text, "a charge did not survive"
    assert any(ion in text for ion in ("Fe²⁺", "Zn²⁺", "Cu²⁺")), "no 2+ ion survived"


@have_sources
def test_a_real_equation_reads_correctly():
    """The line that came out of pypdf as scattered fragments."""
    pages = pdf.read(str(SHEETS / "Electrochemistry.pdf"))
    text = "\n".join(p["text"] for p in pages)
    assert "2Fe(s) + O₂(g) + 4H⁺(aq)" in text


@have_sources
def test_a_split_superscript_is_not_left_with_a_gap():
    """Zn² ⁺ arrives as two runs; it must join."""
    text = "\n".join(p["text"] for p in pdf.read(str(SHEETS / "Electrochemistry.pdf")))
    assert "² ⁺" not in text and "₂ ₊" not in text


@have_sources
def test_page_numbers_do_not_trigger_a_vision_pass():
    """A bare integer on its own line looks exactly like an orphaned script fragment.
    Counting it flagged every page in the book for a pass it does not need."""
    pages = pdf.read(str(NCERT / "kech1dd" / "kech103.pdf"))
    flagged = sum(1 for p in pages if p["residue"] > 3)
    assert flagged / len(pages) < 0.30, f"{flagged}/{len(pages)} pages flagged"


# --- spans: the unit a citation points at ---------------------------------------
@have_sources
def test_spans_are_small_enough_to_check_by_hand():
    """A 4,400-character citation makes verification vacuous."""
    pages = pdf.read(str(NCERT / "kech1dd" / "kech103.pdf"))
    spans = [s for p in pages for s in pdf.split_spans(p["text"])]
    assert spans
    lens = sorted(len(s["text"]) for s in spans)
    median = lens[len(lens) // 2]
    assert median < 1100, f"median span {median} chars"
    assert lens[-1] < 4500


def test_a_paragraph_is_cut_on_sentence_ends_not_mid_clause():
    body = " ".join(f"Sentence number {i} says something about orbitals." for i in range(60))
    spans = pdf.split_spans(body)
    assert len(spans) > 1
    for s in spans[:-1]:
        assert s["text"].rstrip().endswith("."), s["text"][-40:]


def test_kind_of_recognises_a_question():
    assert pdf.kind_of("40. Consider the following cell reaction: ...") == "question"


# --- questions and their answer keys --------------------------------------------
@have_sources
def test_questions_parse_with_their_answers():
    pages = pdf.read(str(SHEETS / "Hydrogen.pdf"))
    qs = questions.parse(pages, "hydrogen")
    assert len(qs) > 50, f"only {len(qs)} questions"
    answered = [q for q in qs if q["answer"]]
    assert len(answered) / len(qs) > 0.8, "most questions should have a key"
    # JEE mixes MCQs with integer-answer questions, whose key is a number and which
    # carry no options at all. Only the lettered ones must have choices — the first
    # version of this test assumed every question was an MCQ and failed on a real one.
    lettered = [q for q in answered if q["answer"][0].isalpha()]
    assert lettered, "expected some multiple-choice questions"
    assert all(len(q["options"]) >= 2 for q in lettered[:10])
    numeric = [q for q in answered if q["answer"][0].isdigit()]
    assert all(q["options"] == {} for q in numeric), "integer questions have no options"


@have_sources
def test_the_answer_key_page_is_not_itself_parsed_as_questions():
    pages = pdf.read(str(SHEETS / "Hydrogen.pdf"))
    qs = questions.parse(pages, "hydrogen")
    assert not any(len(q["stem"]) < 20 for q in qs)


# --- the store ------------------------------------------------------------------
def test_reingesting_an_unchanged_page_is_a_no_op(tmp_path):
    s = Store(tmp_path / "t.db")
    s.add_source("src", "Test", "textbook", "/tmp/x.pdf")
    page = {"page_no": 1, "text": "hello", "sha": "abc", "chars": 5, "residue": 0}
    assert not s.page_unchanged("src", 1, "abc")
    s.add_page("src", page); s.db.commit()
    assert s.page_unchanged("src", 1, "abc"), "a re-run would duplicate this page"
    assert not s.page_unchanged("src", 1, "different"), "a changed page must re-ingest"
    s.close()


def test_the_store_survives_writes_from_several_threads(tmp_path):
    """Agents fan out; the first run died on SQLite's thread affinity."""
    from concurrent.futures import ThreadPoolExecutor
    s = Store(tmp_path / "t.db")
    s.add_source("src", "T", "textbook", "/tmp/x.pdf")
    s.add_page("src", {"page_no": 0, "text": "x", "sha": "s", "chars": 1, "residue": 0})
    s.db.commit()
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: s.upsert_concept(f"c{i}", name=f"C{i}"), range(40)))
    assert s.counts()["concept"] == 40
    s.close()


# --- retrieval ------------------------------------------------------------------
def _corpus():
    import query
    from store import Store
    s = Store()
    if s.counts()["fact"] == 0:
        s.close()
        pytest.skip("no knowledge loaded")
    return s, query


def test_facts_follow_the_concept_ranking():
    """Regression: concepts resolved correctly and the facts came back in insertion
    order, so 'orbit versus orbital' was answered with s-block facts."""
    s, query = _corpus()
    try:
        ctx = query.context(s, "what is the difference between orbit and orbital")
        assert ctx["concepts"], "nothing resolved"
        top = ctx["concepts"][0]["id"]
        assert ctx["facts"], "no facts returned"
        first = ctx["facts"][0]
        assert first["concept"] == ctx["concepts"][0]["name"], \
            f"top fact is from {first['concept']}, not {top}"
    finally:
        s.close()


def test_retrieval_crosses_chapters():
    """The failure that started the redesign: a question spanning two chapters must
    not be answered from one of them."""
    s, query = _corpus()
    try:
        if len({r[0] for r in s.db.execute("SELECT DISTINCT chapter FROM concept")}) < 2:
            pytest.skip("only one chapter loaded")
        ctx = query.context(s, "how does shielding affect ionisation enthalpy and orbital energy")
        chapters = {c["chapter"] for c in ctx["concepts"]}
        assert len(chapters) > 1, f"stayed inside {chapters}"
    finally:
        s.close()


def test_every_fact_can_name_its_source():
    s, query = _corpus()
    try:
        orphan = s.db.execute(
            "SELECT COUNT(*) c FROM fact WHERE span_id IS NULL "
            "OR span_id NOT IN (SELECT id FROM span)").fetchone()["c"]
        assert orphan == 0, f"{orphan} fact(s) cite a span that does not exist"
    finally:
        s.close()
