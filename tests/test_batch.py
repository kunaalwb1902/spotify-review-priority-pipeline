"""Offline tests for the Batch API transport (fake Batches client; zero cost)."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from test_enrich import ENR, RUN_CFG, FakeClient, calls, make_csv  # noqa: E402
from pipeline.batch_enrich import BatchEnricher  # noqa: E402
from pipeline.common import connect  # noqa: E402
from pipeline.enrich import completed_review_ids, pending_texts  # noqa: E402
from pipeline.ingest import ingest  # noqa: E402
from pipeline.llm import load_rates  # noqa: E402
import pytest  # noqa: E402


class FakeBatches:
    """Wraps FakeClient.create per request. script_results: per custom_id order, e.g. 'errored'."""

    def __init__(self, inner, end_after_polls=0, result_script=None):
        self.inner, self.jobs, self.polls = inner, {}, {}
        self.end_after = end_after_polls
        self.result_script = list(result_script or [])
        self.created = []

    def create(self, requests):
        bid = f"msgbatch_{len(self.jobs) + 1:03d}"
        self.jobs[bid] = requests
        self.polls[bid] = 0
        self.created.append(bid)
        return SimpleNamespace(id=bid, processing_status="in_progress")

    def retrieve(self, bid):
        self.polls[bid] += 1
        ended = self.polls[bid] > self.end_after
        return SimpleNamespace(id=bid, processing_status="ended" if ended else "in_progress",
                               request_counts=SimpleNamespace(processing=0 if ended else len(self.jobs[bid]),
                                                              succeeded=len(self.jobs[bid]) if ended else 0))

    def results(self, bid):
        for n, r in enumerate(self.jobs[bid]):
            action = self.result_script.pop(0) if self.result_script else "ok"
            if action == "errored":
                yield SimpleNamespace(custom_id=r["custom_id"], result=SimpleNamespace(type="errored"))
                continue
            if action != "ok":
                self.inner.script.insert(0, action)
            resp = self.inner.create(**r["params"])
            resp.id = f"msg_{bid}_{n}"
            yield SimpleNamespace(custom_id=r["custom_id"], result=SimpleNamespace(type="succeeded", message=resp))


@pytest.fixture
def run(tmp_path):
    ingest(make_csv(tmp_path / "in.csv"), tmp_path / "pipeline.db", tmp_path / "rep.json")
    return SimpleNamespace(dir=tmp_path, con=connect(tmp_path / "pipeline.db"))


def be(run, batches, **over):
    cfg = json.loads(json.dumps(RUN_CFG))
    for k, v in over.items():
        sect, key = k.split("__")
        cfg[sect][key] = v
    client = batches.inner
    client.messages.batches = batches
    return BatchEnricher(run.con, run.dir, ENR, cfg, load_rates(), client, sleep=lambda s: None)


Q = dict(progress=lambda *_: None, sleep=lambda s: None, poll_seconds=0)


def test_batch_happy_path_logs_batch_tier(run):
    fb = FakeBatches(FakeClient())
    s = be(run, fb).run_batch(chunk_requests=2, **Q)
    assert s["texts_completed"] == 120 and s["stop_reason"] == "completed"
    c = calls(run.con)
    assert len(c) == 3 and all(x["tier"] == "batch" and x["outcome"] == "succeeded" for x in c)
    assert len(fb.created) == 2                                          # 3 requests in chunks of 2
    assert len(completed_review_ids(run.con, ENR["label_config"])) == 125


def test_invalid_item_retried_once_then_quarantined(run):
    fb = FakeBatches(FakeClient(), result_script=["bad_quote", "ok", "ok", "bad_quote"])
    be(run, fb).run_batch(**Q)
    q = run.con.execute("SELECT review_id, attempts, reason FROM text_quarantine").fetchall()
    assert [tuple(r) for r in q] == [("id-0000", 2, "quote_not_substring")]
    assert [x["purpose"] for x in calls(run.con)] == ["first", "first", "first", "retry_invalid"]


def test_errored_result_returns_texts_to_pending_and_next_round_finishes(run):
    fb = FakeBatches(FakeClient(), result_script=["errored"])
    s = be(run, fb).run_batch(**Q)
    assert s["texts_completed"] == 120 and s["texts_errored_left_pending"] == 50
    assert [x["outcome"] for x in calls(run.con)].count("failed") == 1
    assert pending_texts(run.con, ENR["label_config"]) == []


def test_interrupt_while_in_flight_then_resume_collects_without_resubmitting(run):
    fb = FakeBatches(FakeClient(), end_after_polls=3)
    e1 = be(run, fb)
    polls = {"n": 0}
    def interrupting_sleep(_):
        polls["n"] += 1
        if polls["n"] == 1:
            e1.stop_requested = True                                 # Ctrl-C while batch is processing
    s1 = e1.run_batch(chunk_requests=1, max_chunks=1, progress=lambda *_: None, sleep=interrupting_sleep, poll_seconds=0)
    assert s1["phase"] == "initial" and s1["texts_completed"] == 0 and s1["stop_reason"].startswith("interrupted")
    assert len(fb.created) == 1
    # Session 2 (resume): must collect msgbatch_001 rather than resubmit those 50 texts.
    s2 = be(run, fb).run_batch(chunk_requests=1, max_chunks=1, **Q)
    assert s2["phase"] == "resume"
    first_job_texts = {x["text_sha256"] for x in json.loads(run.con.execute(
        "SELECT items FROM batch_requests WHERE batch_id='msgbatch_001'").fetchone()[0])}
    resent = [r for r in run.con.execute("SELECT items FROM batch_requests WHERE batch_id!='msgbatch_001'")
              if first_job_texts & {x["text_sha256"] for x in json.loads(r[0])}]
    assert not resent                                                # nothing paid twice
    s3 = be(run, fb).run_batch(**Q)
    assert pending_texts(run.con, ENR["label_config"]) == []
    before = set(json.loads((run.dir / "checkpoint_before.json").read_text())["completed_ids"])
    resumed = {rid for x in calls(run.con) if x["phase"] == "resume" for rid in json.loads(x["review_ids"])}
    assert not (resumed & before)


def test_spend_cap_blocks_submission(run):
    fb = FakeBatches(FakeClient())
    s = be(run, fb, controls__spend_cap_usd=0.01).run_batch(**Q)
    assert s["stop_reason"].startswith("spend_cap") and fb.created == []
    assert len(pending_texts(run.con, ENR["label_config"])) == 120
