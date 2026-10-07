"""Offline tests for Stage 1-2 control logic. A fake client stands in for the API: zero cost, no key.

Run:  .venv/bin/python -m pytest -q tests
"""

import csv
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx2 as httpx
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pipeline.enrich import Enricher, completed_review_ids, pending_texts, validate_item  # noqa: E402
from pipeline.ingest import ingest  # noqa: E402
from pipeline.llm import load_rates  # noqa: E402
from pipeline.prompts import build_enricher  # noqa: E402

RUN_CFG = yaml.safe_load((ROOT / "config" / "run.yaml").read_text())
ENR = build_enricher(RUN_CFG)
FIELDS = ["review_id", "review_text", "review_rating", "review_likes", "app_version", "review_timestamp"]


# ---------------------------------------------------------------------------------------------
# Fake Anthropic client
# ---------------------------------------------------------------------------------------------

def _err(cls, status):
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("simulated", response=httpx.Response(status, request=req, headers={"retry-after": "0"}), body=None)


class FakeClient:
    """script: list of actions consumed one per API call; default 'ok'.
    Actions: ok | bad_quote | drop_one | garbage | max_tokens | rate_limit | server_error"""

    def __init__(self, script=None):
        self.script = list(script or [])
        self.calls = []
        self.messages = SimpleNamespace(create=self.create)

    def create(self, **kw):
        action = self.script.pop(0) if self.script else "ok"
        user = kw["messages"][0]["content"]
        items = json.loads(re.search(r"<reviews>\n(.*)\n</reviews>", user, re.S).group(1))
        self.calls.append({"action": action, "items": items, "model": kw["model"]})
        if action == "rate_limit":
            raise _err(anthropic.RateLimitError, 429)
        if action == "server_error":
            raise _err(anthropic.InternalServerError, 500)
        if action == "bad_request":
            raise _err(anthropic.BadRequestError, 400)
        results = [{"i": it["i"], "quote": it["text"][:40] or it["text"], "topic": "other",
                    "sub": "general_praise", "intent": "praise", "sev": 1, "sent": 0.5, "ent": [], "nr": False}
                   for it in items]
        if action == "bad_quote":
            results[0]["quote"] = "THIS TEXT IS NOT IN THE REVIEW"
        if action == "drop_one":
            results = results[1:]
        text = "not json {" if action == "garbage" else json.dumps({"results": results})
        stop = "max_tokens" if action == "max_tokens" else "end_turn"
        usage = SimpleNamespace(input_tokens=100 + 20 * len(items), cache_creation_input_tokens=0,
                                cache_read_input_tokens=4000, output_tokens=60 * len(items))
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason=stop, usage=usage,
                               _request_id=f"req_fake_{len(self.calls)}")


# ---------------------------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------------------------

def make_csv(path, n_unique=120, dup_of_first=5, empties=2):
    rows = []
    for k in range(n_unique):
        rows.append([f"id-{k:04d}", f"Review number {k}: the app is fine\nline two", "5", "0", "", "2023-01-01 00:00:00"])
    for d in range(dup_of_first):                              # exact duplicates of review 0
        rows.append([f"dup-{d}", rows[0][1], "4", "0", "8.8", "2023-01-02 00:00:00"])
    for e in range(empties):
        rows.append([f"empty-{e}", "   ", "1", "0", "", "2023-01-03 00:00:00"])
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(FIELDS)
        w.writerows(rows)
    return path


@pytest.fixture
def run(tmp_path):
    csv_path = make_csv(tmp_path / "in.csv")
    ingest(csv_path, tmp_path / "pipeline.db", tmp_path / "ingestion_report.json")
    from pipeline.common import connect
    con = connect(tmp_path / "pipeline.db")
    return SimpleNamespace(dir=tmp_path, con=con)


def enricher(run, client, **over):
    cfg = json.loads(json.dumps(RUN_CFG))
    for k, v in over.items():
        sect, key = k.split("__")
        cfg[sect][key] = v
    return Enricher(run.con, run.dir, ENR, cfg, load_rates(), client, sleep=lambda s: None)


def calls(con):
    return [dict(r) for r in con.execute("SELECT * FROM calls ORDER BY started_at, rowid")]


# ---------------------------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------------------------

