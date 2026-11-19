#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com>
# SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL

"""Hardened upstream repository tracking, branch synthesis, and .deb verification helpers."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

MAINTAINER_NAME = "Dmitriy Volkov"
MAINTAINER_EMAIL = "dennshield@duck.com"

ALLOWED_UPSTREAM_URLS = frozenset(
    {
        "https://invent.kde.org/network/kdeconnect-kde.git",
        "https://invent.kde.org/network/kdeconnect-android.git",
    }
)

SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
SRCMD5_RE = re.compile(r"^[0-9a-f]{32}$")
SPECIAL_MODE_RE = re.compile(r"[sStT]")

ALLOWED_DEB_EXACT_PATHS = frozenset(
    {
        "./",
        "./etc",
        "./etc/",
        "./etc/ufw",
        "./etc/ufw/",
        "./etc/xdg",
        "./etc/xdg/",
        "./usr",
        "./usr/",
        "./usr/bin",
        "./usr/include",
        "./usr/lib",
        "./usr/libexec",
        "./usr/share",
    }
)
ALLOWED_DEB_PREFIXES = (
    "./usr/bin/",
    "./usr/include/",
    "./usr/lib/",
    "./usr/libexec/",
    "./usr/share/",
    "./etc/ufw/",
    "./etc/xdg/",
)

ALLOWED_CTRL_FILES = frozenset(
    {
        "./",
        "./conffiles",
        "./control",
        "./md5sums",
        "./postinst",
        "./postrm",
        "./preinst",
        "./prerm",
        "./shlibs",
        "./symbols",
        "./triggers",
    }
)

SCRUBBED_ENV_KEYS = (
    "OSC_CONFIG",
    "SSH_AUTH_SOCK",
    "SSH_AGENT_PID",
    "GIT_SSH_COMMAND",
    "GIT_SSH",
    "GIT_EXEC_PATH",
    "GIT_TEMPLATE_DIR",
    "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_COUNT",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "GITHUB_TOKEN",
    "GITLAB_TOKEN",
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
)

FORBIDDEN_TREE_TOP_PREFIXES = ("debian/", "obs/", ".state/", ".github/workflows/")


@dataclass(frozen=True)
class PluginStackEntry:
    plugin_id: str
    plugin_ref: str
    plugin_base_sha: str


@dataclass(frozen=True)
class SynthesisResult:
    ok: bool
    status: str
    synthesized_sha: str | None = None
    conflict_files: tuple[str, ...] = ()
    error: str = ""


def build_scrubbed_env(base_env: dict[str, str] | None = None, allow_protocol: str = "https") -> dict[str, str]:
    """Return a clean subprocess environment with credentials and Git/SSH hooks stripped."""
    env = dict(os.environ if base_env is None else base_env)
    for key in SCRUBBED_ENV_KEYS:
        env.pop(key, None)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ALLOW_PROTOCOL"] = allow_protocol
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_SYSTEM"] = "/dev/null"
    env["LC_ALL"] = "C"
    return env


def validate_remote_url(url: str, allow_local_test: bool = False) -> str:
    """Validate that a remote repository URL belongs to the strict upstream allowlist."""
    cleaned = url.strip()
    if cleaned in ALLOWED_UPSTREAM_URLS:
        return cleaned
    if allow_local_test and (cleaned.startswith("file://") or Path(cleaned).is_dir()):
        return cleaned
    raise ValueError(f"Rejected disallowed remote URL: {url}")


def build_hardened_git_command(
    repo_dir: Path | None,
    *git_args: str,
    allow_file_protocol: bool = False,
) -> list[str]:
    """Construct a Git invocation with hooks, symlinks, submodules, LFS, and ext protocol disabled."""
    cmd: list[str] = ["git"]
    if repo_dir is not None:
        cmd.extend(["-C", str(repo_dir)])
    cmd.extend(
        [
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.symlinks=false",
            "-c",
            "submodule.recurse=false",
            "-c",
            "protocol.allow=never",
            "-c",
            "protocol.ext.allow=never",
            "-c",
            "protocol.https.allow=always",
        ]
    )
    if allow_file_protocol:
        cmd.extend(["-c", "protocol.file.allow=always"])
    cmd.extend(
        [
            "-c",
            "filter.lfs.smudge=",
            "-c",
            "filter.lfs.clean=",
            "-c",
            "filter.lfs.process=",
            "-c",
            "fetch.fsckObjects=true",
            "-c",
            "transfer.fsckObjects=true",
            "-c",
            "receive.fsckObjects=true",
        ]
    )
    cmd.extend(git_args)
    return cmd


def run_git_hardened(
    repo_dir: Path | None,
    *git_args: str,
    allow_file_protocol: bool = False,
    check: bool = True,
    timeout: int = 300,
    extra_config: tuple[tuple[str, str], ...] = (),
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run Git with hardened flags and a scrubbed environment."""
    config_args: list[str] = []
    for k, v in extra_config:
        config_args.extend(["-c", f"{k}={v}"])
    # Place extra caller configs (such as user.name/user.email) right before the git subcommand.
    prefix_args = config_args + list(git_args)
    cmd = build_hardened_git_command(repo_dir, *prefix_args, allow_file_protocol=allow_file_protocol)
    allow_proto = "https:file" if allow_file_protocol else "https"
    env = build_scrubbed_env(allow_protocol=allow_proto)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        cmd,
        env=env,
        capture_output=True,
        text=True,
        check=check,
        timeout=timeout,
    )


