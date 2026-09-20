#!/usr/bin/python3
"""Categorize "Kommer ej" comments in a training CSV as excused or not,
filling in the "excused" column (ok/not) sportadmin_scraper.py --trainings
leaves blank at scrape time.

Two-step, no API key needed - classification is done by whichever LLM is
driving this script (e.g. Claude Code itself, reading the exported
comments and judging them directly), not by a separate API call:

    pipenv run python3 classify_absences.py export training.csv comments.json
    # ... the LLM reads comments.json and writes classifications.json:
    #     {"<id>": "ok"|"not"|"", ...}  (one entry per id in comments.json;
    #     "" for a genuinely unclear comment - leaves it blank for a human)
    pipenv run python3 classify_absences.py apply training.csv classifications.json

Only "Kommer ej" rows with a non-empty comment and a blank "excused" are
ever touched; already-classified rows are left alone, so export+apply is
safe to re-run after scraping new sessions - it'll only ever surface what's
new.

Manual alternative: "excused" is a plain CSV field - just open the file in
a spreadsheet and type "ok"/"not" directly; neither step here is required
for that path.
"""

import csv
import json
import sys


def load_rows(path):
    with open(path, newline="") as f:
        reader = csv.reader(f, delimiter=",", quotechar="|", quoting=csv.QUOTE_MINIMAL)
        return [row for row in reader]


def write_rows(path, rows):
    with open(path, "w", newline="") as f:
        writer = csv.writer(f, delimiter=",", quotechar="|", quoting=csv.QUOTE_MINIMAL)
        writer.writerows(rows)


def row_id(row):
    """(activity_id, player name) is unique per training CSV."""
    return f"{row[1]}:{row[3]}"


def cmd_export(training_csv, out_json):
    rows = load_rows(training_csv)
    unclassified = [row for row in rows
                    if len(row) > 7 and row[4] == "Kommer ej" and row[6] == "" and row[7].strip()]
    if not unclassified:
        print("Nothing to classify (no unreviewed 'Kommer ej' comments).")
        return

    payload = [{"id": row_id(row), "date": row[0], "player": row[3], "comment": row[7]}
               for row in unclassified]
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"Exported {len(payload)} unreviewed comment(s) -> {out_json}")
    print('Now classify each "id" as "ok" (illness/injury/conflicting organized '
          'activity), "not" (travel/social/other discretionary reason), or "" '
          '(genuinely unclear) and write {"id": category, ...} to a JSON file.')


def cmd_apply(training_csv, classifications_json):
    rows = load_rows(training_csv)
    with open(classifications_json, encoding="utf-8") as f:
        classifications = json.load(f)

    by_id = {row_id(row): row for row in rows if len(row) > 7}
    applied = {"ok": 0, "not": 0, "": 0}
    missing = []
    for rid, category in classifications.items():
        row = by_id.get(rid)
        if row is None:
            missing.append(rid)
            continue
        row[6] = category
        applied[category] = applied.get(category, 0) + 1

    write_rows(training_csv, rows)
    print(f"Applied {sum(applied.values())} classification(s) -> {training_csv}")
    print(f"  ok: {applied.get('ok', 0)}  not: {applied.get('not', 0)}  "
          f"unclear (left blank): {applied.get('', 0)}")
    if missing:
        print(f"  WARNING: {len(missing)} id(s) in {classifications_json} "
              f"not found in {training_csv}: {missing}")


def main():
    if len(sys.argv) != 4 or sys.argv[1] not in ("export", "apply"):
        sys.exit("usage:\n"
                 "  classify_absences.py export TRAINING_CSV COMMENTS_JSON\n"
                 "  classify_absences.py apply  TRAINING_CSV CLASSIFICATIONS_JSON")
    cmd, training_csv, json_path = sys.argv[1:4]
    (cmd_export if cmd == "export" else cmd_apply)(training_csv, json_path)


if __name__ == "__main__":
    main()
