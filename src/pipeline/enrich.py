"""Stage 2 - Enrich. Model reads language; code owns IDs, validation, retries, accounting.

Unit of work: one distinct nonempty text, represented by its ORIGINAL review_id (lowest row_idx).
Other reviews with byte-identical text reuse that result later via cache_source_id.

Per batch (<= 50 texts):
  1. first request -> validate each item in code
  2. invalid/missing items -> ONE retry request containing only those items
  3. still invalid -> capped stronger-model fallback (one text per request) if quota remains,
     otherwise quarantine with reason + attempt count
API errors (429/5xx/connection) are retried with backoff inside LLM.call; if they persist the texts
stay `pending` (not quarantined) because the failure is transient.

Stop conditions: no pending texts | spend cap | --max-batches reached (interruption demo) |
Ctrl-C | too many consecutive API-failed batches. In-flight batches finish and are saved first.
"""

import json
import signal
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from .common import write_json
from .llm import CALLS_SCHEMA, BudgetExceeded, Ledger, LLM
from .prompts import enricher_user_message

SCHEMA = """
CREATE TABLE IF NOT EXISTS results (
    text_sha256     TEXT NOT NULL,
    run_config      TEXT NOT NULL,      -- main label_config of the run (cache key)
    label_config    TEXT NOT NULL,      -- config that actually produced this record (differs for fallback)
    review_id       TEXT NOT NULL,      -- the original review actually sent to the model
    topic TEXT NOT NULL, subtopic TEXT NOT NULL, intent TEXT NOT NULL,
    severity INTEGER NOT NULL, sentiment REAL NOT NULL, entities TEXT NOT NULL,
    evidence_quote TEXT NOT NULL, needs_review INTEGER NOT NULL,
    request_id TEXT NOT NULL, attempts INTEGER NOT NULL, via TEXT NOT NULL,   -- first | retry | fallback
    session_id TEXT NOT NULL, created_at REAL NOT NULL,
    PRIMARY KEY (text_sha256, run_config)
);
CREATE TABLE IF NOT EXISTS text_quarantine (
    text_sha256 TEXT NOT NULL, label_config TEXT NOT NULL, review_id TEXT NOT NULL,
    reason TEXT NOT NULL, attempts INTEGER NOT NULL, last_request_id TEXT, session_id TEXT, created_at REAL,
    PRIMARY KEY (text_sha256, label_config)
);
CREATE TABLE IF NOT EXISTS validation_failures (
    review_id TEXT, text_sha256 TEXT, purpose TEXT, model TEXT, reason TEXT, request_id TEXT,
    session_id TEXT, created_at REAL
);
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY, role TEXT, label_config TEXT, phase TEXT, started_at REAL, ended_at REAL,
    stop_reason TEXT, texts_completed INTEGER, texts_quarantined INTEGER
);
"""


# ----------------------------------------------------------------------------------------------
# Validation (pure functions - unit tested without any API)
# ----------------------------------------------------------------------------------------------

def validate_item(item, text, labels, parent):
    """Return (clean_record, None) or (None, reason). Never repairs the quote: it must be exact."""
    try:
        topic, sub, intent = item["topic"], item["sub"], item["intent"]
        sev, sent, ents, quote, nr = item["sev"], item["sent"], item["ent"], item["quote"], item["nr"]
    except (KeyError, TypeError):
        return None, "missing_field"
    if topic not in labels["topics"]:
        return None, f"bad_topic:{topic}"
    if parent.get(sub) != topic:
        return None, f"subtopic_mismatch:{topic}/{sub}"
    if intent not in labels["intents"]:
        return None, f"bad_intent:{intent}"
    if type(sev) is not int or not 1 <= sev <= 5:
        return None, f"bad_severity:{sev}"
    if type(sent) not in (int, float) or not -1 <= sent <= 1:
        return None, f"bad_sentiment:{sent}"
    if type(nr) is not bool:
        return None, "bad_needs_review"
    if not isinstance(ents, list) or not all(isinstance(e, str) for e in ents):
        return None, "bad_entities"
    if not isinstance(quote, str) or not quote.strip():
        return None, "empty_quote"
    if quote not in text:
        return None, "quote_not_substring"
    clean_ents = []
    for e in ents:
        e = e.strip()
        if e and e not in clean_ents:
            clean_ents.append(e)
    return {"topic": topic, "subtopic": sub, "intent": intent, "severity": sev,
            "sentiment": round(float(sent), 2), "entities": clean_ents, "evidence_quote": quote,
            "needs_review": nr}, None


