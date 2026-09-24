#!/usr/bin/python3

import re
from playwright.sync_api import Playwright, sync_playwright, expect, TimeoutError as PlaywrightTimeoutError, Error as PlaywrightError

import time
import locale
import logging
import json
from datetime import datetime
import argparse

import csv
import sys
import traceback
import os

from credentials import load_credentials
from settings import load_settings
import sa_checks

DEFAULT_TIMEOUT = 10_000

log = logging.getLogger("sportadmin")


def _read_text(name):
    return open(os.path.join(os.path.dirname(os.path.abspath(__file__)), name),
                encoding="utf-8").read()


BLAZOR_IDLE_JS = _read_text("blazor_idle.js")

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

    def __init__(self, playwright:Playwright, verify=False):
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
        # A wide viewport keeps every grid column present so the state/name
        # cells never collapse. Leave locale at the browser default so the
        # identity login page stays in English ("Log in").
        self.context = self.browser.new_context(
            viewport={"width": 1920, "height": 1080},
        )
        # WebSocket / MutationObserver instrumentation — installed in every frame
        # (incl. the cross-origin attendance iframe) before any page script.
        self.context.add_init_script(BLAZOR_IDLE_JS)
        self.page = self.context.new_page()

        # Set Swedish locale for date parsing
        locale.setlocale(locale.LC_TIME, "sv_SE.UTF-8")

        #self.page.set_default_timeout(DEFAULT_TIMEOUT)
        #self.page.set_default_navigation_timeout(DEFAULT_TIMEOUT)

        self.verify = verify
        self.verify_records = []       # per-match dicts (see sa_checks)
        self.run_flags = []            # run-level anomaly codes
        self.run_idx = 0               # 1-based, set by collect()
        self._logged_in = False

        self.start_date = None
        self.end_date = None

        self.curr_month_year = None

        self.series_count = 0
        self.series_idx = -1

        self.row_count = 0
        self.row_idx = 0

        self.series_name = ""
        self.year_label = None
        self.period_set = False

        # training-mode (Kallelser) navigation state
        self.trainings_filter_set = False
        self.activity_type = None
        self.expected_activity_name = None
        self.training_year = None
        self._training_list_total = None
        self._kallelser_reload_retries = 30

    # ------------------------------------------------------------------ waits

    def wait_for_blazor_idle(self, frame, quiet_ms=250, timeout_ms=6000,
                             poll_ms=40, grace_ms=700):
        """Block until the Blazor Server circuit in `frame` has finished a render.

        `frame` must be a Frame (``page.wait_for_selector(...).content_frame()``),
        not a FrameLocator. Settled == at least one new inbound RenderBatch since
        the call (or `grace_ms` elapsed with nothing pending), every batch acked,
        no inbound frame / DOM mutation for `quiet_ms`, and the reconnect modal
        hidden. If the instrumentation is not present in `frame`, fall back to a
        short DOM-quiet wait. Returns True if settled, False on timeout.
        """
        def probe():
            try:
                return frame.evaluate("""() => {
                    const s = window.__blazorIdle;
                    const now = Date.now();
                    const m = document.querySelector('#components-reconnect-modal');
                    const shown = !!(m && /components-reconnect-(show|failed)/.test(m.className));
                    return s ? {have: true, frames: s.frames,
                               sinceFrame: now - s.lastFrameTs,
                               sinceMut: now - s.lastMutTs, modal: shown}
                             : {have: false};
                }""")
            except Exception:
                return None

        p0 = probe()
        if not p0 or not p0.get("have"):
            log.debug("wait_for_blazor_idle: no __blazorIdle in frame, dom-quiet fallback")
            return self._wait_dom_quiet(frame, quiet_ms=quiet_ms, timeout_ms=2500)

        # Not every inbound SignalR frame is a RenderBatch that gets an ack, so
        # tracking pending acks is unreliable — key on frame + DOM quiescence
        # instead. `got_render` ensures the server actually responded.
        base = p0["frames"]
        start = time.time()
        deadline = start + timeout_ms / 1000.0
        last = p0
        while time.time() < deadline:
            s = probe()
            if s and s.get("have"):
                last = s
                got_render = s["frames"] >= base + 1
                elapsed_ms = (time.time() - start) * 1000
                quiet = (s["sinceFrame"] > quiet_ms
                         and s["sinceMut"] > quiet_ms
                         and not s["modal"])
                if quiet and (got_render or elapsed_ms > grace_ms):
                    return True
            time.sleep(poll_ms / 1000.0)
        log.debug("wait_for_blazor_idle timeout: %s", last)
        return False

    def _wait_dom_quiet(self, frame, quiet_ms=250, timeout_ms=2500):
        """Wait until no DOM mutation in `frame` for `quiet_ms` (bounded)."""
        try:
            frame.evaluate("""() => {
                if (window.__domQuiet) return;
                window.__domQuiet = {ts: Date.now()};
                new MutationObserver(() => { window.__domQuiet.ts = Date.now(); })
                  .observe(document.documentElement || document,
                           {childList: true, subtree: true, characterData: true, attributes: true});
            }""")
        except Exception:
            time.sleep(quiet_ms / 1000.0)
            return False
        deadline = time.time() + timeout_ms / 1000.0
        while time.time() < deadline:
            try:
                since = frame.evaluate("() => Date.now() - (window.__domQuiet && window.__domQuiet.ts || 0)")
            except Exception:
                since = quiet_ms + 1
            if since > quiet_ms:
                return True
            time.sleep(0.04)
        return False


    def wait_for_loading_bar_to_complete(self):
        loader = self.page.locator(".viewport-frame-wrapper:has(#vpframe_1) .frame-loader")
        try:
            loader.wait_for(state="hidden", timeout=DEFAULT_TIMEOUT)
        except PlaywrightTimeoutError:
            log.debug("frame-loader still visible after %d ms", DEFAULT_TIMEOUT)

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
        # The matches list is classic ASP (no WebSocket), so there is no render
        # ack to key off. Waiting for a specific load_state is not enough (the
        # series-filter JS keeps mutating the table afterwards — the original bug
        # here read `rows.count` (a bound method, never == an int) so the loop was
        # a fixed 0.4 s sleep and "89 rows instead of 13" slipped through).
        #
        # Now: require a non-zero row count that is stable across 3 consecutive
        # polls, bounded by DEFAULT_TIMEOUT.
        #
        rows = frame.locator("#tblMain tr")

        prev = -1
        stable = 0
        deadline = time.time() + DEFAULT_TIMEOUT / 1000.0
        while time.time() < deadline:
            try:
                n = rows.count()
            except Exception:
                n = -1
            if n > 0 and n == prev:
                stable += 1
                if stable >= 3:
                    break
            else:
                stable = 0
                prev = n
            time.sleep(0.15)
        else:
            log.warning("matches iframe row count did not stabilize (last=%s)", prev)
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
        if self.row_idx >= self.row_count:
            #print("switch serie")
            self.series_idx += 1
            self.row_idx = 0
        #print("Series idx: ", self.series_idx)

        # get all series
        series = frame.get_by_role("link", name=re.compile(series_pattern))
        self.series_count = series.count()
        #print(self.series_count)

        # check end criteria
        if self.series_idx >= self.series_count:
            log.info("all %d series done", self.series_count)
            return None

        # select the current series
        serie = series.nth(self.series_idx)
        self.series_name = serie.inner_text().strip()

        # Content oracle: require #tblMain to actually change after the click
        # before trusting the post-filter row count (the classic-ASP filter JS
        # keeps mutating the table for a moment).
        before = self._matches_fingerprint()
        serie.click()
        self.wait_for_loading_bar_to_complete()
        self._wait_matches_changed(before)

        frame, rows = self.wait_for_matches_iframe_to_complete()
        self.row_count = rows.count()
        log.info("series %r: %d list rows", self.series_name, self.row_count)
        return rows

    def _login(self, email, password):
        """Log in once per browser session; later calls are a no-op."""
        if self._logged_in:
            return
        self.page.goto("https://identity.sportadmin.se/identity/account/login")
        self.page.locator("#loginemail").fill(email)
        self.page.locator("#loginpass").fill(password)
        # Button label is locale-dependent ("Log in" / "Logga in"); the
        # submit control inside the login form is the stable target.
        login_btn = self.page.locator(
            "#loginbutton, form button[type=submit], "
            "button:has-text('Log in'), button:has-text('Logga in')").first
        login_btn.click()
        # "verifierar behörighet" and friends can take ~20 s
        log.info("waiting for dashboard iframes")
        self.page.locator("#vpframe_1") \
            .content_frame.locator(".idealis-fast-paginator") \
            .first \
            .wait_for(timeout=60000)
        self._logged_in = True

    def _printa_frame(self):
        """Return the real Frame for the classic-ASP matches table (name=printa)."""
        for f in self.page.frames:
            if f.name == "printa":
                return f
        return None

    def _printa_frame_synced(self):
        """Like _printa_frame(), but first nudges vpframe_3 - a Playwright
        quirk confirmed live on the Närvaro pages: the nested cross-origin
        "printa" iframe inside vpframe_3 doesn't register as a Playwright
        child frame until something forces a sync on vpframe_3 (a plain
        wait is not enough, even once printa's <iframe> tag already exists
        in vpframe_3's HTML)."""
        for f in self.page.frames:
            if f.name == "vpframe_3":
                try:
                    f.content()
                except Exception:
                    pass
                break
        return self._printa_frame()

    def _retry(self, fn, tries=10, delay=1.0):
        """Retry `fn` (a zero-arg callable that re-fetches whatever frame/
        locator it needs internally) across the printa frame's habit of
        detaching after every navigation - classic-ASP race also worked
        around elsewhere in this file."""
        last_err = None
        for _ in range(tries):
            try:
                return fn()
            except Exception as e:
                last_err = e
                time.sleep(delay)
        raise last_err

    def _goto_narvaro_report(self):
        """Navigate to Närvaro -> 'Rapportera närvaro' and return the printa
        frame once it has actually loaded narvaro_IFRAME.asp. Confirmed live:
        clicking 'Närvaro' alone can transiently land on start/default.asp;
        the sub-tab click is required too."""
        self.page.get_by_role("link", name="Närvaro").click()
        time.sleep(1.5)
        try:
            self.page.get_by_role("link", name="Rapportera närvaro").click(timeout=8000)
        except PlaywrightTimeoutError:
            log.debug("'Rapportera närvaro' sub-tab click timed out (may already be active)")

        deadline = time.time() + 20
        while time.time() < deadline:
            frame = self._printa_frame_synced()
            if frame is not None and "narvaro_IFRAME.asp" in frame.url:
                try:
                    frame.wait_for_load_state("domcontentloaded", timeout=3000)
                    return frame
                except Exception:
                    pass
            else:
                log.debug("waiting for narvaro_IFRAME.asp; printa frame url=%r",
                          frame.url if frame else None)
            time.sleep(0.5)
        raise RuntimeError("could not reach Närvaro 'Rapportera närvaro' report")

    def _select_narvaro_group(self, year):
        """Select the grupp_pk option whose label contains `year` (mirrors
        set_period_dropdown's pattern for Matcher); a no-op if it's already
        selected, which is the common case (the current season's group is
        the default)."""
        def _do():
            f = self._printa_frame_synced()
            sel = f.locator("select#grupp_pk, select[name='grupp_pk']")
            sel.wait_for(timeout=5000)
            options = sel.locator("option")
            target_label = None
            current_label = None
            for i in range(options.count()):
                opt = options.nth(i)
                label = opt.inner_text()
                if opt.get_attribute("selected") is not None:
                    current_label = label
                if str(year) in label:
                    target_label = label
            if target_label is None:
                log.warning("no Närvaro grupp option matched year %r; keeping "
                            "current selection %r", year, current_label)
                return
            if target_label == current_label:
                return
            log.info("Närvaro grupp -> %r", target_label)
            sel.select_option(label=target_label)
            time.sleep(1.5)
        self._retry(_do)

    _NARVARO_MONTH_IDX = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "maj": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "okt": 10, "nov": 11, "dec": 12,
    }

    # One evaluate() per Närvaro page - confirmed live DOM shape:
    #  - td.kort4: month header cells (colspan groups; abbreviated 3-letter
    #    or full Swedish names depending on how many days are in the group).
    #  - td.kort3[id]: day-number header cells, id="a<activityid>".
    #  - td.kort5: the time+type row, same column count/order as the
    #    day-number row within one page; title (own or on a nested [title]
    #    element) is the Swedish activity type.
    #  - td[onmouseover^="aOn("]: per-player presence cells, carrying both
    #    the activity id and member id directly (no positional alignment
    #    needed); present iff the inline style contains #CCFFCC. Cells for
    #    LOK-subsidy-excluded (activity, player) pairs have no onmouseover
    #    at all and are skipped (no data, not absent).
    #  - td[id^="m"]: the frozen name column, id="m<memberid>".
    _NARVARO_PAGE_JS = r"""() => {
        const months = [...document.querySelectorAll('td.kort4')].map(td => ({
            name: (td.innerText || '').trim(),
            colspan: parseInt(td.getAttribute('colspan') || '1', 10),
        }));

        // A not-yet-confirmed "in progress" activity column renders
        // per-player checkbox cells with ids like "box<activityid>_<member
        // id>_..." (also class="kort3") instead of the normal read-only
        // presence markup - excluded by requiring the id to start with "a"
        // (confirmed live: box... ids never do). An unscheduled placeholder
        // column renders id="a" with no digits - kept as a real column (its
        // day/activity_id both parse to NaN, resolved by the Python-side
        // skip-on-unparseable-date path) so column position stays aligned
        // with the time+type row and the month-header colspans.
        const dayCells = [...document.querySelectorAll('td.kort3[id^="a"]')];
        const timeCells = [...document.querySelectorAll('td.kort5')];
        const columns = dayCells.map((td, i) => {
            const aid = parseInt(td.id.slice(1), 10);
            const day = parseInt((td.innerText || '').trim(), 10);
            const timeTd = timeCells[i];
            let time = '', type = '';
            if (timeTd) {
                const txt = (timeTd.innerText || '').trim();
                if (txt.length >= 3) time = txt.slice(0, -2) + ':' + txt.slice(-2);
                type = timeTd.getAttribute('title') || '';
                if (!type) {
                    const el = timeTd.querySelector('[title]');
                    if (el) type = el.getAttribute('title') || '';
                }
            }
            return { activity_id: aid, day: day, time: time, type: type };
        });

        const members = {};
        for (const td of document.querySelectorAll('td[id^="m"]')) {
            const a = td.querySelector('a');
            if (a) members[td.id.slice(1)] = (a.textContent || '').trim();
        }

        const presence = [];
        for (const td of document.querySelectorAll('td[onmouseover^="aOn("]')) {
            const m = /aOn\(a(\d+),m(\d+),/.exec(td.getAttribute('onmouseover') || '');
            if (!m) continue;
            const name = members[m[2]];
            if (!name) continue;
            presence.push({
                activity_id: parseInt(m[1], 10),
                player_name: name,
                present: (td.getAttribute('style') || '').includes('CCFFCC'),
            });
        }

        return { months, columns, presence };
    }"""

    def _narvaro_page_data(self):
        def _do():
            f = self._printa_frame_synced()
            return f.evaluate(self._NARVARO_PAGE_JS)
        return self._retry(_do)

    def collect_narvaro(self, year, activity_name):
        """Scrape actual attendance from Närvaro -> 'Rapportera närvaro',
        restricted to columns tagged with `activity_name` and to
        self.start_date/self.end_date. Cross-referencing against Kallelser
        training rows happens at analysis time by date, not here - the two
        systems use disjoint activity-id namespaces (confirmed live).
        Returns [date, activity_id, player_name, "present"|"absent"] rows."""
        self._goto_narvaro_report()
        self._select_narvaro_group(year)

        rows = []
        curr_year = int(year)
        prev_month_idx = None
        prev_fingerprint = None
        offset = 0
        while offset < 60:
            def _goto(offset=offset):
                f = self._printa_frame_synced()
                f.goto(re.sub(r"offset=\d+", f"offset={offset}", f.url))
            self._retry(_goto)
            time.sleep(1.0)

            page_data = self._narvaro_page_data()
            columns = page_data.get("columns", [])
            months = page_data.get("months", [])
            presence = page_data.get("presence", [])

            # Unscheduled placeholder columns carry a NaN activity_id, and
            # NaN never compares equal to itself - excluded here, or the
            # repeat check below could never fire once any page has one.
            real_ids = [c["activity_id"] for c in columns if c["activity_id"] == c["activity_id"]]
            fingerprint = tuple(sorted(real_ids))
            if fingerprint == prev_fingerprint:
                log.debug("narvaro offset=%d repeats the previous page; "
                          "last page reached", offset)
                break
            prev_fingerprint = fingerprint

            month_per_col = []
            for g in months:
                month_per_col.extend([g["name"]] * max(1, g.get("colspan", 1)))
            if len(month_per_col) != len(columns):
                log.warning("narvaro offset=%d: month-header width %d != "
                            "column count %d; date attribution may be off",
                            offset, len(month_per_col), len(columns))

            activity_dates = {}
            for i, col in enumerate(columns):
                month_name = month_per_col[i] if i < len(month_per_col) else None
                if not month_name:
                    continue
                if month_name.strip() == "-":
                    continue  # unscheduled placeholder column - expected, not a warning
                month_idx = self._NARVARO_MONTH_IDX.get(month_name.strip()[:3].lower())
                if month_idx is None:
                    log.warning("narvaro: unrecognised month label %r; "
                                "skipping column", month_name)
                    continue
                if prev_month_idx is not None and month_idx < prev_month_idx:
                    curr_year += 1
                prev_month_idx = month_idx

                if col["type"] != activity_name:
                    continue
                try:
                    date = datetime(curr_year, month_idx, col["day"])
                except (ValueError, TypeError):
                    continue
                if self.start_date and date < self.start_date:
                    continue
                if self.end_date and date > self.end_date:
                    continue
                activity_dates[col["activity_id"]] = date

            for p in presence:
                date = activity_dates.get(p["activity_id"])
                if date is None:
                    continue
                rows.append([date.date().isoformat(), p["activity_id"], p["player_name"],
                            "present" if p["present"] else "absent"])

            offset += 1
        else:
            log.error("narvaro pagination did not terminate within 60 pages; aborting")

        if not rows:
            log.warning("no Närvaro rows matched activity_name=%r; check the "
                        "site's activity-type vocabulary against --activity-name",
                        activity_name)
        log.info("collected %d Närvaro presence rows", len(rows))
        return rows

    def _matches_fingerprint(self):
        f = self._printa_frame()
        if f is None:
            return ""
        try:
            return f.evaluate("""() => {
                const t = document.querySelector('#tblMain');
                return t ? (t.rows.length + '|' + (t.innerText || '').slice(0, 400)) : '';
            }""")
        except Exception:
            return ""

    def _wait_matches_changed(self, before, timeout_ms=DEFAULT_TIMEOUT):
        """Wait until #tblMain differs from `before` (series filter applied)."""
        deadline = time.time() + timeout_ms / 1000.0
        while time.time() < deadline:
            cur = self._matches_fingerprint()
            if cur and cur != before:
                return True
            time.sleep(0.1)
        log.debug("matches table did not visibly change after series click")
        return False

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
                log.error("Period dropdown select#grupp_pk not found (wanted %r)", year_label)
                sys.exit(2)

            options = period_select.locator("option").all_inner_texts()
            year_pattern = re.compile(rf"{re.escape(year_label)}")
            matching = [opt for opt in options if year_pattern.search(opt)]

            if len(matching) == 1:
                log.info("Period -> %r", matching[0])
                period_select.select_option(label=matching[0])
                self.wait_for_loading_bar_to_complete()
                return

            if len(matching) == 0:
                log.error("no Period option matched year %r", year_label)
            else:
                log.error("multiple Period options matched year %r: %s", year_label, matching)
            sys.exit(2)
        except PlaywrightTimeoutError:
            log.warning("Period dropdown not set (timeout; wanted year %r)", year_label)


    # One self-contained JS step: (re)locate the Virtualize scroll container,
    # optionally scroll it, and return every currently-mounted person row plus
    # the scroll position. `action` is 'top', 'down' or 'read'. A person row is
    # one whose first cell is a birth year or that carries a non-action link; the
    # stable id is the profile-link href when present, else "year|name".
    _HARVEST_STEP_JS = r"""(action) => {
        // The attendance grid is normally table.idealis-table; a very small tab
        // sometimes renders a plain table, so fall back to the widest table that
        // has a birth-year cell.
        let table = document.querySelector('table.idealis-table');
        if (!table) {
            for (const t of document.querySelectorAll('table')) {
                if ([...t.querySelectorAll('td')].some(td => /^(19|20)\d\d$/.test((td.innerText||'').trim()))) {
                    table = t; break;
                }
            }
        }
        let cont = null;
        if (table) {
            let el = table.parentElement;
            while (el && el !== document.body) {
                const s = getComputedStyle(el);
                if ((s.overflowY === 'auto' || s.overflowY === 'scroll')
                    && el.scrollHeight > el.clientHeight + 4) { cont = el; break; }
                el = el.parentElement;
            }
        }
        if (!cont) cont = document.scrollingElement || document.documentElement;

        if (action === 'top') cont.scrollTop = 0;
        else if (action === 'down')
            cont.scrollTop = Math.min(cont.scrollTop + cont.clientHeight * 0.85,
                                      cont.scrollHeight);

        const realHref = (a) => {
            const h = a && a.getAttribute('href') || '';
            return (!h || h === '#' || h.toLowerCase().startsWith('javascript:')) ? '' : h;
        };
        const rows = [];
        let category = null;
        const trs = table ? table.querySelectorAll('tr') : [];
        for (const tr of trs) {
            const tds = [...tr.querySelectorAll('td')];
            if (!tds.length) continue;
            const texts = tds.map(td => (td.innerText || '').trim());
            const joined = texts.join(' ').trim();
            if (tds.length <= 3 && /^(Medlemmar|Ledare)\b/.test(joined)) {
                category = joined.indexOf('Ledare') === 0 ? 'Ledare' : 'Medlemmar';
                continue;
            }
            let link = null, href = '';
            for (const a of tr.querySelectorAll('td a')) {
                const t = (a.textContent || '').trim();
                if (!t || /^(Ändra|Visa|Ta bort|Redigera)$/.test(t)) continue;
                link = a; href = realHref(a); break;
            }
            const year = texts.find(t => /^(19|20)\d\d$/.test(t)) || '';
            if (!link && !year) continue;
            const name = link ? (link.textContent || '').trim()
                : (texts.find(t => /[A-Za-zÅÄÖåäö]{2,}\s+[A-Za-zÅÄÖåäö]/.test(t)) || '');
            rows.push({href: href || (year + '|' + name), link_href: href,
                       name, year, category, cells: texts});
        }
        return {
            rows,
            scroll: {top: cont.scrollTop, h: cont.clientHeight, sh: cont.scrollHeight},
        };
    }"""

    @staticmethod
    def _clean_name(name):
        """Strip the quit/external markers SportAdmin prepends; report if stripped."""
        cleaned = re.sub(r'^\s*(?:warning\b[^\n]*\n[^\n]*\n?|S\s+|-\s+)', '', name).strip()
        cleaned = cleaned.splitlines()[-1].strip() if "\n" in cleaned else cleaned
        return cleaned, (cleaned != name.strip())

    def read_tab(self, tab_pattern, tab_label):
        """Click one attendance tab and scroll-harvest every member + leader row.

        Returns a dict: label_N, label_M (from the "(N/M)" tab label; None if
        unparsed), members/leaders (lists of {name, state, href}), scroll_iters,
        flags.
        """
        flags = []
        fl = self.page.frame_locator("#vpframe_1")
        button = fl.get_by_role("button", name=tab_pattern)

        label_txt = button.inner_text()
        m = re.search(r"\((\d+)\s*/\s*(\d+)\)", label_txt)
        if m:
            label_N, label_M = int(m.group(1)), int(m.group(2))
        else:
            label_N = label_M = None
            flags.append("tab_label_unparsed")

        # Empty tab — nothing to click/scroll/harvest.
        if label_N == 0 and label_M == 0:
            return {"label_N": 0, "label_M": 0, "members": [], "leaders": [],
                    "scroll_iters": 0, "flags": flags}

        def step(action):
            for attempt in range(4):
                try:
                    f = self.page.wait_for_selector("#vpframe_1").content_frame()
                    return f.evaluate(self._HARVEST_STEP_JS, action)
                except Exception as e:
                    if attempt < 3 and ("Execution context was destroyed" in str(e)
                                        or "detached" in str(e)):
                        time.sleep(0.4)
                        continue
                    raise

        # Click the tab and wait until the grid actually contains person rows
        # (or, for a genuinely tiny tab, until it settles). Retry the click a
        # couple of times — a Blazor tab switch occasionally drops the event.
        frame = None
        for click_try in range(3):
            try:
                button.click(timeout=15000)
            except PlaywrightTimeoutError:
                log.debug("read_tab(%s): tab click timed out (try %d)", tab_label, click_try + 1)
            frame = self.page.wait_for_selector("#vpframe_1").content_frame()
            self.wait_for_blazor_idle(frame)
            try:
                expect(button).to_have_attribute("aria-selected", "true", timeout=2000)
            except Exception:
                pass
            ready = False
            for _ in range(20):                       # up to ~4 s
                try:
                    rows = step("read")["rows"]
                except Exception:
                    rows = []
                if rows:
                    ready = True
                    break
                time.sleep(0.2)
            if ready:
                break
        else:
            # Every click attempt left the grid empty. For a labelled-non-empty
            # tab that is a real miss; for a 0-member tab it is expected.
            if label_N:
                flags.append("tab_empty_after_clicks")
                log.warning("read_tab(%s): grid empty after 3 clicks (label %s/%s)",
                            tab_label, label_N, label_M)
            return {"label_N": label_N, "label_M": label_M, "members": [],
                    "leaders": [], "scroll_iters": 0, "flags": flags}

        step("top")
        self.wait_for_blazor_idle(frame)

        harvested = {}          # key -> row dict (first-seen wins)
        MAX_ITERS = 40
        iters = 0
        stale = 0
        while iters < MAX_ITERS:
            iters += 1
            res = step("read")
            added = 0
            for r in res["rows"]:
                key = r["href"] or ("name:" + r["name"])
                if key not in harvested:
                    harvested[key] = r
                    added += 1

            members_seen = sum(1 for r in harvested.values()
                               if r["category"] != "Ledare")
            sc = res["scroll"]
            at_bottom = sc["top"] + sc["h"] >= sc["sh"] - 2

            if label_N is not None and members_seen >= label_N and at_bottom:
                break
            if at_bottom and added == 0:
                stale += 1
                if stale >= 2:
                    break
            else:
                stale = 0
            frame = self.page.wait_for_selector("#vpframe_1").content_frame()
            step("down")
            self.wait_for_blazor_idle(frame)
        else:
            flags.append("scroll_iter_cap")
            log.error("read_tab(%s): hit %d-iteration scroll cap "
                      "(%d/%s members)", tab_label, MAX_ITERS, members_seen, label_N)

        members, leaders = [], []
        called_state = tab_label if tab_label in sa_checks.CALLED_STATES else None
        for r in harvested.values():
            name, dirty = self._clean_name(r["name"])
            if dirty:
                flags.append("name_dirty")
            if called_state:
                state = called_state
            else:
                # "Ej kallad" tab — the row's förhandsrapportering status.
                cand = [c for c in r["cells"] if c in ("Tillgänglig", "Ej tillgänglig")]
                state = cand[0] if cand else "Ej förhandsrapporterad"
            rec = {"name": name, "state": state, "href": r["href"],
                   "link_href": r.get("link_href", ""), "year": r.get("year", ""),
                   "raw_cells": r["cells"]}
            if r["category"] == "Ledare":
                leaders.append(rec)
            else:
                members.append(rec)

        if label_N and not members:
            flags.append("tab_empty_but_labeled")

        return {"label_N": label_N, "label_M": label_M, "members": members,
                "leaders": leaders, "scroll_iters": iters, "flags": flags}


    TABS = (
        ("Kommer",    re.compile(r"^Kommer \(")),
        ("Kommer ej", re.compile(r"^Kommer ej \(")),
        ("Ej svarat", re.compile(r"^Ej svarat \(")),
        ("Ej kallad", re.compile(r"^Ej kallad \(")),
    )

    # Same tab labels/regexes as TABS, first two entries only — training
    # activities under "Kallelser" only need Kommer/Kommer ej collected.
    TRAINING_TABS = TABS[:2]

    def parse_single_match(self, row):
        """Open one match from the list and harvest all four attendance tabs.

        Returns (rows, "") where rows is a list of
        [date, matchid, series, name, state, location] (empty list for a
        skipped/cancelled match, None for a non-match row).
        """
        cells = row.locator("td")
        if cells.count() != 9:
            return (None, "")

        # column layout: DATUM, TID, MATCHID, LAG, PLATS, RESULTAT, ...
        location = cells.nth(5).inner_text().strip()

        matchid_txt = cells.nth(3).inner_text().strip()
        if not matchid_txt.isdigit():
            return (None, "")
        matchid = int(matchid_txt)
        if row.get_by_role("button", name="Visa").count() == 0:
            return (None, "")

        if self.curr_month_year is None:
            log.warning("match %s appears before any month header; skipping", matchid)
            self.run_flags.append("month_header_missing")
            return (None, "")

        date_txt = cells.nth(1).inner_text().strip().rstrip("!").rstrip()
        try:
            d = datetime.strptime(date_txt, "%a %d")
        except ValueError:
            log.warning("match %s: unparseable date %r; skipping", matchid, date_txt)
            self.run_flags.append("date_unparsed")
            return (None, "")
        date = d.replace(year=self.curr_month_year.year,
                         month=self.curr_month_year.month)

        # Per-row date filter — skip out-of-range rows but keep scanning the
        # series (matches are not guaranteed chronological after filtering).
        if self.start_date and date < self.start_date:
            return ([], "")
        if self.end_date and date > self.end_date:
            return ([], "")

        try:
            row.get_by_role("button", name="Visa").click(timeout=20000)
        except PlaywrightTimeoutError:
            log.error("match %s: 'Visa' click timed out; skipping", matchid)
            self.run_flags.append("visa_click_timeout")
            return ([], "")
        frame = self.page.wait_for_selector("#vpframe_1").content_frame()
        if log.isEnabledFor(logging.DEBUG):
            try:
                diag = frame.evaluate(
                    "() => ({url: location.href.split('?')[0], "
                    "idle: !!window.__blazorIdle, "
                    "frames: (window.__blazorIdle||{}).frames, "
                    "ws: !!window.__blazorIdleInstalled})")
                log.debug("match %s vpframe_1 diag: %s", matchid, diag)
            except Exception as e:
                log.debug("match %s diag failed: %s", matchid, e)
        self.wait_for_blazor_idle(frame)
        fl = self.page.frame_locator("#vpframe_1")
        try:
            fl.locator("table.idealis-table").first.wait_for(timeout=15000)
        except PlaywrightTimeoutError:
            log.error("match %s: attendance table never appeared; skipping", matchid)
            self.run_flags.append("attendance_table_missing")
            return ([], "")

        struken = False
        try:
            dt = fl.get_by_role("heading", name="DATUM & TID") \
                   .locator("xpath=following-sibling::div").inner_text()
            struken = bool(re.search(r"Match struken", dt))
        except Exception:
            pass

        record = {
            "run": self.run_idx,
            "matchid": matchid,
            "date": date.date().isoformat(),
            "series": self.series_name,
            "skipped": "struken" if struken else None,
            "tabs": {},
        }
        if struken:
            log.info("match %s (%s) struken — no rows", matchid, date.date())
            if self.verify:
                record["assertions"] = [r.as_dict() for r in sa_checks.check_match(record)]
                record["result"] = "pass"
                self.verify_records.append(record)
            return ([], "")

        debug = log.isEnabledFor(logging.DEBUG)
        match_rows = []
        for tab_label, tab_pat in self.TABS:
            try:
                t = self.read_tab(tab_pat, tab_label)
            except PlaywrightTimeoutError as e:
                log.error("match %s tab %s: %s", matchid, tab_label, e)
                self.run_flags.append("tab_read_timeout")
                t = {"label_N": None, "label_M": None, "members": [],
                     "leaders": [], "scroll_iters": 0, "flags": ["read_timeout"]}
            tab_ok = (t["label_N"] is not None
                      and len(t["members"]) == t["label_N"]
                      and not t["flags"])
            slim = []
            for mem in t["members"]:
                m = {"name": mem["name"], "state": mem["state"],
                     "href": mem["link_href"] or mem["href"]}
                if debug or not tab_ok:
                    m["raw_cells"] = mem["raw_cells"]
                slim.append(m)
            record["tabs"][tab_label] = {
                "label_N": t["label_N"], "label_M": t["label_M"],
                "parsed_members": len(t["members"]),
                "parsed_leaders": len(t["leaders"]),
                "scroll_iters": t["scroll_iters"],
                "flags": t["flags"],
                "members": slim,
            }
            for mem in t["members"]:
                match_rows.append([date.date().isoformat(), matchid,
                                   self.series_name, mem["name"], mem["state"], location])

        parsed = {lbl: record["tabs"][lbl]["parsed_members"] for lbl, _ in self.TABS}
        labels = {lbl: record["tabs"][lbl]["label_N"] for lbl, _ in self.TABS}
        log.info("match %s %s %-24s parsed=%s labels=%s",
                 matchid, date.date(), self.series_name,
                 list(parsed.values()), list(labels.values()))

        if self.verify:
            results = sa_checks.check_match(record)
            record["assertions"] = [r.as_dict() for r in results]
            record["flags"] = sa_checks.match_flags(record)
            record["result"] = "pass" if all(r.ok for r in results) else "warn"
            self.verify_records.append(record)
            for r in results:
                if not r.ok:
                    log.warning("match %s FAIL %s: %s", matchid, r.id, r.detail)

        return (match_rows, "")

    def collect(self, email, password, start_date, end_date, series_pattern,
                year_label=None, run_idx=1, max_matches=0):
        """Run one full scrape. Returns the list of harvested CSV rows."""
        self.year_label = year_label
        self.start_date = start_date
        self.end_date = end_date
        self.run_idx = run_idx
        self.max_matches = max_matches
        self._match_n = 0
        self.run_flags = []
        # reset per-run navigation state
        self.curr_month_year = None
        self.series_count = 0
        self.series_idx = -1
        self.row_count = 0
        self.row_idx = 0
        self.series_name = ""
        self.period_set = False

        #
        # LOGIN (once per browser session; later runs reuse the session)
        #
        self._login(email, password)

        #
        # ON ACTIVITIES LIST PAGE
        #
        # Inspect iframes with Chrome->DevTools->Console:
        #
        # document.querySelectorAll("iframe").forEach((f, i) => {
        #   console.log(`Iframe ${i}:`, f, "src =", f.src || "(no src)");
        # });

        #
        # Iterate over every match in every matching series.
        #
        rows = self.load_matches_page(series_pattern)

        data = []
        stuck_guard = 0
        while rows is not None:
            # Safety net: the ASP row list is month-headers (1 cell) + matches
            # (9 cells); anything else means the index walked off the end.
            if self.row_idx > self.row_count + 5:
                log.error("row index %d exceeded row count %d without advancing "
                          "series; aborting series walk", self.row_idx, self.row_count)
                self.run_flags.append("row_walk_overrun")
                break

            row = rows.nth(self.row_idx)
            self.row_idx += 1

            cell_count = row.locator("td").count()

            if cell_count == 1:
                txt = row.locator("td").first.inner_text().strip()
                try:
                    self.curr_month_year = datetime.strptime(txt, "%B %Y")
                except ValueError:
                    log.debug("non-month single-cell row %r", txt)
                continue
            elif cell_count == 9:
                d, _ = self.parse_single_match(row)
                if d is not None:
                    data.extend(d)
                    self._match_n += 1
                    if self.max_matches and self._match_n >= self.max_matches:
                        log.info("stopping after --max-matches=%d", self.max_matches)
                        break
                # Match detail navigation replaced the list; rebuild it.
                rows = self.load_matches_page(series_pattern)
                stuck_guard = 0
                continue
            else:
                stuck_guard += 1
                if stuck_guard > 50:
                    log.error("50 consecutive unrecognised rows; aborting")
                    self.run_flags.append("unrecognised_rows")
                    break

        log.info("run %d: %d rows harvested; run flags: %s",
                 self.run_idx, len(data), self.run_flags or "none")
        return data

    def _wait_kallelser_table_stable(self, fl, min_stable=3, timeout_s=10):
        """Poll the Kallelser table's row count until it's unchanged across
        `min_stable` consecutive polls (mirrors
        wait_for_matches_iframe_to_complete's stabilization loop).

        wait_for_blazor_idle() alone is NOT reliable here: __blazorIdle
        isn't always detected in this particular frame (falls back to a
        generic dom-quiet heuristic), which can report "settled" before a
        checkbox/dropdown-triggered SignalR round-trip has actually
        refreshed the grid - verified live: without this, a checkbox
        toggle sometimes silently no-ops, leaving the default
        "upcoming only" list in place.
        """
        rows = fl.locator("table.idealis-table tbody tr")
        prev = -1
        stable = 0
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                n = rows.count()
            except Exception:
                n = -1
            if n == prev:
                stable += 1
                if stable >= min_stable:
                    return n
            else:
                stable = 0
                prev = n
            time.sleep(0.2)
        log.warning("Kallelser table row count did not stabilize (last=%s)", prev)
        return prev

    def load_trainings_page(self, activity_type):
        """Navigate to the 'Kallelser' tab and return the current row
        locator, or None once every activity has been walked.

        Unlike Matcher (classic ASP), Kallelser is rendered entirely inside
        the Blazor #vpframe_1 iframe. There is also no series to walk
        through: the whole filtered list is one flat sequence.

        Both filters (checkbox + Typ) are re-verified and, if needed,
        re-applied on EVERY call, not just the first - verified live: after
        opening an activity's detail page and clicking "Kallelser" again to
        come back, the SPA can silently revert to its defaults (checkbox
        re-checked -> "upcoming only", Typ back to "Alla"). When that
        happens mid-walk, row_idx keeps counting into whatever the reverted
        list now shows at that position - a future, unrelated activity -
        while curr_month_year is still whatever it was from the real "vår"
        data, silently misattributing that activity to the wrong date. Row
        count is likewise recomputed fresh every call rather than cached,
        so a reversion is corrected immediately instead of compounding.
        """
        # This entry sequence occasionally times out under load (confirmed
        # live: neither the table nor the "tomt" placeholder appears within
        # 20s) - a handful of retries here is cheap next to losing an
        # entire run's progress (output is only written at the very end).
        for attempt in range(4):
            try:
                self.page.get_by_role("link", name="Kallelser").click()
                frame = self.page.wait_for_selector("#vpframe_1", timeout=45000).content_frame()
                self.wait_for_blazor_idle(frame)
                fl = self.page.frame_locator("#vpframe_1")
                # The initial render can legitimately show "Här var det
                # tomt..." instead of a table - e.g. Grupp defaults to a
                # past year while "Endast kommande aktiviteter" is still
                # checked, which genuinely has zero matches - so wait for
                # either rather than requiring the table specifically; the
                # filter logic below (which tolerates a zero-row table)
                # sorts out the real state from there.
                fl.locator("table.idealis-table").or_(fl.get_by_text("Här var det tomt")) \
                    .first.wait_for(timeout=30000)
                break
            except PlaywrightTimeoutError:
                if attempt == 3:
                    raise
                log.warning("Kallelser entry navigation timed out (attempt %d/4); "
                            "reloading and retrying", attempt + 1)
                try:
                    self.page.reload()
                    self.page.wait_for_load_state("load", timeout=20000)
                except Exception as e:
                    log.debug("Kallelser entry reload raced: %s", e)
                time.sleep(3)

        # Four filters - Grupp (season/year), "Endast kommande
        # aktiviteter" (upcoming-only), Typ (activity type), and Rader per
        # sida (rows/page) - all independently revert to their defaults
        # after a Visa-and-back navigation. Confirmed live: fixing them in
        # one linear pass isn't enough - re-selecting one can itself reset
        # another that was already fixed earlier in the same pass (e.g.
        # changing Typ was observed to silently revert Grupp back to its
        # placeholder), which a single pass has no way to catch. Loop the
        # whole check-and-fix pass until one iteration makes no changes.
        current_grupp = current_typ = None
        for _ in range(6):
            changed = False

            # "Grupp" (options like "Fotboll 2025 - Fotboll P 2014 (40/9)")
            # is what actually scopes Kallelser to a season/year - confirmed
            # live against a screenshot after this went unhandled: without
            # it, the page silently stays on whatever grupp was last
            # selected (e.g. by an earlier Närvaro scrape in the same run,
            # or the site's own default), regardless of --year.
            if self.training_year:
                grupp_select = fl.locator("select").nth(0)
                current_grupp = grupp_select.evaluate(
                    "el => el.selectedOptions[0]?.textContent || ''")
                if str(self.training_year) not in current_grupp:
                    if self.trainings_filter_set:
                        log.warning("Kallelser 'Grupp' filter had reverted to %r; "
                                    "re-selecting year %r", current_grupp, self.training_year)
                    options = grupp_select.locator("option").all_inner_texts()
                    matching = [o for o in options if str(self.training_year) in o]
                    if len(matching) == 1:
                        try:
                            grupp_select.select_option(label=matching[0])
                            self._wait_kallelser_table_stable(fl)
                        except Exception as e:
                            log.debug("Kallelser 'Grupp' select raced: %s", e)
                        changed = True
                    else:
                        log.error("Kallelser 'Grupp' options matching year %r: %s "
                                  "(expected exactly 1)", self.training_year, matching)

            # "Endast kommande aktiviteter" hides everything before today
            # when checked; re-uncheck it any time it's found checked again.
            # Confirmed live: is_checked() can be stale by the time uncheck()
            # actually runs (an async re-render already flipped it back),
            # which Playwright treats as a hard error ("did not change its
            # state") rather than a no-op - harmless here, just retry.
            checkboxes = fl.locator("input[type=checkbox]")
            if checkboxes.count() > 0 and checkboxes.nth(0).is_checked():
                if self.trainings_filter_set:
                    log.warning("Kallelser 'upcoming only' filter had reverted; re-unchecking")
                try:
                    checkboxes.nth(0).uncheck(force=True)
                    self._wait_kallelser_table_stable(fl)
                except Exception as e:
                    log.debug("Kallelser checkbox uncheck raced: %s", e)
                changed = True

            # "Typ" dropdown (options: Alla/Träning/Match-Tävling/Övrigt/
            # Möte/Flerdagsaktivitet) filters by activity type; matched by
            # visible label so a caller-supplied --activity-type just works.
            typ_select = fl.locator("select").nth(1)
            current_typ = typ_select.evaluate("el => el.selectedOptions[0]?.textContent || ''")
            if current_typ != activity_type:
                if self.trainings_filter_set:
                    log.warning("Kallelser 'Typ' filter had reverted to %r; re-selecting %r",
                                current_typ, activity_type)
                try:
                    typ_select.select_option(label=activity_type)
                    self._wait_kallelser_table_stable(fl)
                except Exception as e:
                    log.debug("Kallelser 'Typ' select raced: %s", e)
                changed = True

            # "Rader per sida" (rows per page: 30/60/100) - this list has
            # its own pagination separate from everything above; at the
            # default 30 a full year's worth of trainings silently gets
            # truncated to the first page with no error. Maximize it to cut
            # down how often the next-page click below is needed.
            page_size = fl.locator(".fast-page-pageindicator", has_text=re.compile(r"^100$")).first
            if page_size.count() and "active-pageindicator" not in (page_size.get_attribute("class") or ""):
                try:
                    page_size.click()
                    self._wait_kallelser_table_stable(fl)
                except Exception as e:
                    log.debug("Kallelser page-size click raced: %s", e)
                changed = True

            if not changed:
                break
        else:
            log.warning("Kallelser filters did not converge after 6 passes "
                        "(grupp=%r typ=%r)", current_grupp, current_typ)

        self.row_count = fl.locator("table.idealis-table tbody tr").count()
        if not self.trainings_filter_set:
            log.info("training list: %d rows after filtering to type=%r",
                     self.row_count, activity_type)
            self.trainings_filter_set = True
            # ".fast-page-label" reads "1-100 av 116" - capture the true
            # grand total across all pages, used below to tell a genuinely
            # exhausted list apart from a transient empty render.
            label = fl.locator(".fast-page-label").first
            if label.count():
                m = re.search(r"av (\d+)", label.inner_text())
                if m:
                    self._training_list_total = int(m.group(1))

        if self.row_idx >= self.row_count:
            # Confirmed live: a full year's Träning list spans multiple
            # pages even at 100 rows/page. ".fast-range" holds the two
            # prev/next arrow buttons (icon-only, no text/aria-label); the
            # next one is `disabled` on the last page. Confirmed live this
            # paginator (like ".fast-page-picker") renders twice in the
            # DOM - scope to the first ".fast-range" block specifically,
            # rather than nth(1) across all of them, which could otherwise
            # resolve to the wrong duplicate's prev/next button.
            next_btn = fl.locator(".fast-range").first.locator("button").nth(1)
            if next_btn.count() and next_btn.get_attribute("disabled") is None:
                log.info("training list: page exhausted (%d rows); advancing to next page",
                         self.row_count)
                next_btn.click()
                self._wait_kallelser_table_stable(fl)
                self.row_idx = 0
                self.row_count = fl.locator("table.idealis-table tbody tr").count()
            elif (self.row_count == 0 and self._kallelser_reload_retries > 0
                  and (not self._training_list_total
                       or len(self._seen_training_rows) < self._training_list_total)):
                # Confirmed live: this can also happen on the very first
                # call (before self._training_list_total is even known,
                # e.g. right after login, before Kallelser's own render has
                # caught up with its filter dropdowns already showing the
                # correct values) - not just mid-walk. Retry whenever the
                # true total isn't known yet, or is known and not yet
                # fully harvested; only a KNOWN total already fully
                # harvested skips this branch as genuine completion.
                #
                # The filter-convergence loop above can still land on a
                # transient empty render it doesn't catch
                # (e.g. a delayed reversion firing just after the loop's
                # last no-op pass). We know real data remains (total from
                # the page label > distinct rows harvested so far), so this
                # isn't genuine completion - a full page reload has been
                # the only reliable recovery observed for it.
                self._kallelser_reload_retries -= 1
                log.warning("training list: 0 rows but %d/%s harvested so far; "
                            "hard-reloading Kallelser and retrying (%d retries left)",
                            len(self._seen_training_rows),
                            self._training_list_total if self._training_list_total else "?",
                            self._kallelser_reload_retries)
                # Insurance against misattributing dates after landing back
                # on an early page post-reload: force re-discovery of the
                # current month header rather than trusting whatever value
                # was current before the reload.
                self.curr_month_year = None
                try:
                    self.page.reload()
                    self.page.wait_for_load_state("load", timeout=20000)
                except Exception as e:
                    log.debug("Kallelser reload raced: %s", e)
                time.sleep(3)
                try:
                    return self.load_trainings_page(activity_type)
                except PlaywrightTimeoutError as e:
                    if self._kallelser_reload_retries <= 0:
                        raise
                    log.warning("training list: retry after reload still failed (%s); "
                                "trying again (%d retries left)",
                                e, self._kallelser_reload_retries)
                    return self.load_trainings_page(activity_type)
            else:
                log.info("all %d training rows done", self.row_count)
                return None

        return fl.locator("table.idealis-table tbody tr")

    def parse_single_training(self, row):
        """Open one training activity from the Kallelser list and harvest
        the 'Kommer'/'Kommer ej' attendance tabs.

        Returns (rows, "") where rows is a list of
        [date, activity_id, activity_name, name, state, location, excused,
        comment] (empty list for a skipped/out-of-range activity, None for
        a non-activity row such as a month header). "excused" is always
        written blank here - see the comment at its append site below.
        """
        cells = row.locator("td")
        if cells.count() != 9:
            return (None, "")

        # column layout (confirmed live against Kallelser's Blazor grid):
        # DATUM, TID, AKTIVITET, PLATS, SCHEMALAGD, KOMMER, KOMMER EJ,
        # EJ SVARAT, [Visa button] - the same 9-cell shape as a match row,
        # but month-header rows are ALSO 9 cells here (mostly empty), so
        # cell count alone can't distinguish them; the Visa-button check
        # below does that instead.
        activity_name = cells.nth(2).inner_text().strip()
        location = cells.nth(3).inner_text().strip()

        if row.get_by_role("button", name="Visa").count() == 0:
            return (None, "")

        if self.expected_activity_name and activity_name != self.expected_activity_name:
            # Verified live: type "Träning" is NOT always 1:1 with name
            # "Träning" - inter-club friendlies ("X-Y randigt") and named
            # sessions ("Ambitionsträning - Coerver") can share the type
            # too. Per the user's definition ("activity type is Träning
            # AND activity name is Träning"), only an exact name match
            # counts as a training session to collect; log every mismatch
            # so the exclusion stays auditable instead of silent.
            log.warning("skipping activity named %r (type %r, expected name %r)",
                        activity_name, self.activity_type, self.expected_activity_name)
            self.run_flags.append("activity_type_name_mismatch")
            return ([], "")

        if self.curr_month_year is None:
            log.warning("training activity %r appears before any month header; skipping",
                        activity_name)
            self.run_flags.append("month_header_missing")
            return (None, "")

        date_txt = cells.nth(0).inner_text().strip().rstrip("!").rstrip()
        # "20 - sön" -> the day number; month/year come from the last header.
        day_part = date_txt.split("-")[0].strip()
        try:
            date = self.curr_month_year.replace(day=int(day_part))
        except ValueError:
            log.warning("training activity %r: unparseable date %r; skipping",
                        activity_name, date_txt)
            self.run_flags.append("date_unparsed")
            return (None, "")

        if self.start_date and date < self.start_date:
            return ([], "")
        if self.end_date and date > self.end_date:
            return ([], "")

        try:
            row.get_by_role("button", name="Visa").click(timeout=20000)
        except PlaywrightTimeoutError:
            log.error("training %r: 'Visa' click timed out; skipping", activity_name)
            self.run_flags.append("visa_click_timeout")
            return ([], "")

        frame = self.page.wait_for_selector("#vpframe_1").content_frame()
        self.wait_for_blazor_idle(frame)
        fl = self.page.frame_locator("#vpframe_1")
        try:
            fl.locator("table.idealis-table").first.wait_for(timeout=15000)
        except PlaywrightTimeoutError:
            log.error("training %r: attendance table never appeared; skipping", activity_name)
            self.run_flags.append("attendance_table_missing")
            return ([], "")

        # Kallelser only exposes the activity id in the detail page's URL
        # (activityId=NNNN), unlike a match's visible matchid cell.
        m = re.search(r"activityId=(\d+)", frame.url)
        activity_id = int(m.group(1)) if m else f"{date.date().isoformat()}:{activity_name}"

        # Verified live: __blazorIdle isn't reliably detected on this page,
        # so wait_for_blazor_idle()/the table appearing are NOT proof the
        # tab labels have their real counts yet - the very first activity
        # opened in a run tends to be fine (it got a slower page transition
        # incidentally), but every one after it can read a stale "(0/0)"
        # Kommer label if harvested immediately. Poll the "Kommer" tab's
        # own label until it matches the count the list row already showed
        # (ground truth, read before navigating away above).
        # Verified live: neither wait_for_blazor_idle() nor the attendance
        # table appearing means the tab labels have their real "(N/M)"
        # counts yet - the label (and the list's own aggregate cell) can
        # sit at a transient "(0/0)"/"-" placeholder for a moment after
        # navigation. Poll the "Kommer" label for stability (unchanged
        # across 3 reads) rather than trusting the first read.
        kommer_button = fl.get_by_role("button", name=re.compile(r"^Kommer \("))
        prev_txt, stable, deadline = None, 0, time.time() + 8
        while time.time() < deadline:
            try:
                txt = kommer_button.inner_text(timeout=1000)
            except Exception as e:
                txt = None
                log.debug("training %s: kommer_button.inner_text() failed: %s", activity_id, e)
            if txt and txt == prev_txt:
                stable += 1
                if stable >= 3:
                    break
            else:
                stable = 0
                prev_txt = txt
            time.sleep(0.25)
        else:
            log.warning("training %s: Kommer tab label never stabilized "
                        "(last seen %r); reading anyway", activity_id, prev_txt)
            self.run_flags.append("tab_label_unstable")
        log.debug("training %s: Kommer label after stability wait: %r", activity_id, prev_txt)

        training_rows = []
        for tab_label, tab_pat in self.TRAINING_TABS:
            try:
                t = self.read_tab(tab_pat, tab_label)
            except PlaywrightTimeoutError as e:
                log.error("training %s tab %s: %s", activity_id, tab_label, e)
                self.run_flags.append("tab_read_timeout")
                t = {"label_N": None, "label_M": None, "members": [],
                     "leaders": [], "scroll_iters": 0, "flags": ["read_timeout"]}
            for mem in t["members"]:
                comment = ""
                if tab_label == "Kommer ej":
                    # Confirmed live: this tab's raw cells are
                    # ["", year, name, kallad, läst, svarade, KOMMENTAR, ...].
                    raw = mem.get("raw_cells") or []
                    if len(raw) > 6:
                        comment = raw[6]
                # "excused" is blank at scrape time - a later, separate
                # categorization step (classify_absences.py, manual or
                # LLM-driven) fills it in as "ok"/"not" for Kommer-ej rows.
                training_rows.append([date.date().isoformat(), activity_id,
                                      activity_name, mem["name"], mem["state"],
                                      location, "", comment])

        parsed = {lbl: sum(1 for r in training_rows if r[4] == lbl)
                  for lbl, _ in self.TRAINING_TABS}
        log.info("training %s %s %-10s parsed=%s",
                 activity_id, date.date(), activity_name, list(parsed.values()))

        return (training_rows, "")

    def collect_trainings(self, email, password, start_date, end_date,
                           activity_type, activity_name=None, year=None,
                           run_idx=1, max_matches=0):
        """Run one full training-session scrape. Returns the list of
        harvested CSV rows: [date, activity_id, activity_name, name, state,
        location, excused, comment]."""
        self.start_date = start_date
        self.end_date = end_date
        self.run_idx = run_idx
        self.max_matches = max_matches
        self.activity_type = activity_type
        self.expected_activity_name = activity_name
        self.training_year = year
        self._match_n = 0
        self.run_flags = []
        self.curr_month_year = None
        self.row_count = 0
        self.row_idx = 0
        self.trainings_filter_set = False
        self._seen_training_rows = set()
        self._training_list_total = None
        self._kallelser_reload_retries = 30
        self._max_training_date_seen = None

        self._login(email, password)

        rows = self.load_trainings_page(activity_type)

        data = []
        stuck_guard = 0
        no_progress_refreshes = 0
        try:
            data = self._walk_training_rows(rows, activity_type, stuck_guard, no_progress_refreshes)
        except PlaywrightError as e:
            # Output is only written once, at the very end - losing an
            # exception here would silently discard every activity already
            # harvested this run instead of writing what's real. Every
            # retry/reload mechanism above already exhausts its own budget
            # before propagating, so reaching here means genuinely
            # unrecoverable site trouble - log it and keep what we have.
            log.error("training walk aborted by an unrecoverable error (%s); "
                      "keeping %d rows already harvested", e, len(self._training_data))
            self.run_flags.append("walk_aborted_by_exception")
            data = self._training_data

        log.info("run %d: %d training rows harvested; run flags: %s",
                 self.run_idx, len(data), self.run_flags or "none")
        return data

    def _walk_training_rows(self, rows, activity_type, stuck_guard, no_progress_refreshes):
        self._training_data = []
        while rows is not None:
            if self.row_idx >= self.row_count:
                # Confirmed live: a dedup-skipped row (see below) does NOT
                # re-fetch the page, so a whole page of already-processed
                # rows (e.g. "Sida" having silently reverted to 1 after a
                # multi-page advance) used to run row_idx past the old
                # "+5 overrun" guard before ever getting a chance to
                # advance/reload past it. Refresh here instead, on the
                # boundary itself, same as load_trainings_page's own
                # page-advance/reload-retry logic already does after a
                # successful parse - only abort if that keeps happening
                # without ever reaching a genuinely new row.
                rows = self.load_trainings_page(activity_type)
                no_progress_refreshes += 1
                if no_progress_refreshes > 60:
                    log.error("60 consecutive page refreshes without reaching "
                              "a new row; aborting")
                    self.run_flags.append("row_walk_overrun")
                    break
                continue

            row = rows.nth(self.row_idx)
            self.row_idx += 1

            cell_count = row.locator("td").count()
            if cell_count == 0:
                # The whole table having emptied out mid-transition (e.g.
                # between a page-advance click and its re-render) reads as
                # every remaining row having 0 cells - racing through 50 of
                # those in well under a second used to trip the guard below
                # instantly. Force the row_idx>=row_count refresh path
                # instead of blindly continuing through a stale locator.
                self.row_idx = self.row_count
                continue
            if cell_count != 9:
                stuck_guard += 1
                if stuck_guard > 50:
                    log.error("50 consecutive unrecognised rows; aborting")
                    self.run_flags.append("unrecognised_rows")
                    break
                continue

            if row.get_by_role("button", name="Visa").count() == 0:
                # Month-header (or otherwise non-activity) row - still 9
                # <td>s in Kallelser's Blazor grid, unlike Matcher's 1-cell
                # headers, so this is the discriminator instead of cell count.
                txt = row.locator("td").first.inner_text().strip()
                try:
                    self.curr_month_year = datetime.strptime(txt, "%B %Y")
                    log.debug("training: month header %r -> %s", txt, self.curr_month_year)
                except ValueError:
                    log.debug("training: unparsed month header %r (curr_month_year stays %s)",
                              txt, self.curr_month_year)
                stuck_guard = 0
                continue

            # Defensive dedup against the (date, time, activity) text, not
            # just row_idx - confirmed live that the checkbox/Typ filters
            # silently revert to defaults after every Visa-and-back
            # navigation; if "Sida" (page) ever reverts the same way after
            # a multi-page advance, this stops it from silently
            # re-processing already-harvested rows instead of just
            # re-deriving the same filter state at the same page.
            fingerprint = tuple(row.locator("td").nth(i).inner_text().strip() for i in (0, 1, 2))
            if fingerprint in self._seen_training_rows:
                log.debug("training: skipping already-processed row %r", fingerprint)
                stuck_guard = 0
                continue
            self._seen_training_rows.add(fingerprint)

            d, _ = self.parse_single_training(row)
            if d:
                # Confirmed live: a stale curr_month_year surviving a hard
                # reload can misattribute an activity to the wrong month
                # (e.g. a real 2025-01-29 activity got written as
                # 2025-11-29 after one). We walk forward chronologically by
                # construction, so a date this far behind the latest one
                # already harvested is a red flag, not legitimate data -
                # drop it rather than writing a silently wrong date; it'll
                # either get picked up correctly later in the walk, or
                # surface as a gap for manual follow-up.
                row_date = d[0][0]
                if (self._max_training_date_seen is not None
                        and row_date < self._max_training_date_seen
                        and (datetime.fromisoformat(self._max_training_date_seen)
                             - datetime.fromisoformat(row_date)).days > 60):
                    log.error("training activity %s: date %s implausibly far before "
                              "latest date seen (%s) - likely date misattribution "
                              "after a reload; dropping this activity",
                              d[0][1], row_date, self._max_training_date_seen)
                    self.run_flags.append("date_misattribution_suspected")
                    d = None
                else:
                    if self._max_training_date_seen is None or row_date > self._max_training_date_seen:
                        self._max_training_date_seen = row_date

            if d:
                self._training_data.extend(d)
                self._match_n += 1
                no_progress_refreshes = 0
                if self.max_matches and self._match_n >= self.max_matches:
                    log.info("stopping after --max-matches=%d", self.max_matches)
                    break
            # Activity detail navigation replaced the list; rebuild it.
            rows = self.load_trainings_page(activity_type)
            stuck_guard = 0

        return self._training_data

    def close(self) -> None:
        self.context.close()
        self.browser.close()