def query_remote_head(
    url: str,
    ref: str = "refs/heads/master",
    allow_local_test: bool = False,
    timeout: int = 30,
) -> str:
    """Fetch the 40-hex commit SHA for a remote branch ref without cloning."""
    validated_url = validate_remote_url(url, allow_local_test=allow_local_test)
    proc = run_git_hardened(
        None,
        "ls-remote",
        "--exit-code",
        validated_url,
        ref,
        allow_file_protocol=allow_local_test,
        timeout=timeout,
    )
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == ref:
            sha = parts[0].strip()
            if not SHA40_RE.match(sha):
                raise ValueError(f"Invalid SHA from ls-remote: {sha}")
            return sha
    raise RuntimeError(f"Ref {ref} not found on {validated_url}")


def verify_tree_cleanliness(repo_dir: Path, ref: str = "HEAD") -> list[str]:
    """Reject symlinks, submodules, and leaked packaging/harness directories in a Git tree."""
    cmd = build_hardened_git_command(repo_dir, "ls-tree", "-r", "-z", ref)
    proc = subprocess.run(
        cmd,
        env=build_scrubbed_env(),
        capture_output=True,
        check=True,
        timeout=60,
    )
    issues: list[str] = []
    for raw_entry in proc.stdout.split(b"\0"):
        if not raw_entry:
            continue
        meta_part, _, path_bytes = raw_entry.partition(b"\t")
        if not path_bytes:
            continue
        meta = meta_part.decode("ascii", errors="replace").split()
        mode = meta[0] if meta else ""
        rel = path_bytes.decode("utf-8", errors="replace")
        if mode == "120000":
            issues.append(f"{rel}: symlink (mode 120000) is forbidden")
        elif mode == "160000":
            issues.append(f"{rel}: submodule gitlink (mode 160000) is forbidden")
        elif rel == "debian" or any(rel == p.rstrip("/") or rel.startswith(p) for p in FORBIDDEN_TREE_TOP_PREFIXES):
            issues.append(f"{rel}: forbidden top-level packaging or state path in upstream tree")
    return issues


def _has_parent_traversal(path_str: str) -> bool:
    return ".." in PurePosixPath(path_str).parts


def validate_deb_listing(
    package_name: str,
    tar_listing_lines: list[str],
    expected_version: str | None = None,
    deb_version: str | None = None,
    ctrl_listing_lines: list[str] | None = None,
) -> list[str]:
    """Validate dpkg-deb -c payload paths, mode bits, version, and control tarball members."""
    issues: list[str] = []
    if expected_version is not None:
        if deb_version is None or deb_version != expected_version:
            issues.append(f"{package_name}: version mismatch (got {deb_version!r}, expected {expected_version!r})")

    seen_entries = 0
    for raw_line in tar_listing_lines:
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(None, 5)
        if len(parts) < 6:
            issues.append(f"{package_name}: malformed dpkg-deb listing line: {line}")
            continue
        mode = parts[0]
        raw_path = parts[5]
        if SPECIAL_MODE_RE.search(mode):
            issues.append(f"{package_name}: special mode bits '{mode}' rejected on {raw_path}")

        entry_path = raw_path
        link_target: str | None = None
        if " -> " in raw_path:
            entry_path, link_target = raw_path.split(" -> ", 1)
        elif " link to " in raw_path:
            entry_path, link_target = raw_path.split(" link to ", 1)

        if _has_parent_traversal(entry_path):
            issues.append(f"{package_name}: path traversal rejected: {entry_path}")
            continue
        if link_target and _has_parent_traversal(link_target):
            issues.append(f"{package_name}: link target traversal rejected: {entry_path} -> {link_target}")
            continue

        if entry_path not in ALLOWED_DEB_EXACT_PATHS and not entry_path.startswith(ALLOWED_DEB_PREFIXES):
            issues.append(f"{package_name}: path outside allowlist: {entry_path}")
        seen_entries += 1

    if seen_entries == 0:
        issues.append(f"{package_name}: empty payload listing")

    if ctrl_listing_lines is not None:
        for raw_ctrl in ctrl_listing_lines:
            member = raw_ctrl.strip()
            if not member:
                continue
            if _has_parent_traversal(member) or member not in ALLOWED_CTRL_FILES:
                issues.append(f"{package_name}: unexpected control archive member: {member}")

    return issues


