"""Model-call layer shared by every role: rates, spend ledger, bounded retries, and a call log.

Every attempt (including failures) becomes one row in the `calls` table with a unique request_id.
SDK auto-retries are disabled (max_retries=0) so no attempt is invisible.
"""

import csv
import json
import random
import threading
import time
import uuid
from dataclasses import dataclass, field

from .common import ROOT

CALLS_SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    request_id     TEXT PRIMARY KEY,
    session_id     TEXT,
    role           TEXT NOT NULL,          -- enrich | verify | group | memo
    phase          TEXT,                   -- initial | resume (enrich); null otherwise
    model          TEXT NOT NULL,
    tier           TEXT NOT NULL,          -- standard | batch
    label_config   TEXT,
    review_ids     TEXT NOT NULL,          -- JSON list of review IDs actually sent
    outcome        TEXT NOT NULL,          -- succeeded | failed
    error          TEXT,
    stop_reason    TEXT,
    attempt        INTEGER NOT NULL,       -- API attempt number within this logical request
    purpose        TEXT,                   -- first | retry_invalid | fallback | ...
    input_tokens   INTEGER NOT NULL DEFAULT 0,
    cache_creation_input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_input_tokens     INTEGER NOT NULL DEFAULT 0,
    output_tokens  INTEGER NOT NULL DEFAULT 0,
    usage_known    INTEGER NOT NULL,       -- 0 when the provider returned no usage (e.g. timeout)
    cost_usd       REAL NOT NULL DEFAULT 0,
    started_at     REAL NOT NULL,
    ended_at       REAL NOT NULL,
    max_tokens     INTEGER
);
CREATE TABLE IF NOT EXISTS ledger_events (
    ts REAL, event TEXT, amount_usd REAL, spent_usd REAL, reserved_usd REAL, unreconciled_usd REAL, note TEXT
);
"""

USAGE_FIELDS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")


class BudgetExceeded(Exception):
    pass


def load_rates(path=ROOT / "cost" / "rates.csv"):
    """{(model, tier): {usage_field: usd_per_token}} - price per million divided by 1,000,000 first."""
    rates = {}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rates.setdefault((r["model"], r["tier"]), {})[r["usage_field"]] = float(r["price_usd_per_million_tokens"]) / 1_000_000
    return rates


def cost_of(usage, rate):
    return sum(usage.get(k, 0) * rate.get(k, 0.0) for k in USAGE_FIELDS)


class Ledger:
    """One shared spend ledger. Reserve worst-case before dispatch; settle to actual afterwards.
    Admission rule: refuse when spent + reserved + unreconciled + next_reservation > cap."""

    def __init__(self, cap_usd, db=None, db_lock=None, already_spent=0.0):
        self.cap = cap_usd
        self.spent = already_spent
        self.reserved = 0.0
        self.unreconciled = 0.0   # timeouts: request may or may not have been billed
        self.lock = threading.Lock()
        self.db, self.db_lock = db, db_lock

    def _log(self, event, amount, note=""):
        if self.db is not None:
            with self.db_lock:
                self.db.execute("INSERT INTO ledger_events VALUES (?,?,?,?,?,?,?)",
                                (time.time(), event, amount, self.spent, self.reserved, self.unreconciled, note))
                self.db.commit()

    def committed(self):
        return self.spent + self.reserved + self.unreconciled

    def reserve(self, amount, note=""):
        with self.lock:
            if self.committed() + amount > self.cap:
                raise BudgetExceeded(f"cap ${self.cap:.2f}: spent ${self.spent:.4f} + reserved ${self.reserved:.4f} "
                                     f"+ unreconciled ${self.unreconciled:.4f} + next ${amount:.4f}")
            self.reserved += amount
        self._log("reserve", amount, note)
        return amount

    def settle(self, reservation, actual, unknown=False, note=""):
        with self.lock:
            self.reserved -= reservation
            if unknown:
                self.unreconciled += reservation      # assume worst case until reconciled with the dashboard
            else:
                self.spent += actual
        self._log("settle_unknown" if unknown else "settle", reservation if unknown else actual, note)


@dataclass
class CallResult:
    ok: bool
    text: str = ""
    stop_reason: str = ""
    error: str = ""
    retryable: bool = False
    fatal: bool = False
    request_ids: list = field(default_factory=list)


class LLM:
    def __init__(self, client, rates, ledger, controls, db, db_lock, session_id, sleep=time.sleep):
        self.client, self.rates, self.ledger, self.c = client, rates, ledger, controls
        self.db, self.db_lock, self.session_id, self.sleep = db, db_lock, session_id, sleep

    def _worst_case(self, model, tier, system, user, max_tokens):
        rate = self.rates[(model, tier)]
        est_in = (len(system) + len(user)) // 2 + 50          # deliberately pessimistic (~2 chars/token)
        return est_in * max(rate.get("cache_creation_input_tokens", 0), rate["input_tokens"]) + max_tokens * rate["output_tokens"]

    def _log(self, row):
        cols = ",".join(row)
        with self.db_lock:
            self.db.execute(f"INSERT INTO calls ({cols}) VALUES ({','.join('?' * len(row))})", list(row.values()))
            self.db.commit()

    def call(self, *, role, model, system, user, max_tokens, review_ids, label_config=None, phase=None,
             schema=None, temperature=0, tier="standard", purpose="first", cache_system=True, effort=None):
        import anthropic  # imported lazily so offline replay never needs the SDK or a key

        rate = self.rates[(model, tier)]
        result = CallResult(ok=False)
        max_attempts = self.c["max_api_attempts"]
        for attempt in range(1, max_attempts + 1):
            reservation = self.ledger.reserve(self._worst_case(model, tier, system, user, max_tokens),
                                              note=f"{role}:{purpose}")
            started = time.time()
            row = {"session_id": self.session_id, "role": role, "phase": phase, "model": model, "tier": tier,
                   "label_config": label_config, "review_ids": json.dumps(review_ids), "attempt": attempt,
                   "purpose": purpose, "max_tokens": max_tokens, "started_at": started}
            kwargs = dict(model=model, max_tokens=max_tokens,
                          system=[{"type": "text", "text": system,
                                   **({"cache_control": {"type": "ephemeral"}} if cache_system else {})}],
                          messages=[{"role": "user", "content": user}])
            if schema is not None:
                kwargs["output_config"] = {"format": {"type": "json_schema", "schema": schema}}
            if effort is not None:
                kwargs.setdefault("output_config", {})["effort"] = effort
            if temperature is not None:
                # SDK 1.x dropped `temperature` from create(); the API still honours it on Haiku 4.5.
                kwargs["extra_body"] = {"temperature": temperature}
            try:
                resp = self.client.messages.create(**kwargs)
            except anthropic.APIError as exc:  # code bugs (TypeError etc.) are NOT caught: they must crash
                ended = time.time()
                timeout = isinstance(exc, anthropic.APITimeoutError)
                status = getattr(exc, "status_code", None)
                retryable = (timeout or isinstance(exc, (anthropic.RateLimitError, anthropic.APIConnectionError,
                                                          anthropic.InternalServerError))
                             or (isinstance(status, int) and (status >= 500 or status in (408, 409, 429))))
                self.ledger.settle(reservation, 0.0, unknown=timeout, note=f"{type(exc).__name__}")
                rid = f"local-{uuid.uuid4()}"
                self._log({**row, "request_id": rid, "outcome": "failed",
                           "error": f"{type(exc).__name__}: {str(exc)[:300]}" + (" [charge unknown]" if timeout else ""),
                           "usage_known": 0, "cost_usd": 0.0, "ended_at": ended})
                result.request_ids.append(rid)
                result.error, result.retryable = f"{type(exc).__name__}", retryable
                result.fatal = not retryable            # 400/401/403/404: config problem -> stop the run
                if not retryable or attempt == max_attempts:
                    return result
                delay = self._retry_after(exc) or min(self.c["backoff_max_seconds"],
                                                      self.c["backoff_base_seconds"] * 2 ** (attempt - 1))
                self.sleep(delay * (0.5 + random.random()))       # jitter
                continue

            ended = time.time()
            usage = {k: int(getattr(resp.usage, k, 0) or 0) for k in USAGE_FIELDS}
            cost = cost_of(usage, rate)
            self.ledger.settle(reservation, cost, note=f"{role}:{purpose}")
            text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")
            ok = resp.stop_reason in ("end_turn", "stop_sequence")
            rid = getattr(resp, "_request_id", None) or f"local-{uuid.uuid4()}"
            self._log({**row, "request_id": rid, "outcome": "succeeded" if ok else "failed",
                       "error": None if ok else f"stop_reason={resp.stop_reason}", "stop_reason": resp.stop_reason,
                       **usage, "usage_known": 1, "cost_usd": cost, "ended_at": ended})
            result.request_ids.append(rid)
            result.ok, result.text, result.stop_reason = ok, text, resp.stop_reason
            if not ok:
                result.error = f"stop_reason={resp.stop_reason}"   # max_tokens/refusal: not retried here; caller decides
            return result
        return result

    @staticmethod
    def _retry_after(exc):
        try:
            return float(exc.response.headers.get("retry-after"))
        except Exception:
            return None
