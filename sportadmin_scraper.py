#!/usr/bin/python3

import re
from playwright.sync_api import Playwright, sync_playwright, expect, TimeoutError as PlaywrightTimeoutError

import time
import locale
from datetime import datetime
import argparse

from enum import IntEnum, auto
import csv
import sys
import traceback
import os

from credentials import load_credentials

DEFAULT_TIMEOUT = 10_000

#
# pipenv run playwright codegen https://www.sportadmin.se
# pipenv run playwright codegen --target python https://www.sportadmin.se
#

#
# Should present "console" for interactive exploration, but does not
# PWDEBUG=1 pipenv run python inspector_setup.py
#

#
# pipenv run python sportadmin_scraper.py <email> <password>
#

# XPath tootls:
# 1. chrome DevTools, Elements tab, ctrl-f, "Find by string, selector or XPtah
# 2. https://devhints.io/xpath
#

def strikethrough(text: str) -> str:
    return ''.join(char + '\u0336' for char in text)

def pretty_print(element):
    print(element.get_attribute('outerHTML'))
    outer = element.evaluate("node => node.outerHTML")
    print(outer)
    print(element.inner_html())

#
# Date in activity details page have some different formats
# depending on the actual month
#
def parseDate(date):
    formats = (
            "%a %d %b. %H:%M",
            "%a %d %b %H:%M",
            "%a %d %B %H:%M",
            )

    for f in formats:
        try:
            return datetime.strptime(date, f)
        except:
            pass

    raise Exception("No matching format found for " + date)


