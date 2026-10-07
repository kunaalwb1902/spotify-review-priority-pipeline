"""Render role prompts and output schemas from config, and derive label_config.

label_config = "<model>+<prompt_version>+<schema_version>+<labels_version>+h<hash>"
The hash covers the rendered system prompt, the JSON schema and every output-affecting setting,
so any edit produces a new label_config and the exact-text result cache misses (contract rule).
"""

import hashlib
import json
from pathlib import Path

import yaml

from .common import INTENTS, ROOT, TOPICS


def load_yaml(rel):
    return yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))


def labels():
    cfg = load_yaml("config/labels.yaml")
    assert list(cfg["topics"]) == list(TOPICS), "labels.yaml topics must match the contract exactly"
    assert set(cfg["intents"]) == set(INTENTS), "labels.yaml intents must match the contract exactly"
    subs = [s for t in cfg["topics"].values() for s in t["subtopics"]]
    assert len(subs) == len(set(subs)), "subtopic names must be unique across topics"
    return cfg


def subtopic_parent(cfg):
    return {s: t for t, v in cfg["topics"].items() for s in v["subtopics"]}


def _example_result(lab):
    return {"quote": lab["evidence_quote"], "topic": lab["topic"], "sub": lab["subtopic"], "intent": lab["intent"],
            "sev": lab["severity"], "sent": lab["sentiment"], "ent": lab["entities"], "nr": lab["needs_review"]}


def render_enricher_system(cfg, template_rel):
    tpl = (ROOT / template_rel).read_text(encoding="utf-8")
    topics = "\n".join(
        f"- `{t}`: {v['definition'].strip()} Subtopics: {', '.join(f'`{s}`' for s in v['subtopics'])}."
        for t, v in cfg["topics"].items())
    intents = "\n".join(f"{n}. `{k}`: {v}" for n, (k, v) in enumerate(cfg["intents"].items(), 1))
    severity = "\n".join(f"- {k}: {v}" for k, v in cfg["severity"].items())
    rules = "\n".join(f"- {r}" for r in cfg["rules"])
    examples = "\n\n".join(
        f"Review: {json.dumps(ex['text'], ensure_ascii=False)}\n"
        f"Result: {json.dumps(_example_result(ex['label']), ensure_ascii=False)}\n"
        f"Why: {ex['why']}"
        for ex in cfg["examples"])
    out = tpl
    for key, val in {"evidence_quote": cfg["evidence_quote"].strip(), "sentiment": cfg["sentiment"].strip(),
                     "entities": cfg["entities"].strip(), "needs_review": cfg["needs_review"].strip(),
                     "topics": topics, "intents": intents, "severity": severity, "rules": rules,
                     "examples": examples}.items():
        out = out.replace("{" + key + "}", val)
    assert "{" not in out.replace("{\"", "").split("## Worked examples")[0], "unfilled placeholder in prompt"
    return out


def enricher_schema(cfg):
    subs = [s for v in cfg["topics"].values() for s in v["subtopics"]]
    item = {
        "type": "object",
        "properties": {
            "i": {"type": "integer"},
            "quote": {"type": "string"},
            "topic": {"type": "string", "enum": list(cfg["topics"])},
            "sub": {"type": "string", "enum": subs},
            "intent": {"type": "string", "enum": list(cfg["intents"])},
            "sev": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
            "sent": {"type": "number"},
            "ent": {"type": "array", "items": {"type": "string"}},
            "nr": {"type": "boolean"},
        },
        "required": ["i", "quote", "topic", "sub", "intent", "sev", "sent", "ent", "nr"],
        "additionalProperties": False,
    }
    return {"type": "object", "properties": {"results": {"type": "array", "items": item}},
            "required": ["results"], "additionalProperties": False}


def enricher_user_message(items):
    """items: list of (local_i, text). JSON-encoded so quotes/newlines/injections stay inert data."""
    payload = json.dumps([{"i": i, "text": t} for i, t in items], ensure_ascii=False)
    return (f"Classify each of these {len(items)} reviews. Return exactly {len(items)} results, one per `i`.\n"
            f"<reviews>\n{payload}\n</reviews>")


def build_enricher(run_cfg):
    cfg = labels()
    e = run_cfg["enrich"]
    system = render_enricher_system(cfg, e["prompt"])
    schema = enricher_schema(cfg)
    settings = {k: e[k] for k in ("model", "thinking", "temperature")}
    digest = hashlib.sha256(json.dumps([system, schema, settings], sort_keys=True).encode()).hexdigest()[:10]
    label_config = f"{e['model']}+{e['prompt_version']}+{e['schema_version']}+{cfg['version']}+h{digest}"
    return {"system": system, "schema": schema, "label_config": label_config, "labels": cfg,
            "parent": subtopic_parent(cfg), "settings": e}
