"""Stage 2 via the Message Batches API (50% price, asynchronous). Same prompt, schema, validation and
label_config as the sync path - only the transport and billing tier differ.

Durability rules (so an interruption never loses paid work or pays twice):
  * Every request is written to `batch_requests` BEFORE submission; the batch id is recorded right after.
  * On start, ANY uncollected batch (from any earlier session) is polled and collected first.
    Texts that are in flight are never resubmitted.
  * "Retry invalid output once" is enforced from `validation_failures`, so it survives restarts:
    0 prior failures -> first attempt, 1 -> retry (alone with other retries), 2 -> quarantine.
  * API-level errored/expired results leave texts pending for the next round (bounded rounds).
  * Spend: worst-case cost of a whole submission is reserved before it is sent.
"""

import json
import math
import signal
import time
import uuid

from .enrich import Enricher, completed_review_ids, parse_and_validate, pending_texts
from .common import write_json
from .llm import BudgetExceeded, cost_of
from .prompts import enricher_user_message

BATCH_SCHEMA = """
CREATE TABLE IF NOT EXISTS batch_jobs (
    batch_id TEXT PRIMARY KEY, session_id TEXT, phase TEXT, purpose TEXT, created_at REAL,
    status TEXT, n_requests INTEGER, reserved_usd REAL, collected_at REAL
);
CREATE TABLE IF NOT EXISTS batch_requests (
    custom_id TEXT PRIMARY KEY, batch_id TEXT, session_id TEXT, phase TEXT, purpose TEXT,
    items TEXT NOT NULL, max_tokens INTEGER, collected INTEGER NOT NULL DEFAULT 0
);
"""