class SportadminGamesScraper:

    def __init__(self, playwright:Playwright):
        # Chromium can sporadically fail to launch in this environment; retry a few times.
        headless_env = os.getenv("HEADLESS", "0").strip().lower()
        headless = headless_env in ("1", "true", "yes", "y")

        last_err = None
        launch_args = ["--no-sandbox", "--disable-setuid-sandbox"]
        for _ in range(5):
            try:
                self.browser = playwright.chromium.launch(headless=headless, args=launch_args)
                last_err = None
                break
            except Exception as e:
                last_err = e
                time.sleep(1)
        if last_err is not None:
            raise last_err
        self.context = self.browser.new_context()
        self.page = self.context.new_page()

        # Set Swedish locale for date parsing
        locale.setlocale(locale.LC_TIME, "sv_SE.UTF-8")

        #self.page.set_default_timeout(DEFAULT_TIMEOUT)
        #self.page.set_default_navigation_timeout(DEFAULT_TIMEOUT)

        self.curr_month_year = None

        self.series_count = 0
        self.series_idx = -1

        self.row_count = 0
        self.row_idx = 0

        self.series_name = ""
        self.year_label = None
        self.period_set = False


    def wait_for_loading_bar_to_complete(self):
        loader = self.page.locator(".viewport-frame-wrapper:has(#vpframe_1) .frame-loader")
        try:
            loader.wait_for(state="hidden", timeout=DEFAULT_TIMEOUT)
        except TimeoutError:
            pass

    def wait_for_matches_iframe_to_complete(self):
        #print("wait for table in iframe to complete loading")
        frame = self.page.frame_locator("#vpframe_3") \
                         .frame_locator("iframe[name=\"printa\"]")
        frame.locator("#tblMain").wait_for()

        ## wait for matches page to update

        #
        # NOTE:
        # These frame elements sporadiacally fail.
        # ChatGPT states that the iframe is reloaded
        # and therefore the inne_frame handles are invalid
        #

        #iframe_el = self.page.wait_for_selector("#vpframe_3")
        #outer_frame = iframe_el.content_frame()

        #inner_iframe_el = outer_frame.wait_for_selector("iframe[name='printa']")
        #inner_frame = inner_iframe_el.content_frame()
        ## finished too quickly, the effect of clicking a
        ## series button has not always filtered of the data.
        ## somtimes 89 rows are read instead of 13 (of which 8
        ## are actual matches)
        ##inner_frame.wait_for_load_state("domcontentloaded")
        #inner_frame.wait_for_load_state("load")
        #inner_frame.wait_for_load_state("networkidle")

        #
        # NOTE:
        # Waiting for a specific load_state  is not a definitive
        # indicator that the element transformations have completed
        # (Javascript activities are not monitored).
        #
        # Instead, the most reliable way seems to poll and
        # check that the row count has stabilized. Not the
        # solution I was hoping for ;(
        #

        # read match table rows
        rows = frame.locator("#tblMain tr")

        # try to monitor when the javascript manipulation has completed
        prev_row_count = 0
        while True:
            time.sleep(0.2)
            tmp_row_count = rows.count
            if tmp_row_count == prev_row_count:
                break
            else:
                prev_row_count = tmp_row_count
        return frame, rows


    def load_matches_page(self, series_pattern):
        # click on "Matcher" to return to the list of matches.
        self.page.get_by_role("link", name="Matcher").click()

        frame, rows = self.wait_for_matches_iframe_to_complete()

        if self.year_label and not self.period_set:
            self.set_period_dropdown(self.year_label)
            self.period_set = True
            frame, rows = self.wait_for_matches_iframe_to_complete()

        # have we parsed all matches in the selected series
        if self.row_idx >= (self.row_count-1):
            #print("switch serie")
            self.series_idx += 1
            self.row_idx = 0
        #print("Series idx: ", self.series_idx)

        # get all series
        series = frame.get_by_role("link", name=re.compile(series_pattern))
        self.series_count = series.count()
        #print(self.series_count)

        # check end criteria
        if self.series_idx == self.series_count:
            print("all series done")
            return None

        # select the current series
        serie = series.nth(self.series_idx)
        self.series_name = serie.inner_text().strip()
        #print(self.series_name)
        serie.click()
        self.wait_for_loading_bar_to_complete()

        frame, rows = self.wait_for_matches_iframe_to_complete()
        self.row_count = rows.count()
        print("matches: ", self.row_count)
        return rows

    def set_period_dropdown(self, year_label: str) -> None:
        # Period dropdown is select#group_pk on the "Matcher" page.
        try:
            def _find_group_pk():
                for f in self.page.frames:
                    try:
                        sel = f.locator("select#grupp_pk, select[name='grupp_pk']")
                        if sel.count() > 0:
                            return sel
                    except Exception:
                        continue
                return None

            period_select = None
            deadline = time.time() + (DEFAULT_TIMEOUT / 1000)
            while time.time() < deadline and period_select is None:
                period_select = _find_group_pk()
                if period_select is None:
                    time.sleep(0.2)

            if period_select is None:
                print(f"Error: Period dropdown select#group_pk not found (wanted year '{year_label}').")
                sys.exit(2)

            options = period_select.locator("option").all_inner_texts()
            year_pattern = re.compile(rf"{re.escape(year_label)}")
            matching = [opt for opt in options if year_pattern.search(opt)]

            if len(matching) == 1:
                period_select.select_option(label=matching[0])
                self.wait_for_loading_bar_to_complete()
                return

            if len(matching) == 0:
                print(f"Error: No Period option matched year '{year_label}'.")
            else:
                print(f"Error: Multiple Period options matched year '{year_label}':")
                for opt in matching:
                    print(f"- {opt}")
            sys.exit(2)
        except PlaywrightTimeoutError:
            print(f"Warning: Period dropdown not set (timeout; wanted year '{year_label}')")


    def click_on_tab_and_read_for_table(self, tab_pattern):
        frame_locator = self.page.frame_locator("#vpframe_1")
        button = frame_locator.get_by_role("button", name=tab_pattern)

        # check in the button label if there are players to read
        m = re.search(r"\((\d+)\/", button.inner_text())
        player_count = int(m.group(1))
        print(f"{player_count}, ", end="")
        if player_count == 0:
            return None, player_count

        # else click on tab
        button.click()
        self.wait_for_loading_bar_to_complete()

        # try to wait until the clicked tab becomes active/selected
        try:
            expect(button).to_have_attribute("aria-selected", "true", timeout=3000)
        except Exception:
            try:
                expect(button).to_have_class(re.compile(r"\bactive\b"), timeout=3000)
            except Exception:
                pass

        # try to wait for a header label that matches the tab (if present)
        try:
            header = frame_locator.get_by_role("heading", name=re.compile(r"^(Kommer|Kommer ej|Ej svarat|Ej kallad)$"))
            header.first.wait_for(state="visible", timeout=1500)
        except Exception:
            pass

        # wait for iframe to complete loading
        frame = self.page.wait_for_selector("#vpframe_1").content_frame()
        frame.wait_for_load_state("domcontentloaded")

        # NOTE: Below never really worked...
        #print("check if there table is empty without waiting")
        #empty_group = frame.locator(".table-empty-group").is_visible(timeout=0)
        #print(empty_group)
        ##if frame.locator("div.table-empty-group").is_visible(timeout=0):
        #if empty_group:
        #    #print("no players in this tab")
        #    return frame, None, None

        # wait for table to attach/appear with a tight retry loop
        table = frame.locator("table.idealis-table")
        try:
            table.wait_for(state="attached", timeout=3000)
            table.wait_for(state="visible", timeout=3000)
        except PlaywrightTimeoutError:
            # re-click tab and retry once quickly
            button.click()
            self.wait_for_loading_bar_to_complete()
            frame = self.page.wait_for_selector("#vpframe_1").content_frame()
            table = frame.locator("table.idealis-table")
            table.wait_for(state="attached", timeout=3000)
            try:
                table.wait_for(state="visible", timeout=3000)
            except PlaywrightTimeoutError:
                raise PlaywrightTimeoutError("Table did not become visible after retry")

        # wait for last (relevant) row to become visible
        rows = frame.locator("table.idealis-table tbody tr")
        # there might be more rows; e.g. "Ledare", but at least
        # wait for known content (players + "Medlemmar")
        rows.nth(player_count).wait_for(state="visible")

        # small stabilization window to avoid reading before filter applies
        prev = -1
        stable = 0
        for _ in range(6):
            count = rows.count()
            if count == prev:
                stable += 1
                if stable >= 2:
                    break
            else:
                stable = 0
                prev = count
            time.sleep(0.05)
        #rows.nth(player_count).wait_for(state="attached")

        #while rows.count() <= player_count:
        #    #print("waiting for player data...")
        #    print(".", end="")
        #    time.sleep(0.2)

        # NOTE:
        # return a snapshot of the table data instead of returning
        # a live locator that can change because of DOM updates

        # Get a stable snapshot: list[list[str]] of cell texts
        # Re-locate rows just before evaluation and retry once if the iframe navigates.
        table_data = None
        for attempt in range(2):
            try:
                rows = frame.locator("table.idealis-table tbody tr")
                table_data = rows.evaluate_all(
                    """trs => trs.map(tr =>
                        Array.from(tr.querySelectorAll('td'), td => td.innerText.trim())
                    )"""
                )
                break
            except Exception as e:
                if "Execution context was destroyed" in str(e) and attempt == 0:
                    time.sleep(0.2)
                    continue
                raise

        if table_data is None:
            raise RuntimeError("Failed to read table data after retry")

        return table_data, player_count


    def parse_single_match(self, row):
        data = []

        # get cells in the row
        cells = row.locator("td")

        cell_count = cells.count()

        # matchid cell
        matchid_locator = cells.nth(3)

        # row must have a series matchid in the correct cell
        if matchid_locator.count() > 0 and \
           matchid_locator.inner_text().isdigit():
            matchid = int(matchid_locator.inner_text())
            #print(matchid)

            # "Visa" button
            match_button = row.get_by_role("button", name="Visa")

            # row must have a "Visa" button
            if match_button.count() > 0:

                # date - <weekday> <day of month>
                date = cells.nth(1).inner_text().strip().rstrip("!").rstrip()
                date = datetime.strptime(date, "%a %d")
                date = date.replace(year=self.curr_month_year.year,
                                    month=self.curr_month_year.month)
                #print(date.date())

                if date < start_date:
                    print("too old activity")
                    return (None, "")

                if date > end_date:
                    #print("too new activity")
                    return (None, "done")

                # This row is a match, go to match details.
                match_button.click()
                self.wait_for_loading_bar_to_complete()

                # wait for iframe to complete loading
                frame = self.page.wait_for_selector("#vpframe_1").content_frame()
                frame.wait_for_load_state("networkidle")

                # wait for table in iframe to complete loading
                frame = self.page.frame_locator("#vpframe_1")
                frame.locator("table.idealis-table").wait_for()

                # check if the match is cancelled
                _date = frame.get_by_role("heading", name="DATUM & TID") \
                            .locator("xpath=following-sibling::div") \
                            .inner_text()

                pattern = re.compile(r"Match struken")
                if pattern.search(_date):
                    print("Match struken")
                    return (None, "")

                # intermedia debug print before we start extracting plater data
                print("%s, %s, %s, " % (matchid, date.date(), self.series_name), end="")

                # number players in each tab
                summary = [0,] * 4

                # tab data
                tabs = (
                    (0, "Kommer",    re.compile(r"^Kommer \(.*$")),
                    (1, "Kommer ej", re.compile(r"^Kommer ej \(.*$")),
                    (2, "Ej svarat", re.compile(r"^Ej svarat \(.*$")),
                    (3, "Ej kallad", re.compile(r"^Ej kallad \(.*$")),
                )

                print("")
                # iterate over each tab
                for tab_idx, tab_label, tab_pattern in tabs:

                    rows, player_count = self.click_on_tab_and_read_for_table(tab_pattern)

                    # quick escape if there are no players in the tab
                    if rows is None:
                        continue

                    # --- Find "Medlemmar" in column 1 ---
                    row_idx = -1
                    for i, row in enumerate(rows):
                        if len(row) > 1 and row[1] == "Medlemmar":
                            row_idx = i
                            break

                    # --- Read player rows until "Ledare" in column 1 ---
                    nbr_players = 0
                    for row in rows[row_idx + 1:]:
                        label = row[1] if len(row) > 1 else ""
                        if label == "Ledare":
                            break

                        name = row[2] if len(row) > 2 else ""

                        if tab_label == "Ej kallad":
                            state = (row[7] if len(row) > 7 else "").strip() or "Ej förhandsrapporterad"
                        else:
                            state = row[10] if len(row) > 10 else ""

                        data.append([date.date(), matchid, self.series_name, name, state])
                        nbr_players += 1


                    #def td_text(row_handle, idx):
                    #    """Return trimmed text of <td> at index idx for a given <tr> handle; '' if missing."""
                    #    cells = row_handle.query_selector_all("td")
                    #    if idx < 0 or idx >= len(cells):
                    #        return ""
                    #    return cells[idx].inner_text().strip()

                    ## --- Find the row labeled "Medlemmar" in td[1] ---
                    #row_idx = -1
                    #for i, row in enumerate(rows):
                    #    if td_text(row, 1) == "Medlemmar":
                    #        row_idx = i
                    #        break

                    #if row_idx == -1:
                    #    raise RuntimeError('Could not find the "Medlemmar" row')

                    ## --- Read all player rows until we hit "Ledare" ---
                    #nbr_players = 0
                    #for row in rows[row_idx + 1 : row_count]:
                    #    label = td_text(row, 1)
                    #    if label == "Ledare":
                    #        break

                    #    name = td_text(row, 2)

                    #    if tab_label == "Ej kallad":
                    #        state = td_text(row, 7) or "Ej förhandsrapporterad"
                    #    else:
                    #        state = td_text(row, 10)

                    #    data.append([date.date(), matchid, self.series_name, name, state])
                    #    nbr_players += 1

                    # Optional:
                    # print(f'found "Medlemmar" at row {row_idx}, parsed {nbr_players} players')