def _write_csv(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f, delimiter=",", quotechar="|", quoting=csv.QUOTE_MINIMAL)
        for r in rows:
            w.writerow(r)
    log.info("wrote %d rows -> %s", len(rows), path)


def _write_jsonl(path, records):
    with open(path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    log.info("wrote %d verify records -> %s", len(records), path)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Scrape SportAdmin match attendance into a CSV.")

    # Credentials are read only from the untracked .credentials file — never
    # from the command line or environment.
    ap.add_argument("--credentials", default=None,
                    help="Path to credentials file "
                         "(default: ./.credentials, then alongside this script)")
    ap.add_argument("--start-date", default=None,
                    help="Earliest match date to keep (YYYY-MM-DD; default: no lower bound)")
    ap.add_argument("--end-date", default=None,
                    help="Latest match date to keep (YYYY-MM-DD; default: no upper bound)")
    ap.add_argument("--year", default=None,
                    help="Year to select in the Period dropdown (default: current year)")
    ap.add_argument("--series-pattern", default=None,
                    help="Regex matched against series link names (e.g. vår). "
                         "In --trainings mode this isn't used for navigation "
                         "(Kallelser has no series concept) but still feeds "
                         "the output filename scope, e.g. --series-pattern vår "
                         "-> ..._vår_2026_training.csv")
    ap.add_argument("--trainings", action="store_true",
                    help="Scrape training sessions from the 'Kallelser' tab "
                         "instead of matches from 'Matcher'. Writes a "
                         "..._training.csv file. --activity-type/"
                         "--activity-name apply only in this mode.")
    ap.add_argument("--activity-type", default="Träning",
                    help="Activity type to select on the Kallelser page "
                         "(--trainings mode only; default: Träning)")
    ap.add_argument("--activity-name", default="Träning",
                    help="Expected activity name for the selected "
                         "--activity-type (--trainings mode only; default: "
                         "Träning) - every harvested activity is checked "
                         "against this and a mismatch is logged as a "
                         "warning, verifying the two are actually 1:1 "
                         "rather than assuming it")
    ap.add_argument("--runs", type=int, default=1,
                    help="Scrape N times in one session; writes PREFIX.run{k}.csv "
                         "per run and a majority-merged PREFIX.csv")
    ap.add_argument("--repeat", type=int, default=None,
                    help="Deprecated alias for --runs")
    ap.add_argument("--output", default=None,
                    help="Output path prefix (default: SCRAPER_OUTPUT from "
                         ".settings, or ./sportadmin if unset; --series-pattern "
                         "and --year (defaulted if not given) plus --start-date/"
                         "--end-date (only if given) are appended to it)")
    ap.add_argument("--verify", action="store_true",
                    help="Run per-match consistency checks; write PREFIX_verify.jsonl")
    ap.add_argument("--max-matches", type=int, default=0,
                    help="Stop after N matches (tuning aid; 0 = all)")
    ap.add_argument("--debug", action="store_true", help="Verbose logging")
    args = ap.parse_args()

    if args.trainings and args.verify:
        ap.error("--verify is not supported together with --trainings "
                 "(sa_checks/verify_scrape are match-specific)")

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )

    runs = args.repeat if args.repeat is not None else args.runs
    runs = max(1, runs)

    args.year = args.year or datetime.now().strftime("%Y")
    args.series_pattern = args.series_pattern or ""
    # --start-date/--end-date stay None when not given - no date filtering
    # is applied (see SportadminGamesScraper.collect()) - rather than
    # silently defaulting to a wide range.

    # The resolved scope values narrow the filename, so e.g. two runs a
    # year apart never silently collide on the same prefix. Unset dates
    # are simply omitted.
    scope_parts = [re.sub(r"[^0-9A-Za-zÅÄÖåäö]+", "-", v).strip("-")
                   for v in (args.series_pattern, args.year, args.start_date, args.end_date)
                   if v]

    if args.output is not None:
        prefix = args.output
    else:
        base = load_settings().get("SCRAPER_OUTPUT", "").strip() or "sportadmin"
        prefix = "_".join([base] + scope_parts)
    narvaro_prefix = prefix  # sibling to _training, not chained after it
    if args.trainings:
        prefix += "_training"
    out_dir = os.path.dirname(prefix)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    start_date = datetime.fromisoformat(args.start_date) if args.start_date else None
    end_date = datetime.fromisoformat(args.end_date) if args.end_date else None
    email, password = load_credentials(args.credentials)

    exit_code = 0
    with sync_playwright() as playwright:
        sp = SportadminGamesScraper(playwright, verify=args.verify)
        all_runs = []
        try:
            for k in range(1, runs + 1):
                if runs > 1:
                    log.info("=== run %d/%d ===", k, runs)
                if args.trainings:
                    rows = sp.collect_trainings(email, password, start_date, end_date,
                                                args.activity_type, args.activity_name,
                                                year=args.year, run_idx=k,
                                                max_matches=args.max_matches)
                else:
                    rows = sp.collect(email, password, start_date, end_date,
                                      args.series_pattern, args.year, run_idx=k,
                                      max_matches=args.max_matches)
                all_runs.append(rows)
                _write_csv(f"{prefix}.run{k}.csv" if runs > 1 else f"{prefix}.csv", rows)

                if args.trainings and k == 1:
                    # Actual attendance, not subject to the live-tab-race
                    # flakiness --runs exists for - scraped once regardless
                    # of --runs N.
                    narvaro_rows = sp.collect_narvaro(args.year, args.activity_name)
                    _write_csv(f"{narvaro_prefix}_narvaro.csv", narvaro_rows)
        except PlaywrightTimeoutError as e:
            log.error("PlaywrightTimeoutError: %s", e)
            traceback.print_exc()
            exit_code = 1
        finally:
            sp.close()

        if runs > 1 and all_runs:
            if args.trainings:
                merged, nondet = sa_checks.row_majority_training(all_runs)
            else:
                merged, nondet = sa_checks.row_majority(all_runs)
            _write_csv(f"{prefix}.csv", merged)
            if nondet:
                exit_code = exit_code or 2
                log.warning("%d non-deterministic (id,player) keys across %d runs",
                            len(nondet), runs)
                for nd in nondet[:20]:
                    log.warning("  nondeterministic: %s", nd)

        if args.verify:
            _write_jsonl(f"{prefix}_verify.jsonl", sp.verify_records)
            bad = [r for r in sp.verify_records if r.get("result") not in ("pass", None)]
            if bad:
                exit_code = exit_code or 2
                log.warning("%d/%d matches did not pass verification",
                            len(bad), len(sp.verify_records))

    sys.exit(exit_code)
