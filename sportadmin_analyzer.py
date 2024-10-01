#!/usr/bin/python3

import time
from enum import IntEnum, auto
import csv
import sys
import datetime
import re
import pandas as pd

class ReportState(IntEnum):
    PRE_REPORT_AVAILABLE = 1
    PRE_REPORT_NOT_AVAILABLE = 2
    PRE_REPORT_NOT_REPORTED = 3
    CALLED_COMING = 4
    CALLED_NOT_COMING = 5
    CALLED_NO_ANSWER = 6

class SportadminGamesAnalyzer:

    def analyze(self):

        #
        # Read all data and add some calculated columns
        #

        with open('sportadmin.csv', newline='') as csvfile:

            data = []

            reader = csv.reader(csvfile, delimiter=',', quotechar='|', quoting=csv.QUOTE_MINIMAL)
            # by using the csv module to read the file
            # we get back correct quotations and delimiters

            for row in reader:
                # separate ReportState into separate columns
                cols = [0] * 6
                cols[int(row[-1])-1] = 1
                row.extend(cols)

                # create week number
                week_num = datetime.date.fromisoformat(row[0]).isocalendar()[1]

                # convert to date
                date = datetime.datetime.strptime(row[0], "%Y-%m-%d")

                # extrax the series differentiator
                series = re.sub(r"P10 Sydvästra ([ABCD])[123], höst", r"\g<1>", row[2])

                # create verbose report state
                report_state = ReportState(int(row[4]))

                header = []
                # add new columns
                row.insert(0, date)
                row.insert(1, week_num)
                row.insert(2, report_state)
                row.insert(3, series)
                data.append(row)

            header = ["date", "match number", "series name", "player name", "ReportState", "available", "not available", "not reported", "coming", "not coming", "not answered"]
            header.insert(0, "date")
            header.insert(1, "week")
            header.insert(2, "report state")
            header.insert(3, "series")

        # filter on date range
        data = filter(lambda row: "höst" in row[6], data[1:])

        # filter on actually played the game
        data = filter(lambda row: row[2] == ReportState.CALLED_COMING, data)

        # filter out P2015
        data = filter(lambda row: row[7][:2] != "- ", data)

        # exapand the filter, othewise after a single walkthrough the iterator will need to be reset
        data = list(data)

        for row in data:
            print(row)

        print(f"len: {len(data)}")
        print(header)

        df = pd.DataFrame(data, columns = header)
        #print(df.describe())

        self.distribution(df)
        self.multiples(df)


    def multiples(self, df):
        # group and count "matches/week"
        #grouped_df = df.groupby(["player name", "week"]).size()
        #filtered_df= grouped_df[grouped_df > 1]
        #print(filtered_df)

        # group and count "matches/week, but include which series were played"
        # use counting operator 'size' on the 'week' column to count the number of items in each group
        grouped_df = df.groupby(["player name", "week"]).agg(
            count=('week', 'size'),
            series_names=('series', lambda x: ','.join(sorted(x)))
            )
        # don't append .reset_index() above to avoid flattening to a regular DataFrame
        # and instead keep the MultiIndex format which enables automatic pretty printing
        # in groups by "player name"
        filtered_df = grouped_df[grouped_df['count'] > 1]
        print(filtered_df)

        # flatten to a regular DataFrame
        filtered_df = filtered_df.reset_index()

        # group and count "number of times more than one match/week was played
        multiples_df = filtered_df.groupby(["player name"]).agg(
            count=('player name', 'size'),
            series_names=('series_names', lambda x: ' : '.join(x))
            ).reset_index()
        sorted_df = multiples_df.sort_values(by='count', ascending=False)
        print(sorted_df)



    def distribution(self, df):
        # Group by "series" and "player name", then count the occurrences
        grouped_df = df.groupby(["series", "player name"]).size().reset_index(name='count')

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
        print(column_df)


if __name__ == "__main__":
    print("Hello World!")

    sp = SportadminGamesAnalyzer()
    sp.analyze()