#                    # Find the row labelled "Medlemmar"
#                    row_idx=0
#                    for row_idx in range(row_count):
#                        row = rows.nth(row_idx)
#                        if row.locator("td").nth(1).inner_text() == 'Medlemmar':
#                            break
#                    #print("found Medlemmar at row ", row_idx)
#
#                    # Read all player rows
#                    nbr_players = 0
#                    for player_idx in range(row_idx+1, row_count):
#                        row = rows.nth(player_idx)
#                        #print(row.all_inner_texts())
#                        #print(row.count())
#
#                        if (row_count == 0) or (row.locator("td").nth(1).inner_text() == 'Ledare'):
#                            #print("no more players")
#                            break
#                        else:
#                            nbr_players += 1
#                            name = row.locator("td").nth(2).inner_text()
#
#                            if tab_label == "Ej kallad":
#                                state = row.locator("td").nth(7).inner_text()
#                                if len(state.strip()) == 0:
#                                    state = "Ej förhandsrapporterad"
#                            else:
#                                state = row.locator("td").nth(10).inner_text()
#                                #state = label
#
#                            data.append([date.date(), matchid, self.series_name, name, state])

                    # accumulate the number of players
                    if player_count != nbr_players:
                        print(f"player count mismatchi ({tab_pattern}): expected {player_count} vs parsed {nbr_players} (rows: {row_idx}->{len(rows)})")
                        for d in data:
                            print(d)
                        input("Press Enter to continue...")

                    summary[tab_idx] = player_count

                # debug
                #for row in data:
                #    print(row)
                #print("="*40)

                #
                # END OF MATCH
                #
                print("")
                print(", ".join(map(str, summary)))
                return (data, "")
        else:
            return (None, "")

    def collect(self,  email, password, start_date, end_date, series_pattern, year_label=None) -> None:
        self.year_label = year_label

        #
        # LOAD LOGIN PAGE
        #
        self.page.goto("https://identity.sportadmin.se/identity/account/login")

        #
        # GO TO PROFILE PAGE
        #
        self.page.locator("#loginemail").fill(email)
        self.page.locator("#loginpass").fill(password)
        self.page.get_by_role("button", name="Log in").click()
        #self.page.locator("xpath=//form/button[@type='submit']").click()

        # to load due to some "verifierar behörighet" and other
        # activities that are performed. typically this takes
        # less than 20 seconds

