#!/usr/bin/python3
"""Load simple KEY=VALUE settings from an optional ``.settings`` file, shared
by ``sportadmin_scraper.py`` and ``sportadmin_analyzer.py`` (e.g.
``SCRAPER_OUTPUT``, ``ANALYZER_OUTPUT``, ``HOME_LOCATIONS``).

File lookup order mirrors credentials.py's default_path(): ``./.settings``
in the current directory, then ``.settings`` next to this module. A missing
file is not an error - callers just get an empty dict back.
"""

import os


def default_path():
    """Return the .settings file path, preferring the current directory."""
    cwd_path = os.path.join(os.getcwd(), ".settings")
    if os.path.exists(cwd_path):
        return cwd_path
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), ".settings")


def load_settings(path=None):
    """Read simple KEY=VALUE lines from an optional .settings file. Missing
    file is not an error - just returns {}."""
    path = path or default_path()
    values = {}
    if not os.path.exists(path):
        return values
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            values[key.strip()] = val.strip().strip('"').strip("'")
    return values
