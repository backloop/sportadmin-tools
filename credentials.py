#!/usr/bin/python3
"""Load SportAdmin login credentials from an untracked ``.credentials`` file.

The file holds ``KEY=VALUE`` lines and must never be committed
(it is listed in ``.gitignore``). Keep it ``chmod 600``::

    SPORTADMIN_EMAIL=you@example.com
    SPORTADMIN_PASSWORD=your-password

File lookup order: the ``--credentials`` argument, then ``./.credentials`` in
the current directory, then ``.credentials`` next to this script.

The credentials file is the only source. Credentials are never read from
the environment and never accepted as command-line arguments.
"""

import os
import stat
import sys

EMAIL_KEY = "SPORTADMIN_EMAIL"
PASSWORD_KEY = "SPORTADMIN_PASSWORD"


def default_path():
    """Return the credentials file path, preferring the current directory."""
    cwd_path = os.path.join(os.getcwd(), ".credentials")
    if os.path.exists(cwd_path):
        return cwd_path
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), ".credentials")


def _parse(path):
    values = {}
    with open(path) as f:
        for lineno, raw in enumerate(f, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                raise ValueError(f"{path}:{lineno}: expected KEY=VALUE, got {raw!r}")
            key, _, val = line.partition("=")
            values[key.strip()] = val.strip().strip('"').strip("'")
    return values


def _warn_if_not_private(path):
    mode = stat.S_IMODE(os.stat(path).st_mode)
    if mode & 0o077:
        print(
            f"Warning: {path} is group/world accessible (mode {mode:03o}); "
            f"run: chmod 600 {path}",
            file=sys.stderr,
        )


def load_credentials(path=None):
    """Return ``(email, password)`` read from the credentials file.

    Exits with a helpful message if the file is missing or does not define
    both values.
    """
    path = path or default_path()

    if not os.path.exists(path):
        sys.exit(
            f"Credentials file not found: {path}\n"
            "Create it (chmod 600) containing:\n"
            "    SPORTADMIN_EMAIL=you@example.com\n"
            "    SPORTADMIN_PASSWORD=your-password"
        )

    _warn_if_not_private(path)
    try:
        values = _parse(path)
    except (OSError, ValueError) as e:
        sys.exit(f"Error reading credentials file: {e}")

    email = values.get(EMAIL_KEY)
    password = values.get(PASSWORD_KEY)

    if not email or not password:
        sys.exit(
            f"{path} must define both {EMAIL_KEY} and {PASSWORD_KEY}:\n"
            "    SPORTADMIN_EMAIL=you@example.com\n"
            "    SPORTADMIN_PASSWORD=your-password"
        )

    return email, password


if __name__ == "__main__":
    # Smoke test: report which fields resolved, without printing the password.
    e, p = load_credentials()
    print(f"email resolved: {e}")
    print(f"password resolved: {'yes (%d chars)' % len(p) if p else 'no'}")
