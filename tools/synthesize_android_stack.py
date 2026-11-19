#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com>
# SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL

"""Synthesize and compliance-check an ephemeral multi-plugin integration branch."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

try:
    from tools.check_kde_compliance import check_upstream_branch
    from tools.plugin_registry import active_android_plugins, active_desktop_plugins, load_registry
    from tools.upstream_sync import PluginStackEntry, run_git_hardened, synthesize_plugin_stack
except ImportError:
    from check_kde_compliance import check_upstream_branch
    from plugin_registry import active_android_plugins, active_desktop_plugins, load_registry
    from upstream_sync import PluginStackEntry, run_git_hardened, synthesize_plugin_stack


def _parse_local_env(env_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not env_path.is_file():
        return values
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, val = line.split("=", 1)
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        values[key.strip()] = val
    return values


def _ensure_disk_tmpdir(root: Path) -> Path:
    raw = os.environ.get("TMPDIR", "").strip()
    tmpdir = Path(raw) if raw else (root / ".state" / "tmp")
    tmpdir.mkdir(parents=True, exist_ok=True)
    if shutil.which("findmnt"):
        proc = subprocess.run(
            ["findmnt", "-n", "-o", "FSTYPE", "-T", str(tmpdir)],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip() == "tmpfs":
            raise RuntimeError(f"Refusing tmpfs TMPDIR: {tmpdir}")
    return tmpdir


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Synthesize active Continuity plugin branches onto an upstream base")
    parser.add_argument("--desktop", action="store_true", help="Target desktop branches instead of Android")
    parser.add_argument("--repo", type=Path, help="Explicit path to external KDE Connect checkout")
    parser.add_argument("--base", default="origin/master", help="Base ref in upstream checkout (default: origin/master)")
    parser.add_argument("--registry", type=Path, default=root / "plugins.toml", help="Path to plugins.toml")
    parser.add_argument("--dry-run", action="store_true", help="Test stack synthesis in an isolated temporary clone")
    parser.add_argument("--target-branch", default="work/continuity-stack", help="Output branch name when writing in-repo")
    args = parser.parse_args()

    try:
        tmpdir = _ensure_disk_tmpdir(root)
        all_entries = load_registry(args.registry.resolve())
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    entries = active_desktop_plugins(all_entries) if args.desktop else active_android_plugins(all_entries)
    if not entries:
        print("No active plugins configured for target.", file=sys.stderr)
        return 1

    env_map = _parse_local_env(root / ".state" / "local.env")
    env_key = "KDECONNECT_DESKTOP_SRC" if args.desktop else "KDECONNECT_ANDROID_SRC"
    repo_arg = args.repo
    if repo_arg is None:
        raw_repo = os.environ.get(env_key) or env_map.get(env_key, "")
        if not raw_repo:
            print(f"ERROR: Missing --repo and {env_key} in .state/local.env", file=sys.stderr)
            return 1
        repo_path = Path(raw_repo)
        if not repo_path.is_absolute():
            repo_path = root / repo_path
    else:
        repo_path = repo_arg
    repo_path = repo_path.resolve()
    if not (repo_path / ".git").exists():
        print(f"ERROR: Git checkout not found: {repo_path}", file=sys.stderr)
        return 1

    base_proc = run_git_hardened(
        repo_path,
        "rev-parse",
        "--verify",
        args.base,
        allow_file_protocol=True,
        check=False,
    )
    if base_proc.returncode != 0:
        print(f"ERROR: Base ref '{args.base}' not found in {repo_path}", file=sys.stderr)
        return 1
    base_sha = base_proc.stdout.strip()

    stack_entries: list[PluginStackEntry] = []
    source_branches: list[str] = []
    for item in entries:
        branch = item.desktop_branch if args.desktop else item.android_branch
        branch_ref = f"refs/heads/{branch}"
        ver_proc = run_git_hardened(
            repo_path,
            "rev-parse",
            "--verify",
            branch_ref,
            allow_file_protocol=True,
            check=False,
        )
        if ver_proc.returncode != 0:
            print(f"ERROR: Missing active branch '{branch}' for plugin '{item.id}'", file=sys.stderr)
            return 1

        compliance = check_upstream_branch(repo_path, args.base, head_ref=branch_ref)
        if compliance:
            print(f"ERROR: Compliance failed for {item.id} ({branch}): {compliance}", file=sys.stderr)
            return 1

        mb_proc = run_git_hardened(
            repo_path,
            "merge-base",
            branch_ref,
            base_sha,
            allow_file_protocol=True,
            check=False,
        )
        if mb_proc.returncode != 0:
            print(f"ERROR: Unable to find merge-base for {branch}: {mb_proc.stderr.strip()}", file=sys.stderr)
            return 1
        merge_base_sha = mb_proc.stdout.strip()
        stack_entries.append(
            PluginStackEntry(
                plugin_id=item.id,
                plugin_ref=branch_ref,
                plugin_base_sha=merge_base_sha,
            )
        )
        source_branches.append(branch)

    if args.dry_run:
        with tempfile.TemporaryDirectory(dir=tmpdir, prefix="stack-dryrun-") as tmp:
            tmp_repo = Path(tmp) / "repo"
            subprocess.check_call(["git", "init", "-q", "-b", "master", str(tmp_repo)])
            fetch_specs = [f"+{base_sha}:refs/remotes/origin/master"]
            fetch_specs.extend(f"+refs/heads/{b}:refs/heads/{b}" for b in source_branches)
            run_git_hardened(
                tmp_repo,
                "fetch",
                "--update-shallow",
                str(repo_path),
                *fetch_specs,
                allow_file_protocol=True,
            )
            synth = synthesize_plugin_stack(
                repo_dir=tmp_repo,
                upstream_ref="refs/remotes/origin/master",
                entries=stack_entries,
                target_branch="obs/dry-run-stack",
                allow_file_protocol=True,
            )
            if not synth.ok or not synth.synthesized_sha:
                print(f"ERROR: Dry-run synthesis failed ({synth.status}): {synth.error}", file=sys.stderr)
                return 1
            stack_issues = check_upstream_branch(tmp_repo, "refs/remotes/origin/master", head_ref="obs/dry-run-stack")
            if stack_issues:
                print(f"ERROR: Synthesized stack compliance failed: {stack_issues}", file=sys.stderr)
                return 1
            print(
                f"DRY_RUN_STACK_OK plugins={','.join(e.plugin_id for e in stack_entries)} "
                f"head={synth.synthesized_sha[:12]}"
            )
            return 0

    status_proc = run_git_hardened(
        repo_path,
        "status",
        "--porcelain",
        "--untracked-files=no",
        allow_file_protocol=True,
    )
    if status_proc.stdout.strip():
        print(
            f"ERROR: Tracked modifications present in {repo_path}; commit or stash before synthesizing stack",
            file=sys.stderr,
        )
        return 1

    synth = synthesize_plugin_stack(
        repo_dir=repo_path,
        upstream_ref=base_sha,
        entries=stack_entries,
        target_branch=args.target_branch,
        allow_file_protocol=True,
    )
    if not synth.ok or not synth.synthesized_sha:
        print(f"ERROR: Stack synthesis failed ({synth.status}): {synth.error}", file=sys.stderr)
        return 1

    stack_issues = check_upstream_branch(repo_path, base_sha, head_ref=args.target_branch)
    if stack_issues:
        print(f"ERROR: Synthesized stack compliance failed: {stack_issues}", file=sys.stderr)
        return 1

    print(
        f"STACK_SYNTHESIS_OK branch={args.target_branch} "
        f"plugins={','.join(e.plugin_id for e in stack_entries)} head={synth.synthesized_sha[:12]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
