#!/usr/bin/env bash
set -euo pipefail

# 10 verified runs of the spring season (determinism check). Thin wrapper
# around run_verify.sh — pass a baseline CSV as $1 to also diff against it.
# Defaults to the current year; override with YEAR=2025 etc.
RUNS=10 SERIES="${SERIES:-vår}" exec ./run_verify.sh "$@"