def parse_and_validate(result_text, batch, labels, parent):
    """batch: list of dicts with local i, review_id, text. Returns ({i: record}, {i: reason})."""
    good, bad = {}, {}
    try:
        payload = json.loads(result_text)
        items = payload["results"]
        assert isinstance(items, list)
    except Exception:
        return {}, {b["i"]: "unparseable_output" for b in batch}
    by_i = {b["i"]: b for b in batch}
    seen = set()
    for item in items:
        i = item.get("i") if isinstance(item, dict) else None
        if i not in by_i:
            continue                       # foreign / hallucinated id: ignored, never saved
        if i in seen:
            bad[i] = "duplicate_id_in_output"
            good.pop(i, None)
            continue
        seen.add(i)
        rec, reason = validate_item(item, by_i[i]["text"], labels, parent)
        if rec:
            good[i] = rec
        else:
            bad[i] = reason
    for i in by_i:
        if i not in good and i not in bad:
            bad[i] = "missing_from_output"
    return good, bad


# ----------------------------------------------------------------------------------------------
# Orchestration
# ----------------------------------------------------------------------------------------------

def completed_review_ids(con, label_config):
    """All review IDs whose text has a saved result (originals and exact-text duplicates)."""
    rows = con.execute("""SELECT r.review_id FROM reviews r JOIN results x
                          ON x.text_sha256 = r.text_sha256 AND x.run_config = ?
                          WHERE r.status != 'quarantined' ORDER BY r.row_idx""", (label_config,))
    return [r[0] for r in rows]


def pending_texts(con, label_config, limit=None):
    sql = """SELECT r.text_sha256, r.review_id, r.review_text FROM reviews r
             JOIN (SELECT text_sha256, MIN(row_idx) AS first_idx FROM reviews
                   WHERE status != 'quarantined' GROUP BY text_sha256) f
               ON r.text_sha256 = f.text_sha256 AND r.row_idx = f.first_idx
             WHERE r.text_sha256 NOT IN (SELECT text_sha256 FROM results WHERE run_config = ?)
               AND r.text_sha256 NOT IN (SELECT text_sha256 FROM text_quarantine WHERE label_config = ?)
             ORDER BY r.row_idx"""
    rows = con.execute(sql + (f" LIMIT {int(limit)}" if limit else ""), (label_config, label_config)).fetchall()
    return [{"text_sha256": a, "review_id": b, "text": c} for a, b, c in rows]


