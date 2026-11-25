#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com>
# SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL

"""Hardened lockfile verifier and reproducible stack builder for external users."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys

try:
    from tools.check_kde_compliance import check_upstream_branch
    from tools.obs_packaging import build_kdeconnect_bundle
    from tools.plugin_registry import active_android_plugins, active_desktop_plugins, load_registry
    from tools.upstream_sync import (
        MAINTAINER_EMAIL,
        MAINTAINER_NAME,
        SHA40_RE,
        run_git_hardened,
        validate_remote_url,
        verify_tree_cleanliness,
    )
except ImportError:
    from check_kde_compliance import check_upstream_branch
    from obs_packaging import build_kdeconnect_bundle
    from plugin_registry import active_android_plugins, active_desktop_plugins, load_registry
    from upstream_sync import (
        MAINTAINER_EMAIL,
        MAINTAINER_NAME,
        SHA40_RE,
        run_git_hardened,
        validate_remote_url,
        verify_tree_cleanliness,
    )

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = 1
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_URLS = {
    "desktop": "https://invent.kde.org/network/kdeconnect-kde.git",
    "android": "https://invent.kde.org/network/kdeconnect-android.git",
}


def ensure_disk_tmpdir(root: Path) -> Path:
    """Resolve TMPDIR and refuse tmpfs staging paths."""
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


def _validate_safe_patch_path(repo_root: Path, raw_rel: str, target: str, plugin_id: str) -> Path | None:
    if not isinstance(raw_rel, str) or not raw_rel or raw_rel.startswith("/") or "\\" in raw_rel:
        return None
    posix = PurePosixPath(raw_rel)
    if any(part in ("", ".", "..") for part in posix.parts):
        return None
    expected_prefix = f"patches/{target}/{plugin_id}/"
    if not raw_rel.startswith(expected_prefix) or not raw_rel.endswith(".patch"):
        return None
    patches_root = (repo_root / "patches").resolve()
    cur = repo_root
    for part in posix.parts:
        cur = cur / part
        if cur.is_symlink():
            return None
    if not cur.is_file():
        return None
    try:
        resolved = cur.resolve()
        if not resolved.is_relative_to(patches_root):
            return None
    except OSError:
        return None
    return cur


def verify_lockfile(
    repo_root: Path,
    lock_path: Path | None = None,
    registry_path: Path | None = None,
) -> tuple[dict, list[str]]:
    """Verify stack.lock.json schema, registry alignment, URLs, SHAs, and patch SHA-256 digests."""
    repo_root = repo_root.resolve()
    lock_file = (lock_path or (repo_root / "stack.lock.json")).resolve()
    reg_file = (registry_path or (repo_root / "plugins.toml")).resolve()
    issues: list[str] = []

    try:
        registry_entries = load_registry(reg_file)
    except Exception as exc:
        return {}, [f"Invalid registry {reg_file}: {exc}"]

    if not lock_file.is_file() or lock_file.is_symlink():
        return {}, [f"Missing or invalid lockfile: {lock_file}"]

    try:
        lock_data = json.loads(lock_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {}, [f"Failed to parse {lock_file}: {exc}"]

    if not isinstance(lock_data, dict):
        return {}, ["Lockfile root must be a JSON object"]
    if lock_data.get("schema_version") != SCHEMA_VERSION:
        issues.append(f"Unsupported schema_version: {lock_data.get('schema_version')!r}")

    targets_raw = lock_data.get("targets")
    if not isinstance(targets_raw, dict):
        return lock_data, issues + ["Missing 'targets' mapping in lockfile"]

    expected_by_target = {
        "desktop": [(e.id, e.desktop_branch) for e in active_desktop_plugins(registry_entries)],
        "android": [(e.id, e.android_branch) for e in active_android_plugins(registry_entries)],
    }

    referenced_patches: set[Path] = set()
    for target in ("desktop", "android"):
        t_obj = targets_raw.get(target)
        if not isinstance(t_obj, dict):
            issues.append(f"Missing target block '{target}' in lockfile")
            continue

        url = str(t_obj.get("upstream_url", ""))
        if url != EXPECTED_URLS[target]:
            issues.append(f"{target}: unexpected upstream_url {url!r}")
        else:
            try:
                validate_remote_url(url)
            except ValueError as exc:
                issues.append(f"{target}: {exc}")

        for sha_field in ("base_sha", "expected_tree_sha", "expected_head_sha"):
            val = t_obj.get(sha_field, "")
            if not isinstance(val, str) or not SHA40_RE.match(val):
                issues.append(f"{target}: invalid {sha_field}: {val!r}")

        plugins_raw = t_obj.get("plugins")
        if not isinstance(plugins_raw, list):
            issues.append(f"{target}: 'plugins' must be a list")
            continue

        got_pairs = [
            (p.get("id"), p.get("branch")) if isinstance(p, dict) else (None, None)
            for p in plugins_raw
        ]
        if got_pairs != expected_by_target[target]:
            issues.append(
                f"{target}: lockfile plugin order/branches {got_pairs!r} != plugins.toml {expected_by_target[target]!r}"
            )
            continue

        for p_obj in plugins_raw:
            pid = p_obj["id"]
            for sha_field in ("expected_tree_sha", "expected_head_sha"):
                val = p_obj.get(sha_field, "")
                if not isinstance(val, str) or not SHA40_RE.match(val):
                    issues.append(f"{target}/{pid}: invalid {sha_field}: {val!r}")

            patches_raw = p_obj.get("patches")
            if not isinstance(patches_raw, list) or not patches_raw:
                issues.append(f"{target}/{pid}: missing non-empty 'patches' list")
                continue

            for idx, rec in enumerate(patches_raw):
                if not isinstance(rec, dict):
                    issues.append(f"{target}/{pid}: patches[{idx}] must be an object")
                    continue
                rel_path = rec.get("path", "")
                exp_sha = rec.get("sha256", "")
                exp_bytes = rec.get("bytes")
                if not isinstance(exp_sha, str) or not SHA256_RE.match(exp_sha):
                    issues.append(f"{target}/{pid}: patches[{idx}] invalid sha256 {exp_sha!r}")
                    continue
                if not isinstance(exp_bytes, int) or exp_bytes <= 0:
                    issues.append(f"{target}/{pid}: patches[{idx}] invalid byte length {exp_bytes!r}")
                    continue
                patch_file = _validate_safe_patch_path(repo_root, str(rel_path), target, pid)
                if patch_file is None:
                    issues.append(f"{target}/{pid}: unsafe or missing patch path {rel_path!r}")
                    continue
                data = patch_file.read_bytes()
                if len(data) != exp_bytes:
                    issues.append(
                        f"{rel_path}: size mismatch (expected {exp_bytes}, got {len(data)})"
                    )
                actual_sha = hashlib.sha256(data).hexdigest()
                if actual_sha != exp_sha:
                    issues.append(
                        f"{rel_path}: SHA-256 mismatch (expected {exp_sha}, got {actual_sha})"
                    )
                referenced_patches.add(patch_file.resolve())

    patches_root = repo_root / "patches"
    if patches_root.is_symlink():
        issues.append("patches/: symlink directory is forbidden")
    elif patches_root.is_dir():
        for dirpath, dirs, files in os.walk(patches_root, followlinks=False):
            current = Path(dirpath)
            for d in dirs:
                dpath = current / d
                if dpath.is_symlink():
                    issues.append(f"{dpath.relative_to(repo_root).as_posix()}: symlink is forbidden")
            for name in files:
                fpath = current / name
                if fpath.is_symlink():
                    issues.append(f"{fpath.relative_to(repo_root).as_posix()}: symlink is forbidden")
                    continue
                if fpath.resolve() not in referenced_patches:
                    issues.append(
                        f"{fpath.relative_to(repo_root).as_posix()}: unreferenced file under patches/"
                    )

    return lock_data, issues


def reconstruct_target_stack(
    repo_dir: Path,
    base_sha: str,
    plugins: list[tuple[str, str, list[Path]]],
    target_branch: str = "repro/continuity-stack",
    allow_file_protocol: bool = False,
    check_compliance: bool = True,
) -> dict:
    """Deterministically apply per-plugin patches and synthesize combined stack in repo_dir."""
    fixed_committer = {
        "GIT_COMMITTER_NAME": MAINTAINER_NAME,
        "GIT_COMMITTER_EMAIL": MAINTAINER_EMAIL,
    }
    git_identity = (("user.name", MAINTAINER_NAME), ("user.email", MAINTAINER_EMAIL))

    plugin_results: list[dict] = []
    for plugin_id, branch, patch_files in plugins:
        run_git_hardened(
            repo_dir,
            "checkout",
            "-f",
            "-B",
            branch,
            base_sha,
            allow_file_protocol=allow_file_protocol,
        )
        run_git_hardened(repo_dir, "clean", "-fdx", allow_file_protocol=allow_file_protocol)
        run_git_hardened(
            repo_dir,
            "am",
            "--committer-date-is-author-date",
            "--no-gpg-sign",
            *[str(p) for p in patch_files],
            allow_file_protocol=allow_file_protocol,
            extra_config=git_identity,
            extra_env=fixed_committer,
        )
        tree_issues = verify_tree_cleanliness(repo_dir, "HEAD")
        if tree_issues:
            raise RuntimeError(f"Tree cleanliness failed for {plugin_id}: {tree_issues}")
        if check_compliance:
            comp_issues = check_upstream_branch(repo_dir, base_sha, head_ref="HEAD")
            if comp_issues:
                raise RuntimeError(f"Upstream compliance failed for {plugin_id}: {comp_issues}")
        tree_sha = run_git_hardened(
            repo_dir, "rev-parse", "HEAD^{tree}", allow_file_protocol=allow_file_protocol
        ).stdout.strip()
        head_sha = run_git_hardened(
            repo_dir, "rev-parse", "HEAD", allow_file_protocol=allow_file_protocol
        ).stdout.strip()
        plugin_results.append(
            {
                "id": plugin_id,
                "branch": branch,
                "tree_sha": tree_sha,
                "head_sha": head_sha,
            }
        )

    run_git_hardened(
        repo_dir,
        "checkout",
        "-f",
        "-B",
        target_branch,
        base_sha,
        allow_file_protocol=allow_file_protocol,
    )
    run_git_hardened(repo_dir, "clean", "-fdx", allow_file_protocol=allow_file_protocol)

    for _plugin_id, branch, _patch_files in plugins:
        commits_raw = run_git_hardened(
            repo_dir,
            "rev-list",
            "--reverse",
            f"{base_sha}..{branch}",
            allow_file_protocol=allow_file_protocol,
        ).stdout
        for commit_sha in [line.strip() for line in commits_raw.splitlines() if line.strip()]:
            meta = run_git_hardened(
                repo_dir,
                "show",
                "-s",
                "--format=%aN%n%aE%n%aI",
                commit_sha,
                allow_file_protocol=allow_file_protocol,
            ).stdout.splitlines()
            if len(meta) != 3:
                raise RuntimeError(f"Unexpected commit metadata format for {commit_sha}")
            author_name, author_email, author_date = meta
            run_git_hardened(
                repo_dir,
                "cherry-pick",
                "--no-gpg-sign",
                commit_sha,
                allow_file_protocol=allow_file_protocol,
                extra_config=git_identity,
                extra_env={
                    **fixed_committer,
                    "GIT_COMMITTER_DATE": author_date,
                    "GIT_AUTHOR_NAME": author_name,
                    "GIT_AUTHOR_EMAIL": author_email,
                    "GIT_AUTHOR_DATE": author_date,
                },
            )

    stack_tree_issues = verify_tree_cleanliness(repo_dir, "HEAD")
    if stack_tree_issues:
        raise RuntimeError(f"Combined stack tree cleanliness failed: {stack_tree_issues}")

    stack_tree_sha = run_git_hardened(
        repo_dir, "rev-parse", "HEAD^{tree}", allow_file_protocol=allow_file_protocol
    ).stdout.strip()
    stack_head_sha = run_git_hardened(
        repo_dir, "rev-parse", "HEAD", allow_file_protocol=allow_file_protocol
    ).stdout.strip()
    return {
        "plugins": plugin_results,
        "tree_sha": stack_tree_sha,
        "head_sha": stack_head_sha,
    }


def fetch_pinned_base_commit(
    repo_dir: Path,
    remote_source: str,
    base_sha: str,
    allow_file_protocol: bool = False,
) -> None:
    """Initialize repo_dir and fetch a single pinned base commit into refs/remotes/upstream/base."""
    repo_dir.mkdir(parents=True, exist_ok=True)
    run_git_hardened(repo_dir, "init", "-q", "-b", "upstream-base")
    fetch_args = ["fetch", "--no-tags"]
    if allow_file_protocol:
        fetch_args.append("--update-shallow")
    else:
        fetch_args.extend(["--depth", "1"])
    fetch_args.extend([remote_source, f"+{base_sha}:refs/remotes/upstream/base"])
    run_git_hardened(repo_dir, *fetch_args, allow_file_protocol=allow_file_protocol)
    fetched_sha = run_git_hardened(repo_dir, "rev-parse", "refs/remotes/upstream/base").stdout.strip()
    if fetched_sha != base_sha:
        raise RuntimeError(f"Fetched SHA mismatch: expected {base_sha}, got {fetched_sha}")
    run_git_hardened(repo_dir, "checkout", "-f", "-B", "upstream-base", base_sha)


def apply_locked_target(
    repo_root: Path,
    target: str,
    out_dir: Path,
    lock_path: Path | None = None,
    mirror_repo: Path | None = None,
    emit_deb_source: Path | None = None,
    obs_rev: int = 1,
    emit_gentoo_overlay: Path | None = None,
    gentoo_ebuild_version: str = "27.03.70_p1",
    check_compliance: bool = True,
) -> dict:
    """Reproduce a verified locked target checkout (and optional Debian/Gentoo packaging)."""
    if target not in EXPECTED_URLS:
        raise ValueError(f"Unsupported target: {target}")
    if emit_deb_source is not None and target != "desktop":
        raise ValueError("--emit-deb-source is only supported for --target desktop")
    if emit_gentoo_overlay is not None and target != "desktop":
        raise ValueError("--emit-gentoo-overlay is only supported for --target desktop")
    repo_root = repo_root.resolve()
    ensure_disk_tmpdir(repo_root)

    lock_data, issues = verify_lockfile(repo_root, lock_path=lock_path)
    if issues:
        raise ValueError("Lockfile verification failed:\n  - " + "\n  - ".join(issues))

    if out_dir.is_symlink():
        raise ValueError(f"Refusing symlink output path: {out_dir}")
    if out_dir.exists():
        if not out_dir.is_dir():
            raise ValueError(f"Output path exists and is not a directory: {out_dir}")
        if any(out_dir.iterdir()):
            raise ValueError(f"Refusing non-empty output directory: {out_dir}")
    else:
        out_dir.mkdir(parents=True, exist_ok=True)
    out_dir = out_dir.resolve()

    target_lock = lock_data["targets"][target]
    base_sha = target_lock["base_sha"]
    upstream_url = target_lock["upstream_url"]

    if mirror_repo is not None:
        mirror_resolved = mirror_repo.resolve()
        if not mirror_resolved.is_dir():
            raise FileNotFoundError(f"Mirror repository not found: {mirror_resolved}")
        fetch_pinned_base_commit(out_dir, str(mirror_resolved), base_sha, allow_file_protocol=True)
    else:
        validated = validate_remote_url(upstream_url)
        fetch_pinned_base_commit(out_dir, validated, base_sha, allow_file_protocol=False)

    plugin_inputs = [
        (
            p["id"],
            p["branch"],
            [(repo_root / rec["path"]).resolve() for rec in p["patches"]],
        )
        for p in target_lock["plugins"]
    ]
    rebuilt = reconstruct_target_stack(
        repo_dir=out_dir,
        base_sha=base_sha,
        plugins=plugin_inputs,
        target_branch="repro/continuity-stack",
        allow_file_protocol=False,
        check_compliance=check_compliance,
    )

    for actual, expected in zip(rebuilt["plugins"], target_lock["plugins"]):
        if actual["tree_sha"] != expected["expected_tree_sha"]:
            raise RuntimeError(
                f"{target}/{actual['id']}: tree SHA mismatch "
                f"(expected {expected['expected_tree_sha']}, got {actual['tree_sha']})"
            )
        if actual["head_sha"] != expected["expected_head_sha"]:
            raise RuntimeError(
                f"{target}/{actual['id']}: HEAD SHA mismatch "
                f"(expected {expected['expected_head_sha']}, got {actual['head_sha']})"
            )

    if rebuilt["tree_sha"] != target_lock["expected_tree_sha"]:
        raise RuntimeError(
            f"{target}: stack tree SHA mismatch "
            f"(expected {target_lock['expected_tree_sha']}, got {rebuilt['tree_sha']})"
        )
    if rebuilt["head_sha"] != target_lock["expected_head_sha"]:
        raise RuntimeError(
            f"{target}: stack HEAD SHA mismatch "
            f"(expected {target_lock['expected_head_sha']}, got {rebuilt['head_sha']})"
        )

    summary: dict = {
        "target": target,
        "out_dir": str(out_dir),
        "branch": "repro/continuity-stack",
        "base_sha": base_sha,
        "tree_sha": rebuilt["tree_sha"],
        "head_sha": rebuilt["head_sha"],
        "plugins": rebuilt["plugins"],
    }

    if emit_deb_source is not None:
        if target != "desktop":
            raise ValueError("--emit-deb-source is only supported for --target desktop")
        if emit_deb_source.is_symlink():
            raise ValueError(f"Refusing symlink --emit-deb-source path: {emit_deb_source}")
        deb_dir = emit_deb_source.resolve()
        bundle = build_kdeconnect_bundle(
            desktop_src=out_dir,
            overlay_debian_dir=repo_root / "obs" / "kdeconnect" / "debian",
            output_dir=deb_dir,
            obs_rev=obs_rev,
        )
        manifest = json.loads(Path(bundle["manifest"]).read_text(encoding="utf-8"))
        summary["deb_source"] = bundle
        summary["deb_source"]["manifest_sha256"] = hashlib.sha256(
            Path(bundle["manifest"]).read_bytes()
        ).hexdigest()
        summary["deb_source"]["files"] = manifest["files"]

    if emit_gentoo_overlay is not None:
        if emit_gentoo_overlay.is_symlink():
            raise ValueError(f"Refusing symlink --emit-gentoo-overlay path: {emit_gentoo_overlay}")
        try:
            from tools.gentoo_packaging import emit_gentoo_overlay as _emit_gentoo_overlay
        except ImportError:
            from gentoo_packaging import emit_gentoo_overlay as _emit_gentoo_overlay
        summary["gentoo_overlay"] = _emit_gentoo_overlay(
            repo_root=repo_root,
            output_dir=emit_gentoo_overlay.resolve(),
            ebuild_version=gentoo_ebuild_version,
            lock_path=lock_path,
        )

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify lockfile and reproduce locked Continuity stacks")
    sub = parser.add_subparsers(dest="command", required=True)

    ver_p = sub.add_parser("verify-lock", help="Verify stack.lock.json and patch SHA-256 digests")
    ver_p.add_argument("--lock", type=Path, default=ROOT / "stack.lock.json")
    ver_p.add_argument("--registry", type=Path, default=ROOT / "plugins.toml")

    out_p = sub.add_parser("checkout", help="Fetch pinned upstream SHA and apply verified patches")
    out_p.add_argument("--target", choices=("desktop", "android"), required=True)
    out_p.add_argument("--out", type=Path, required=True)
    out_p.add_argument("--lock", type=Path, default=ROOT / "stack.lock.json")
    out_p.add_argument("--mirror", type=Path, help="Optional local upstream mirror for offline builds/tests")
    out_p.add_argument("--emit-deb-source", type=Path, help="Optional output dir for 3.0 (quilt) .dsc bundle")
    out_p.add_argument("--obs-rev", type=int, default=1)
    out_p.add_argument(
        "--emit-gentoo-overlay",
        type=Path,
        help="Optional output dir for a verified Gentoo Portage EAPI 8 overlay",
    )
    out_p.add_argument(
        "--gentoo-ebuild-version",
        default="27.03.70_p1",
        help="Ebuild version for --emit-gentoo-overlay (default: 27.03.70_p1)",
    )

    args = parser.parse_args()
    if args.command == "verify-lock":
        lock_data, issues = verify_lockfile(ROOT, lock_path=args.lock, registry_path=args.registry)
        if issues:
            print("Lockfile verification failed:", file=sys.stderr)
            for item in issues:
                print(f"  - {item}", file=sys.stderr)
            return 1
        d_cnt = len(lock_data["targets"]["desktop"]["plugins"])
        a_cnt = len(lock_data["targets"]["android"]["plugins"])
        print(f"Lockfile OK (desktop={d_cnt} plugins, android={a_cnt} plugins)")
        return 0

    if args.command == "checkout":
        try:
            res = apply_locked_target(
                repo_root=ROOT,
                target=args.target,
                out_dir=args.out,
                lock_path=args.lock,
                mirror_repo=args.mirror,
                emit_deb_source=args.emit_deb_source,
                obs_rev=args.obs_rev,
                emit_gentoo_overlay=args.emit_gentoo_overlay,
                gentoo_ebuild_version=args.gentoo_ebuild_version,
            )
        except Exception as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(res, indent=2))
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
