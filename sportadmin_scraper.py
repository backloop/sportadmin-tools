#!/usr/bin/python3

import re
from playwright.sync_api import Playwright, sync_playwright, expect, TimeoutError as PlaywrightTimeoutError

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

    def _printa_frame(self):
        """Return the real Frame for the classic-ASP matches table (name=printa)."""
        for f in self.page.frames:
            if f.name == "printa":
                return f
        return None

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
        if not self._logged_in:
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
                    help="Regex matched against series link names (e.g. vår)")
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
                rows = sp.collect(email, password, start_date, end_date,
                                  args.series_pattern, args.year, run_idx=k,
                                  max_matches=args.max_matches)
                all_runs.append(rows)
                _write_csv(f"{prefix}.run{k}.csv" if runs > 1 else f"{prefix}.csv", rows)
        except PlaywrightTimeoutError as e:
            log.error("PlaywrightTimeoutError: %s", e)
            traceback.print_exc()
            exit_code = 1
        finally:
            sp.close()

        if runs > 1 and all_runs:
            merged, nondet = sa_checks.row_majority(all_runs)
            _write_csv(f"{prefix}.csv", merged)
            if nondet:
                exit_code = exit_code or 2
                log.warning("%d non-deterministic (matchid,player) keys across %d runs",
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
