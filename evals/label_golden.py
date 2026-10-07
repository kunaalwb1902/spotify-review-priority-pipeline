"""Hand-label the golden 50 in the terminal. No model is involved and nothing here is sent anywhere.

    .venv/bin/python evals/label_golden.py              # label / resume where you left off
    .venv/bin/python evals/label_golden.py --redo 7     # relabel review #7
    .venv/bin/python evals/label_golden.py --show       # print your labels so far

Saves after every review to evals/golden_50_human.csv (atomic write), so you can quit any time.
These human labels are only ever read by the evaluation step - never by a prompt.
"""

import argparse
import csv
import os
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "raw" / "golden_50_to_label.csv"
OUT = ROOT / "evals" / "golden_50_human.csv"
CFG = yaml.safe_load((ROOT / "config" / "labels.yaml").read_text())

TOPICS = list(CFG["topics"])
INTENTS = list(CFG["intents"])
SOURCE_COLS = ["review_id", "review_text", "review_rating", "review_likes", "app_version", "review_timestamp"]
LABEL_COLS = ["topic", "intent", "sentiment", "severity", "entities", "evidence_quote", "needs_review",
              "subtopic", "ambiguous", "acceptable_alternatives", "notes"]


def load():
    rows = list(csv.DictReader(SRC.open(encoding="utf-8", newline="")))
    done = {}
    if OUT.exists():
        for r in csv.DictReader(OUT.open(encoding="utf-8", newline="")):
            if r.get("topic"):
                done[r["review_id"]] = r
    return rows, done


