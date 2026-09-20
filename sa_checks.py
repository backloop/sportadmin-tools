#!/usr/bin/python3
"""Pure verification logic for the SportAdmin scrape — no I/O, no Playwright.

Imported by ``sportadmin_scraper.py`` (inline, live per-match checks) and by
``verify_scrape.py`` (offline, from the produced CSV).

A "record" is the per-match dict the scraper builds:

    {
      "matchid": 131613018,
      "date": "2025-04-26",
      "series": "P11 Sydvästra A2, vår",
      "skipped": None | "struken",
      "tabs": {
        "Kommer":    {"label_N": 12, "label_M": 3,
                      "parsed_members": 12, "parsed_leaders": 3,
                      "scroll_iters": 2, "flags": [],
                      "members": [{"name": "...", "state": "Kommer", "href": "/member/123"}, ...]},
        "Kommer ej": {...}, "Ej svarat": {...}, "Ej kallad": {...},
      },
    }
"""

CALLED_STATES = ("Kommer", "Kommer ej", "Ej svarat")
PRE_REPORT_STATES = ("Tillgänglig", "Ej tillgänglig", "Ej förhandsrapporterad")
STATES = frozenset(CALLED_STATES + PRE_REPORT_STATES)

TAB_ORDER = ("Kommer", "Kommer ej", "Ej svarat", "Ej kallad")

_NAME_BAD_PREFIX = ("warning", "S ", "- ")


class Result:
    __slots__ = ("id", "ok", "detail")

    def __init__(self, id, ok, detail=""):
        self.id = id
        self.ok = bool(ok)
        self.detail = detail

    def as_dict(self):
        return {"id": self.id, "ok": self.ok, "detail": self.detail}

    def __repr__(self):
        return f"Result({self.id}, {self.ok}, {self.detail!r})"


def field_sanity(name, state):
    """Return a list of issue codes for one (name, state) pair (empty == clean)."""
    issues = []
    if name is None or name == "":
        issues.append("name_empty")
    else:
        if "\n" in name or "\r" in name:
            issues.append("name_newline")
        if name.startswith("warning"):
            issues.append("name_warning_prefix")
        if name[:2] in ("S ", "- "):
            issues.append("name_marker_prefix")
        if name != name.strip():
            issues.append("name_untrimmed")
    if state not in STATES:
        issues.append("state_unknown")
    return issues