def verify_debs_dir(debs_dir: Path, expected_version: str | None = None) -> list[str]:
    """Run static dpkg-deb checks over all .deb files in a staged binary directory."""
    deb_files = sorted(debs_dir.glob("*.deb"))
    if not deb_files:
        return [f"{debs_dir}: no .deb packages found"]

    issues: list[str] = []
    for deb in deb_files:
        pkg_name = subprocess.check_output(["dpkg-deb", "-f", str(deb), "Package"], text=True).strip()
        deb_ver = subprocess.check_output(["dpkg-deb", "-f", str(deb), "Version"], text=True).strip()
        tar_lines = subprocess.check_output(["dpkg-deb", "-c", str(deb)], text=True).splitlines()
        ctrl_tar = subprocess.run(["dpkg-deb", "--ctrl-tarfile", str(deb)], capture_output=True, check=True)
        ctrl_lines = subprocess.run(
            ["tar", "-tf", "-"],
            input=ctrl_tar.stdout,
            capture_output=True,
            check=True,
        ).stdout.decode("utf-8", errors="replace").splitlines()
        issues.extend(
            validate_deb_listing(
                package_name=pkg_name,
                tar_listing_lines=tar_lines,
                expected_version=expected_version,
                deb_version=deb_ver,
                ctrl_listing_lines=ctrl_lines,
            )
        )
    return issues


