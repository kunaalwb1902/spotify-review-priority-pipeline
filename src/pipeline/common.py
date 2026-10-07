"""Shared paths and helpers. The source-row hash comes from the course checker itself."""

import hashlib
import importlib.util
import json
import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
CHECKER_PATH = Path(os.environ.get("CHECKER_PATH", RAW / "check_submission.py"))


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_submission", CHECKER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checker = _load_checker()
FIELDS = checker.FIELDS            # review_id, review_text, review_rating, review_likes, app_version, review_timestamp
TOPICS = checker.TOPICS
INTENTS = checker.INTENTS
row_sha = checker.row_sha          # exact grading-contract hash; never reimplement
csv_rows = checker.csv_rows        # strict, multiline-safe reader used by the grader


def file_sha256(path):
    return checker.sha(path)


def text_sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def is_empty(text):
    return not text.strip()        # same rule as the checker's profile


def connect(db_path):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # Shared with worker threads; every access goes through the caller's lock.
    con = sqlite3.connect(db_path, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def write_json(path, value):
    """Atomic write: temp file then rename, so a crash never leaves half a file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
