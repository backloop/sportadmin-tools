#!/usr/bin/env bash
set -euo pipefail

PW_DISABLE_CRASHPAD=1 exec xvfb-run -a pipenv run python sportadmin_scraper.py --repeat 10 --series-pattern vår <epost> <lösenord>
