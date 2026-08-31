#!/usr/bin/env bash
set -euo pipefail

# Credentials are read from the untracked ./.credentials file (chmod 600); see credentials.py.
PW_DISABLE_CRASHPAD=1 exec xvfb-run -a pipenv run python sportadmin_scraper.py --repeat 10 --series-pattern vår