class BatchEnricher(Enricher):
    TIER = "batch"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.con.executescript(BATCH_SCHEMA)
        self.client = self.llm.client
        self.phase = None

    # ---------------------------------------------------------------- helpers
    def _params(self, local, max_tokens):
        return {"model": self.s["model"], "max_tokens": max_tokens, "temperature": self.s["temperature"],
                "system": [{"type": "text", "text": self.enr["system"], "cache_control": {"type": "ephemeral"}}],
                "messages": [{"role": "user", "content": enricher_user_message([(x["i"], x["text"]) for x in local])}],
                "output_config": {"format": {"type": "json_schema", "schema": self.enr["schema"]}}}

    def _worst_case(self, local, max_tokens):
        rate = self.llm.rates[(self.s["model"], self.TIER)]
        user_chars = sum(len(x["text"]) for x in local) + 60 * len(local)
        est_in = (len(self.enr["system"]) + user_chars) // 2 + 50
        return est_in * max(rate["cache_creation_input_tokens"], rate["input_tokens"]) + max_tokens * rate["output_tokens"]

    def _failures(self):
        rows = self.con.execute("""SELECT text_sha256, COUNT(*) FROM validation_failures
                                   WHERE model = ? GROUP BY text_sha256""", (self.s["model"],))
        return dict(rows.fetchall())

    def _in_flight(self):
        shas = set()
        for (items,) in self.con.execute("SELECT items FROM batch_requests WHERE collected = 0 AND batch_id IS NOT NULL"):
            shas.update(x["text_sha256"] for x in json.loads(items))
        return shas

    # ---------------------------------------------------------------- submit
    def submit(self, texts, purpose, chunk_requests, progress, group_size=None, per_review_tokens=None):
        size = group_size or self.s["batch_size"]
        per_review = per_review_tokens or self.s["output_tokens_per_review"]
        groups = [texts[k:k + size] for k in range(0, len(texts), size)]
        submitted = []
        for c in range(0, len(groups), chunk_requests):
            chunk = groups[c:c + chunk_requests]
            reqs, rows, reserve = [], [], 0.0
            for g in chunk:
                local = [{"i": n, "review_id": t["review_id"], "text": t["text"], "text_sha256": t["text_sha256"]}
                         for n, t in enumerate(g, 1)]
                max_tokens = min(self.s["max_output_tokens"], per_review * len(local) + 200)
                cid = f"e{uuid.uuid4().hex[:20]}"
                reqs.append({"custom_id": cid, "params": self._params(local, max_tokens)})
                rows.append((cid, None, self.session_id, self.phase, purpose,
                             json.dumps([{k: x[k] for k in ("i", "review_id", "text_sha256")} for x in local]), max_tokens))
                reserve += self._worst_case(local, max_tokens)
            try:
                self.ledger.reserve(reserve, note=f"batch submit {len(reqs)} req")
            except BudgetExceeded as exc:
                progress(f"  spend cap: not submitting {len(groups) - c} more requests ({exc})")
                return submitted, f"spend_cap: {exc}"
            with self.lock, self.con:
                self.con.executemany("INSERT INTO batch_requests (custom_id,batch_id,session_id,phase,purpose,items,max_tokens)"
                                     " VALUES (?,?,?,?,?,?,?)", rows)
            try:
                batch = self.client.messages.batches.create(requests=reqs)
            except Exception as exc:
                with self.lock, self.con:
                    self.con.executemany("DELETE FROM batch_requests WHERE custom_id=?", [(r[0],) for r in rows])
                self.ledger.settle(reserve, 0.0, note="batch submit failed")
                progress(f"  submit failed ({type(exc).__name__}: {str(exc)[:120]}); texts stay pending")
                return submitted, f"submit_failed: {type(exc).__name__}"
            with self.lock, self.con:
                self.con.execute("UPDATE batch_requests SET batch_id=? WHERE custom_id IN (%s)" % ",".join("?" * len(rows)),
                                 [batch.id] + [r[0] for r in rows])
                self.con.execute("INSERT INTO batch_jobs VALUES (?,?,?,?,?,?,?,?,?)",
                                 (batch.id, self.session_id, self.phase, purpose, time.time(), "submitted",
                                  len(reqs), reserve, None))
            submitted.append(batch.id)
            progress(f"  submitted {batch.id}: {len(reqs)} requests / {sum(len(g) for g in chunk):,} texts ({purpose})")
        return submitted, None

    # ---------------------------------------------------------------- collect
    def collect(self, batch_id, progress):
        job = self.con.execute("SELECT * FROM batch_jobs WHERE batch_id=?", (batch_id,)).fetchone()
        reqs = {r["custom_id"]: r for r in self.con.execute(
            "SELECT * FROM batch_requests WHERE batch_id=? AND collected=0", (batch_id,))}
        rate = self.llm.rates[(self.s["model"], self.TIER)]
        now = time.time()
        calls, results, quarantine, failures, done_ids = [], [], [], [], []
        stats = {"ok": 0, "invalid": 0, "errored": 0, "quarantined": 0}
        prior = self._failures()
        for entry in self.client.messages.batches.results(batch_id):
            row = reqs.get(entry.custom_id)
            if row is None:
                continue
            items = json.loads(row["items"])
            texts = {t[0]: t[1] for t in self.con.execute(
                "SELECT text_sha256, review_text FROM reviews WHERE review_id IN (%s)" % ",".join("?" * len(items)),
                [x["review_id"] for x in items])}
            local = [{**x, "text": texts[x["text_sha256"]]} for x in items]
            base = {"session_id": self.session_id, "role": "enrich", "phase": row["phase"], "model": self.s["model"],
                    "tier": self.TIER, "label_config": self.label_config,
                    "review_ids": json.dumps([x["review_id"] for x in items]), "attempt": 1,
                    "purpose": row["purpose"], "max_tokens": row["max_tokens"],
                    "started_at": job["created_at"], "ended_at": now}
            done_ids.append(entry.custom_id)
            if entry.result.type != "succeeded":
                calls.append({**base, "request_id": f"{batch_id}:{entry.custom_id}", "outcome": "failed",
                              "error": f"batch_result={entry.result.type}", "usage_known": 0, "cost_usd": 0.0})
                stats["errored"] += len(items)                    # texts stay pending -> next round
                continue
            msg = entry.result.message
            usage = {k: int(getattr(msg.usage, k, 0) or 0) for k in
                     ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")}
            ok = msg.stop_reason in ("end_turn", "stop_sequence")
            rid = getattr(msg, "id", None) or f"{batch_id}:{entry.custom_id}"
            calls.append({**base, "request_id": rid, "outcome": "succeeded" if ok else "failed",
                          "error": None if ok else f"stop_reason={msg.stop_reason}", "stop_reason": msg.stop_reason,
                          **usage, "usage_known": 1, "cost_usd": cost_of(usage, rate)})
            text = "".join(getattr(b, "text", "") for b in msg.content if getattr(b, "type", "") == "text")
            good, bad = parse_and_validate(text, local, self.enr["labels"], self.enr["parent"]) if ok \
                else ({}, {x["i"]: f"call_failed:stop_reason={msg.stop_reason}" for x in local})
            for x in local:
                if x["i"] in good:
                    rec = good[x["i"]]
                    via = "retry" if row["purpose"] == "retry_invalid" else "first"
                    results.append((x["text_sha256"], self.label_config, self.label_config, x["review_id"],
                                    rec["topic"], rec["subtopic"], rec["intent"], rec["severity"], rec["sentiment"],
                                    json.dumps(rec["entities"], ensure_ascii=False), rec["evidence_quote"],
                                    int(rec["needs_review"]), rid, prior.get(x["text_sha256"], 0) + 1, via,
                                    self.session_id, now))
                    stats["ok"] += 1
                else:
                    reason = bad.get(x["i"], "unknown")
                    failures.append((x["review_id"], x["text_sha256"], row["purpose"], self.s["model"], reason, rid,
                                     self.session_id, now))
                    stats["invalid"] += 1
                    if prior.get(x["text_sha256"], 0) + 1 >= 2:     # this was the one retry -> quarantine
                        quarantine.append((x["text_sha256"], self.label_config, x["review_id"], reason, 2, rid,
                                           self.session_id, now))
                        stats["quarantined"] += 1
        actual = sum(c.get("cost_usd", 0.0) for c in calls)
        with self.lock, self.con:                                  # one transaction per collected batch
            for c in calls:
                self.con.execute(f"INSERT OR IGNORE INTO calls ({','.join(c)}) VALUES ({','.join('?' * len(c))})",
                                 list(c.values()))
            self.con.executemany("INSERT OR IGNORE INTO results VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", results)
            self.con.executemany("INSERT INTO validation_failures VALUES (?,?,?,?,?,?,?,?)", failures)
            self.con.executemany("INSERT OR IGNORE INTO text_quarantine VALUES (?,?,?,?,?,?,?,?)", quarantine)
            self.con.executemany("UPDATE batch_requests SET collected=1 WHERE custom_id=?", [(d,) for d in done_ids])
            # requests the API never returned are released so their texts become pending again
            self.con.execute("UPDATE batch_requests SET collected=-1 WHERE batch_id=? AND collected=0", (batch_id,))
            self.con.execute("UPDATE batch_jobs SET status='collected', collected_at=? WHERE batch_id=?", (now, batch_id))
        # Reserved either at submit (this session) or re-reserved at start-up reconciliation (earlier session).
        self.ledger.settle(job["reserved_usd"], actual, note=f"collect {batch_id}")
        progress(f"  collected {batch_id}: +{stats['ok']:,} ok, {stats['invalid']} invalid "
                 f"({stats['quarantined']} quarantined), {stats['errored']} errored | ${actual:.4f}")
        return stats

    def wait_and_collect(self, batch_ids, poll_seconds, sleep, progress):
        open_ids = list(batch_ids)
        totals = {"ok": 0, "invalid": 0, "errored": 0, "quarantined": 0}
        while open_ids and not self.stop_requested:
            for bid in list(open_ids):
                b = self.client.messages.batches.retrieve(bid)
                if b.processing_status == "ended":
                    for k, v in self.collect(bid, progress).items():
                        totals[k] += v
                    open_ids.remove(bid)
                else:
                    rc = b.request_counts
                    progress(f"  {bid}: {b.processing_status} (processing {rc.processing}, done {rc.succeeded})")
            if open_ids and not self.stop_requested:
                sleep(poll_seconds)
        return totals, open_ids

    # ---------------------------------------------------------------- driver
    def run_batch(self, limit=None, chunk_requests=500, max_chunks=None, poll_seconds=60, max_rounds=8,
                  progress=print, sleep=time.sleep):
        prior = self.con.execute("SELECT COUNT(*) FROM sessions WHERE role='enrich' AND label_config=?",
                                 (self.label_config,)).fetchone()[0]
        self.phase = "resume" if prior else "initial"
        started = time.time()
        if self.phase == "resume" and not (self.run_dir / "checkpoint_before.json").exists():
            # first resume only: the snapshot marks the real interruption point and is never overwritten
            write_json(self.run_dir / "checkpoint_before.json",
                       {"label_config": self.label_config, "saved_at": started,
                        "completed_ids": completed_review_ids(self.con, self.label_config)})
        with self.lock, self.con:
            self.con.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?)",
                             (self.session_id, "enrich", self.label_config, self.phase, started, None, None, 0, 0))
        old = signal.getsignal(signal.SIGINT)
        def on_sigint(*_):
            self.stop_requested = True
            progress("\n  Ctrl-C: stopping after current step. Submitted batches keep running server-side and "
                     "will be COLLECTED (not resubmitted) on resume.")
        signal.signal(signal.SIGINT, on_sigint)
        stop_reason, totals = "completed", {"ok": 0, "invalid": 0, "errored": 0, "quarantined": 0}
        try:
            # 1. reconcile anything already in flight
            outstanding = [r[0] for r in self.con.execute("SELECT batch_id FROM batch_jobs WHERE status!='collected'")]
            if outstanding:
                self.ledger.reserved += sum(r[0] for r in self.con.execute(
                    "SELECT reserved_usd FROM batch_jobs WHERE status!='collected'"))
                progress(f"[{self.phase}] collecting {len(outstanding)} batch(es) submitted earlier (no resubmission)")
                t, left = self.wait_and_collect(outstanding, poll_seconds, sleep, progress)
                totals = {k: totals[k] + t[k] for k in totals}
            chunks_used = 0
            for rnd in range(max_rounds):
                if self.stop_requested:
                    break
                fails = self._failures()
                in_flight = self._in_flight()
                todo = [t for t in pending_texts(self.con, self.label_config) if t["text_sha256"] not in in_flight]
                if limit is not None:
                    todo = todo[:max(0, limit - totals["ok"])]
                if not todo:
                    break
                first = [t for t in todo if fails.get(t["text_sha256"], 0) == 0]
                retry = [t for t in todo if fails.get(t["text_sha256"], 0) == 1]
                progress(f"[{self.phase}] round {rnd + 1}: {len(first):,} first-attempt + {len(retry):,} retry texts "
                         f"({self.label_config})")
                ids, deferred = [], False
                # retries go in small groups with more output room (max_tokens is not part of label_config)
                for texts, purpose, kw in ((first, "first", {}),
                                           (retry, "retry_invalid", {"group_size": 10, "per_review_tokens": 200})):
                    if not texts:
                        continue
                    budget_chunks = None if max_chunks is None else max(0, max_chunks - chunks_used)
                    if budget_chunks == 0:
                        break
                    if budget_chunks is not None:
                        texts = texts[:budget_chunks * chunk_requests * self.s["batch_size"]]
                    sub, err = self.submit(texts, purpose, chunk_requests, progress, **kw)
                    chunks_used += len(sub)
                    ids += sub
                    if err:
                        if err.startswith("spend_cap") and ids:
                            deferred = True        # blocked by RESERVATIONS: collect, release, continue next round
                            progress("  reservations at cap: collecting in-flight batches before submitting more")
                        else:
                            stop_reason = err      # nothing in flight and still over cap -> real stop
                        break
                t, left = self.wait_and_collect(ids, poll_seconds, sleep, progress)
                totals = {k: totals[k] + t[k] for k in totals}
                if stop_reason != "completed" or (max_chunks is not None and chunks_used >= max_chunks):
                    break
            if self.stop_requested:
                stop_reason = "interrupted (in-flight batches will be collected on resume)"
            elif stop_reason == "completed" and pending_texts(self.con, self.label_config, 1):
                stop_reason = "stopped with texts pending (chunk/round/limit bound reached)"
        finally:
            signal.signal(signal.SIGINT, old)
            ended = time.time()
            with self.lock, self.con:
                self.con.execute("UPDATE sessions SET ended_at=?, stop_reason=?, texts_completed=?, texts_quarantined=? "
                                 "WHERE session_id=?", (ended, stop_reason, totals["ok"], totals["quarantined"],
                                                        self.session_id))
            if self.phase == "resume":
                write_json(self.run_dir / "checkpoint_after.json",
                           {"label_config": self.label_config, "saved_at": ended,
                            "completed_ids": completed_review_ids(self.con, self.label_config)})
        summary = {"session_id": self.session_id, "phase": self.phase, "transport": "batch", "stop_reason": stop_reason,
                   "texts_completed": totals["ok"], "texts_quarantined": totals["quarantined"],
                   "texts_errored_left_pending": totals["errored"], "wall_clock_seconds": round(ended - started, 2),
                   "spent_usd": round(self.ledger.spent, 6)}
        progress(f"  stop: {stop_reason} | {json.dumps(summary)}")
        return summary
