#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com>
# SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL

"""Export clean per-plugin patch series and generate cryptographic stack.lock.json."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

try:
    from tools.check_kde_compliance import check_upstream_branch
    from tools.plugin_registry import active_android_plugins, active_desktop_plugins, load_registry
    from tools.reproduce_stack import (
        EXPECTED_URLS,
        SCHEMA_VERSION,
        ensure_disk_tmpdir,
        fetch_pinned_base_commit,
        reconstruct_target_stack,
        verify_lockfile,
    )
    from tools.synthesize_android_stack import _parse_local_env
    from tools.upstream_sync import run_git_hardened
except ImportError:
    from check_kde_compliance import check_upstream_branch
    from plugin_registry import active_android_plugins, active_desktop_plugins, load_registry
    from reproduce_stack import (
        EXPECTED_URLS,
        SCHEMA_VERSION,
        ensure_disk_tmpdir,
        fetch_pinned_base_commit,
        reconstruct_target_stack,
        verify_lockfile,
    )
    from synthesize_android_stack import _parse_local_env
    from upstream_sync import run_git_hardened

ROOT = Path(__file__).resolve().parents[1]


def export_target_patches(
    repo_root: Path,
    source_repo: Path,
    target: str,
    entries: list[tuple[str, str]],
    tmpdir: Path,
    base_ref: str = "origin/master",
    check_compliance: bool = True,
) -> dict:
    """Export one target's patches and compute deterministic reproduced tree/commit SHAs."""
    source_repo = source_repo.resolve()
    if not (source_repo / ".git").exists():
        raise FileNotFoundError(f"Git checkout not found: {source_repo}")

    base_shas: set[str] = set()
    plugin_branches: list[tuple[str, str, str]] = []
    for plugin_id, branch in entries:
        ref = f"refs/heads/{branch}"
        ver_proc = run_git_hardened(
            source_repo, "rev-parse", "--verify", ref, allow_file_protocol=True, check=False
        )
        if ver_proc.returncode != 0:
            raise RuntimeError(f"Missing active branch '{branch}' for {target}/{plugin_id}")
        mb_proc = run_git_hardened(
            source_repo, "merge-base", ref, base_ref, allow_file_protocol=True
        )
        merge_base = mb_proc.stdout.strip()
        base_shas.add(merge_base)
        if check_compliance:
            issues = check_upstream_branch(source_repo, merge_base, head_ref=ref)
            if issues:
                raise RuntimeError(f"Compliance failed for {target}/{plugin_id} ({branch}): {issues}")
        plugin_branches.append((plugin_id, branch, merge_base))

    if len(base_shas) != 1:
        raise RuntimeError(
            f"{target}: active branches have diverging merge-bases against {base_ref}: {sorted(base_shas)}"
        )
    shared_base = next(iter(base_shas))

    target_patch_dir = repo_root / "patches" / target
    if target_patch_dir.exists():
        shutil.rmtree(target_patch_dir)
    target_patch_dir.mkdir(parents=True, exist_ok=True)

    exported_plugins: list[tuple[str, str, list[Path]]] = []
    patch_records_by_id: dict[str, list[dict]] = {}
    for plugin_id, branch, merge_base in plugin_branches:
        plugin_dir = target_patch_dir / plugin_id
        plugin_dir.mkdir(parents=True, exist_ok=True)
        run_git_hardened(
            source_repo,
            "format-patch",
            "--zero-commit",
            "--no-signature",
            "--full-index",
            "--no-stat",
            "--output-directory",
            str(plugin_dir),
            f"{merge_base}..refs/heads/{branch}",
            allow_file_protocol=True,
        )
        patch_files = sorted(plugin_dir.glob("*.patch"))
        if not patch_files:
            raise RuntimeError(f"{target}/{plugin_id}: exported 0 patch files from {merge_base}..{branch}")
        records: list[dict] = []
        for pfile in patch_files:
            data = pfile.read_bytes()
            records.append(
                {
                    "path": pfile.relative_to(repo_root).as_posix(),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "bytes": len(data),
                }
            )
        exported_plugins.append((plugin_id, branch, patch_files))
        patch_records_by_id[plugin_id] = records

    with tempfile.TemporaryDirectory(dir=tmpdir, prefix=f"export-verify-{target}-") as tmp:
        verify_repo = Path(tmp) / "repo"
        fetch_pinned_base_commit(
            repo_dir=verify_repo,
            remote_source=str(source_repo),
            base_sha=shared_base,
            allow_file_protocol=True,
        )

        rebuilt = reconstruct_target_stack(
            repo_dir=verify_repo,
            base_sha=shared_base,
            plugins=exported_plugins,
            target_branch="repro/continuity-stack",
            allow_file_protocol=False,
            check_compliance=check_compliance,
        )

    plugin_lock_entries: list[dict] = []
    for item in rebuilt["plugins"]:
        pid = item["id"]
        plugin_lock_entries.append(
            {
                "id": pid,
                "branch": item["branch"],
                "expected_tree_sha": item["tree_sha"],
                "expected_head_sha": item["head_sha"],
                "patches": patch_records_by_id[pid],
            }
        )

    return {
        "upstream_url": EXPECTED_URLS[target],
        "base_sha": shared_base,
        "expected_tree_sha": rebuilt["tree_sha"],
        "expected_head_sha": rebuilt["head_sha"],
        "plugins": plugin_lock_entries,
    }


