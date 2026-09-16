#!/usr/bin/python3
"""Build an aggregating index.html for the analyzer's output directory.

Scans a directory for *.html files (as sportadmin_analyzer.py writes them,
one per scraped CSV) and writes an index.html into the same directory with
a horizontal tab bar - one tab per file - that swaps an iframe to show the
selected page. Re-run this any time after adding/removing analyzer output
files to refresh the tab list.
"""

import argparse
import glob
import html
import os
import re

from settings import load_settings

INDEX_FILENAME = "index.html"


SEASON_ORDER = {"vår": 0, "höst": 1, "vinter": 2}


def _tab_label(path):
    """Prefer the page's own <title>; fall back to a cleaned-up filename."""
    with open(path, encoding="utf-8") as f:
        head = f.read(4096)
    m = re.search(r"<title>([^<]*)</title>", head)
    if m:
        return m.group(1).strip()
    name = os.path.splitext(os.path.basename(path))[0]
    name = re.sub(r"^sportadmin_?", "", name)
    return name.replace("_", " ") or os.path.basename(path)


def _sort_key(label):
    """Increasing year, then vår/höst/vinter within a year. Labels that
    don't carry a recognizable season/year (e.g. a hand-edited title) sort
    after everything else, alphabetically."""
    m = re.search(r"(vår|höst|vinter)\s+(\d{4})", label, re.IGNORECASE)
    if not m:
        return (1, 0, 0, label)
    season = m.group(1).lower()
    year = int(m.group(2))
    return (0, year, SEASON_ORDER[season], label)


def build_index(out_dir):
    pattern = os.path.join(out_dir, "*.html")
    files = [
        f for f in glob.glob(pattern)
        if os.path.basename(f) != INDEX_FILENAME
    ]
    if not files:
        raise SystemExit(f"No .html files found in {out_dir!r} - nothing to index")

    tabs = [{"label": _tab_label(path), "filename": os.path.basename(path)} for path in files]
    tabs.sort(key=lambda t: _sort_key(t["label"]))
    for i, t in enumerate(tabs):
        t["active"] = i == 0

    tab_buttons = "\n".join(
        f'      <button class="tab{" active" if t["active"] else ""}" '
        f'data-src="{html.escape(t["filename"])}">{html.escape(t["label"])}</button>'
        for t in tabs
    )
    first_src = html.escape(tabs[0]["filename"])

    page = f"""<!doctype html>
<html lang="sv">
<head>
<meta charset="utf-8">
<title>Spelmönster - alla säsonger</title>
<style>
  :root {{
    color-scheme: light dark;
    --bg: #f5f5f5;
    --tabbar-bg: #e2e2e2;
    --tab-fg: #444;
    --tab-active-bg: #ffffff;
    --tab-active-fg: #111;
    --border: #ccc;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #1c1c1c;
      --tabbar-bg: #2a2a2a;
      --tab-fg: #bbb;
      --tab-active-bg: #111;
      --tab-active-fg: #fff;
      --border: #444;
    }}
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ height: 100%; margin: 0; background: var(--bg); font-family: system-ui, sans-serif; }}
  #tabbar {{
    display: flex;
    flex-wrap: wrap;
    gap: 2px;
    background: var(--tabbar-bg);
    border-bottom: 1px solid var(--border);
    padding: 6px 6px 0 6px;
  }}
  .tab {{
    border: 1px solid var(--border);
    border-bottom: none;
    border-radius: 6px 6px 0 0;
    background: var(--tabbar-bg);
    color: var(--tab-fg);
    padding: 8px 16px;
    font-size: 14px;
    cursor: pointer;
  }}
  .tab.active {{
    background: var(--tab-active-bg);
    color: var(--tab-active-fg);
    font-weight: 600;
  }}
  .tab:hover {{ color: var(--tab-active-fg); }}
  #frame {{
    display: block;
    width: 100%;
    height: calc(100% - 45px);
    border: none;
    background: var(--tab-active-bg);
  }}
</style>
</head>
<body>
  <div id="tabbar">
{tab_buttons}
  </div>
  <iframe id="frame" src="{first_src}"></iframe>
  <script>
    document.getElementById("tabbar").addEventListener("click", (e) => {{
      const btn = e.target.closest(".tab");
      if (!btn) return;
      document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
      btn.classList.add("active");
      document.getElementById("frame").src = btn.dataset.src;
    }});
  </script>
</body>
</html>
"""

    out_path = os.path.join(out_dir, INDEX_FILENAME)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"wrote {len(files)} tab(s) -> {out_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Build a tabbed index.html over sportadmin_analyzer.py's output HTML files.")
    ap.add_argument("--dir", default=None,
                    help="Directory to scan (default: ANALYZER_OUT_DIR from "
                         ".settings, or the current directory if unset)")
    args = ap.parse_args()

    out_dir = args.dir
    if out_dir is None:
        out_dir = load_settings().get("ANALYZER_OUT_DIR", "").strip() or "."

    build_index(out_dir)
