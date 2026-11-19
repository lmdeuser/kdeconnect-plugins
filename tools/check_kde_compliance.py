#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com>
# SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL

"""Validate REUSE 3.0 SPDX headers for this repository and upstream KDE Invent
compliance rules on external plugin branches.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

COPYRIGHT_RE = re.compile(r"SPDX-FileCopyrightText:\s*\S+")
LICENSE_RE = re.compile(r"SPDX-License-Identifier:\s*(.+)")
LICENSE_OP_RE = re.compile(r"\b(?:OR|AND|WITH)\b")

FORBIDDEN_UPSTREAM_PREFIXES = (
    "debian/",
    "obs/",
    ".agents/",
    ".agent/",
    "prototype/",
    ".github/workflows/",
    ".state/",
)

ASCII_REQUIRED_SUFFIXES = (
    ".cpp",
    ".cc",
    ".cxx",
    ".h",
    ".hpp",
    ".c",
    ".kt",
    ".java",
    ".qml",
    ".cmake",
)

SPDX_REQUIRED_SUFFIXES = ASCII_REQUIRED_SUFFIXES + (".py", ".sh", ".xml")

AI_COAUTHOR_RE = re.compile(
    r"(?im)^Co-authored-by:\s*.*(?:claude|gemini|codex|copilot|chatgpt|openai|anthropic|assistant|antigravity|\bbot\b|\bai\b)"
)


def _known_licenses(root: Path) -> set[str]:
    licenses_dir = root / "LICENSES"
    if not licenses_dir.is_dir():
        return set()
    return {path.stem for path in licenses_dir.glob("*.txt") if path.is_file()}


def _clean_spdx_expression(raw: str) -> str:
    cleaned = raw.strip()
    for suffix in ("-->", "*/"):
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)].strip()
    return cleaned


def _extract_license_tokens(expr: str) -> list[str]:
    stripped = expr.replace("(", " ").replace(")", " ")
    tokens: list[str] = []
    for part in LICENSE_OP_RE.split(stripped):
        for item in part.split():
            if item:
                tokens.append(item)
    return tokens


def _check_header_lines(rel: str, text: str, known: set[str]) -> list[str]:
    head = "\n".join(text.splitlines()[:25])
    issues: list[str] = []
    if not COPYRIGHT_RE.search(head):
        issues.append(f"{rel}: missing SPDX-FileCopyrightText header in first 25 lines")
    lic_match = LICENSE_RE.search(head)
    if not lic_match:
        issues.append(f"{rel}: missing SPDX-License-Identifier header in first 25 lines")
        return issues

    expr = _clean_spdx_expression(lic_match.group(1))
    tokens = _extract_license_tokens(expr)
    if not tokens:
        issues.append(f"{rel}: empty SPDX-License-Identifier expression")
        return issues
    for token in tokens:
        if token not in known:
            issues.append(f"{rel}: license '{token}' has no file in LICENSES/")
    return issues


def _iter_repo_files(root: Path) -> list[Path]:
    if (root / ".git").exists():
        raw = subprocess.check_output(["git", "-C", str(root), "ls-files", "-z"])
        files = []
        for item in raw.split(b"\0"):
            if not item:
                continue
            path = root / item.decode("utf-8")
            if path.is_file():
                files.append(path)
        return files

    files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        parts = path.relative_to(root).parts
        if any(p in parts for p in (".git", ".state", "upstreams", "worktrees", "__pycache__")):
            continue
        if path.suffix in (".pyc", ".pyo"):
            continue
        files.append(path)
    return files


def check_repo_reuse(root: Path) -> list[str]:
    """Check that all repository files carry valid SPDX headers matching LICENSES/."""
    known = _known_licenses(root)
    if not known:
        return ["LICENSES/: directory is missing or contains no *.txt license texts"]

    issues: list[str] = []
    for path in _iter_repo_files(root):
        rel = path.relative_to(root).as_posix()
        if rel.startswith("LICENSES/"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            issues.append(f"{rel}: unreadable as UTF-8 text")
            continue
        issues.extend(_check_header_lines(rel, text, known))
    return issues


def _find_non_ascii_line(text: str) -> tuple[int, str] | None:
    for lineno, line in enumerate(text.splitlines(), start=1):
        for ch in line:
            if ord(ch) > 127:
                return lineno, f"U+{ord(ch):04X}"
    return None


def _find_non_ascii_in_diff(
    repo_path: Path,
    base_ref: str,
    head_ref: str,
    rel: str,
) -> tuple[int, str] | None:
    patch = subprocess.check_output(
        ["git", "-C", str(repo_path), "diff", "-U0", f"{base_ref}..{head_ref}", "--", rel],
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    lineno = 0
    for line in patch.splitlines():
        if line.startswith("@@"):
            plus_part = line.split("+", 1)[1].split(" ", 1)[0]
            lineno = int(plus_part.split(",", 1)[0])
        elif line.startswith("+") and not line.startswith("+++ "):
            for ch in line[1:]:
                if ord(ch) > 127:
                    return lineno, f"U+{ord(ch):04X}"
            lineno += 1
    return None


def check_upstream_branch(repo_path: Path, base_ref: str, head_ref: str = "HEAD") -> list[str]:
    """Validate an external KDE Connect upstream branch against Invent contribution rules."""
    issues: list[str] = []
    known = _known_licenses(repo_path)

    diff_raw = subprocess.check_output(
        ["git", "-C", str(repo_path), "diff", "--name-status", f"{base_ref}..{head_ref}"],
        text=True,
    )
    for line in diff_raw.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0]
        if status.startswith("D"):
            continue
        rel = parts[-1]

        if any(rel == p.rstrip("/") or rel.startswith(p) for p in FORBIDDEN_UPSTREAM_PREFIXES):
            issues.append(f"{rel}: non-upstream packaging or harness path leaked into upstream branch")
            continue

        try:
            content = subprocess.check_output(
                ["git", "-C", str(repo_path), "show", f"{head_ref}:{rel}"],
                text=True,
                encoding="utf-8",
            )
        except (subprocess.CalledProcessError, UnicodeDecodeError):
            continue

        name = Path(rel).name
        if rel.endswith(ASCII_REQUIRED_SUFFIXES) or name == "CMakeLists.txt":
            bad_char = (
                _find_non_ascii_line(content)
                if status.startswith("A")
                else _find_non_ascii_in_diff(repo_path, base_ref, head_ref, rel)
            )
            if bad_char:
                lineno, codepoint = bad_char
                issues.append(f"{rel}:{lineno}: contains non-ASCII character ({codepoint}); use ASCII")

        if status.startswith("A") and (rel.endswith(SPDX_REQUIRED_SUFFIXES) or name == "CMakeLists.txt"):
            if known:
                issues.extend(_check_header_lines(rel, content, known))
            else:
                head = "\n".join(content.splitlines()[:25])
                if not COPYRIGHT_RE.search(head) or not LICENSE_RE.search(head):
                    issues.append(f"{rel}: missing SPDX header")

    commit_log = subprocess.check_output(
        ["git", "-C", str(repo_path), "log", "--format=%H%n%B%n---END---", f"{base_ref}..{head_ref}"],
        text=True,
        encoding="utf-8",
    )
    for entry in commit_log.split("---END---\n"):
        if not entry.strip():
            continue
        if AI_COAUTHOR_RE.search(entry):
            sha = entry.splitlines()[0][:12]
            issues.append(f"commit {sha}: prohibited AI Co-authored-by trailer (use Assisted-by: only if requested)")

    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate REUSE 3.0 and KDE upstream branch rules")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd(), help="Harness repository root")
    parser.add_argument("--upstream-repo", type=Path, help="External KDE Connect checkout to validate")
    parser.add_argument("--base", default="origin/master", help="Base ref for upstream branch diff")
    parser.add_argument("--ref", default="HEAD", help="Target ref for upstream branch diff (default: HEAD)")
    args = parser.parse_args()

    if args.upstream_repo is not None:
        issues = check_upstream_branch(args.upstream_repo.resolve(), args.base, head_ref=args.ref)
        label = "Upstream KDE Invent compliance"
    else:
        issues = check_repo_reuse(args.repo_root.resolve())
        label = "REUSE compliance"

    if issues:
        print(f"{label} check failed:", file=sys.stderr)
        for item in issues:
            print(f"  - {item}", file=sys.stderr)
        return 1

    print(f"{label} check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