def export_all_targets(
    repo_root: Path,
    desktop_repo: Path,
    android_repo: Path,
    tmpdir: Path,
    base_ref: str = "origin/master",
    registry_path: Path | None = None,
    lock_path: Path | None = None,
    check_compliance: bool = True,
) -> dict:
    """Export patches for both desktop and Android targets and write stack.lock.json."""
    repo_root = repo_root.resolve()
    reg_entries = load_registry((registry_path or (repo_root / "plugins.toml")).resolve())
    desktop_entries = [(e.id, e.desktop_branch) for e in active_desktop_plugins(reg_entries)]
    android_entries = [(e.id, e.android_branch) for e in active_android_plugins(reg_entries)]

    desktop_lock = export_target_patches(
        repo_root=repo_root,
        source_repo=desktop_repo,
        target="desktop",
        entries=desktop_entries,
        tmpdir=tmpdir,
        base_ref=base_ref,
        check_compliance=check_compliance,
    )
    android_lock = export_target_patches(
        repo_root=repo_root,
        source_repo=android_repo,
        target="android",
        entries=android_entries,
        tmpdir=tmpdir,
        base_ref=base_ref,
        check_compliance=check_compliance,
    )

    lock_payload = {
        "schema_version": SCHEMA_VERSION,
        "targets": {
            "desktop": desktop_lock,
            "android": android_lock,
        },
    }
    out_lock = (lock_path or (repo_root / "stack.lock.json")).resolve()
    out_lock.write_text(json.dumps(lock_payload, indent=2) + "\n", encoding="utf-8")

    _, issues = verify_lockfile(repo_root, lock_path=out_lock, registry_path=registry_path)
    if issues:
        raise RuntimeError("Generated lockfile failed verification: " + "; ".join(issues))
    return lock_payload


def _resolve_repo_path(repo_root: Path, cli_arg: Path | None, env_map: dict[str, str], env_key: str) -> Path:
    if cli_arg is not None:
        return cli_arg.resolve()
    raw = os.environ.get(env_key) or env_map.get(env_key, "")
    if not raw:
        raise ValueError(f"Missing --{env_key.lower()} and {env_key} in .state/local.env")
    p = Path(raw)
    return (p if p.is_absolute() else (repo_root / p)).resolve()


def main() -> int:
    parser = argparse.ArgumentParser(description="Export verified plugin patches and stack.lock.json")
    parser.add_argument("--desktop-repo", type=Path, help="Path to local kdeconnect-kde checkout")
    parser.add_argument("--android-repo", type=Path, help="Path to local kdeconnect-android checkout")
    parser.add_argument("--base", default="origin/master", help="Base ref in upstream checkouts (default: origin/master)")
    parser.add_argument("--registry", type=Path, default=ROOT / "plugins.toml")
    parser.add_argument("--lock", type=Path, default=ROOT / "stack.lock.json")
    args = parser.parse_args()

    try:
        tmpdir = ensure_disk_tmpdir(ROOT)
        env_map = _parse_local_env(ROOT / ".state" / "local.env")
        desktop_repo = _resolve_repo_path(ROOT, args.desktop_repo, env_map, "KDECONNECT_DESKTOP_SRC")
        android_repo = _resolve_repo_path(ROOT, args.android_repo, env_map, "KDECONNECT_ANDROID_SRC")
        lock_payload = export_all_targets(
            repo_root=ROOT,
            desktop_repo=desktop_repo,
            android_repo=android_repo,
            tmpdir=tmpdir,
            base_ref=args.base,
            registry_path=args.registry,
            lock_path=args.lock,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(
        f"EXPORT_OK desktop_tree={lock_payload['targets']['desktop']['expected_tree_sha'][:12]} "
        f"android_tree={lock_payload['targets']['android']['expected_tree_sha'][:12]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