def test_ingest_counts_and_quarantine(run):
    rep = json.loads((run.dir / "ingestion_report.json").read_text())["counts"]
    assert rep["records"] == 127 and rep["empty_review_text_quarantined"] == 2
    assert rep["distinct_nonempty_texts"] == 120 and rep["rows_reusable_by_exact_text"] == 5


def test_validate_item_rules():
    labels, parent = ENR["labels"], ENR["parent"]
    good = {"quote": "crash", "topic": "playback", "sub": "crash_or_wont_open", "intent": "complaint",
            "sev": 4, "sent": -0.5, "ent": [" Android ", ""], "nr": False}
    rec, why = validate_item(good, "it will crash daily", labels, parent)
    assert why is None and rec["entities"] == ["Android"]
    for patch, reason in [({"quote": "Crash"}, "quote_not_substring"),          # case matters: exact only
                          ({"sub": "ads"}, "subtopic_mismatch"),
                          ({"sev": 6}, "bad_severity"), ({"sev": 4.0}, "bad_severity"),
                          ({"sent": 1.5}, "bad_sentiment"), ({"nr": "false"}, "bad_needs_review"),
                          ({"intent": "angry"}, "bad_intent"), ({"quote": "  "}, "empty_quote")]:
        _, why = validate_item({**good, **patch}, "it will crash daily", labels, parent)
        assert why.startswith(reason), (patch, why)


def test_happy_path_batches_and_dedupe(run):
    client = FakeClient()
    summary = enricher(run, client).run(progress=lambda *_: None)
    assert summary["stop_reason"] == "completed" and summary["texts_completed"] == 120
    sent_texts = [it["text"] for c in client.calls for it in c["items"]]
    assert len(sent_texts) == 120 == len(set(sent_texts))          # duplicates never sent
    assert all(len(c["items"]) <= 50 for c in client.calls)         # contract batch limit
    assert len(completed_review_ids(run.con, ENR["label_config"])) == 125   # 120 originals + 5 dups
    assert all(c["outcome"] == "succeeded" and c["phase"] == "initial" for c in calls(run.con))


def test_invalid_item_retried_once_alone(run):
    client = FakeClient(["bad_quote"])                              # first item of first batch is bad
    enricher(run, client, fallback__max_fraction=0).run(progress=lambda *_: None)
    assert len(client.calls[1]["items"]) == 1                       # retry carries only the bad item
    row = run.con.execute("SELECT via, attempts FROM results WHERE review_id='id-0000'").fetchone()
    assert tuple(row) == ("retry", 2)


def test_twice_invalid_is_quarantined_with_reason(run):
    client = FakeClient(["bad_quote", "bad_quote"])
    enricher(run, client, fallback__max_fraction=0).run(progress=lambda *_: None)
    q = run.con.execute("SELECT review_id, reason, attempts FROM text_quarantine").fetchall()
    assert [tuple(r) for r in q] == [("id-0000", "quote_not_substring", 2)]
    assert len(pending_texts(run.con, ENR["label_config"])) == 0


def test_dropped_and_garbage_outputs(run):
    client = FakeClient(["drop_one", "ok", "garbage", "ok"])
    enricher(run, client, fallback__max_fraction=0).run(progress=lambda *_: None)
    assert run.con.execute("SELECT COUNT(*) FROM results").fetchone()[0] == 120
    assert run.con.execute("SELECT COUNT(*) FROM text_quarantine").fetchone()[0] == 0


def test_fallback_is_capped_and_logged(run):
    client = FakeClient(["bad_quote", "bad_quote"])                 # fallback call (3rd) succeeds
    enricher(run, client, fallback__max_fraction=0.01).run(progress=lambda *_: None)
    assert client.calls[2]["model"] == RUN_CFG["fallback"]["model"] and len(client.calls[2]["items"]) == 1
    row = run.con.execute("SELECT via, label_config FROM results WHERE review_id='id-0000'").fetchone()
    assert row["via"] == "fallback" and row["label_config"].startswith(RUN_CFG["fallback"]["model"])
    assert pending_texts(run.con, ENR["label_config"]) == []        # fallback result counts as done


