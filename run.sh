#!/bin/bash

#pipenv run python sportadmin_scraper.py <epost> <lösenord> --start-date 2025-05-01 --end-date 2025-05-31
#pipenv run python sportadmin_scraper.py <epost> <lösenord> --year 2025

PW_DISABLE_CRASHPAD=1 exec xvfb-run -a pipenv run python sportadmin_scraper.py --series-pattern vår <epost> <lösenord>
