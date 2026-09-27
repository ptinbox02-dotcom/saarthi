"""Local sentence embeddings. No API, no network, no torch.

Retrieval at request time has a budget measured against a model call that already costs
about two seconds, so a few tens of milliseconds here is invisible — but a *network* hop
is not, and neither is a provider that can be switched off. This runs an ONNX MiniLM on
the CPU: roughly 20ms for a query, and it is the same code path whether the corpus lives
on a laptop or inside the deployed container.

It also fixes a real weakness. Keyword routing answered "why does ionisation enthalpy
dip at oxygen" with electron-gain-enthalpy facts, because the question shares no words
with Hund's rule or electron pairing. Embeddings match on meaning, which is what a
student's phrasing requires.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
MODEL = HERE / "models" / "minilm"
_session = _tokenizer = None


def _load():
    global _session, _tokenizer
    if _session is None:
        import onnxruntime
        from tokenizers import Tokenizer
        if not (MODEL / "model.onnx").exists():
            raise RuntimeError(f"no embedding model at {MODEL}")
        _session = onnxruntime.InferenceSession(
            str(MODEL / "model.onnx"), providers=["CPUExecutionProvider"])
        _tokenizer = Tokenizer.from_file(str(MODEL / "tokenizer.json"))
        _tokenizer.enable_truncation(max_length=256)
        _tokenizer.enable_padding()
    return _session, _tokenizer


def encode(texts: list[str], batch: int = 32) -> np.ndarray:
    """Mean-pooled, L2-normalised embeddings — so a dot product is cosine similarity."""
    sess, tok = _load()
    names = {i.name for i in sess.get_inputs()}
    out = []
    for i in range(0, len(texts), batch):
        enc = tok.encode_batch([t[:2000] for t in texts[i:i + batch]])
        ids = np.array([e.ids for e in enc], dtype=np.int64)
        mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
        feed = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in names:
            feed["token_type_ids"] = np.zeros_like(ids)
        hidden = sess.run(None, {k: v for k, v in feed.items() if k in names})[0]
        m = mask[..., None].astype(np.float32)
        pooled = (hidden * m).sum(1) / np.clip(m.sum(1), 1e-9, None)
        out.append(pooled / np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-9, None))
    return np.vstack(out).astype(np.float32)


class Index:
    """Vectors beside the corpus, in the same file. Flat, because at this size an
    approximate index would add a dependency and a tuning parameter to save nothing."""

    def __init__(self, db: sqlite3.Connection):
        self.db = db
        db.execute("CREATE TABLE IF NOT EXISTS vec ("
                   "kind TEXT NOT NULL, ref TEXT NOT NULL, dim INTEGER NOT NULL,"
                   " v BLOB NOT NULL, PRIMARY KEY (kind, ref))")
        self._cache: dict[str, tuple[list[str], np.ndarray]] = {}

    def build(self, kind: str, rows: list[tuple[str, str]]) -> int:
        if not rows:
            return 0
        vecs = encode([t for _, t in rows])
        self.db.executemany(
            "INSERT OR REPLACE INTO vec(kind,ref,dim,v) VALUES (?,?,?,?)",
            [(kind, ref, vecs.shape[1], vecs[i].tobytes()) for i, (ref, _) in enumerate(rows)])
        self.db.commit()
        self._cache.pop(kind, None)
        return len(rows)

    def _matrix(self, kind: str):
        if kind not in self._cache:
            rows = self.db.execute(
                "SELECT ref, v FROM vec WHERE kind=? ORDER BY ref", (kind,)).fetchall()
            refs = [r[0] for r in rows]
            mat = (np.vstack([np.frombuffer(r[1], dtype=np.float32) for r in rows])
                   if rows else np.zeros((0, 384), dtype=np.float32))
            self._cache[kind] = (refs, mat)
        return self._cache[kind]

    def search(self, kind: str, query: str, k: int = 8) -> list[tuple[str, float]]:
        refs, mat = self._matrix(kind)
        if not refs:
            return []
        q = encode([query])[0]
        scores = mat @ q
        top = np.argsort(-scores)[:k]
        return [(refs[i], float(scores[i])) for i in top]
