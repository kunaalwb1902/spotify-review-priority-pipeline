"""End-to-end offline test: all six stages with a fake model playing every role, an interruption and
resume, then the course's own check_submission.py on the exported grading/ folder. Zero API cost."""

import csv
import json
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pipeline.orchestrator import load_cfg, run_pipeline  # noqa: E402

FIELDS = ["review_id", "review_text", "review_rating", "review_likes", "app_version", "review_timestamp"]
TEXTS = [  # (text, topic, sub, intent, sev)
    ("App crashes every time I open it", "playback", "crash_or_wont_open", "complaint", 4),
    ("Can't pick songs without premium, uninstalling", "billing", "premium_only_features", "cancellation", 3),
    ("Too many ads between songs", "usability", "ads", "complaint", 2),
    ("Love it", "other", "general_praise", "praise", 1),
    ("Cannot log in at all", "access", "login_failure", "complaint", 4),
    ("Please add a sleep timer", "usability", "navigation_and_controls", "request", 1),
]


class RoleFake:
    """Answers enrich / verify / group / memo requests, recognised by their system prompts."""

    def __init__(self):
        self.calls = []
        self.messages = SimpleNamespace(create=self.create)

    def create(self, **kw):
        system = kw["system"][0]["text"]
        user = kw["messages"][0]["content"]
        role = ("verify" if "INDEPENDENT AUDITOR" in system else "group" if "GROUPING namer" in system
                else "memo" if "MEMO WRITER" in system else "enrich")
        self.calls.append(role)
        lookup = {t: x for t, *x in TEXTS}
        if role in ("enrich", "verify"):
            items = json.loads(re.search(r"<reviews>\n(.*)\n</reviews>", user, re.S).group(1))
            res = []
            for it in items:
                topic, sub, intent, sev = lookup[re.sub(r" \(v\d+\)$", "", it["text"])]
                if role == "enrich":
                    res.append({"i": it["i"], "quote": it["text"][:20], "topic": topic, "sub": sub, "intent": intent,
                                "sev": sev, "sent": -0.5 if sev > 1 else 0.8, "ent": [], "nr": False})
                else:
                    res.append({"i": it["i"], "topic": topic, "intent": intent, "sev": sev, "unsure": False})
            text = json.dumps({"results": res})
        elif role == "group":
            issues = json.loads(re.search(r"<issues>\n(.*)\n</issues>", user, re.S).group(1))
            text = json.dumps({"issues": [{"issue_id": i["issue_id"], "name": "Named " + i["issue_id"][4:],
                                           "description": "Customers report this problem."} for i in issues]})
        else:
            pack = json.loads(re.search(r"<evidence>\n(.*)\n</evidence>", user, re.S).group(1))
            top = pack["top_issues"][0]
            ex = top["examples"][0]
            text = (f"## Recommendation\nPrioritize `{top['issue_id']}`: {{{top['issue_id']}.complaint_count}} complaints, "
                    f"severity sum {{{top['issue_id']}.severity_sum}}, mean {{{top['issue_id']}.mean_severity}}, "
                    f"of {{RUN.completed}} completed records. \"{ex['quote']}\" [review {ex['review_id']}]\n")
        usage = SimpleNamespace(input_tokens=500, cache_creation_input_tokens=0, cache_read_input_tokens=0,
                                output_tokens=100)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn",
                               usage=usage, _request_id=f"req_{role}_{len(self.calls)}")


def make_csv(path, copies=20):
    rows, k = [], 0
    for c in range(copies):
        for t, *_ in TEXTS:
            text = t if c < 15 else f"{t} (v{c})"          # mostly exact duplicates, some distinct variants
            rows.append([f"r{k:05d}", text, "3", "0", "", f"2023-0{1 + c % 9}-01 00:00:00"])
            k += 1
    rows.append(["r-empty", "", "1", "0", "", "2023-01-01 00:00:00"])
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(FIELDS)
        w.writerows(rows)
    return path


def test_end_to_end_with_resume_passes_course_checker(tmp_path):
    csv_path = make_csv(tmp_path / "input.csv")
    run_dir, evals = tmp_path / "run", tmp_path / "evals"
    cfg = load_cfg()
    cfg["enrich"]["batch_size"] = 5
    fake = RoleFake()
    # Session 1: interrupted after 2 batches -> downstream stages must NOT run on partial data.
    first = run_pipeline(csv_path, run_dir, fake, max_batches=2, progress=lambda *_: None, eval_dir=evals, cfg=cfg)
    assert first["stopped"] and first["pending"] > 0
    assert "verify" not in fake.calls and "memo" not in fake.calls
    # Session 2: resume finishes enrichment and runs every stage.
    out = run_pipeline(csv_path, run_dir, fake, progress=lambda *_: None, eval_dir=evals, cfg=cfg)
    assert not out["partial"] and out["records"]["unprocessed"] == 0
    assert out["records"]["quarantined_empty"] == 1 and out["records"]["completed_via_cache"] > 0
    assert set(fake.calls) == {"enrich", "verify", "group", "memo"}
    assert json.loads((run_dir / "memo_check.json").read_text())["passed"]

    # Deterministic rerank from saved files, no model.
    before = (run_dir / "ranking.csv").read_bytes()
    n_calls = len(fake.calls)
    from pipeline.rank import rank
    rank(run_dir)
    assert (run_dir / "ranking.csv").read_bytes() == before and len(fake.calls) == n_calls

    # The course checker on our exported grading folder.
    ck = ROOT / "data" / "raw" / "check_submission.py"
    subprocess.run([sys.executable, ck, "reference", "--full", csv_path, "--analysis", csv_path,
                    "--out", tmp_path / "ref.json"], check=True, capture_output=True)
    subprocess.run([sys.executable, ck, "check", "--reference", tmp_path / "ref.json",
                    "--submission", run_dir / "grading", "--out", tmp_path / "self.json"], check=True, capture_output=True)
    report = json.loads((tmp_path / "self.json").read_text())
    assert report.get("issue_counts", {}) == {}, report.get("examples")
    assert report["coverage"]["labelable_completion_fraction"] == 1.0      # empty text excluded from denominator
    assert report["working_coverage_point_candidate"] == 1.0
