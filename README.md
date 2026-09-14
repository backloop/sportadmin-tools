# sportadmin-tools
Scrape website using Playwright+Python to easily traverse login/password page and iframes.

## Setup
### Use pipenv to create a sandbox environment
    pipenv install

### Credentials
The scraper reads the SportAdmin login from an untracked `.credentials` file
in the current directory (git-ignored, keep it `chmod 600`):

    SPORTADMIN_EMAIL=you@example.com
    SPORTADMIN_PASSWORD=your-password

    chmod 600 .credentials

File lookup order: `--credentials <path>` / `./.credentials` / `.credentials`
next to the script. This file is the only source — credentials cannot be
passed as command-line arguments or environment variables.

### Settings
Both tools read optional defaults from an untracked `.settings` file
(git-ignored, `KEY=VALUE` lines, `#` comments), looked up in the same order
as `.credentials` (current directory, then next to the script). Nothing in
it is required — everything below has a fallback if the file or a given key
is missing.

    # sportadmin_analyzer.py: regex fragments matched against a match's
    # location; a match counts as "home" if any of them match, else "away".
    HOME_LOCATIONS="<location1>","<location2>"

    # sportadmin_scraper.py: path+filename prefix to write the scraped CSV
    # to; the scraper appends .csv (and .run{k}.csv/_verify.jsonl next to
    # it). Falls back to ./sportadmin.csv if unset.
    SCRAPER_OUTPUT=output_scraper/sportadmin

    # sportadmin_analyzer.py: directory to write the network graph HTML
    # to, named after the input CSV (e.g. sportadmin_vår_2025.csv ->
    # sportadmin_vår_2025.html). Falls back to the current directory
    # if unset.
    ANALYZER_OUT_DIR=output_analyzer

Each key can also be set for a single run via a matching CLI flag
(`--home-locations`, scraper's `--output`, analyzer's `--out-dir`), which
takes precedence over `.settings`. The scraper writes filenames that vary
by scope (series/year/date range — see below), so unlike the other two,
the analyzer's input CSV isn't a `.settings` default: it's a required
positional argument on every run.

### Run commands inside pipenv
    pipenv run python3 sportadmin_scraper.py --series-pattern vår --year 2025
    pipenv run python3 sportadmin_analyzer.py

Headful under Xvfb via `./run.sh`. Set `HEADLESS=1` to run headless.

## Tools
### sportadmin_scraper.py
Scrapes every match in the matching series/period and writes
`date,matchid,series,player_name,state,location` rows to `sportadmin.csv`
(delimiter `,`, quotechar `|`). One row per club member per match, taken from
the four attendance tabs (`Kommer`, `Kommer ej`, `Ej svarat`, `Ej kallad`).

Key flags:

| flag | meaning |
|---|---|
| `--series-pattern RE` | regex matched against series link names (e.g. `vår`) |
| `--year YYYY` | Period dropdown selection (default: current year) |
| `--start-date` / `--end-date` | keep only matches in the range (default: no bound on that side) |
| `--runs N` | scrape N times; write `PREFIX.run{k}.csv` + a majority-merged `PREFIX.csv` |
| `--output PREFIX` | output path prefix (default: `SCRAPER_OUTPUT` from `.settings` or `./sportadmin` if unset, with `--series-pattern`/`--year` appended (defaulted if not given) plus `--start-date`/`--end-date` appended only if given, e.g. `sportadmin_vår_2025.csv`; the directory is created if missing) |
| `--verify` | run per-match consistency checks; write `PREFIX_verify.jsonl` |
| `--max-matches N` | stop after N matches (tuning aid) |
| `--debug` | verbose logging |

The attendance grid is Blazor Server (server-rendered DOM over a SignalR
WebSocket) inside a cross-origin iframe and uses row virtualization.
`blazor_idle.js` (installed via `add_init_script`) instruments the socket so
`wait_for_blazor_idle()` can tell when a render batch has settled; each tab is
then scroll-harvested until every row is read.

### Verification

