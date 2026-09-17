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

from settings import load_settings
import generate_index


SEASONS = ("vår", "höst", "vinter")


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
            header = ["date", "match number", "series name", "player name", "report state", "location"]
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
            print(f"ERROR: no rows found for season {self.season!r} in {filename}. "
                  f"Seasons present: {', '.join(available) if available else '(none detected)'}")
            exit(1)

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

        if self.args.obfuscate:
            # Get unique player names
            unique_players = self.df['player name'].unique()

            # Create a mapping from actual player names to obfuscated names
            player_mapping = {name: f"Player_{i+1:02d}" for i, name in enumerate(unique_players)}

            # Apply the mapping to the 'player name' column
            self.df['player name'] = self.df['player name'].map(player_mapping)

    def analyze(self):

        self.available_distribution()
        self.played_distribution()
        self.played_multiples()
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
            "availability_table": self._series_breakdown_table(
                (ReportState.PRE_REPORT_AVAILABLE, ReportState.CALLED_COMING), sort_by_total=True),
            "played_table": self._series_breakdown_table(
                (ReportState.CALLED_COMING,), sort_by_total=False),
            "multiples_table": self._multiples_table((ReportState.CALLED_COMING,)),
        }

        self._write_play_network_html(network_data)


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
    parser.add_argument('input', type=str, help="The raw scraped CSV to analyze")
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

    # Parse the arguments
    args = parser.parse_args()

    if args.list:
        order = {s: i for i, s in enumerate(SEASONS)}
        for season in sorted(detect_seasons(args.input), key=lambda s: order[s]):
            print(season)
        raise SystemExit(0)

    if args.home_locations is None:
        raw = load_settings().get("HOME_LOCATIONS", "")
        args.home_locations = [p.strip() for p in raw.split(",") if p.strip()]

    if args.out_dir is None:
        args.out_dir = load_settings().get("ANALYZER_OUT_DIR", "").strip()

    sp = SportadminGamesAnalyzer(args)
    sp.load(args.input)
    sp.analyze()
