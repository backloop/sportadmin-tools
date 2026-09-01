#!/usr/bin/env bash
set -euo pipefail

# Scrape a season N times with the per-match verification harness, write a
# majority-merged CSV, then run the offline checks (cross-run determinism and,
# if a baseline is given, a diff against it).
#
#   SERIES=vår YEAR=2025 RUNS=5 ./run_verify.sh [baseline.csv]
#
# Outputs under ./out/:
#   <prefix>.run{1..N}.csv   per-run rows
#   <prefix>.csv             majority-merged rows
#   <prefix>_verify.jsonl    one record per match (grep '"result": "warn"')

SERIES="${SERIES:-vår}"
YEAR="${YEAR:-2025}"
RUNS="${RUNS:-5}"
START="${START:-${YEAR}-01-01}"
END="${END:-${YEAR}-12-31}"
PREFIX="${PREFIX:-out/${SERIES}${YEAR}}"
BASELINE="${1:-}"

mkdir -p "$(dirname "$PREFIX")"

PW_DISABLE_CRASHPAD=1 xvfb-run -a pipenv run python sportadmin_scraper.py \
    --series-pattern "$SERIES" --year "$YEAR" \
    --start-date "$START" --end-date "$END" \
    --verify --runs "$RUNS" --out "$PREFIX"

verify_args=(--csv "${PREFIX}.csv" --runs "${PREFIX}".run*.csv --season "$SERIES")
[ -n "$BASELINE" ] && verify_args+=(--baseline "$BASELINE")

pipenv run python verify_scrape.py "${verify_args[@]}"
