"""Stage 4 - Group.

Input:  records.jsonl (saved handoff from enrichment).
Code:   every completed complaint/cancellation record joins exactly one issue,
        issue_id = "ISS-<topic>-<subtopic>" (stable across runs; allow_multi_issue = false).
Model:  ONE bounded request suggests a name + description per issue from <= 6 example quotes each.
        It cannot change membership. Invalid/missing names fall back to a code-generated default.
Output: membership.csv (issue_id, review_id), issues.csv (accepted mapping + names + provenance).
"""

import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from .common import ROOT
from .runtime import cache_get, cache_key, cache_put

PRODUCT_AREA = {"access": "access", "usability": "usability", "playback": "playback",
                "billing": "billing_support", "support": "billing_support",
                "downloads": "downloads", "catalog": "catalog", "other": "other"}
EXAMPLES_PER_ISSUE = 6


def issue_id(topic, subtopic):
    return f"ISS-{topic}-{subtopic}"


def open_records(path):
    """records.jsonl, or the gzipped copy in grading/ (what the release ships) when the plain file is absent."""
    import gzip
    path = Path(path)
    for cand in (path, path.with_name(path.name + ".gz"), path.parent / "grading" / (path.name + ".gz")):
        if cand.exists():
            return gzip.open(cand, "rt", encoding="utf-8") if cand.suffix == ".gz" else open(cand, encoding="utf-8")
    raise FileNotFoundError(f"{path} (or grading/{path.name}.gz) not found - download grading.zip from the release")


def read_records(path):
    with open_records(path) as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def build_membership(records_path):
    members = defaultdict(list)
    quotes = {}
    for rec in read_records(records_path):
        if rec["status"] == "completed" and rec["intent"] in ("complaint", "cancellation"):
            iid = issue_id(rec["topic"], rec["subtopic"])
            members[iid].append(rec["review_id"])
            if "cache_source_id" not in rec:                      # examples come from distinct texts only
                quotes.setdefault(iid, []).append((rec["review_id"], rec["evidence_quote"], rec["severity"]))
    return members, quotes


def pick_examples(quotes, k=EXAMPLES_PER_ISSUE):
    """Deterministic, unbiased-by-order sample: lowest sha256(review_id); prefer quotes <= 200 chars."""
    ranked = sorted(quotes, key=lambda q: (len(q[1]) > 200, hashlib.sha256(q[0].encode()).hexdigest()))
    return ranked[:k]


def default_name(iid):
    _, topic, sub = iid.split("-", 2)
    return f"{topic.title()}: {sub.replace('_', ' ')}"


def name_issues(con, llm, run_cfg, members, quotes):
    """Returns ({issue_id: {name, description, source, request_id}}, model_call_made: bool)."""
    system = (ROOT / "prompts" / "grouper_v1.md").read_text(encoding="utf-8")
    pack = [{"issue_id": iid, "members": len(members[iid]),
             "example_quotes": [q for _, q, _ in pick_examples(quotes.get(iid, []))]}
            for iid in sorted(members)]
    schema = {"type": "object", "additionalProperties": False, "required": ["issues"],
              "properties": {"issues": {"type": "array", "items": {
                  "type": "object", "additionalProperties": False, "required": ["issue_id", "name", "description"],
                  "properties": {"issue_id": {"type": "string"}, "name": {"type": "string"},
                                 "description": {"type": "string"}}}}}}
    model = run_cfg["group"]["model"]
    key = cache_key("group", system, pack, schema, model)
    cached, req = cache_get(con, key)
    names, called = {}, False
    if cached is None and pack:
        user = "Name each issue.\n<issues>\n" + json.dumps(pack, ensure_ascii=False) + "\n</issues>"
        res = llm.call(role="group", model=model, system=system, user=user, schema=schema,
                       max_tokens=min(8000, 120 * len(pack) + 300), review_ids=[], purpose="name_issues",
                       cache_system=False)
        called = True
        if res.ok:
            try:
                cached = json.loads(res.text)["issues"]
                req = res.request_ids[-1]
                cache_put(con, key, "group", cached, req)
            except (ValueError, KeyError, TypeError):
                cached = None
    for item in cached or []:
        iid = item.get("issue_id") if isinstance(item, dict) else None
        name, desc = (item.get("name") or "").strip(), (item.get("description") or "").strip()
        if iid in members and iid not in names and 0 < len(name) <= 80 and 0 < len(desc) <= 300:
            names[iid] = {"name": name, "description": desc, "source": "model", "request_id": req}
    for iid in members:                                            # code fallback; never blocks ranking
        names.setdefault(iid, {"name": default_name(iid), "description": "", "source": "default",
                               "request_id": None})
    return names, called


def write_group_outputs(out_dir, members, quotes, names):
    out_dir = Path(out_dir)
    with open(out_dir / "membership.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["issue_id", "review_id"])
        for iid in sorted(members):
            for rid in members[iid]:
                w.writerow([iid, rid])
    with open(out_dir / "issues.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["issue_id", "topic", "subtopic", "product_area", "member_count", "name", "description",
                    "name_source", "name_request_id", "example_review_ids"])
        for iid in sorted(members):
            _, topic, sub = iid.split("-", 2)
            ex = [rid for rid, _, _ in pick_examples(quotes.get(iid, []))]
            n = names[iid]
            w.writerow([iid, topic, sub, PRODUCT_AREA[topic], len(members[iid]), n["name"], n["description"],
                        n["source"], n["request_id"] or "", "|".join(ex)])
