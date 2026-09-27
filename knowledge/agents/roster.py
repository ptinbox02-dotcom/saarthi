"""The agents. One per kind of thing a tutor needs to know.

Facts are the least of it and the only part a textbook hands over directly. Methods are
what coaching material has and a textbook does not — how a question is attacked, not
what is true. Connections are what let the tutor answer across chapters instead of
saying "that is not in this chapter", which is the failure that started this design.
"""
from __future__ import annotations

import json
import re

from base import Agent, listy

SLUG = re.compile(r"[^a-z0-9]+")


def slug(s: str) -> str:
    return SLUG.sub("-", s.lower()).strip("-")[:60]


def _spans_text(spans, cap=14000) -> str:
    out, n = [], 0
    for s in spans:
        line = f"[span {s['id']} · p{s['page_no']}] {s['text']}"
        if n + len(line) > cap:
            break
        out.append(line); n += len(line)
    return "\n\n".join(out)


class Concepts(Agent):
    """What is this chapter actually about? Everything else hangs off these ids."""
    name = "concepts"
    table = "concept"
    batch_size = 1
    schema = {"type": "object", "properties": {"concepts": {"type": "array", "items": {
        "type": "object",
        "properties": {"name": {"type": "string"}, "summary": {"type": "string"},
                       "order": {"type": "integer"}},
        "required": ["name", "summary"]}}}, "required": ["concepts"]}

    def __init__(self, store, provider, source_id, chapter):
        super().__init__(store, provider)
        self.source_id, self.chapter = source_id, chapter

    def targets(self):
        return [self.store.spans_for(source_id=self.source_id)]

    def prompt(self, spans):
        return (
            "You are mapping a JEE chemistry chapter into the concepts it teaches.\n\n"
            "A concept is something a student can be stuck on and a question can be "
            "about — 'periodic trends in atomic radius', not 'chemistry' and not "
            "'the second paragraph'. Aim for 8 to 20 for a chapter.\n\n"
            "Return each with a short name, a one-sentence summary, and the order it "
            "is taught in.\n\nCHAPTER TEXT:\n" + _spans_text(spans))

    def rows(self, spans, result):
        out = []
        for i, c in enumerate(listy(result, "concepts")):
            if not c.get("name"):
                continue
            out.append({"id": slug(c["name"]), "name": c["name"],
                        "summary": c.get("summary", ""), "chapter": self.chapter,
                        "syllabus_seq": c.get("order", i)})
        # concepts are the join key; a duplicate id must update rather than collide
        for r in out:
            self.store.upsert_concept(r.pop("id"), **r)
        self.store.db.commit()
        return []


class ConceptAgent(Agent):
    """Shared shape for agents that work one concept at a time."""

    def targets(self):
        return [dict(r) for r in self.store.db.execute(
            "SELECT id, name, summary, chapter FROM concept ORDER BY syllabus_seq")]

    def evidence(self, concept, limit=24):
        """Spans that mention this concept. Keyword for now; vectors once indexed."""
        words = [w for w in re.split(r"\W+", concept["name"].lower()) if len(w) > 3]
        if not words:
            return []
        like = " OR ".join("lower(s.text) LIKE ?" for _ in words)
        rows = self.store.db.execute(
            f"SELECT s.id, s.text, p.page_no, p.source_id FROM span s "
            f"JOIN page p ON p.id=s.page_id WHERE ({like}) LIMIT {limit}",
            [f"%{w}%" for w in words]).fetchall()
        return [dict(r) for r in rows]


class Facts(ConceptAgent):
    name = "facts"
    table = "fact"
    schema = {"type": "object", "properties": {"facts": {"type": "array", "items": {
        "type": "object",
        "properties": {"claim": {"type": "string"}, "detail": {"type": "string"},
                       "span_id": {"type": "integer"}},
        "required": ["claim", "span_id"]}}}, "required": ["facts"]}

    def prompt(self, c):
        return (
            f"Extract the checkable facts a tutor must never get wrong about: "
            f"{c['name']} — {c.get('summary','')}\n\n"
            "Rules:\n"
            "- One claim per fact, stated plainly. Quote numbers exactly as the source "
            "gives them, with units.\n"
            "- Every fact MUST cite the span id it came from. No span, no fact.\n"
            "- Do not infer, combine or round. If the source does not say it, leave it "
            "out — a missing fact is recoverable, a wrong one is not.\n\n"
            "SPANS:\n" + _spans_text(self.evidence(c)))

    def rows(self, c, result):
        return [{"concept_id": c["id"], "claim": f["claim"],
                 "detail": f.get("detail", ""), "span_id": f["span_id"],
                 "status": "candidate"}
                for f in listy(result, "facts") if f.get("claim") and f.get("span_id")]


