#!/usr/bin/python3

import time
from enum import IntEnum, auto
import csv
import sys
import datetime
import re
import pandas as pd
import io
import textwrap
import html
import argparse
import collections
import itertools
import json
import os
import glob

from settings import load_settings
import generate_index


SEASONS = ("vår", "höst", "vinter")


class NoMatchingSeasonError(Exception):
    """Raised by load() when --season filters out every row in a file."""


def resolve_input_files(path):
    """Accept either a single CSV path or a directory - in the latter case,
    every *.csv file directly inside it is analyzed in turn. A *_training.csv
    file is never treated as a primary input (7-column schema, no season
    marker of its own) - it's only ever consumed via training_sibling_path()
    when its plain-match counterpart is analyzed."""
    if os.path.isdir(path):
        return sorted(f for f in glob.glob(os.path.join(path, "*.csv"))
                      if not f.endswith("_training.csv"))
    return [path]


def training_sibling_path(csv_path):
    """sportadmin_vår_2025.csv -> sportadmin_vår_2025_training.csv (same dir)."""
    stem, ext = os.path.splitext(csv_path)
    return stem + "_training" + ext


def narvaro_sibling_path(csv_path):
    """sportadmin_vår_2025.csv -> sportadmin_vår_2025_narvaro.csv (same dir) -
    sibling to, not chained after, training_sibling_path()'s file."""
    stem, ext = os.path.splitext(csv_path)
    return stem + "_narvaro" + ext


WEEKDAY_MAP = {"mån": 0, "tis": 1, "ons": 2, "tor": 3, "fre": 4, "lör": 5, "sön": 6}


def parse_weekdays(spec):
    """None -> None (no filter). 'mån,ons' -> {0, 2}. Comma-separated,
    case-insensitive, whitespace-trimmed 3-letter Swedish abbreviations.
    Raises ValueError naming the bad token if one isn't recognized."""
    if not spec:
        return None
    days = set()
    for token in spec.split(","):
        key = token.strip().lower()
        if key not in WEEKDAY_MAP:
            raise ValueError(f"unrecognized weekday {token.strip()!r} "
                             f"(expected one of: {', '.join(WEEKDAY_MAP)})")
        days.add(WEEKDAY_MAP[key])
    return days


def detect_seasons(filename):
    """Return the set of season names (see SEASONS) whose marker appears in
    any row's raw series-name field of the given scraped CSV."""
    seasons = set()
    with open(filename, newline='') as csvfile:
        reader = csv.reader(csvfile, delimiter=',', quotechar='|', quoting=csv.QUOTE_MINIMAL)
        for row in reader:
            if len(row) < 3:
                continue
            for season in SEASONS:
                if season in row[2]:
                    seasons.add(season)
    return seasons


class ReportState():
    PRE_REPORT_AVAILABLE = "Tillgänglig"
    PRE_REPORT_NOT_AVAILABLE = "Ej tillgänglig"
    PRE_REPORT_NOT_REPORTED = "Ej förhandsrapporterad"
    CALLED_COMING = "Kommer"
    CALLED_NOT_COMING = "Kommer ej"
    CALLED_NO_ANSWER = "Ej svarat"


