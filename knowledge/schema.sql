-- The chemistry knowledge layer.
--
-- Six kinds of thing, because a tutor needs more than claims. Facts are what is true;
-- methods are how a question is attacked; derivations are why a result holds;
-- connections are what links two chapters; misconceptions are what students actually
-- believe instead; figures are what has to be drawn. Only the first of those comes
-- free from a textbook, and it is the least useful on its own.
--
-- Every row that asserts anything carries a span_ref. Not for provenance theatre —
-- it is what lets a claim be verified against its source, reviewed in seconds instead
-- of minutes, and repaired when the source turns out to be wrong.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS source (
  id          TEXT PRIMARY KEY,
  title       TEXT NOT NULL,
  kind        TEXT NOT NULL CHECK (kind IN ('textbook','module','sheet','notes')),
  lang        TEXT NOT NULL DEFAULT 'en',
  class       TEXT,
  subject     TEXT NOT NULL DEFAULT 'chemistry',
  path        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS page (
  id          TEXT PRIMARY KEY,           -- source_id:page_no
  source_id   TEXT NOT NULL REFERENCES source(id),
  page_no     INTEGER NOT NULL,
  sha         TEXT NOT NULL,              -- of the reconstructed text; re-runs skip matches
  chars       INTEGER NOT NULL,
  residue     INTEGER NOT NULL DEFAULT 0, -- orphan notation fragments left behind
  needs_vision INTEGER NOT NULL DEFAULT 0,
  UNIQUE (source_id, page_no)
);

-- A span is a passage of the source. Prose, a formula, a figure, or a question.
CREATE TABLE IF NOT EXISTS span (
  id          INTEGER PRIMARY KEY,
  page_id     TEXT NOT NULL REFERENCES page(id),
  ord         INTEGER NOT NULL,
  kind        TEXT NOT NULL CHECK (kind IN ('prose','formula','heading','question','figure')),
  text        TEXT NOT NULL,
  latex       TEXT,
  figure_path TEXT
);
CREATE INDEX IF NOT EXISTS span_page ON span(page_id);

CREATE TABLE IF NOT EXISTS concept (
  id          TEXT PRIMARY KEY,           -- stable slug, the join key
  name        TEXT NOT NULL,
  name_hi     TEXT,                       -- from the NCERT Hindi edition
  chapter     TEXT,
  syllabus_seq INTEGER,                   -- given by the syllabus, never inferred
  summary     TEXT,
  status      TEXT NOT NULL DEFAULT 'candidate'
);

CREATE TABLE IF NOT EXISTS fact (
  id          INTEGER PRIMARY KEY,
  concept_id  TEXT NOT NULL REFERENCES concept(id),
  claim       TEXT NOT NULL,
  detail      TEXT,
  span_id     INTEGER REFERENCES span(id),
  status      TEXT NOT NULL DEFAULT 'candidate'
                CHECK (status IN ('candidate','verified','approved','rejected')),
  verdict     TEXT,                       -- why the verifier passed or failed it
  approved_by TEXT,
  approved_at TEXT
);
CREATE INDEX IF NOT EXISTS fact_concept ON fact(concept_id);

-- How a question is attacked. The thing coaching material has that a textbook does not.
CREATE TABLE IF NOT EXISTS method (
  id           INTEGER PRIMARY KEY,
  name         TEXT NOT NULL,
  trigger      TEXT NOT NULL,             -- how you recognise it applies
  steps        TEXT NOT NULL,             -- json array
  common_error TEXT,
  difficulty   TEXT,
  span_id      INTEGER REFERENCES span(id),
  status       TEXT NOT NULL DEFAULT 'candidate'
);
CREATE TABLE IF NOT EXISTS method_concept (
  method_id   INTEGER NOT NULL REFERENCES method(id),
  concept_id  TEXT NOT NULL REFERENCES concept(id),
  PRIMARY KEY (method_id, concept_id)
);

CREATE TABLE IF NOT EXISTS derivation (
  id          INTEGER PRIMARY KEY,
  concept_id  TEXT NOT NULL REFERENCES concept(id),
  result      TEXT NOT NULL,
  steps       TEXT NOT NULL,
  span_id     INTEGER REFERENCES span(id),
  status      TEXT NOT NULL DEFAULT 'candidate'
);

-- What makes the tutor able to answer across chapters instead of refusing.
CREATE TABLE IF NOT EXISTS connection (
  id          INTEGER PRIMARY KEY,
  concept_a   TEXT NOT NULL REFERENCES concept(id),
  concept_b   TEXT NOT NULL REFERENCES concept(id),
  relation    TEXT NOT NULL,
  explanation TEXT NOT NULL,
  span_id     INTEGER REFERENCES span(id),
  status      TEXT NOT NULL DEFAULT 'candidate'
);
CREATE INDEX IF NOT EXISTS conn_a ON connection(concept_a);
CREATE INDEX IF NOT EXISTS conn_b ON connection(concept_b);

CREATE TABLE IF NOT EXISTS question (
  id          INTEGER PRIMARY KEY,
  source_id   TEXT NOT NULL REFERENCES source(id),
  page_id     TEXT REFERENCES page(id),
  number      INTEGER,
  stem        TEXT NOT NULL,
  options     TEXT,                       -- json {"A": "...", ...}
  answer      TEXT,
  concept_id  TEXT REFERENCES concept(id),
  method_id   INTEGER REFERENCES method(id),
  difficulty  TEXT
);
CREATE INDEX IF NOT EXISTS q_concept ON question(concept_id);

-- A wrong option in a well-made question is a catalogued student error.
CREATE TABLE IF NOT EXISTS misconception (
  id            INTEGER PRIMARY KEY,
  concept_id    TEXT NOT NULL REFERENCES concept(id),
  wrong_belief  TEXT NOT NULL,
  why_tempting  TEXT,
  question_id   INTEGER REFERENCES question(id),
  refuted_by    INTEGER REFERENCES fact(id),
  status        TEXT NOT NULL DEFAULT 'candidate'
);

CREATE TABLE IF NOT EXISTS figure (
  id          INTEGER PRIMARY KEY,
  concept_id  TEXT REFERENCES concept(id),
  page_id     TEXT NOT NULL REFERENCES page(id),
  image_path  TEXT NOT NULL,
  caption     TEXT,
  description TEXT
);

-- What each agent did, so a bad batch can be found and re-run.
CREATE TABLE IF NOT EXISTS agent_run (
  id          INTEGER PRIMARY KEY,
  agent       TEXT NOT NULL,
  target      TEXT NOT NULL,
  provider    TEXT,
  started_at  TEXT NOT NULL,
  finished_at TEXT,
  produced    INTEGER DEFAULT 0,
  ok          INTEGER DEFAULT 0,
  note        TEXT
);
