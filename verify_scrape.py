#!/usr/bin/python3
"""Offline verification of a produced SportAdmin scrape CSV.

Runs the CSV-checkable subset of ``sa_checks`` plus cross-run determinism and an
optional diff against a known-good baseline. Exits non-zero if any requested
check fails. No Playwright, no network.

    verify_scrape.py --csv sportadmin.csv \
        [--runs sportadmin.run1.csv sportadmin.run2.csv ...] \
        [--baseline sportadmin_vt25.csv] [--season vår|höst]
"""

import argparse
import csv
import sys
from collections import Counter, defaultdict

import sa_checks

DIALECT = dict(delimiter=",", quotechar="|", quoting=csv.QUOTE_MINIMAL)


def load(path):
    """Return list of [date, matchid, series, name, state]."""
    rows = []
    with open(path, newline="") as f:
        for r in csv.reader(f, **DIALECT):
            if len(r) < 5:
                continue
            rows.append([r[0], r[1], r[2], r[3], r[4]])
    return rows


def season_filter(rows, season):
    if not season:
        return rows
    return [r for r in rows if season in r[2]]


def check_rows(rows):
    """Per-row + per-match checks that need only the CSV. Returns (ok, messages)."""
    msgs = []
    ok = True

    # field sanity
    dirty = []
    for r in rows:
        iss = sa_checks.field_sanity(r[3], r[4])
        if iss:
            dirty.append((r[1], r[3], iss))
    if dirty:
        ok = False
        msgs.append(f"field_sanity: {len(dirty)} row(s)")
        for mid, name, iss in dirty[:10]:
            msgs.append(f"    match {mid}: {name!r} -> {','.join(iss)}")

    # state whitelist
    bad_states = Counter(r[4] for r in rows if r[4] not in sa_checks.STATES)
    if bad_states:
        ok = False
        msgs.append(f"state_in_whitelist: {dict(bad_states)}")

    # (matchid, name) uniqueness within a match
    per_match = defaultdict(list)
    for r in rows:
        per_match[r[1]].append(r)
    dup_matches = 0
    for mid, mrows in per_match.items():
        names = Counter(r[3] for r in mrows)
        dups = [n for n, c in names.items() if c > 1]
        if dups:
            dup_matches += 1
            msgs.append(f"    match {mid}: duplicate player rows {dups}")
    if dup_matches:
        ok = False
        msgs.append(f"player_unique_across_tabs: {dup_matches} match(es) with duplicates")

    # rows-per-match distribution
    sizes = sorted((len(v), k) for k, v in per_match.items())
    if sizes:
        counts = [s for s, _ in sizes]
        typical = Counter(counts).most_common(1)[0][0]
        outliers = [(k, s) for s, k in sizes if s < typical - 1]
        msgs.append(f"rows/match: min={counts[0]} max={counts[-1]} typical={typical} "
                    f"({len(per_match)} matches)")
        if outliers:
            ok = False
            for mid, s in outliers:
                msgs.append(f"    match {mid}: only {s} rows (typical {typical}) — likely under-read")

    return ok, msgs


def check_determinism(run_paths, season):
    runs = [season_filter(load(p), season) for p in run_paths]
    merged, nondet = sa_checks.row_majority(runs)
    msgs = [f"determinism: {len(run_paths)} runs, {len(merged)} distinct (matchid,player) keys"]
    if nondet:
        for nd in nondet[:20]:
            msgs.append(f"    {nd}")
    score = 1.0 - (len(nondet) / len(merged) if merged else 0)
    msgs.append(f"determinism score: {score:.4f}")
    return not nondet, msgs


def check_baseline(rows, baseline_path, season):
    base = season_filter(load(baseline_path), season)
    def keyset(rs):
        return {(r[1], r[3]) for r in rs}       # (matchid, name)
    a, b = keyset(rows), keyset(base)
    only_new = sorted(a - b)
    only_base = sorted(b - a)
    msgs = [f"baseline: current has {len(a)} (matchid,player) keys, "
            f"baseline {len(b)}"]
    for mid, name in only_base[:30]:
        msgs.append(f"    MISSING vs baseline: match {mid} {name!r}")
    for mid, name in only_new[:30]:
        msgs.append(f"    EXTRA vs baseline:   match {mid} {name!r}")
    # state disagreements on shared keys
    def state_map(rs):
        return {(r[1], r[3]): r[4] for r in rs}
    sa_map, sb_map = state_map(rows), state_map(base)
    disagree = [(k, sa_map[k], sb_map[k]) for k in a & b if sa_map[k] != sb_map[k]]
    for k, s1, s2 in disagree[:30]:
        msgs.append(f"    STATE DIFF: match {k[0]} {k[1]!r}: {s1!r} vs baseline {s2!r}")
    ok = not only_new and not only_base and not disagree
    return ok, msgs


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", required=True)
    ap.add_argument("--runs", nargs="*", default=[])
    ap.add_argument("--baseline")
    ap.add_argument("--season", choices=["vår", "höst"])
    args = ap.parse_args()

    rows = season_filter(load(args.csv), args.season)
    print(f"# {args.csv}: {len(rows)} rows"
          + (f" (season {args.season})" if args.season else ""))

    all_ok = True

    ok, msgs = check_rows(rows)
    all_ok &= ok
    print(("PASS" if ok else "FAIL") + "  csv checks")
    for m in msgs:
        print("  " + m)

    if args.runs:
        ok, msgs = check_determinism(args.runs, args.season)
        all_ok &= ok
        print(("PASS" if ok else "FAIL") + "  cross-run determinism")
        for m in msgs:
            print("  " + m)

    if args.baseline:
        ok, msgs = check_baseline(rows, args.baseline, args.season)
        all_ok &= ok
        print(("PASS" if ok else "FAIL") + "  baseline diff")
        for m in msgs:
            print("  " + m)

    print("\n" + ("ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED"))
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