class SportadminGamesAnalyzer:

    def __init__(self, args):
        self.args = args
        self.training_df = None
        self.narvaro_df = None

    def pretty_print(self, df, show_index, description):

        with io.StringIO() as df_io:
            # !show_index - Print the DataFrame without the row index
            print(df.to_string(index=show_index), file=df_io)
            df_string = df_io.getvalue()
            # to_string() right-pads columns to a fixed width, which leaves
            # trailing whitespace on rows whose last cell is short/empty
            df_string = "\n".join(line.rstrip() for line in df_string.splitlines())
            max_width = max(len(line) for line in df_string.splitlines())

        # dedent and then remove any leading newlines
        dedented_description = textwrap.dedent(description).strip()
        formatted_description= textwrap.fill(dedented_description, width=max_width)

        print("")
        print("="*max_width)
        print(f"{self.season.upper()} {self.year}")
        print("")
        print(formatted_description)
        print("")
        print(df_string)
        print("="*max_width)


    def load(self, filename):

        #
        # Read all data and add some calculated columns
        #

        # --home-locations gives one or more regex fragments; a match's
        # location counts as "home" if it matches any of them, else "away".
        # With no patterns given, every match is classified "away".
        home_locations = getattr(self.args, "home_locations", None) or []
        home_pattern = re.compile("|".join(home_locations)) if home_locations else None

        with open(filename, newline='') as csvfile:

            data = []

            reader = csv.reader(csvfile, delimiter=',', quotechar='|', quoting=csv.QUOTE_MINIMAL)
            # by using the csv module to read the file
            # we get back correct quotations and delimiters

            for row in reader:
                # separate ReportState into separate columns
                #cols = [0] * 6
                #cols[int(row[-1])-1] = 1
                #row.extend(cols)

                # older CSVs (scraped before "location" was added) have only
                # 5 fields; pad so the column count always matches header
                if len(row) < 6:
                    row.append("")

                # create week number
                week_num = datetime.date.fromisoformat(row[0]).isocalendar()[1]

                # convert to date
                date = datetime.datetime.strptime(row[0], "%Y-%m-%d")

                # extract the series differentiator. The district name is
                # sometimes prefixed with a birth-year infix, e.g.
                # "P12 (f.2014) Sydvästra A1, vinter" instead of the plain
                # "P12 Nordvästra A, vår" - the infix is optional.
                series = re.sub(r"[PF][0-9]{2} (?:\(f\.[0-9]{4}\) )?[A-Ö-a-ö]+ ([A-D][1-5]?), (vår|höst|vinter)", r"\g<1>", row[2])

                # remove trailing comment with syntax "<first name> [middle name] <last name> - <comment>"
                row[3] = re.sub(r"(.*) - .*", r"\g<1>", row[3])

                # create verbose report state
                #report_state = ReportState(int(row[4]))
                report_state  = row[4]

                # home vs away, based on --home-locations against the venue
                home_away = "home" if home_pattern and home_pattern.search(row[5]) else "away"

                header = []
                # add new columns
                row.insert(0, date)
                row.insert(1, week_num)
                row.insert(2, report_state)
                row.insert(3, series)
                row.insert(4, home_away)
                data.append(row)

            #header = ["date", "match number", "series name", "player name", "ReportState", "available", "not available", "not reported", "coming", "not coming", "not answered"]
            # "date_str" (not "date" - the parsed datetime prepended below
            # already claims that name; a duplicate column label would make
            # df["date"] return a DataFrame instead of a Series).
            header = ["date_str", "match number", "series name", "player name", "report state", "location"]
            header.insert(0, "date")
            header.insert(1, "week")
            header.insert(2, "ReportState")
            header.insert(3, "series")
            header.insert(4, "home_away")

        # filter on season, if --season was given; otherwise analyze
        # everything in the file (argparse's choices=SEASONS already
        # rejects an invalid --season value before we get here).
        season = getattr(self.args, "season", None)
        if season:
            self.season = season
            data = filter(lambda row: self.season in row[7], data)
        else:
            self.season = "alla"

        # filter out external players, where player names start with "- <first name> <last name>"
        #data = filter(lambda row: row[8][:2] != "- ", data)

        # expand the filter, otherwise after a single walkthrough the iterator is exhausted
        data = list(data)

        if not data:
            available = sorted(detect_seasons(filename), key=SEASONS.index)
            raise NoMatchingSeasonError(
                f"no rows found for season {self.season!r} in {filename}. "
                f"Seasons present: {', '.join(available) if available else '(none detected)'}")

        # all matches must be within the same year (artificial limitation for pretty_print()
        years = {row[0].year for row in data}
        if len(years) > 1:
            print("ERROR: Too many years")
            exit(1)
        self.year = years.pop()

        for row in data:
            print(row)

        print(f"len: {len(data)}")
        print(header)

        self.df = pd.DataFrame(data, columns = header)
        #print(df.describe())


    def load_training(self, filename):
        """Parse a training-session CSV (8 fields: date, activity_id,
        activity_name, player_name, state, location, excused, comment -
        written by sportadmin_scraper.py --trainings) into self.training_df.
        "excused" is blank until a separate categorization step
        (classify_absences.py, manual or LLM-driven) marks a "Kommer ej"
        row "ok"/"not"; a bare 7-field row (pre-"excused" schema) is still
        accepted, with excused defaulting to "".

        --weekdays is applied here and only here: it narrows which training
        sessions count toward the sliding-average attendance report,
        without touching the match-based reports in self.df."""
        weekday_set = getattr(self.args, "weekday_set", None)

        rows = []
        with open(filename, newline='') as csvfile:
            reader = csv.reader(csvfile, delimiter=',', quotechar='|', quoting=csv.QUOTE_MINIMAL)
            for row in reader:
                if len(row) == 7:
                    date_str, activity_id, activity_name, name, state, location, comment = row
                    excused = ""
                else:
                    if len(row) < 8:
                        row = row + [""] * (8 - len(row))
                    date_str, activity_id, activity_name, name, state, location, excused, comment = row[:8]
                date = datetime.datetime.strptime(date_str, "%Y-%m-%d")
                iso_year, iso_week, iso_weekday = date.isocalendar()
                weekday = iso_weekday - 1  # 0=Monday, matching WEEKDAY_MAP
                if weekday_set is not None and weekday not in weekday_set:
                    continue
                rows.append([date, date_str, iso_year, iso_week, weekday,
                            activity_id, activity_name, name, state, location,
                            excused, comment])

        columns = ["date", "date_str", "iso_year", "iso_week", "weekday",
                   "activity_id", "activity_name", "player name", "state",
                   "location", "excused", "comment"]
        self.training_df = pd.DataFrame(rows, columns=columns)

        # Soft sanity check, not a hard failure — catches an accidentally
        # mismatched sibling file (e.g. a stale training CSV left behind
        # after re-scraping the match CSV for a different date range).
        if getattr(self, "df", None) is not None and len(self.training_df):
            match_min, match_max = self.df["date"].min(), self.df["date"].max()
            train_min, train_max = self.training_df["date"].min(), self.training_df["date"].max()
            if train_max < match_min or train_min > match_max:
                print(f"WARNING: training data ({train_min.date()}..{train_max.date()}) "
                      f"doesn't overlap the match data ({match_min.date()}..{match_max.date()})")


    def load_narvaro(self, filename):
        """Parse a Närvaro (actual attendance) sibling CSV (4 fields: date,
        activity_id, player_name, state - written by sportadmin_scraper.py's
        --trainings run) into self.narvaro_df. "state" is the literal word
        "present" or "absent". Cross-referencing against self.training_df
        happens by date in _weekly_attendance_rates() - the two sources use
        disjoint activity-id namespaces (confirmed against the live site),
        so activity_id here is kept only for traceability, never joined on."""
        rows = []
        with open(filename, newline='') as csvfile:
            reader = csv.reader(csvfile, delimiter=',', quotechar='|', quoting=csv.QUOTE_MINIMAL)
            for row in reader:
                if len(row) < 4:
                    continue
                date_str, activity_id, name, state = row[:4]
                date = datetime.datetime.strptime(date_str, "%Y-%m-%d")
                rows.append([date, activity_id, name, state == "present"])

        columns = ["date", "activity_id", "player name", "present"]
        self.narvaro_df = pd.DataFrame(rows, columns=columns)
        self._resolve_narvaro_names()


    def _resolve_narvaro_names(self):
        """Confirmed live: Närvaro's frozen name column truncates long
        names to a fixed width plus a literal ".." (e.g. "Nikodemus
        Osterkamp .." for "Nikodemus Osterkamp Söderström") - without
        this, every such player's narvaro_df rows silently fail to join
        against training_df by name and read as 0% Närvaro. Remap any
        name ending in ".." to the one training_df name it's an unambiguous
        prefix of; leave it as-is (logging a warning) if that's not the
        case, rather than guessing."""
        if self.training_df is None:
            return
        full_names = self.training_df["player name"].unique()
        truncated = self.narvaro_df["player name"].str.endswith("..")
        mapping = {}
        for name in self.narvaro_df.loc[truncated, "player name"].unique():
            prefix = name[:-2]
            candidates = [f for f in full_names if f.startswith(prefix)]
            if len(candidates) == 1:
                mapping[name] = candidates[0]
            else:
                print(f"WARNING: Närvaro name {name!r} (truncated) matched "
                      f"{len(candidates)} training-data names {candidates}; "
                      f"leaving unresolved")
        if mapping:
            self.narvaro_df["player name"] = self.narvaro_df["player name"].replace(mapping)


    def _apply_obfuscation(self):
        """Replace real player names with Player_NN everywhere, using ONE
        mapping shared across self.df/self.training_df/self.narvaro_df (each
        if loaded) so the same person gets the same alias in every report."""
        if not self.args.obfuscate:
            return
        names = pd.concat([
            self.df['player name'],
            self.training_df['player name'] if self.training_df is not None else pd.Series(dtype=str),
            self.narvaro_df['player name'] if self.narvaro_df is not None else pd.Series(dtype=str),
        ]).unique()
        mapping = {name: f"Player_{i+1:02d}" for i, name in enumerate(names)}
        self.df['player name'] = self.df['player name'].map(mapping)
        if self.training_df is not None:
            self.training_df['player name'] = self.training_df['player name'].map(mapping)
        if self.narvaro_df is not None:
            self.narvaro_df['player name'] = self.narvaro_df['player name'].map(mapping)


    def analyze(self):

        self.played_distribution()
        self.available_distribution()
        self.played_multiples()
        if self.training_df is not None:
            self.training_attendance_trend()
        self.play_network()


    def played_multiples(self):
        self.multiples(self.df,
                       (ReportState.CALLED_COMING,),
                       """
                       Lista veckor som spelare dubblerat matcher och i
                       vilka serier de dubblerat vid varje tillfälle.
                       (matcher i andra åldersgrupper ej inräknade))
                       """)


    # minimum clique size for the client-side k-clique percolation
    # clustering in sportadmin_template.html (kept here as the single
    # source of truth, emitted into network_data as "clique_k")
    CLIQUE_K = 3

    def play_network(self):
        """Build a co-occurrence graph of which players actually played
        matches together and render it as an interactive force-directed
        graph (sportadmin.html). Clustering itself runs client-side,
        live, driven by the "minst antal matcher ihop" slider - see
        computeClusters()/recluster() in sportadmin_template.html."""

        played = self.df[self.df['ReportState'] == ReportState.CALLED_COMING]

        games_played = played.groupby('player name').size().to_dict()

        series_breakdown = collections.defaultdict(collections.Counter)
        for (player, series), count in played.groupby(['player name', 'series']).size().items():
            series_breakdown[player][series] = int(count)

        co_play = collections.Counter()
        for _, roster in played.groupby('match number')['player name']:
            names = sorted(set(roster))
            for a, b in itertools.combinations(names, 2):
                co_play[(a, b)] += 1

        node_ids = sorted(games_played.keys())
        edge_list = [(a, b, w) for (a, b), w in co_play.items()]

        nodes = []
        for player in node_ids:
            breakdown = series_breakdown[player]
            dominant = breakdown.most_common(1)[0][0] if breakdown else None
            nodes.append({
                "id": player,
                "games": games_played[player],
                "series": dict(breakdown),
                "dominant_series": dominant,
            })

        edges = [{"source": a, "target": b, "weight": w} for a, b, w in edge_list]

        network_data = {
            "season": self.season,
            "year": self.year,
            "matches": int(played['match number'].nunique()),
            "series_list": sorted(played['series'].unique().tolist()),
            "clique_k": self.CLIQUE_K,
            "nodes": nodes,
            "edges": edges,
            "played_table": self._series_breakdown_table(
                (ReportState.CALLED_COMING,), sort_by_total=False),
            "availability_table": self._series_breakdown_table(
                (ReportState.PRE_REPORT_AVAILABLE, ReportState.CALLED_COMING), sort_by_total=True),
            "multiples_table": self._multiples_table((ReportState.CALLED_COMING,)),
        }

        training_trend = self._build_training_trend_json()
        if training_trend is not None:
            network_data["training_trend"] = training_trend

        self._write_play_network_html(network_data)


    # ~2 months of trailing weekly training sessions (single source of
    # truth like CLIQUE_K, emitted into the HTML data as "window_weeks").
    TRAINING_ROLLING_WEEKS = 8

    @staticmethod
    def _iso_week_axis(start_date, end_date):
        """Continuous list of (iso_year, iso_week) tuples, one per calendar
        week, from start_date's week through end_date's week inclusive -
        so a week with zero training sessions (e.g. a holiday break) still
        gets its own entry rather than being silently skipped."""
        weeks = []
        cur = start_date - datetime.timedelta(days=start_date.weekday())
        last_monday = end_date - datetime.timedelta(days=end_date.weekday())
        while cur <= last_monday:
            weeks.append(cur.isocalendar()[:2])
            cur += datetime.timedelta(days=7)
        return weeks

    def _weekly_attendance_rates(self):
        """Return (weeks, rate_df, green_rate_df, red_rate_df). `weeks` is
        the continuous (iso_year, iso_week) axis covering self.training_df's
        full date range; every returned DataFrame has one row per week in
        `weeks` and one column per player.

        - rate_df ("blue"): (player's "Kommer" count in the window) /
          (every training session that actually occurred in the window,
          the same denominator for every player) * 100. Unaffected by
          Närvaro data (own, wider denominator - deliberate: blue is a
          self-report rate, not a presence rate).

        - green_rate_df/red_rate_df: (None, None) if self.narvaro_df is
          absent/empty, or if no training date has a matching Närvaro
          record. Otherwise both share a SEPARATE, smaller denominator:
          only sessions whose date also has Närvaro data (a session the
          coach hasn't logged attendance for yet is excluded from this
          denominator, not counted as 0 or as an automatic absence).
          - green: (player's Närvaro "present" count among matched
            sessions in the window) / matched-denominator * 100.
          - red: (player's count of matched sessions where they were
            EITHER present in Närvaro OR had an excused ("ok") "Kommer
            ej" row) / matched-denominator * 100 - the union is taken per
            (player, date) pair, before any weekly aggregation, so a
            player who is both present and separately marked excused for
            the same date is counted once, not twice. red >= green
            pointwise by construction.

        A window with zero sessions in its denominator is NaN; a window
        with sessions a player has no rows in is 0%, not NaN (they
        attended none of what happened)."""
        df = self.training_df
        weeks = self._iso_week_axis(df["date"].min(), df["date"].max())

        sessions = df.drop_duplicates("activity_id").groupby(["iso_year", "iso_week"]).size()
        sessions_per_week = pd.Series([int(sessions.get(w, 0)) for w in weeks], index=weeks)

        players = sorted(df["player name"].unique())

        def counts_by_week_player(filtered):
            counts = filtered.groupby(["iso_year", "iso_week", "player name"]).size()
            return pd.DataFrame(
                {p: [int(counts.get((y, w, p), 0)) for (y, w) in weeks] for p in players},
                index=weeks)

        kommer_per_player_week = counts_by_week_player(df[df["state"] == "Kommer"])

        def rolling_rate(counts_df, denom_series):
            rolling_counts = counts_df.rolling(self.TRAINING_ROLLING_WEEKS, min_periods=1).sum()
            rolling_denom = denom_series.rolling(self.TRAINING_ROLLING_WEEKS, min_periods=1).sum()
            rate = rolling_counts.div(rolling_denom, axis=0) * 100
            return rate.where(rolling_denom > 0)

        rate_df = rolling_rate(kommer_per_player_week, sessions_per_week)

        if self.narvaro_df is None or self.narvaro_df.empty:
            return weeks, rate_df, None, None

        matched_dates = set(df["date"].unique()) & set(self.narvaro_df["date"].unique())
        if not matched_dates:
            return weeks, rate_df, None, None

        train_m = df[df["date"].isin(matched_dates)]
        narvaro_m = self.narvaro_df[self.narvaro_df["date"].isin(matched_dates)]
        # every matched date has exactly one (iso_year, iso_week) - lift it
        # from training_df rather than recomputing isocalendar() again.
        date_to_yw = dict(zip(train_m["date"], zip(train_m["iso_year"], train_m["iso_week"])))

        narvaro_sessions = train_m.drop_duplicates("activity_id").groupby(
            ["iso_year", "iso_week"]).size()
        narvaro_sessions_per_week = pd.Series(
            [int(narvaro_sessions.get(w, 0)) for w in weeks], index=weeks)

        def counts_from_pairs(pairs):
            counts = collections.Counter()
            for date, player in pairs:
                yw = date_to_yw.get(date)
                if yw is None:
                    continue
                counts[(yw[0], yw[1], player)] += 1
            return pd.DataFrame(
                {p: [counts.get((y, w, p), 0) for (y, w) in weeks] for p in players},
                index=weeks)

        present_pairs = set(zip(narvaro_m.loc[narvaro_m["present"], "date"],
                                narvaro_m.loc[narvaro_m["present"], "player name"]))
        excused_mask = (train_m["state"] == "Kommer ej") & (train_m["excused"] == "ok")
        excused_pairs = set(zip(train_m.loc[excused_mask, "date"],
                                train_m.loc[excused_mask, "player name"]))
        # Union BEFORE weekly aggregation - a (date, player) pair present in
        # both sets still contributes exactly one occurrence.
        union_pairs = present_pairs | excused_pairs

        present_per_player_week = counts_from_pairs(present_pairs)
        union_per_player_week = counts_from_pairs(union_pairs)

        green_rate_df = rolling_rate(present_per_player_week, narvaro_sessions_per_week)
        red_rate_df = rolling_rate(union_per_player_week, narvaro_sessions_per_week)
        return weeks, rate_df, green_rate_df, red_rate_df

    def training_attendance_trend(self):
        """Console report: one row per player with a block-character
        sparkline + latest value for each available curve - "Kommer"
        (blue, self-reported RSVP, always present), "Närvaro" (green,
        actual attendance) and "Närvaro+ursäkt" (red, Närvaro plus an
        excused "Kommer ej", never double-counting a player who is both).
        The Närvaro/Närvaro+ursäkt columns are omitted entirely (not
        blank) when no Närvaro sibling file was loaded, so files without
        one render exactly as before this feature existed. Same
        pretty_print()/DataFrame-of-rendered-strings idiom as
        distribution_by_series()'s ASCII bars."""
        weeks, rate_df, green_rate_df, red_rate_df = self._weekly_attendance_rates()
        if not weeks:
            return

        blocks = "▁▂▃▄▅▆▇█"
        def sparkline(values):
            chars = []
            for v in values:
                if pd.isna(v):
                    chars.append(" ")
                else:
                    level = min(7, max(0, round(v / 100 * 7)))
                    chars.append(blocks[level])
            return "".join(chars)

        def last_str(series):
            last = series.iloc[-1]
            return last, (f"{last:.0f}%" if pd.notna(last) else "-")

        have_narvaro = green_rate_df is not None and red_rate_df is not None

        rows = []
        for player in rate_df.columns:
            blue_last, blue_str = last_str(rate_df[player])
            row = {
                "player name": player,
                "Kommer": sparkline(rate_df[player].tolist()),
                "Kommer %": blue_str,
            }
            sort_key = blue_last
            if have_narvaro:
                green_last, green_str = last_str(green_rate_df[player])
                red_last, red_str = last_str(red_rate_df[player])
                row["Närvaro"] = sparkline(green_rate_df[player].tolist())
                row["Närvaro %"] = green_str
                row["Närvaro+ursäkt"] = sparkline(red_rate_df[player].tolist())
                row["Närvaro+ursäkt %"] = red_str
                sort_key = red_last
            row["_sort"] = sort_key if pd.notna(sort_key) else -1
            rows.append(row)
        out_df = pd.DataFrame(rows).sort_values(
            ["_sort", "player name"], ascending=[False, True]).drop(columns="_sort")

        weekday_note = ""
        weekday_set = getattr(self.args, "weekday_set", None)
        if weekday_set:
            inv = {v: k for k, v in WEEKDAY_MAP.items()}
            weekday_note = (" Endast " + ", ".join(inv[d] for d in sorted(weekday_set))
                            + "-träningar är medräknade.")

        denom_note = (" \"Kommer\" räknas mot samtliga träningar som faktiskt "
                      "genomfördes under fönstret; \"Närvaro\"/\"Närvaro+ursäkt\" "
                      "räknas mot de av dem där närvaro faktiskt registrerats."
                      if have_narvaro else
                      " Andelen räknas mot samtliga träningar som faktiskt "
                      "genomfördes under fönstret.")

        self.pretty_print(out_df, False, f"""
                          Glidande {self.TRAINING_ROLLING_WEEKS}-veckors snitt av
                          träningsnärvaro per spelare, vecka för vecka.
                          {denom_note}
                          {weekday_note}
                          """)

    def _build_training_trend_json(self):
        """JSON-friendly equivalent of training_attendance_trend(), for the
        HTML chart. None if no training data was loaded. Each row always
        carries "values"/"last" (blue, Kommer). When a Närvaro sibling
        file was loaded and has at least one matched session, it also
        carries "narvaro_values"/"narvaro_last" (green, actual presence)
        and "adjusted_values"/"adjusted_last" (red, presence OR excused
        Kommer-ej - same field names the chart has always used for its
        second curve, now sourced from Närvaro instead of Kommer+excused),
        and "last"/the sort key switch to the red curve as the headline
        number."""
        if self.training_df is None or self.training_df.empty:
            return None
        weeks, rate_df, green_rate_df, red_rate_df = self._weekly_attendance_rates()
        if not weeks:
            return None

        week_labels = [f"{y}-W{w:02d}" for (y, w) in weeks]
        have_narvaro = green_rate_df is not None and red_rate_df is not None

        def to_values(series):
            return [None if pd.isna(v) else round(float(v), 1) for v in series]

        def last_of(values):
            return next((v for v in reversed(values) if v is not None), None)

        rows = []
        for player in rate_df.columns:
            values = to_values(rate_df[player])
            last = last_of(values)
            row = {"player": player, "values": values, "last": last}
            if have_narvaro:
                green_values = to_values(green_rate_df[player])
                red_values = to_values(red_rate_df[player])
                row["narvaro_values"] = green_values
                row["narvaro_last"] = last_of(green_values)
                row["adjusted_values"] = red_values
                row["adjusted_last"] = last_of(red_values)
                row["last"] = row["adjusted_last"]
            rows.append(row)
        rows.sort(key=lambda r: (-(r["last"] if r["last"] is not None else -1), r["player"]))

        weekday_set = getattr(self.args, "weekday_set", None)
        weekdays = None
        if weekday_set:
            inv = {v: k for k, v in WEEKDAY_MAP.items()}
            weekdays = [inv[d] for d in sorted(weekday_set)]

        return {
            "window_weeks": self.TRAINING_ROLLING_WEEKS,
            "weekdays": weekdays,
            "weeks": week_labels,
            "rows": rows,
        }


    def _multiples_table(self, states):
        """Player x double-booked-week list for the given ReportState set,
        JSON-friendly equivalent of multiples()'s ASCII table: which
        players played more than one match in the same week, how many
        times, and which series were involved each time."""

        filtered_df = self.df[self.df['ReportState'].isin(states)]
        grouped = filtered_df.groupby(["player name", "week"]).agg(
            count=('week', 'size'),
            series_names=('series', lambda x: ','.join(sorted(x)))
        )
        doubled = grouped[grouped['count'] > 1].reset_index()

        rows = [{"player": player, "count": len(group), "occasions": group['series_names'].tolist()}
                for player, group in doubled.groupby('player name')]
        rows.sort(key=lambda r: (-r['count'], r['player']))
        return rows


    def _series_breakdown_table(self, states, sort_by_total):
        """Player x series match-count table for the given ReportState set,
        JSON-friendly equivalent of distribution_by_series()'s ASCII bar
        chart (same grouping/sort semantics, plain data instead of bars).
        Only players with at least one qualifying match appear. Each cell
        is split into home/away counts (see --home-locations) so the page
        can render them in two colors."""

        filtered_df = self.df[self.df['ReportState'].isin(states)]
        counts = collections.defaultdict(lambda: collections.defaultdict(lambda: {"home": 0, "away": 0}))
        for (player, series, home_away), count in filtered_df.groupby(['player name', 'series', 'home_away']).size().items():
            counts[player][series][home_away] = int(count)

        series_columns = sorted({s for c in counts.values() for s in c})
        players = list(counts.keys())

        def cell_total(p, s):
            c = counts[p].get(s, {})
            return c.get("home", 0) + c.get("away", 0)

        if sort_by_total:
            players.sort(key=lambda p: (-sum(cell_total(p, s) for s in series_columns), p))
        else:
            players.sort(key=lambda p: tuple(-cell_total(p, s) for s in series_columns) + (p,))

        rows = [{"player": p,
                 "counts": {s: {"home": counts[p].get(s, {}).get("home", 0),
                                "away": counts[p].get(s, {}).get("away", 0)}
                            for s in series_columns}}
                for p in players]
        return {"series_columns": series_columns, "rows": rows}


    def _write_play_network_html(self, network_data):
        template_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sportadmin_template.html")
        with open(template_path, encoding="utf-8") as f:
            template = f.read()

        # guard against a "</script" substring in the data breaking out of
        # the inline <script> tag it gets embedded in
        json_str = json.dumps(network_data, ensure_ascii=False).replace("</", "<\\/")

        page_title = f"Spelmönster {network_data['season'].upper()} {network_data['year']}"

        html_out = template.replace("__NETWORK_DATA_JSON__", json_str)
        html_out = html_out.replace("__PAGE_TITLE__", html.escape(page_title))

        # Output filename mirrors the input CSV's basename (e.g.
        # sportadmin_vår_2025.csv -> sportadmin_vår_2025.html), placed in
        # ANALYZER_OUT_DIR/--out-dir (or the current directory if unset).
        basename = os.path.splitext(os.path.basename(self.args.input))[0]
        out_dir = getattr(self.args, "out_dir", "") or ""
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, basename + ".html") if out_dir else basename + ".html"

        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html_out)

        generate_index.build_index(out_dir or ".")


    def played_distribution(self):
        self.distribution_by_series(self.df,
                          (ReportState.CALLED_COMING,),
                          """
                          Fördelning av spelade matcher per serie.
                          Varje streck är en match.
                          """)

        self.distribution_by_week(self.df,
                          (ReportState.CALLED_COMING,),
                          """
                          Fördelning av spelade matcher per serie och vecka.
                          """)


    def available_distribution(self):
        self.distribution_by_series(self.df,
                          (ReportState.PRE_REPORT_AVAILABLE,
                          ReportState.CALLED_COMING),
                          """
                          Fördelning av tillgänglighet
                          (förhandsrapporterad som "tillgänglig" eller faktiskt spelat).
                          Varje streck är en match. Syftet med tabellen är att
                          förstå om det finns spelare som aktivt undviker att
                          anmäla sig till eller spela i vissa serier.
                          """,
                          sort_by_total=True)


    def multiples(self, df, states, description):

        filtered_df = df[df['ReportState'].isin(states)]

        # group and count "matches/week, but include which series were played"
        # use counting operator 'size' on the 'week' column to count the number of items in each group
        grouped_df = filtered_df.groupby(["player name", "week"]).agg(
            count=('week', 'size'),
            series_names=('series', lambda x: ','.join(sorted(x)))
            )
        # don't append .reset_index() above to avoid flattening to a regular DataFrame
        # and instead keep the MultiIndex format which enables automatic pretty printing
        # in groups by "player name"
        filtered_df = grouped_df[grouped_df['count'] > 1]
        #print(filtered_df)

        # flatten to a regular DataFrame
        filtered_df = filtered_df.reset_index()

        # group and count "number of times more than one match/week was played
        multiples_df = filtered_df.groupby(["player name"]).agg(
            count=('player name', 'size'),
            series_names=('series_names', lambda x: ' : '.join(x))
            ).reset_index()
        sorted_df = multiples_df.sort_values(by='count', ascending=False)

        self.pretty_print(sorted_df, False, description)


    def distribution_by_series(self, df, states, description, sort_by_total=False):

        filtered_df = df[df['ReportState'].isin(states)]

        # Group by "series", "player name" and home/away, then count the occurrences
        grouped_df = filtered_df.groupby(["series", "player name", "home_away"]).size().reset_index(name='count')

        # Pivot home and away matches into separate tables so each cell's
        # bar can show both counts, then align them onto the same
        # (player, series) grid via a union of both pivots' index/columns.
        home_pivot = grouped_df[grouped_df['home_away'] == 'home'] \
            .pivot(index="player name", columns="series", values="count")
        away_pivot = grouped_df[grouped_df['home_away'] == 'away'] \
            .pivot(index="player name", columns="series", values="count")

        all_players = sorted(set(home_pivot.index) | set(away_pivot.index))
        all_series = sorted(set(home_pivot.columns) | set(away_pivot.columns))
        home_pivot = home_pivot.reindex(index=all_players, columns=all_series, fill_value=0).fillna(0)
        away_pivot = away_pivot.reindex(index=all_players, columns=all_series, fill_value=0).fillna(0)

        series_columns = list(home_pivot.columns)
        total_pivot = home_pivot + away_pivot

        if sort_by_total:
            # Sort by the sum across all series (descending), then player name ascending
            order = total_pivot.sum(axis=1).reset_index(name='total') \
                .sort_values(by=['total', 'player name'], ascending=[False, True])
        else:
            # Sort by series in descending order, and then player name in ascending order
            order = total_pivot.reset_index().sort_values(by=series_columns + ['player name'],
                                     ascending=[False] * len(series_columns) + [True])
        sorted_players = order['player name'].tolist()

        home_pivot = home_pivot.loc[sorted_players]
        away_pivot = away_pivot.loc[sorted_players]

        # Home matches are '|', away matches are '.' - each streck is a match.
        def ascii_bar(home, away):
            return '|' * int(home) + '.' * int(away)

        column_df = pd.DataFrame(index=sorted_players, columns=series_columns)
        column_df.index.name = "player name"
        for column in series_columns:
            column_df[column] = [ascii_bar(home_pivot.loc[p, column], away_pivot.loc[p, column])
                                  for p in sorted_players]

        self.pretty_print(column_df, True, description)


    def distribution_by_week(self, df, states, description):

        # .copy() the filtered DataFrame to avoid
        # SettingWithCopyWarning: A value is trying to be set on a copy of a slice from a DataFrame.
        filtered_df = df[df['ReportState'].isin(states)].copy()

        # Merge "week number" and "series name" into a unique identifier
        filtered_df['week_series'] = filtered_df['week'].astype(str) + '_' + filtered_df['series']

        # A player can have more than one match in the same series in the
        # same calendar week (e.g. a rescheduled match) - plain pivot() can't
        # put two values in one cell and crashes ("duplicate entries, cannot
        # reshape"). Report it, then combine them with ',' instead of losing
        # the extra match.
        duplicates = filtered_df.duplicated(subset=['player name', 'week_series'])
        if duplicates.any():
            for _, dup_row in filtered_df.loc[duplicates, ['player name', 'week_series']].drop_duplicates().iterrows():
                print(f"Multiple matches in same week: {dup_row['player name']} / {dup_row['week_series']}")

        # Define a function to set the 'entry' value based on 'series'
        # Apply the function to create the 'entry' column
        filtered_df['entry'] = filtered_df['series'].apply(lambda x: x)

        # Pivot the DataFrame with "player name" as rows, "week_series" as columns;
        # join multiple entries in the same cell with ',' instead of crashing.
        # Missing combinations (no match that week) are filled with ''.
        pivot_df = filtered_df.pivot_table(index='player name', columns='week_series', values='entry',
                                            aggfunc=lambda vals: ','.join(vals), fill_value='')

        self.pretty_print(pivot_df, True, description)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Analyze player data exported from SportAdmin")

    # Add arguments
    parser.add_argument('input', type=str,
                         help="The raw scraped CSV to analyze, or a directory - "
                              "every *.csv file directly inside it is then analyzed in turn")
    parser.add_argument('-o', '--obfuscate', action="store_true", help="Obfuscate the player names in the output graphs")
    parser.add_argument('--home-locations', nargs='+', metavar="PATTERN", default=None,
                         help="Regex pattern(s) matched against a match's location; any match "
                              "counts the match as home, everything else as away. Defaults to "
                              "HOME_LOCATIONS from .settings (comma-separated) if not given.")
    parser.add_argument('--out-dir', metavar="DIR", default=None,
                         help="Output directory for the network graph HTML, named "
                              "after the input CSV (e.g. foo.csv -> DIR/foo.html); "
                              "default: ANALYZER_OUT_DIR from .settings, or the "
                              "current directory if unset")
    parser.add_argument('--season', choices=SEASONS, default=None,
                         help="Only analyze this season. Without it, all "
                              "data in the file is analyzed together.")
    parser.add_argument('--list', action="store_true",
                         help="List the seasons present in the input CSV and exit")
    parser.add_argument('--weekdays', metavar="LIST", default=None,
                         help="Comma-separated Swedish weekday abbreviations "
                              "(mån,tis,ons,tor,fre,lör,sön; case-insensitive, "
                              "whitespace trimmed) restricting which weekdays' "
                              "training sessions count toward the sliding-"
                              "average attendance report. Applies ONLY to "
                              "that report, never to the match-based ones. "
                              "Default: every weekday.")

    # Parse the arguments
    args = parser.parse_args()

    try:
        args.weekday_set = parse_weekdays(args.weekdays)
    except ValueError as e:
        parser.error(str(e))

    input_files = resolve_input_files(args.input)
    if not input_files:
        raise SystemExit(f"No .csv files found in {args.input!r}")

    if args.list:
        order = {s: i for i, s in enumerate(SEASONS)}
        seasons = set()
        for f in input_files:
            seasons |= detect_seasons(f)
        for season in sorted(seasons, key=lambda s: order[s]):
            print(season)
        raise SystemExit(0)

    if args.home_locations is None:
        raw = load_settings().get("HOME_LOCATIONS", "")
        args.home_locations = [p.strip() for p in raw.split(",") if p.strip()]

    if args.out_dir is None:
        args.out_dir = load_settings().get("ANALYZER_OUT_DIR", "").strip()

    batch = len(input_files) > 1
    analyzed = 0
    for csv_path in input_files:
        if batch:
            print(f"\n### {csv_path} ###")
        file_args = argparse.Namespace(**vars(args))
        file_args.input = csv_path
        sp = SportadminGamesAnalyzer(file_args)
        try:
            sp.load(csv_path)
        except NoMatchingSeasonError as e:
            # A directory scan expects some files not to have the requested
            # season - skip those and keep going; a single explicit file
            # with no matching rows is a real usage error, so still fail.
            if batch:
                print(f"WARNING: {e}; skipping")
                continue
            raise SystemExit(f"ERROR: {e}")

        training_path = training_sibling_path(csv_path)
        if os.path.exists(training_path):
            sp.load_training(training_path)

        narvaro_path = narvaro_sibling_path(csv_path)
        if sp.training_df is not None and os.path.exists(narvaro_path):
            sp.load_narvaro(narvaro_path)

        sp._apply_obfuscation()
        sp.analyze()
        analyzed += 1

    if batch and analyzed == 0:
        raise SystemExit(f"ERROR: no files in {args.input!r} matched season {args.season!r}")
