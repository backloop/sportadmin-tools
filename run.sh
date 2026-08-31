#!/bin/bash

# Credentials are read from the untracked ./.credentials file (chmod 600).
# See credentials.py; use --credentials <path> to point elsewhere.
#
#pipenv run python sportadmin_scraper.py --start-date 2025-05-01 --end-date 2025-05-31
#pipenv run python sportadmin_scraper.py --year 2025

PW_DISABLE_CRASHPAD=1 exec xvfb-run -a pipenv run python sportadmin_scraper.py --series-pattern vår