def save(rows, done):
    tmp = OUT.with_suffix(".csv.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SOURCE_COLS + LABEL_COLS)
        w.writeheader()
        for r in rows:
            lab = done.get(r["review_id"], {})
            w.writerow({**{c: r[c] for c in SOURCE_COLS}, **{c: lab.get(c, "") for c in LABEL_COLS}})
    os.replace(tmp, OUT)


def ask(prompt, valid=None, default=None):
    while True:
        raw = input(prompt).strip()
        if raw.lower() == "q":
            raise KeyboardInterrupt
        if not raw and default is not None:
            return default
        if valid is None or raw in valid:
            return raw
        print(f"   ↳ please enter one of: {', '.join(valid)}")


def pick(name, options):
    menu = "  ".join(f"{i}={o}" for i, o in enumerate(options, 1))
    print(f"   {menu}")
    i = ask(f"   {name} #: ", [str(i) for i in range(1, len(options) + 1)])
    return options[int(i) - 1]


def recover_quote(pasted, text):
    """Map a quote copied from the wrapped display back to the exact original span.
    The display adds line breaks + indentation, so compare with whitespace collapsed, then return
    the untouched substring of the original review (never an edited version)."""
    if pasted in text:
        return pasted
    words = pasted.split()
    if not words:
        return None
    import re
    pattern = r"\s+".join(re.escape(w) for w in words)
    m = re.search(pattern, text)
    return m.group(0) if m else None


def sentence_spans(text):
    """(start, end) index pairs of sentences/lines in the ORIGINAL text, whitespace-trimmed."""
    import re
    spans = []
    for m in re.finditer(r"[^.!?\n]+[.!?…]*", text):
        a, b = m.start(), m.end()
        while a < b and text[a].isspace():
            a += 1
        while b > a and text[b - 1].isspace():
            b -= 1
        if b > a:
            spans.append((a, b))
    return spans


def pick_quote(raw, text, pieces):
    """'' -> whole text; '3' or '2-4' -> exact original span covering those pieces; else typed text."""
    import re
    if not raw:
        return text
    m = re.fullmatch(r"(\d+)(?:\s*-\s*(\d+))?", raw)
    if m and pieces:
        lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
        if 1 <= lo <= hi <= len(pieces):
            return text[pieces[lo - 1][0]:pieces[hi - 1][1]]
        return None
    return recover_quote(raw, text)


def label_one(n, total, row):
    text = row["review_text"]
    print("\n" + "=" * 78)
    print(f"Review {n}/{total}   id={row['review_id']}")
    print("-" * 78)
    # Printed exactly as stored (no tool-side wrapping/indent), so a copy from the terminal
    # is the original text; the terminal's own soft-wrap does not insert line breaks.
    print(text)
    print("-" * 78)
    print("(Stars/metadata hidden on purpose: label the text only.)   q = save & quit")

    topic = pick("topic", TOPICS)
    subs = CFG["topics"][topic]["subtopics"]
    sub = pick("subtopic", subs)
    intent = pick("intent  [precedence: cancellation > complaint > request > praise > unclear]", INTENTS)
    print("   severity: " + " | ".join(f"{k}={v.split('—')[0].split(';')[0][:38]}" for k, v in CFG["severity"].items()))
    severity = ask("   severity 1-5: ", ["1", "2", "3", "4", "5"])
    while True:
        s = ask("   sentiment -1.0..1.0 (e.g. -0.6, 0, 0.8): ")
        try:
            if -1 <= float(s) <= 1:
                sentiment = f"{float(s):.1f}"
                break
        except ValueError:
            pass
        print("   ↳ a number between -1 and 1")
    entities = ask("   entities, comma-separated (Enter = none): ", default="")
    entities = "|".join(e.strip().lower() for e in entities.split(",") if e.strip())
    pieces = sentence_spans(text)
    if len(pieces) > 1:
        print("   quote options (exact pieces of the review):")
        for k, (a, b) in enumerate(pieces, 1):
            print(f"     [{k}] {text[a:b]}")
    while True:
        raw = input("   evidence quote: number like 2 or 2-3, Enter = whole review, or type/paste text: ").strip()
        if raw.lower() == "q":
            raise KeyboardInterrupt
        q = pick_quote(raw, text, pieces)
        if q and q.strip() and q in text:
            print(f"   ↳ quote: {q!r}")
            break
        print("   ↳ not found in the review; try a number from the list")
    needs = ask("   needs_review? y/n [n]: ", ["y", "n"], default="n") == "y"
    amb = ask("   ambiguous (another label also defensible)? y/n [n]: ", ["y", "n"], default="n") == "y"
    alts, notes = "", ""
    if amb:
        alts = ask("   acceptable alternatives (e.g. topic=usability; severity=2): ", default="")
    notes = ask("   note / reasoning (Enter = skip): ", default="")
    return {"topic": topic, "subtopic": sub, "intent": intent, "severity": severity, "sentiment": sentiment,
            "entities": entities, "evidence_quote": q, "needs_review": str(needs).lower(),
            "ambiguous": str(amb).lower(), "acceptable_alternatives": alts, "notes": notes}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--redo", type=int, help="relabel review number N (1-50)")
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args()
    rows, done = load()

    if args.show:
        for i, r in enumerate(rows, 1):
            d = done.get(r["review_id"])
            tag = f"{d['topic']:<9} {d['intent']:<12} sev={d['severity']} sent={d['sentiment']}" if d else "-- not labeled --"
            print(f"{i:>2}. {tag}  | {r['review_text'][:50]!r}")
        print(f"\n{len(done)}/{len(rows)} labeled -> {OUT.relative_to(ROOT)}")
        return

    todo = [args.redo - 1] if args.redo else [i for i, r in enumerate(rows) if r["review_id"] not in done]
    print(f"{len(done)}/{len(rows)} already labeled. Definitions: config/labels.yaml")
    try:
        for i in todo:
            done[rows[i]["review_id"]] = label_one(i + 1, len(rows), rows[i])
            save(rows, done)
            print(f"   ✓ saved ({len(done)}/{len(rows)})")
    except (KeyboardInterrupt, EOFError):
        print("\nStopped - progress is saved.")
    save(rows, done)
    if len(done) == len(rows):
        print("All 50 labeled. 🎉")


if __name__ == "__main__":
    sys.exit(main())