#        #
#        # SELECT PROFILE: NIKE-LEDARE
#        #
#        # sometimes this page is not loaded, so check that first.
#        if self.driver.current_url == "https://identity.sportadmin.se/profile/user/gateway":
#            #self.driver.find_element(By.CSS_SELECTOR, ".userprofile:nth-child(2) .title").click()
#
#            # auto-select the first element
#            userprofile = self.driver.find_element(By.CLASS_NAME, "userprofile").click()
#

        #
        # WAIT FOR DEFAULT PAGE TO LOADED
        #
        print("wait for iframes to start loading")
        self.page.locator("#vpframe_1") \
            .content_frame.locator(".idealis-fast-paginator") \
            .first \
            .wait_for(timeout=60000)

        #
        # ON ACTIVITIES LIST PAGE
        #
        # Inspect iframes with Chrome->DevTools->Console:
        #
        # document.querySelectorAll("iframe").forEach((f, i) => {
        #   console.log(`Iframe ${i}:`, f, "src =", f.src || "(no src)");
        # });

        #
        # Itrate over all matches
        #
        rows = self.load_matches_page(series_pattern)

        data = []
        while rows is not None:

            # select current row
            row = rows.nth(self.row_idx)
            self.row_idx += 1

            # get table cells in the row
            cells = row.locator("td")

            # number of td elements
            cell_count = cells.count()

            # row must have the correct number of cells
            if cell_count == 1:
                # month+year
                self.curr_month_year = datetime.strptime(cells.first.inner_text(), "%B %Y")
                #print("Month: ", self.curr_month_year.date())
                continue
            elif cell_count == 9:
                d, reason = self.parse_single_match(row)
                #if d is not None:
                #    for e in d:
                #        print(e)

                if reason == "done" and self.series_idx == (self.series_count-1):
                    print("read all series and all matches")
                    break
                elif reason == "done":
                    print("emulate that all rows are read")
                    self.row_idx = self.row_count

                if d is not None:
                    data.extend(d)

                # We likely clicked on the tabs in the match details page,
                # "Tillbaka" button will take us to "Kallelser" instead of "Matcher".
                #page.frame_locator("#vpframe_1").locator("button.btn.back-btn").click()
                # so we click on "Matcher" instead before parsing each match

                rows = self.load_matches_page(series_pattern)
                continue

        with open('sportadmin.csv', 'w', newline='') as csvfile:
            writer = csv.writer(csvfile, delimiter=',', quotechar='|', quoting=csv.QUOTE_MINIMAL)
            for player in data:
                writer.writerow(player)

        # ---------------------
        # Leave closing to caller so we can optionally repeat within one browser session.

    def close(self) -> None:
        self.context.close()
        self.browser.close()


