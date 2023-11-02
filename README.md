# sportadmin-tools
Scrape website using Selenium+Python to easily traverse login/password page and iframes.

## Setup
### Use pipenv to create a sandbox environment 
    pipenv install

### Run commands inside pipenv
    pipenv run python3 sportadmin_scraper.py loginmail password
    pipenv run python3 sportadmin_analyzer.py

## Tools
### sportadmin_scaper.py
Scrapes all "match" entries and collects all available players data. Currently only supports "current year". 
Stores the result in sportadmin.csv 

### sportadmin_analyzer.py
Reads sportadmin.csv and presents player statistics
* Who's your teammate? (matrix showing how often each pplayer played together)
* How many matches did you have to pass (not being called that weekend despite being available)
* How many times did you play in each team?
