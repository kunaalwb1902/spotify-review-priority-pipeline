"""Shared construction of the model-call layer for downstream roles (verify, group, memo)
and a small result cache for downstream artifacts keyed by (role, input hash)."""

import hashlib
import json
import threading
import time
import uuid

from .llm import CALLS_SCHEMA, LLM, Ledger, load_rates

ARTIFACT_SCHEMA = """
CREATE TABLE IF NOT EXISTS artifact_cache (
    cache_key TEXT PRIMARY KEY, role TEXT, output TEXT, request_id TEXT, created_at REAL
);
"""


def make_llm(con, run_cfg, client, role):
    con.executescript(CALLS_SCHEMA + ARTIFACT_SCHEMA)
    lock = threading.Lock()
    spent = con.execute("SELECT COALESCE(SUM(cost_usd),0) FROM calls").fetchone()[0]
    ledger = Ledger(run_cfg["controls"]["spend_cap_usd"], con, lock, already_spent=spent)
    session = f"{role}-{time.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"
    return LLM(client, load_rates(), ledger, run_cfg["controls"], con, lock, session)


def cache_key(role, *parts):
    return role + ":" + hashlib.sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]


def cache_get(con, key):
    con.executescript(ARTIFACT_SCHEMA)
    row = con.execute("SELECT output, request_id FROM artifact_cache WHERE cache_key=?", (key,)).fetchone()
    return (json.loads(row[0]), row[1]) if row else (None, None)


def cache_put(con, key, role, output, request_id):
    with con:
        con.execute("INSERT OR REPLACE INTO artifact_cache VALUES (?,?,?,?,?)",
                    (key, role, json.dumps(output, ensure_ascii=False), request_id, time.time()))