if __name__ == "__main__":
    print("Hello World!")

    arg_parser = argparse.ArgumentParser(description="Parse a date string with range check")

    # Credentials are read only from the untracked .credentials file. They can
    # never be passed on the command line or via environment variables.
    arg_parser.add_argument("--credentials", default=None,
                            help="Path to credentials file "
                                 "(default: ./.credentials, then alongside this script)")

    # Optional arguments
    arg_parser.add_argument("--start-date", help="Earliest allowed date (YYYY-MM-DD)", type=str, default="2001-01-01")
    arg_parser.add_argument("--end-date", help="Latest allowed date (YYYY-MM-DD)", type=str, default=datetime.now().strftime("%Y-%m-%d"))
    arg_parser.add_argument("--year", help="Year to match in Period dropdown after page load (e.g. 2025)", type=str, default="2025")
    arg_parser.add_argument("--repeat", help="Repeat the scrape N times within one browser session", type=int, default=1)
    arg_parser.add_argument("--series-pattern", help="Substring to use when matching series names", type=str, default="")
    args = arg_parser.parse_args()

    start_date = datetime.fromisoformat(args.start_date)
    end_date = datetime.fromisoformat(args.end_date)

    email, password = load_credentials(args.credentials)

    with sync_playwright() as playwright:
        try:
            sp = SportadminGamesScraper(playwright)
            try:
                for i in range(args.repeat):
                    if args.repeat > 1:
                        print(f"--- REPEAT {i + 1}/{args.repeat} ---")
                    sp.collect(
                        email,
                        password,
                        start_date,
                        end_date,
                        args.series_pattern,
                        args.year,
                    )
            finally:
                sp.close()
        except PlaywrightTimeoutError as e:
            print(e)
            traceback.print_exc()
            input("Press Enter to continue...")