class Enricher:
    def __init__(self, con, run_dir, enr, run_cfg, rates, client, sleep=time.sleep):
        self.con, self.run_dir, self.enr, self.cfg = con, run_dir, enr, run_cfg
        self.lock = threading.Lock()
        con.executescript(SCHEMA + CALLS_SCHEMA)
        self.label_config = enr["label_config"]
        self.s = enr["settings"]
        self.controls = run_cfg["controls"]
        self.fallback_cfg = run_cfg["fallback"]
        prior_spend = con.execute("SELECT COALESCE(SUM(cost_usd),0) FROM calls").fetchone()[0]
        self.ledger = Ledger(self.controls["spend_cap_usd"], con, self.lock, already_spent=prior_spend)
        self.session_id = f"enrich-{time.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"
        self.llm = LLM(client, rates, self.ledger, self.controls, con, self.lock, self.session_id, sleep=sleep)
        self.stop_requested = False
        self.fallbacks_used = con.execute(
            "SELECT COUNT(*) FROM results WHERE via='fallback'").fetchone()[0]
        distinct = con.execute("SELECT COUNT(DISTINCT text_sha256) FROM reviews WHERE status!='quarantined'").fetchone()[0]
        self.fallback_cap = int(distinct * self.fallback_cfg["max_fraction"])

    # -- one batch: runs in a worker thread, never touches results tables ------------------------
    def process_batch(self, texts, phase):
        batch = [{"i": n, **t} for n, t in enumerate(texts, 1)]
        attempts = {b["i"]: 0 for b in batch}
        saved, last_reason, req_of = {}, {}, {}
        fail_log = []                                         # every invalid item, even if later rescued
        api_failed = False
        budget_stop = None
        fatal = None

        def run(sub, purpose, model=None, label_config=None):
            nonlocal api_failed, budget_stop, fatal
            if budget_stop or fatal:
                return
            local = [{"i": n, **{k: b[k] for k in ("review_id", "text", "text_sha256")}, "orig_i": b["i"]}
                     for n, b in enumerate(sub, 1)]
            max_tokens = min(self.s["max_output_tokens"], self.s["output_tokens_per_review"] * len(local) + 200)
            try:
                res = self.llm.call(role="enrich", phase=phase, model=model or self.s["model"],
                                    system=self.enr["system"], schema=self.enr["schema"],
                                    user=enricher_user_message([(x["i"], x["text"]) for x in local]),
                                    max_tokens=max_tokens, review_ids=[x["review_id"] for x in local],
                                    label_config=label_config or self.label_config,
                                    temperature=None if model else self.s["temperature"], purpose=purpose)
            except BudgetExceeded as exc:
                budget_stop = str(exc)
                return
            for x in local:
                attempts[x["orig_i"]] += 1
            if not res.ok and res.retryable:
                api_failed = True                               # transient: leave pending
                return
            if res.fatal:
                fatal = res.error                               # config/auth error: never quarantine for it
                return
            good, bad = parse_and_validate(res.text, local, self.enr["labels"], self.enr["parent"]) if res.ok \
                else ({}, {x["i"]: f"call_failed:{res.error}" for x in local})
            for x in local:
                if x["i"] in good:
                    saved[x["orig_i"]] = (good[x["i"]], purpose, model or self.s["model"],
                                          label_config or self.label_config)
                    req_of[x["orig_i"]] = res.request_ids[-1]
                else:
                    last_reason[x["orig_i"]] = bad.get(x["i"], "unknown")
                    req_of[x["orig_i"]] = res.request_ids[-1] if res.request_ids else None
                    fail_log.append((x["review_id"], x["text_sha256"], purpose, model or self.s["model"],
                                     last_reason[x["orig_i"]], req_of[x["orig_i"]]))

        run(batch, "first")
        if api_failed or budget_stop or fatal:
            return {"saved": saved, "quarantine": {}, "api_failed": api_failed, "budget_stop": budget_stop,
                    "fatal": fatal, "batch": batch, "attempts": attempts, "req": req_of, "fail_log": fail_log}
        retry = [b for b in batch if b["i"] not in saved]
        if retry:
            run(retry, "retry_invalid")                         # the ONE invalid-output retry
        still = [b for b in batch if b["i"] not in saved]
        quarantine = {}
        for b in still:
            if api_failed or budget_stop or fatal:
                continue                                        # transient failure / cap / fatal: stays pending
            if self._take_fallback_slot():
                fb_model = self.fallback_cfg["model"]
                fb_config = self.label_config.replace(self.s["model"], fb_model, 1)
                run([b], "fallback", model=fb_model, label_config=fb_config)
            if b["i"] not in saved and not (budget_stop or fatal):
                quarantine[b["i"]] = last_reason.get(b["i"], "unknown")
        return {"saved": saved, "quarantine": quarantine, "api_failed": api_failed, "budget_stop": budget_stop,
                "fatal": fatal, "batch": batch, "attempts": attempts, "req": req_of, "fail_log": fail_log}

    def _take_fallback_slot(self):
        with self.lock:
            if self.fallbacks_used < self.fallback_cap:
                self.fallbacks_used += 1
                return True
        return False

    # -- main thread: the only writer of results ---------------------------------------------------
    def commit(self, out):
        now = time.time()
        with self.lock:
            with self.con:                                       # one transaction per batch = atomic
                for f in out.get("fail_log", []):
                    self.con.execute("INSERT INTO validation_failures VALUES (?,?,?,?,?,?,?,?)",
                                     (*f, self.session_id, now))
                for b in out["batch"]:
                    i = b["i"]
                    if i in out["saved"]:
                        rec, via, model, cfg = out["saved"][i]
                        self.con.execute(
                            "INSERT OR IGNORE INTO results VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (b["text_sha256"], self.label_config, cfg, b["review_id"],
                             rec["topic"], rec["subtopic"], rec["intent"], rec["severity"], rec["sentiment"],
                             json.dumps(rec["entities"], ensure_ascii=False), rec["evidence_quote"],
                             int(rec["needs_review"]), out["req"][i], out["attempts"][i],
                             {"first": "first", "retry_invalid": "retry", "fallback": "fallback"}[via],
                             self.session_id, now))
                    elif i in out["quarantine"]:
                        self.con.execute("INSERT OR IGNORE INTO text_quarantine VALUES (?,?,?,?,?,?,?,?)",
                                         (b["text_sha256"], self.label_config, b["review_id"], out["quarantine"][i],
                                          out["attempts"][i], out["req"].get(i), self.session_id, now))
        return (sum(1 for b in out["batch"] if b["i"] in out["saved"]), len(out["quarantine"]))

    def run(self, limit=None, max_batches=None, workers=None, progress=print):
        workers = workers or self.controls["workers"]
        prior = self.con.execute("SELECT COUNT(*) FROM sessions WHERE role='enrich' AND label_config=?",
                                 (self.label_config,)).fetchone()[0]
        phase = "resume" if prior else "initial"
        started = time.time()
        if phase == "resume" and not (self.run_dir / "checkpoint_before.json").exists():
            # first resume only: the snapshot marks the real interruption point and is never overwritten
            write_json(self.run_dir / "checkpoint_before.json",
                       {"label_config": self.label_config, "saved_at": started,
                        "completed_ids": completed_review_ids(self.con, self.label_config)})
        with self.con:
            self.con.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?)",
                             (self.session_id, "enrich", self.label_config, phase, started, None, None, 0, 0))

        todo = pending_texts(self.con, self.label_config, limit)
        size = self.s["batch_size"]
        assert 1 <= size <= 50, "contract: at most 50 reviews per enrichment request"
        batches = [todo[k:k + size] for k in range(0, len(todo), size)]
        if max_batches is not None:
            batches = batches[:max_batches]
        progress(f"[{phase}] {self.label_config}\n  pending texts={len(todo):,} -> {len(batches)} batches "
                 f"of <= {size}, workers={workers}, cap=${self.ledger.cap:.2f}")

        old_handler = signal.getsignal(signal.SIGINT)
        def on_sigint(*_):
            self.stop_requested = True
            progress("\n  Ctrl-C received: finishing in-flight batches, then saving and stopping...")
        signal.signal(signal.SIGINT, on_sigint)

        done_texts = quarantined = consecutive_api_fail = 0
        stop_reason = "completed"
        queue = list(batches)
        try:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                in_flight = set()
                while queue or in_flight:
                    while queue and len(in_flight) < workers and not self.stop_requested and stop_reason == "completed":
                        in_flight.add(pool.submit(self.process_batch, queue.pop(0), phase))
                    if not in_flight:
                        break
                    finished, in_flight = wait(in_flight, return_when=FIRST_COMPLETED)
                    for fut in finished:
                        out = fut.result()
                        ok, q = self.commit(out)
                        if out.get("fatal"):
                            stop_reason = f"fatal_api_error: {out['fatal']} (texts left pending; fix config and rerun)"
                        if out.get("budget_stop"):
                            stop_reason = f"spend_cap: {out['budget_stop']}"
                        done_texts += ok
                        quarantined += q
                        consecutive_api_fail = consecutive_api_fail + 1 if out["api_failed"] else 0
                        if consecutive_api_fail >= 3:
                            stop_reason = "api_unavailable: 3 consecutive batches failed after retries"
                        progress(f"  batch saved: +{ok} completed, +{q} quarantined"
                                 f"{' (API failure: left pending)' if out['api_failed'] else ''} | "
                                 f"total {done_texts:,} | spent ${self.ledger.spent:.4f}")
                    if self.stop_requested and stop_reason == "completed":
                        stop_reason = "interrupted"
            if stop_reason == "completed" and max_batches is not None and len(todo) > max_batches * size:
                stop_reason = f"max_batches={max_batches} reached"
        finally:
            signal.signal(signal.SIGINT, old_handler)
            ended = time.time()
            with self.con:
                self.con.execute("UPDATE sessions SET ended_at=?, stop_reason=?, texts_completed=?, texts_quarantined=? "
                                 "WHERE session_id=?", (ended, stop_reason, done_texts, quarantined, self.session_id))
            if phase == "resume":
                write_json(self.run_dir / "checkpoint_after.json",
                           {"label_config": self.label_config, "saved_at": ended,
                            "completed_ids": completed_review_ids(self.con, self.label_config)})
        summary = {"session_id": self.session_id, "phase": phase, "stop_reason": stop_reason,
                   "texts_completed": done_texts, "texts_quarantined": quarantined,
                   "wall_clock_seconds": round(ended - started, 2), "spent_usd": round(self.ledger.spent, 6),
                   "unreconciled_usd": round(self.ledger.unreconciled, 6)}
        progress(f"  stop: {stop_reason} | {json.dumps(summary)}")
        return summary
