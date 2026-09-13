#!/bin/bash
set -euo pipefail

# One scrape of the spring ("vår") season into SCRAPER_OUTPUT/sportadmin.csv
# (see .settings; defaults to ./sportadmin.csv if unset).
# Credentials come from the untracked ./.credentials file (chmod 600); see
# credentials.py. Use --credentials <path> to point elsewhere.
#
# Scrapes the current year unless overridden. Other examples:
#   pipenv run python sportadmin_scraper.py --series-pattern höst --year 2025
#   pipenv run python sportadmin_scraper.py --start-date 2025-05-01 --end-date 2025-05-31
#   ./run_verify.sh                       # 5 runs + consistency checks

PW_DISABLE_CRASHPAD=1 exec xvfb-run -a pipenv run python sportadmin_scraper.py \
    --series-pattern vår