def compute_overlay_sha256(overlay_dir: Path) -> str:
    """Compute a deterministic SHA-256 hash of all regular files in an OBS packaging overlay."""
    hasher = hashlib.sha256()
    for path in sorted(overlay_dir.rglob("*"), key=lambda p: p.relative_to(overlay_dir).as_posix()):
        if not path.is_file():
            continue
        rel = path.relative_to(overlay_dir).as_posix()
        data = path.read_bytes()
        hasher.update(rel.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(str(len(data)).encode("ascii"))
        hasher.update(b"\0")
        hasher.update(data)
    return hasher.hexdigest()


def compute_cycle_fingerprint(upstream_sha: str, plugin_sha: str, overlay_sha: str) -> str:
    """Compute the composite build fingerprint for upstream, plugin, and overlay revisions."""
    payload = f"{upstream_sha}:{plugin_sha}:{overlay_sha}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def compute_stack_fingerprint(upstream_sha: str, plugin_shas: dict[str, str], overlay_sha: str) -> str:
    """Compute a deterministic fingerprint over upstream HEAD, ordered plugin SHAs, and overlay."""
    ordered = ",".join(f"{key}={plugin_shas[key]}" for key in sorted(plugin_shas))
    payload = f"{upstream_sha}:{ordered}:{overlay_sha}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def synthesize_plugin_stack(
    repo_dir: Path,
    upstream_ref: str,
    entries: list[PluginStackEntry],
    target_branch: str = "obs/kdeconnect-staging",
    allow_file_protocol: bool = False,
) -> SynthesisResult:
    """Cherry-pick multiple plugin branches onto upstream_ref without mutating source refs."""
    if not entries:
        return SynthesisResult(ok=False, status="empty_stack", error="No plugin entries supplied")

    planned_picks: list[tuple[PluginStackEntry, list[str]]] = []
    for entry in entries:
        if not SHA40_RE.match(entry.plugin_base_sha):
            return SynthesisResult(
                ok=False,
                status="invalid_base_sha",
                error=f"{entry.plugin_id}: invalid base SHA {entry.plugin_base_sha}",
            )

        rev_proc = run_git_hardened(
            repo_dir,
            "rev-list",
            "--reverse",
            f"{entry.plugin_base_sha}..{entry.plugin_ref}",
            allow_file_protocol=allow_file_protocol,
            check=False,
        )
        if rev_proc.returncode != 0:
            return SynthesisResult(
                ok=False,
                status="rev_list_failed",
                error=f"{entry.plugin_id}: {rev_proc.stderr.strip()}",
            )
        commits = [line.strip() for line in rev_proc.stdout.splitlines() if line.strip()]
        if not commits:
            return SynthesisResult(
                ok=False,
                status="empty_range",
                error=f"{entry.plugin_id}: no plugin commits found above base SHA",
            )

        plugin_tree_issues = verify_tree_cleanliness(repo_dir, entry.plugin_ref)
        if plugin_tree_issues:
            return SynthesisResult(
                ok=False,
                status="dirty_tree",
                error=f"{entry.plugin_id}: " + "; ".join(plugin_tree_issues),
            )
        planned_picks.append((entry, commits))

    run_git_hardened(repo_dir, "checkout", "-f", "-B", target_branch, upstream_ref, allow_file_protocol=allow_file_protocol)
    run_git_hardened(repo_dir, "reset", "--hard", upstream_ref, allow_file_protocol=allow_file_protocol)
    run_git_hardened(repo_dir, "clean", "-fdx", allow_file_protocol=allow_file_protocol)

    for entry, commits in planned_picks:
        cp_proc = run_git_hardened(
            repo_dir,
            "cherry-pick",
            "--no-gpg-sign",
            *commits,
            allow_file_protocol=allow_file_protocol,
            check=False,
            extra_config=(("user.name", MAINTAINER_NAME), ("user.email", MAINTAINER_EMAIL)),
        )
        if cp_proc.returncode != 0:
            conflicts_proc = run_git_hardened(
                repo_dir,
                "diff",
                "--name-only",
                "--diff-filter=U",
                allow_file_protocol=allow_file_protocol,
                check=False,
            )
            conflicts = tuple(line.strip() for line in conflicts_proc.stdout.splitlines() if line.strip())
            run_git_hardened(repo_dir, "cherry-pick", "--abort", allow_file_protocol=allow_file_protocol, check=False)
            run_git_hardened(repo_dir, "reset", "--hard", upstream_ref, allow_file_protocol=allow_file_protocol)
            run_git_hardened(repo_dir, "clean", "-fdx", allow_file_protocol=allow_file_protocol)
            return SynthesisResult(
                ok=False,
                status="rebase_conflict",
                conflict_files=conflicts,
                error=f"{entry.plugin_id}: {cp_proc.stderr.strip() or 'cherry-pick conflict'}",
            )

    tree_issues = verify_tree_cleanliness(repo_dir, "HEAD")
    if tree_issues:
        return SynthesisResult(
            ok=False,
            status="dirty_tree",
            error="; ".join(tree_issues),
        )

    head_sha = run_git_hardened(repo_dir, "rev-parse", "HEAD", allow_file_protocol=allow_file_protocol).stdout.strip()
    return SynthesisResult(ok=True, status="ok", synthesized_sha=head_sha)


def synthesize_plugin_branch(
    repo_dir: Path,
    upstream_ref: str,
    plugin_ref: str,
    plugin_base_sha: str,
    target_branch: str = "obs/kdeconnect-staging",
    allow_file_protocol: bool = False,
) -> SynthesisResult:
    """Cherry-pick plugin_base_sha..plugin_ref onto upstream_ref without rewriting plugin_ref."""
    return synthesize_plugin_stack(
        repo_dir=repo_dir,
        upstream_ref=upstream_ref,
        entries=[
            PluginStackEntry(
                plugin_id="screenstream",
                plugin_ref=plugin_ref,
                plugin_base_sha=plugin_base_sha,
            )
        ],
        target_branch=target_branch,
        allow_file_protocol=allow_file_protocol,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Hardened upstream tracking and .deb inspection CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    debs_p = sub.add_parser("verify-debs", help="Validate staged .deb package listings and versions")
    debs_p.add_argument("debs_dir", type=Path)
    debs_p.add_argument("expected_version", nargs="?", default=None)

    args = parser.parse_args()
    if args.command == "verify-debs":
        issues = verify_debs_dir(args.debs_dir.resolve(), expected_version=args.expected_version)
        if issues:
            for item in issues:
                print(f"ERROR: {item}", file=sys.stderr)
            return 1
        print(f"Static .deb verification OK ({args.debs_dir})")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