def _int_or_none(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def check_match(record):
    """Run every per-match assertion. Returns a list of Result."""
    out = []
    if record.get("skipped") == "struken":
        out.append(Result("struken", True, "match cancelled — no rows expected"))
        return out

    tabs = record.get("tabs", {})

    # ---- gather ----
    label_n = {t: _int_or_none(tabs.get(t, {}).get("label_N")) for t in TAB_ORDER}
    label_m = {t: _int_or_none(tabs.get(t, {}).get("label_M")) for t in TAB_ORDER}
    parsed_mem = {t: len(tabs.get(t, {}).get("members", [])) for t in TAB_ORDER}
    parsed_led = {t: tabs.get(t, {}).get("parsed_leaders", 0) for t in TAB_ORDER}

    all_labels_ok = all(label_n[t] is not None for t in TAB_ORDER)
    roster_members = sum(v for v in label_n.values() if v is not None)
    roster_leaders = sum(v for v in label_m.values() if v is not None)

    members_all = []
    for t in TAB_ORDER:
        for mem in tabs.get(t, {}).get("members", []):
            members_all.append((t, mem))

    # ---- A0: every tab label parsed ----
    out.append(Result(
        "tab_labels_parsed", all_labels_ok,
        "" if all_labels_ok else
        "unparsed: " + ", ".join(t for t in TAB_ORDER if label_n[t] is None)))

    # ---- A1: sum parsed members == sum label N == roster ----
    total_parsed = sum(parsed_mem.values())
    a1_ok = all_labels_ok and total_parsed == roster_members
    out.append(Result(
        "sum_members_eq_roster", a1_ok,
        f"parsed {total_parsed} vs label-sum {roster_members}"
        + ("" if all_labels_ok else " (some labels unparsed)")))

    # ---- A2: per tab parsed == label ----
    for t in TAB_ORDER:
        n, m = label_n[t], label_m[t]
        ok = (n is not None and parsed_mem[t] == n
              and (m is None or parsed_led[t] == m))
        out.append(Result(
            f"tab_count[{t}]", ok,
            f"members {parsed_mem[t]}/{n}, leaders {parsed_led[t]}/{m}"))

    # ---- A3: (matchid, name) unique across the 4 tabs ----
    seen = {}
    dups = []
    for t, mem in members_all:
        key = mem.get("name", "")
        if key in seen:
            dups.append(f"{key!r} in {seen[key]} & {t}")
        else:
            seen[key] = t
    out.append(Result("player_unique_across_tabs", not dups, "; ".join(dups)))

    # ---- A4: every state in STATES ----
    bad_states = sorted({mem.get("state", "") for _, mem in members_all
                         if mem.get("state", "") not in STATES})
    out.append(Result("state_in_whitelist", not bad_states,
                      "unknown: " + ", ".join(repr(s) for s in bad_states)))

    # ---- A5: tab/state coherence ----
    incoherent = []
    for t, mem in members_all:
        s = mem.get("state", "")
        if t in CALLED_STATES:
            if s != t:
                incoherent.append(f"{mem.get('name')!r}: {t} tab but state {s!r}")
        else:  # Ej kallad
            if s not in PRE_REPORT_STATES:
                incoherent.append(f"{mem.get('name')!r}: Ej kallad state {s!r}")
    out.append(Result("tab_state_coherent", not incoherent,
                      "; ".join(incoherent[:5])))

    # ---- A6: field sanity ----
    field_issues = []
    for _, mem in members_all:
        iss = field_sanity(mem.get("name", ""), mem.get("state", ""))
        if iss:
            field_issues.append(f"{mem.get('name')!r}: {','.join(iss)}")
    out.append(Result("field_sanity", not field_issues,
                      "; ".join(field_issues[:5])))

    # ---- A7: distinct member hrefs == roster ----
    hrefs = [mem.get("href") for t in TAB_ORDER
             for mem in tabs.get(t, {}).get("members", []) if mem.get("href")]
    uniq = set(hrefs)
    a7_ok = (not all_labels_ok) or (len(uniq) == roster_members)
    out.append(Result("distinct_hrefs_eq_roster", a7_ok,
                      f"{len(uniq)} distinct hrefs vs roster {roster_members}"
                      f" ({len(members_all) - len(hrefs)} rows without href)"))

    return out


def match_flags(record):
    """Row-level issue list for the verify jsonl (name + codes + raw state)."""
    flags = []
    for t in TAB_ORDER:
        tab = record.get("tabs", {}).get(t, {})
        for f in tab.get("flags", []):
            flags.append({"tab": t, "code": f})
        for mem in tab.get("members", []):
            iss = field_sanity(mem.get("name", ""), mem.get("state", ""))
            if iss:
                flags.append({"tab": t, "player": mem.get("name"),
                              "state": mem.get("state"), "codes": iss})
    return flags


def _majority_merge(runs, id_field="matchid", extra_specs=None):
    """Shared majority-vote merge for both match and training rows.

    Each row is [date, id, name2, name, state, location, ...extra] - i.e.
    the first 6 fields always mean date/id/series-or-activity-name/player/
    state/location. `extra_specs`, if given, is a list of (build, reduce)
    pairs, one per additional trailing field in row order: build(row)
    extracts that field from a raw row (or "" if the row is too short),
    reduce(values) collapses everything seen for a given key across every
    run into the one merged value - used so free-text/manually-edited
    fields (a training's Kommentar, its "excused" marker) can be carried
    through without affecting the unanimity check below, which is based on
    `state` only.

    Returns (merged_rows, nondeterministic) where nondeterministic lists
    every (id, name) key whose state/presence isn't unanimous across runs.
    """
    from collections import Counter

    extra_specs = extra_specs or []
    n = len(runs)
    # key -> list of (date, series/activity_name, state, location, *extra) per run
    seen = {}
    for run in runs:
        run_keys = {}
        for row in run:
            date, rid, series, name, state = row[0], row[1], row[2], row[3], row[4]
            location = row[5] if len(row) > 5 else ""
            extras = tuple(build(row) for build, _ in extra_specs)
            key = (str(rid), name)
            run_keys.setdefault(key, []).append((date, series, state, location) + extras)
        for key, vals in run_keys.items():
            seen.setdefault(key, []).append(vals)

    merged = []
    nondeterministic = []
    for key, per_run in sorted(seen.items()):
        present = len(per_run)
        states = [v[2] for run_vals in per_run for v in run_vals]
        cnt = Counter(states)
        top_state, top_n = cnt.most_common(1)[0]
        # representative date/series/location (first seen)
        date0, series0, _, location0 = per_run[0][0][:4]
        unanimous = present == n and len(cnt) == 1 and all(len(v) == 1 for v in per_run)
        if not unanimous:
            nondeterministic.append({
                id_field: key[0], "player": key[1],
                "present_in_runs": present, "of_runs": n,
                "states": dict(cnt),
            })
        row_out = [date0, int(key[0]) if key[0].isdigit() else key[0],
                   series0, key[1], top_state, location0]
        for i, (_, reduce_fn) in enumerate(extra_specs):
            all_vals = [v[4 + i] for run_vals in per_run for v in run_vals]
            row_out.append(reduce_fn(all_vals))
        merged.append(row_out)
    return merged, nondeterministic


def row_majority(runs):
    """Merge N runs (each a list of [date, matchid, series, name, state,
    location] - location is optional, defaults to "" for older 5-field rows).

    Returns (merged_rows, nondeterministic) where nondeterministic is a list of
    dicts describing every (matchid, name) key whose (state) or presence is not
    unanimous across all runs.
    """
    return _majority_merge(runs, id_field="matchid")


def row_majority_training(runs):
    """Merge N runs of 8-field training rows: [date, activity_id,
    activity_name, name, state, location, excused, comment].

    Majority-vote on `state` only, exactly like row_majority() - neither the
    comment nor the "excused" marker (free text / manually or LLM-edited,
    could legitimately differ or be re-typed between scrapes) ever counts
    against unanimity. Both are merged as the first non-empty value seen
    for that (activity_id, name) key across all runs.
    """
    def first_nonempty(vals):
        for v in vals:
            if v:
                return v
        return ""

    return _majority_merge(runs, id_field="activity_id", extra_specs=[
        (lambda row: row[6] if len(row) > 6 else "", first_nonempty),  # excused
        (lambda row: row[7] if len(row) > 7 else "", first_nonempty),  # comment
    ])
