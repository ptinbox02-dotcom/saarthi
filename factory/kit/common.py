#!/usr/bin/env python3
"""Shared helpers for the Saarthi micro-lecture factory."""
from __future__ import annotations

import json
import os
import re
import subprocess
import shlex
from dataclasses import dataclass, field, asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FACTORY = ROOT / "factory"
CFG_PATH = FACTORY / "config.yaml"
TOPICS_PATH = FACTORY / "topics.yaml"
VENV_PY = ROOT / ".venv" / "bin" / "python"


# ----- config / env --------------------------------------------------------
def load_yaml(p: Path) -> dict:
    import yaml
    return yaml.safe_load(p.read_text())


def load_env(path: Path = ROOT / ".env") -> dict:
    """Minimal .env reader. Never logs values, never writes them anywhere."""
    env = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    env.update({k: v for k, v in os.environ.items() if k.endswith("_API_KEY")})
    return env


# ----- shell ---------------------------------------------------------------
def sh(cmd, check=True, capture=True, cwd=None, timeout=None) -> subprocess.CompletedProcess:
    if isinstance(cmd, str):
        cmd = shlex.split(cmd)
    return subprocess.run(
        cmd, check=check, cwd=cwd, timeout=timeout,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
        text=True,
    )


def ffprobe_duration(path: Path) -> float:
    r = sh(["ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=nw=1:nk=1", str(path)])
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def ffprobe_streams(path: Path) -> dict:
    r = sh(["ffprobe", "-v", "error", "-show_streams", "-show_format",
            "-of", "json", str(path)])
    return json.loads(r.stdout)


# ----- eval plumbing -------------------------------------------------------
@dataclass
class EvalResult:
    step: str
    passed: bool
    score: float = 0.0
    feedback: str = ""
    details: dict = field(default_factory=dict)
    # False when re-running the step cannot possibly change the outcome (e.g. a missing
    # human/agent judgement) — the runner stops at once instead of burning retries.
    retryable: bool = True

    def to_dict(self):
        return asdict(self)


@dataclass
class Ctx:
    topic_id: str
    topic: dict
    cfg: dict
    lesson_dir: Path
    reports: list = field(default_factory=list)
    force: bool = False
    state: dict = field(default_factory=dict)

    # convenience paths
    @property
    def script_path(self) -> Path:      return self.lesson_dir / "script.md"
    @property
    def fact_path(self) -> Path:        return self.lesson_dir / "fact_sheet.md"
    @property
    def outline_path(self) -> Path:     return self.lesson_dir / "outline.md"
    @property
    def scenes_dir(self) -> Path:       return self.lesson_dir / "scenes"
    @property
    def clips_dir(self) -> Path:        return self.lesson_dir / "clips"
    @property
    def audio_dir(self) -> Path:        return self.lesson_dir / "audio"
    @property
    def build_dir(self) -> Path:        return self.lesson_dir / "build"
    @property
    def final_mp4(self) -> Path:        return self.lesson_dir / f"{self.topic_id}.mp4"
    @property
    def reel_mp4(self) -> Path:         return self.lesson_dir / f"{self.topic_id}_reel.mp4"

    def ensure_dirs(self):
        for d in (self.scenes_dir, self.clips_dir, self.audio_dir, self.build_dir):
            d.mkdir(parents=True, exist_ok=True)


# ----- text utilities ------------------------------------------------------
BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.S)
FACT_TAG_RE = re.compile(r"\[F\d+\]")


def strip_md(text: str) -> str:
    """Remove markdown emphasis but keep the words."""
    text = BOLD_RE.sub(r"\1", text)
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\1", text)
    text = text.replace("`", "")
    return text


def words(text: str) -> list[str]:
    return [w for w in re.split(r"[^\wऀ-ॿ]+", text.lower()) if w]


def mmss(seconds: float) -> str:
    m, s = divmod(int(round(seconds)), 60)
    return f"{m}:{s:02d}"
