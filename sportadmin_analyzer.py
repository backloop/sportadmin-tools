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

class ReportState(IntEnum):
    PRE_REPORT_AVAILABLE = 1
    PRE_REPORT_NOT_AVAILABLE = 2
    PRE_REPORT_NOT_REPORTED = 3
    CALLED_COMING = 4
    CALLED_NOT_COMING = 5
    CALLED_NO_ANSWER = 6

class SportadminGamesAnalyzer:

    def __init__(self, args):
        self.args = args

    def pretty_print(self, df, show_index, description, filename):

        with io.StringIO() as df_io:
            # !show_index - Print the DataFrame without the row index
            print(df.to_string(index=show_index), file=df_io)
            df_string = df_io.getvalue()
            max_width = max(len(line) for line in df_string.splitlines())

        # dedent and then remove any leading newlines
        dedented_description = textwrap.dedent(description).strip()
        formatted_description= textwrap.fill(dedented_description, width=max_width)

        with io.StringIO() as buffer:
            # Redirect stdout to the buffer
            original_stdout = sys.stdout
            sys.stdout = buffer

            print("")
            print("="*max_width)
            print(f"{self.season.upper()} {self.year}")
            print("")
            print(formatted_description)
            print("")
            print(df_string)
            print("="*max_width)

            # Reset stdout to its original value
            sys.stdout = original_stdout

            # Get the value from the buffer
            captured_output = buffer.getvalue()

            print(captured_output)
            escaped_output = html.escape(captured_output)

        # Step 4: Wrap the output in a <pre> tag to preserve formatting
        html_output = f"<html><body><pre>{escaped_output}</pre></body></html>"

        # Step 5: Write the HTML content to a file
        with open(filename, "w") as file:
            file.write(html_output)


    def load(self, filename):

        #
        # Read all data and add some calculated columns
        #

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

                # create week number
                week_num = datetime.date.fromisoformat(row[0]).isocalendar()[1]

                # convert to date
                date = datetime.datetime.strptime(row[0], "%Y-%m-%d")

                # extract the series differentiator
                series = re.sub(r"[PF][0-9]{2} [A-Ö-a-ö]+ ([A-D][1-5]), (vår|höst)", r"\g<1>", row[2])

                # remove trailing comment with syntax "<first name> [middle name] <last name> - <comment>"
                row[3] = re.sub(r"(.*) - .*", r"\g<1>", row[3])

                # create verbose report state
                report_state = ReportState(int(row[4]))

                header = []
                # add new columns
                row.insert(0, date)
                row.insert(1, week_num)
                row.insert(2, report_state)
                row.insert(3, series)
                data.append(row)

            #header = ["date", "match number", "series name", "player name", "ReportState", "available", "not available", "not reported", "coming", "not coming", "not answered"]
            header = ["date", "match number", "series name", "player name", "report state"]
            header.insert(0, "date")
            header.insert(1, "week")
            header.insert(2, "ReportState")
            header.insert(3, "series")

        # filter on season
        self.season = input("Which season [vår, höst]? ")
        if not self.season in ("vår", "höst"):
            print("ERROR: Incorrect season")
            exit(1)
        data = filter(lambda row: self.season in row[6], data)

        # filter out external players, where player names start with "- <first name> <last name>"
        #data = filter(lambda row: row[7][:2] != "- ", data)

        # expand the filter, otherwise after a single walkthrough the iterator is exhausted
        data = list(data)

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


    def played_multiples(self):
        self.multiples(self.df,
                       (ReportState.CALLED_COMING,),
                       """
                       Lista veckor som spelare dubblerat matcher och i
                       vilka serier de dubblerat vid varje tillfälle.
                       (matcher i andra åldersgrupper ej inräknade))
                       """,
                       "played_multiples.html")


    def played_distribution(self):
        self.distribution(self.df,
                          (ReportState.CALLED_COMING,),
                          """
                          Fördelning av spelade matcher per serie.
                          Varje streck är en match.
                          """,
                          "played_distribution_total.html")

        self.distribution_per_week(self.df,
                          (ReportState.CALLED_COMING,),
                          """
                          Fördelning av spelade matcher per serie och vecka.
                          """,
                          "played_distribution_per_week.html")


    def available_distribution(self):
        self.distribution(self.df,
                          (ReportState.PRE_REPORT_AVAILABLE,
                          ReportState.CALLED_COMING,
                          ReportState.CALLED_NOT_COMING),
                          """
                          Fördelning av anmäld tillgänglighet
                          (förhandsrapporterad som "tillgänglig", eller faktiskt spelat).
                          Varje streck är en match.
                          """,
                          "available_distribution.html")


    def multiples(self, df, states, description, html_filename):

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

        self.pretty_print(sorted_df, False, description, html_filename)


    def distribution(self, df, states, description, html_filename):

        filtered_df = df[df['ReportState'].isin(states)]

        # Group by "series" and "player name", then count the occurrences
        grouped_df = filtered_df.groupby(["series", "player name"]).size().reset_index(name='count')

        # Sort by "series" first and then by "count" within each "series" in descending order
        sorted_df = grouped_df.sort_values(by=["series", "count", "player name"], ascending=[True, False, True])
        #print(sorted_df)

        # Pivot the data so that "series" becomes columns, and "player name" remains as the index
        pivot_df = grouped_df.pivot(index="player name", columns="series", values="count")

        # Optionally, fill missing values (if any player doesn't have data in some series) with 0
        pivot_df = pivot_df.fillna(0)
        #print(pivot_df)

        # First, get a list of the series columns
        series_columns = list(pivot_df.columns)

        # Sort by series in descending order, and then player name in ascending order
        column_df = pivot_df.sort_values(by=series_columns + ['player name'],
                                 ascending=[False] * len(series_columns) + [True])
        #print(column_df)

        # Define a function to convert integer values to ASCII bars
        def ascii_bar(value, max_value=10, bar_char='|'):
            # Scale the bar length relative to the maximum value, adjust as needed
            #bar_length = int((value / column_df[series_columns].values.max()) * max_value)
            bar_length = int(value)
            return bar_char * bar_length

        # Apply the ASCII bar transformation to the "series" columns
        #ascii_styled_df = column_df.applymap(lambda x: ascii_bar(x) if x > 0 else "")
        #print(ascii_styled_df)

        # Apply the ASCII bar transformation only to the "series" columns
        for column in series_columns:
            column_df[column] = column_df[column].apply(lambda x: ascii_bar(x))

        self.pretty_print(column_df, True, description, html_filename)


    def distribution_per_week(self, df, states, description, html_filename):

        # .copy() the filtered DataFrame to avoid
        # SettingWithCopyWarning: A value is trying to be set on a copy of a slice from a DataFrame.
        filtered_df = df[df['ReportState'].isin(states)].copy()

        # Merge "week number" and "series name" into a unique identifier
        filtered_df['week_series'] = filtered_df['week'].astype(str) + '_' + filtered_df['series']

        # Check for duplicates
        duplicates = filtered_df.duplicated(subset=['player name', 'week_series'])
        print("Duplicates found:", duplicates.any())

        # Define a function to set the 'entry' value based on 'series'
        # Apply the function to create the 'entry' column
        filtered_df['entry'] = filtered_df['series'].apply(lambda x: x)

        # Pivot the DataFrame with "player name" as rows, "week_series" as columns, and 1 for entries
        pivot_df = filtered_df.pivot(index='player name', columns='week_series', values='entry')

        # Fill missing values (indicating no entry) with 0
        pivot_df = pivot_df.fillna('')

        self.pretty_print(pivot_df, True, description, html_filename)


if __name__ == "__main__":
    print("Hello World!")

    parser = argparse.ArgumentParser(description="Analyze player data exported from SportAdmin")

    # Add arguments
    parser.add_argument('-o', '--obfuscate', action="store_true", help="Obsfuscate the player names in the output graphs")

    # Parse the arguments
    args = parser.parse_args()

    sp = SportadminGamesAnalyzer(args)
    sp.load("sportadmin.csv")
    sp.analyze()