class Methods(ConceptAgent):
    """The thing coaching material has that a textbook does not."""
    name = "methods"
    table = "method"
    schema = {"type": "object", "properties": {"methods": {"type": "array", "items": {
        "type": "object",
        "properties": {"name": {"type": "string"}, "trigger": {"type": "string"},
                       "steps": {"type": "array", "items": {"type": "string"}},
                       "common_error": {"type": "string"},
                       "difficulty": {"type": "string"},
                       "span_id": {"type": "integer"}},
        "required": ["name", "trigger", "steps"]}}}, "required": ["methods"]}

    def evidence(self, concept, limit=30):
        rows = super().evidence(concept, limit)
        qs = self.store.db.execute(
            "SELECT id, stem FROM question WHERE concept_id=? LIMIT 20",
            (concept["id"],)).fetchall()
        return rows + [{"id": None, "page_no": 0, "source_id": "q",
                        "text": f"QUESTION: {r['stem']}"} for r in qs]

    def prompt(self, c):
        return (
            f"Identify the problem-solving methods for: {c['name']}.\n\n"
            "A method is a recipe a student applies, not a fact they recall. For each: "
            "how you recognise a question needs it (the trigger), the ordered steps, "
            "and the step where students most often go wrong.\n\n"
            "Only methods the material actually demonstrates. Two good ones beat eight "
            "invented ones.\n\nMATERIAL:\n" + _spans_text(self.evidence(c)))

    def rows(self, c, result):
        out = []
        for m in listy(result, "methods"):
            if not (m.get("name") and m.get("steps")):
                continue
            out.append({"name": m["name"], "trigger": m.get("trigger", ""),
                        "steps": json.dumps(m["steps"], ensure_ascii=False),
                        "common_error": m.get("common_error", ""),
                        "difficulty": m.get("difficulty", ""),
                        "span_id": m.get("span_id"), "status": "candidate"})
        return out


class Connections(ConceptAgent):
    """Why the tutor can answer across chapters instead of refusing."""
    name = "connections"
    table = "connection"
    workers = 3
    schema = {"type": "object", "properties": {"connections": {"type": "array", "items": {
        "type": "object",
        "properties": {"other": {"type": "string"}, "relation": {"type": "string"},
                       "explanation": {"type": "string"}},
        "required": ["other", "relation", "explanation"]}}}, "required": ["connections"]}

    def prompt(self, c):
        others = [dict(r) for r in self.store.db.execute(
            "SELECT id, name, chapter FROM concept WHERE id != ? ORDER BY syllabus_seq",
            (c["id"],))]
        catalogue = "\n".join(f"- {o['id']} : {o['name']} ({o['chapter']})" for o in others)
        return (
            f"Which other concepts does '{c['name']}' genuinely connect to, and how?\n\n"
            "A connection is a link a student needs in order to understand one thing "
            "through another — a shared equation, a cause, a special case. Not 'both "
            "are chemistry'. Give the relation in a few words and one sentence saying "
            "why it matters.\n\n"
            "Use only ids from this list, and return at most six.\n\n"
            f"CONCEPTS:\n{catalogue}")

    def rows(self, c, result):
        known = {r["id"] for r in self.store.db.execute("SELECT id FROM concept")}
        out = []
        for k in listy(result, "connections")[:6]:
            other = slug(k.get("other", ""))
            if other in known and other != c["id"]:
                out.append({"concept_a": c["id"], "concept_b": other,
                            "relation": k["relation"], "explanation": k["explanation"],
                            "status": "candidate"})
        return out


class Misconceptions(Agent):
    """Every wrong option in a good question is a student error someone catalogued."""
    name = "misconceptions"
    table = "misconception"

    schema = {"type": "object", "properties": {"misconceptions": {"type": "array", "items": {
        "type": "object",
        "properties": {"wrong_belief": {"type": "string"},
                       "why_tempting": {"type": "string"},
                       "question_id": {"type": "integer"},
                       "concept_id": {"type": "string"}},
        "required": ["wrong_belief", "question_id"]}}}, "required": ["misconceptions"]}

    def targets(self):
        rows = self.store.db.execute(
            "SELECT id, stem, options, answer, concept_id FROM question "
            "WHERE answer IS NOT NULL AND options != '{}' LIMIT 400").fetchall()
        return [list(rows[i:i + 12]) for i in range(0, len(rows), 12)]

    def prompt(self, batch):
        items = []
        for q in batch:
            opts = json.loads(q["options"] or "{}")
            items.append(f"[question {q['id']}] {q['stem']}\n"
                         + "\n".join(f"  ({k}) {v}" for k, v in opts.items())
                         + f"\n  correct: {q['answer']}")
        return (
            "Each question below has a correct answer and distractors. A well-written "
            "distractor is not random — it is the answer a student gets when they hold "
            "a specific wrong belief.\n\n"
            "For each distractor worth naming, state the wrong belief plainly, in the "
            "student's terms, and why it is tempting. Skip distractors that are merely "
            "arithmetic slips.\n\n" + "\n\n".join(items))

    def rows(self, batch, result):
        by_id = {q["id"]: q for q in batch}
        out = []
        for m in listy(result, "misconceptions"):
            q = by_id.get(m.get("question_id"))
            cid = m.get("concept_id") or (q and q["concept_id"])
            if not (cid and m.get("wrong_belief")):
                continue
            out.append({"concept_id": cid, "wrong_belief": m["wrong_belief"],
                        "why_tempting": m.get("why_tempting", ""),
                        "question_id": m.get("question_id"), "status": "candidate"})
        return out
