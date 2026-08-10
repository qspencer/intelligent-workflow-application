"""Interactive labeler for the recall-validation crux set.

Shows each repeat-sender message one at a time (sender + subject, blind to any
classifier output) and takes a single-key source label. Writes back after every
answer, so you can quit (q) and resume — it skips rows you have already labeled.

Reads/writes the template produced by the label-set generator:
    data/email_triage/<account>_labels_template.csv

Usage (from backend/):
    uv run python tools/label_email_recall.py --account qspencer@gmail.com
    uv run python tools/label_email_recall.py --account qspencer@gmail.com --relabel  # revisit all
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data" / "email_triage"

# The two-axis SOURCE categories (what the mail IS), with a one-line reminder each.
CATEGORIES = [
    ("personal", "a real person writing to you (not automated)"),
    ("notification", "transactional/account/security: receipts, codes, alerts, orders"),
    ("newsletter", "recurring editorial/content subscription you signed up for"),
    ("promotion", "marketing/sales/offers/discounts"),
    ("spam", "unsolicited or deceptive junk"),
]
VALID = {c for c, _ in CATEGORIES}


def _read_template(path: Path) -> tuple[list[str], list[str], list[dict[str, str]]]:
    """-> (comment_lines, header, rows). Preserves the leading `#` comments."""
    comments: list[str] = []
    header: list[str] = []
    rows: list[dict[str, str]] = []
    with path.open(newline="") as fh:
        for raw in csv.reader(fh):
            if not raw:
                continue
            if raw[0].startswith("#"):
                comments.append(raw[0])
            elif raw and raw[0] == "message_id":
                header = raw
            elif header:
                rows.append(dict(zip(header, raw + [""] * (len(header) - len(raw)), strict=False)))
    return comments, header, rows


def _write_template(path: Path, comments: list[str], header: list[str], rows: list[dict[str, str]]):
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        for c in comments:
            w.writerow([c])
        w.writerow(header)
        for r in rows:
            w.writerow([r.get(h, "") for h in header])


def _body_index(account: str) -> dict[str, str]:
    """message_id -> body_text, for on-demand viewing."""
    idx: dict[str, str] = {}
    for f in (DATA_DIR / account).glob("*.json"):
        try:
            m = json.loads(f.read_text())
        except Exception:
            continue
        mid = m.get("message_id")
        if mid:
            idx[str(mid)] = str(m.get("body_text") or "")
    return idx


def _prompt() -> None:
    print("\n  Label by SOURCE — what the mail IS. Options:")
    for i, (cat, hint) in enumerate(CATEGORIES, 1):
        print(f"    {i}  {cat:13} {hint}")
    print("    b  show body     s  skip (leave blank)     q  save & quit")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--account", default="qspencer@gmail.com")
    ap.add_argument("--relabel", action="store_true", help="revisit rows already labeled")
    args = ap.parse_args()

    path = DATA_DIR / f"{args.account}_labels_template.csv"
    if not path.exists():
        print(f"No label template at {path}. Generate it first.")
        raise SystemExit(1)
    comments, header, rows = _read_template(path)
    bodies = _body_index(args.account)

    todo = [r for r in rows if args.relabel or not r.get("label")]
    done_already = len(rows) - len([r for r in rows if not r.get("label")])
    if not todo:
        print(f"All {len(rows)} rows already labeled. Use --relabel to revisit.")
        return
    print(f"{len(rows)} messages, {done_already} already labeled, {len(todo)} to go.")
    print("One key per message; it saves after each answer (Ctrl-C is safe).")

    labeled = 0
    by_num = {str(i): c for i, (c, _) in enumerate(CATEGORIES, 1)}
    for n, row in enumerate(todo, 1):
        while True:
            print("\n" + "─" * 74)
            print(f"  [{n}/{len(todo)}]  {row['received_at'][:10]}  {row['sender']}")
            print(f"  Subject: {row['subject']}")
            if row.get("label"):
                print(f"  (current label: {row['label']})")
            _prompt()
            try:
                choice = input("  > ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                choice = "q"
            if choice in by_num:
                row["label"] = by_num[choice]
            elif choice in VALID:  # allow typing the word
                row["label"] = choice
            elif choice == "b":
                body = bodies.get(row.get("message_id", ""), "")
                print("\n  ── body ──")
                print("  " + (body[:1200].replace("\n", "\n  ") if body else "(no text body)"))
                continue  # re-prompt the same message
            elif choice == "s":
                break
            elif choice == "q":
                _write_template(path, comments, header, rows)
                print(
                    f"\nSaved. {labeled} labeled this session; "
                    f"{sum(1 for r in rows if r.get('label'))}/{len(rows)} total."
                )
                return
            else:
                print("  ? enter 1-5, a category word, b, s, or q")
                continue
            _write_template(path, comments, header, rows)  # save after each
            labeled += 1
            print(f"  ✓ {row['label']}")
            break

    _write_template(path, comments, header, rows)
    total = sum(1 for r in rows if r.get("label"))
    print(f"\nDone. {labeled} labeled this session; {total}/{len(rows)} total.")
    print("Tell me 'labeled' and I'll build + run the verdict path.")


if __name__ == "__main__":
    main()
