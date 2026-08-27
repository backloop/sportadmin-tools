#!/usr/bin/env bash
set -euo pipefail

TARGET_SUCCESS="${TARGET_SUCCESS:-10}"
MAX_ATTEMPTS="${MAX_ATTEMPTS:-50}"
SLEEP_SECONDS="${SLEEP_SECONDS:-2}"

success=0
attempt=0

while [[ "${success}" -lt "${TARGET_SUCCESS}" ]]; do
  attempt=$((attempt + 1))
  if [[ "${attempt}" -gt "${MAX_ATTEMPTS}" ]]; then
    echo "ERROR: Reached MAX_ATTEMPTS=${MAX_ATTEMPTS} with success=${success}/${TARGET_SUCCESS}."
    exit 1
  fi

  echo "ATTEMPT ${attempt} (success=${success}/${TARGET_SUCCESS})"
  if ./run.sh; then
    success=$((success + 1))
  else
    echo "ATTEMPT ${attempt} FAILED"
    sleep "${SLEEP_SECONDS}"
  fi
done

echo "DONE: success=${success}/${TARGET_SUCCESS} within attempts=${attempt}"