`--verify` writes one JSON record per match to `PREFIX_verify.jsonl` with the
tab labels `(N/M)`, parsed member/leader counts, and the result of eight
consistency assertions (`sa_checks.py`) — e.g. Σ parsed members == Σ label N,
every `(matchid, player)` unique across tabs, every state in the known set,
no dirty names. Nothing is ever discarded: all harvested rows are written and
anything suspect is flagged in the jsonl (`grep '"result": "warn"'`).

`verify_scrape.py` re-runs the CSV-checkable subset offline, plus cross-run
determinism and an optional `--baseline` diff:

    ./run_verify.sh sportadmin_vt25.csv        # 5 runs + checks vs a baseline
    ./run_10x.sh sportadmin_vt25.csv           # 10 runs (thin wrapper around run_verify.sh)
    pipenv run python verify_scrape.py --csv sportadmin.csv --season vår

Note: when re-scraping a past season, the `Ej kallad` tab reflects *current* club
membership — players who have since left will be absent (and the tab labels
exclude them too), so an old baseline can legitimately differ by those names.

### sportadmin_analyzer.py
Reads `sportadmin.csv` and prints player statistics to the console:
* Total reported availability, pre-reported or actually played.
* How many times each player played in each series.
* Which weekends players played multiple games.

    pipenv run python3 sportadmin_analyzer.py sportadmin.csv

`INPUT` (the raw scraped CSV) is a required positional argument — the
scraper's output filenames vary by scope (series/year/date range), so
there's no fixed default to fall back to. Prompts interactively for the
season (`vår`/`höst`/`vinter`) on every run. Flags:
`-o`/`--obfuscate` (replace player names with `Player_NN` everywhere, as
in the samples below), and `--home-locations PATTERN [PATTERN ...]`
(regex fragments matched against each match's venue; a match is "home" if
any pattern matches, else "away" — the availability and played-per-series
tables split their bars into home/away). With no `--home-locations` given,
it falls back to `HOME_LOCATIONS` (comma-separated) in a `.settings` file
next to the script, e.g.:

    HOME_LOCATIONS="<location1>","<location2>"

and `--out-dir DIR` (output directory for the network graph HTML; default:
`ANALYZER_OUT_DIR` from `.settings`, or the current directory if unset;
the directory is created if missing).

It also writes an HTML file into that directory, named after the input CSV
(e.g. `sportadmin_vår_2025.csv` -> `sportadmin_vår_2025.html`): an
interactive force-directed graph of which players actually played matches
together, clustered live in the browser — a k-clique-percolation algorithm
re-runs as you drag the "Minst
antal matcher ihop" (minimum matches together) slider, so tightening or
loosening the threshold regroups and recolors players on the fly (a player
can end up in zero, one, or several groups). The same page opens with
three season-overview tables — reported availability, matches played per
series, and double-booked weeks — the same data as the three console
reports above, rendered as bar tables. Open the file directly in a
browser; it needs no server.

## Output samples
```
======================================
Fördelning av spelade matcher per
serie. Varje streck är en match.

series             A1       B3      D1
player name                           
Player_01    ||||||||    |||||        
Player_41    ||||||||      |||        
Player_10    ||||||||       ||        
Player_18    ||||||||        |        
Player_20    ||||||||        |        
Player_06     |||||||    |||||        
Player_31     |||||||     ||||        
Player_43        ||||                 
Player_02         |||    |||||       |
Player_40         |||      |||       |
Player_24         |||      |||        
Player_14         |||       ||        
Player_11          ||  |||||||        
Player_04          ||    |||||       |
Player_08          ||    |||||        
Player_25          ||     ||||      ||
Player_15          ||      |||       |
Player_29          ||        |       |
Player_42          ||        |        
Player_26           |   ||||||       |
Player_07           |   ||||||        
Player_36           |       ||       |
Player_12           |        |      ||
Player_45           |        |        
Player_21           |              |||
Player_23               ||||||      ||
Player_09                   ||   |||||
Player_27                   ||   |||||
Player_05                    |   |||||
Player_03                    |    ||||
Player_19                    |    ||||
Player_17                    |     |||
Player_35                    |       |
Player_44                    |        
Player_22                       ||||||
Player_28                         ||||
Player_33                         ||||
Player_38                         ||||
Player_13                          |||
Player_16                          |||
Player_37                          |||
======================================
```

