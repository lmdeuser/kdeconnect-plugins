#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com>
# SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL

"""Install or remove the managed KDE Connect OBS controller cron block."""

import argparse
import os
import subprocess
from pathlib import Path

BEGIN = "# BEGIN KDECONNECT OBS"
END = "# END KDECONNECT OBS"


def job() -> str:
    root = Path(__file__).resolve().parents[1]
    return f"47 * * * * /usr/bin/timeout 90m {root}/tools/cron-cycle.sh"


def remove_block(previous: str) -> str:
    lines = previous.splitlines()
    begins = [idx for idx, line in enumerate(lines) if line == BEGIN]
    ends = [idx for idx, line in enumerate(lines) if line == END]
    if len(begins) != len(ends) or len(begins) > 1 or (begins and begins[0] > ends[0]):
        raise ValueError("Ambiguous managed cron block")
    if begins:
        del lines[begins[0] : ends[0] + 1]
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def merge(previous: str) -> str:
    cleaned = remove_block(previous)
    if cleaned and not cleaned.endswith("\n"):
        cleaned += "\n"
    return cleaned + BEGIN + "\n" + job() + "\n" + END + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Install or remove the KDE Connect OBS controller cron job")
    parser.add_argument("--remove", action="store_true", help="Remove only the managed KDE Connect cron block")
    parser.add_argument("--dry-run", action="store_true", help="Print updated crontab without writing it")
    args = parser.parse_args()

    env = {**os.environ, "LC_ALL": "C"}
    result = subprocess.run(["crontab", "-l"], text=True, capture_output=True, check=False, env=env)
    no_crontab = bool(result.returncode and "no crontab for" in result.stderr.lower())
    if result.returncode and not no_crontab:
        raise SystemExit("Cannot read existing crontab; left unchanged")

    existing = "" if no_crontab else result.stdout
    if args.remove:
        updated = remove_block(existing)
        if args.dry_run:
            print(updated, end="")
            return 0
        if not updated:
            if not no_crontab:
                subprocess.run(["crontab", "-r"], check=True, env=env)
        else:
            subprocess.run(["crontab", "-"], input=updated, text=True, check=True, env=env)
        print("Removed KDE Connect controller cron block; unrelated jobs preserved.")
        return 0

    updated = merge(existing)
    if args.dry_run:
        print(updated, end="")
        return 0

    subprocess.run(["crontab", "-"], input=updated, text=True, check=True, env=env)
    print("Hourly KDE Connect controller cron installed; unrelated jobs preserved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
