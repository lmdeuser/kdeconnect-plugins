#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com>
# SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL

"""Scan repository files to prevent secrets, private infrastructure details,
Cyrillic text, paid CI workflows, or binary artifacts from entering the public tree.
"""

import argparse
import ipaddress
import os
import re
import subprocess
import sys
from pathlib import Path

IPV4_RE = re.compile(rb"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b")
CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")

# Build local-path regexes dynamically so this module never triggers itself.
HOME_PATH_RE = re.compile(rb"/" + rb"home/" + rb"[^/\s\"'`]+")
SRV_TEMP_RE = re.compile(rb"/" + rb"srv/" + rb"temp\b")

SECRET_PATTERNS = [
    re.compile(rb"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(rb"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(rb"glpat-[A-Za-z0-9_-]{16,}"),
    re.compile(rb"(?i)(?:password|secret|token)\s*[=:]\s*[\"'][^\"']{8,}[\"']"),
]

FORBIDDEN_SUFFIXES = (
    ".deb",
    ".ddeb",
    ".udeb",
    ".dsc",
    ".changes",
    ".buildinfo",
    ".apk",
    ".aab",
    ".keystore",
    ".jks",
    ".pem",
    ".key",
    ".zip",
    ".tgz",
    ".tar.gz",
    ".tar.xz",
    ".tar.bz2",
    ".tar.zst",
)


def _iter_staged_entries(root: Path) -> list[tuple[str, bytes]]:
    raw = subprocess.check_output(["git", "-C", str(root), "ls-files", "-z"])
    entries: list[tuple[str, bytes]] = []
    for item in raw.split(b"\0"):
        if not item:
            continue
        rel = item.decode("utf-8")
        data = subprocess.check_output(["git", "-C", str(root), "show", f":{rel}"])
        entries.append((rel, data))
    return entries


def _iter_worktree_entries(root: Path, skip_state_dir: bool) -> list[tuple[str, bytes]]:
    entries: list[tuple[str, bytes]] = []
    for dirpath, dirs, files in os.walk(root):
        current = Path(dirpath)
        skipped_dirs = {".git", "__pycache__"}
        if current == root and skip_state_dir:
            skipped_dirs.update({".state", "upstreams", "worktrees", "LICENSES"})
        dirs[:] = sorted(d for d in dirs if d not in skipped_dirs)
        for name in sorted(files):
            if current == root and skip_state_dir and name == "LICENSES":
                continue
            if name.endswith((".pyc", ".pyo")):
                continue
            path = current / name
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            entries.append((rel, path.read_bytes()))
    entries.sort(key=lambda item: item[0])
    return entries


def _check_path_policy(rel: str, skip_state_dir: bool) -> str | None:
    lower = rel.lower()
    if rel == ".github/workflows" or rel.startswith(".github/workflows/"):
        return f"{rel}: GitHub Actions workflows are prohibited (paid runners)"
    if not skip_state_dir and (
        rel in (".state", "upstreams", "worktrees")
        or rel.startswith((".state/", "upstreams/", "worktrees/"))
    ):
        return f"{rel}: untracked .state/upstreams/worktrees files must not be published"
    if rel.endswith(FORBIDDEN_SUFFIXES) or lower.endswith(FORBIDDEN_SUFFIXES):
        return f"{rel}: binary, package, archive, or key artifact is prohibited"
    name = Path(rel).name
    if (name == ".env" or name.startswith(".env.")) and name != ".env.example":
        return f"{rel}: environment secret file is prohibited"
    return None


def _check_content_policy(rel: str, data: bytes) -> list[str]:
    issues: list[str] = []
    if b"\x00" in data:
        issues.append(f"{rel}: contains binary NUL bytes")
        return issues

    if HOME_PATH_RE.search(data):
        issues.append(f"{rel}: contains local home directory path")
    if SRV_TEMP_RE.search(data):
        issues.append(f"{rel}: contains local workstation scratch path")

    for pattern in SECRET_PATTERNS:
        if pattern.search(data):
            issues.append(f"{rel}: matched secret or private key pattern")
            break

    for match in IPV4_RE.findall(data):
        try:
            addr = ipaddress.ip_address(match.decode("ascii"))
        except ValueError:
            continue
        if not addr.is_global:
            issues.append(f"{rel}: contains non-global IPv4 address")
            break

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        issues.append(f"{rel}: is not valid UTF-8 text")
        return issues

    if CYRILLIC_RE.search(text):
        issues.append(f"{rel}: contains Cyrillic text (public tree must be English-only)")

    return issues


def scan_repository(
    root: Path,
    staged_only: bool = True,
    skip_state_dir: bool = False,
) -> list[str]:
    """Return a list of policy violation descriptions for the given repository."""
    if staged_only:
        entries = _iter_staged_entries(root)
    else:
        entries = _iter_worktree_entries(root, skip_state_dir=skip_state_dir)

    issues: list[str] = []
    for rel, data in entries:
        path_issue = _check_path_policy(rel, skip_state_dir=skip_state_dir if not staged_only else False)
        if path_issue:
            issues.append(path_issue)
            continue
        if rel.startswith("LICENSES/"):
            continue
        issues.extend(_check_content_policy(rel, data))
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate public repository tree safety")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Repository root directory (default: current working directory)",
    )
    parser.add_argument(
        "--worktree",
        action="store_true",
        help="Scan non-.state working tree files instead of the Git index",
    )
    args = parser.parse_args()

    issues = scan_repository(
        args.root.resolve(),
        staged_only=not args.worktree,
        skip_state_dir=args.worktree,
    )
    if issues:
        print("Public tree check rejected:", file=sys.stderr)
        for item in issues:
            print(f"  - {item}", file=sys.stderr)
        return 1

    print("Public tree check passed; review the complete diff before pushing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