def test_rate_limit_backoff_then_success(run):
    client = FakeClient(["rate_limit", "rate_limit"])
    enricher(run, client).run(progress=lambda *_: None)
    c = calls(run.con)
    assert [x["outcome"] for x in c[:3]] == ["failed", "failed", "succeeded"]
    assert c[0]["request_id"].startswith("local-") and c[2]["attempt"] == 3
    assert run.con.execute("SELECT COUNT(*) FROM results").fetchone()[0] == 120


def test_persistent_api_failure_leaves_pending_and_stops(run):
    client = FakeClient(["server_error"] * 100)
    s = enricher(run, client, controls__max_api_attempts=2).run(progress=lambda *_: None)
    assert s["stop_reason"].startswith("api_unavailable") or s["texts_completed"] == 0
    assert run.con.execute("SELECT COUNT(*) FROM text_quarantine").fetchone()[0] == 0   # not quarantined
    assert len(pending_texts(run.con, ENR["label_config"])) == 120


def test_spend_cap_stops_before_overspending(run):
    client = FakeClient()
    s = enricher(run, client, controls__spend_cap_usd=0.05).run(progress=lambda *_: None)
    assert s["stop_reason"].startswith("spend_cap")
    spent = run.con.execute("SELECT SUM(cost_usd) FROM calls").fetchone()[0] or 0
    assert spent <= 0.05
    assert run.con.execute("SELECT COUNT(*) FROM text_quarantine").fetchone()[0] == 0


def test_interrupt_then_resume_never_resends_completed(run):
    client = FakeClient()
    first = enricher(run, client).run(max_batches=1, progress=lambda *_: None)
    assert first["phase"] == "initial" and first["texts_completed"] == 50
    second = enricher(run, client).run(progress=lambda *_: None)
    assert second["phase"] == "resume" and second["texts_completed"] == 70
    before = set(json.loads((run.dir / "checkpoint_before.json").read_text())["completed_ids"])
    after = set(json.loads((run.dir / "checkpoint_after.json").read_text())["completed_ids"])
    assert len(before) == 55 and before < after and len(after) == 125      # 50 originals + 5 dups first
    resumed = [c for c in calls(run.con) if c["phase"] == "resume"]
    sent_on_resume = {rid for c in resumed for rid in json.loads(c["review_ids"])}
    assert not (sent_on_resume & before)                            # checker's `reprocessed_checkpoint`


def test_warm_rerun_makes_zero_calls(run):
    client = FakeClient()
    enricher(run, client).run(progress=lambda *_: None)
    n = len(client.calls)
    s = enricher(run, client).run(progress=lambda *_: None)
    assert len(client.calls) == n and s["texts_completed"] == 0     # cache hit: no new enrichment calls


def test_label_config_changes_when_prompt_changes():
    cfg = json.loads(json.dumps(RUN_CFG))
    cfg["enrich"]["temperature"] = 0.3
    assert build_enricher(cfg)["label_config"] != ENR["label_config"]


def test_bad_request_stops_run_without_quarantine(run):
    client = FakeClient(["bad_request"])
    s = enricher(run, client).run(progress=lambda *_: None)
    assert s["stop_reason"].startswith("fatal_api_error")
    assert run.con.execute("SELECT COUNT(*) FROM text_quarantine").fetchone()[0] == 0
    assert len(pending_texts(run.con, ENR["label_config"])) == 120
    assert len(client.calls) == 1                                   # no retry storm on a config error


def test_code_bug_crashes_instead_of_quarantining(run):
    class Broken(FakeClient):
        def create(self, **kw):
            raise TypeError("simulated programming error")
    with pytest.raises(TypeError):
        enricher(run, Broken()).run(progress=lambda *_: None)
    assert run.con.execute("SELECT COUNT(*) FROM text_quarantine").fetchone()[0] == 0


def test_temperature_sent_via_extra_body_only_for_primary_model(run):
    seen = []
    class Spy(FakeClient):
        def create(self, **kw):
            seen.append((kw["model"], kw.get("extra_body"), "temperature" in kw))
            return super().create(**kw)
    enricher(run, Spy(["bad_quote", "bad_quote"]), fallback__max_fraction=0.01).run(progress=lambda *_: None)
    assert seen[0] == (RUN_CFG["enrich"]["model"], {"temperature": 0}, False)
    assert seen[2] == (RUN_CFG["fallback"]["model"], None, False)   # Sonnet 5.5 rejects non-default temperature