```
========================================================
Lista veckor som spelare dubblerat matcher och i vilka
serier de dubblerat vid varje tillfälle. (matcher i
andra åldersgrupper ej inräknade))

player name  count                          series_names
  Player_01      5 A1,B3 : A1,B3 : A1,B3 : A1,B3 : A1,B3
  Player_06      4         A1,B3 : A1,B3 : A1,B3 : A1,B3
  Player_31      4         A1,B3 : A1,B3 : A1,B3 : A1,B3
  Player_41      3                 A1,B3 : A1,B3 : A1,B3
  Player_10      2                         A1,B3 : A1,B3
  Player_02      1                                 B3,D1
  Player_04      1                                 B3,D1
  Player_11      1                                 A1,B3
  Player_18      1                                 A1,B3
  Player_20      1                                 A1,B3
  Player_19      1                                 B3,D1
  Player_25      1                                 B3,D1
  Player_23      1                                 B3,D1
========================================================
```

```
=====================================================================================================================================================
Fördelning av spelade matcher per serie och vecka.

week_series 33_A1 33_B3 33_D1 34_A1 34_B3 34_D1 35_A1 35_B3 35_D1 36_A1 36_B3 36_D1 37_A1 37_B3 38_A1 38_B3 38_D1 39_A1 39_B3 39_D1 40_A1 40_B3 40_D1
player name                                                                                                                                          
Player_01      A1    B3          A1    B3          A1                A1                A1    B3    A1                A1    B3          A1    B3      
Player_02            B3                B3    D1    A1                      B3                B3    A1                      B3          A1            
Player_03            B3                                        D1                D1                            D1                                  D1
Player_04            B3          A1                      B3    D1          B3                B3          B3                            A1            
Player_05            B3                      D1                                  D1                            D1                D1                D1
Player_06      A1    B3          A1                A1    B3                B3          A1    B3    A1    B3          A1                A1            
Player_07            B3                B3          A1                      B3                            B3                B3                B3      
Player_08            B3                B3          A1                                        B3          B3          A1                      B3      
Player_09            B3                      D1                D1                D1                      B3                      D1                D1
Player_10      A1    B3          A1                A1    B3          A1                A1          A1                A1                A1            
Player_11            B3                B3                B3                B3          A1                B3          A1    B3                B3      
Player_12      A1                                                          B3                                                    D1                D1
Player_13                  D1                D1                                                                                  D1                  
Player_14      A1                      B3                B3                                                          A1                A1            
Player_15                  D1    A1                      B3                B3          A1                B3                                          
Player_16                                                      D1                D1                                              D1                  
Player_17                  D1                D1                D1                                                                            B3      
Player_18      A1                A1                A1                A1                A1          A1                A1                A1    B3      
Player_19                                    D1                                  D1                      B3    D1                D1                  
Player_20      A1                A1    B3          A1                A1                A1          A1                A1                A1            
Player_21                  D1    A1                                                                                              D1                D1
Player_22                  D1                D1                D1                D1                            D1                D1                  
Player_23                  D1          B3                B3                B3                B3          B3    D1          B3                        
Player_24      A1                A1                A1                      B3                B3                                              B3      
Player_25                  D1          B3                B3    D1    A1                A1                B3                B3                        
Player_26                  D1          B3                B3          A1                      B3          B3                B3                B3      
Player_27                  D1                D1          B3                      D1                            D1                D1          B3      
Player_28                  D1                                                    D1                                              D1                D1
Player_29                                                                              A1          A1                      B3                      D1
Player_31      A1                A1    B3          A1                A1                            A1    B3          A1    B3          A1    B3      
Player_33                                    D1                D1                                              D1                D1                  
Player_35                                                                                                                  B3                      D1
Player_36                        A1                      B3                                                                B3                      D1
Player_37                                    D1                                  D1                                                                D1
Player_38                                    D1                D1                D1                                              D1                  
Player_40      A1                      B3                            A1                      B3                D1    A1                      B3      
Player_41      A1                A1                A1    B3          A1                A1    B3    A1                A1                A1    B3      
Player_42                                                            A1                      B3                                        A1            
Player_43                                                            A1                A1                            A1                A1            
Player_44                                                                  B3                                                                        
Player_45                                                                  B3                      A1                                                
=====================================================================================================================================================
```
