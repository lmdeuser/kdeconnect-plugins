#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com>
# SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL

"""Host-side OBS controller entrypoint and release-gate checks."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

try:
    from tools.check_kde_compliance import check_upstream_branch
    from tools.obs_packaging import build_kdeconnect_bundle, strip_spdx_comments, strip_xml_comments, verify_bundle_manifest
    from tools.upstream_sync import (
        SRCMD5_RE,
        build_scrubbed_env,
        compute_cycle_fingerprint,
        compute_overlay_sha256,
        query_remote_head,
        run_git_hardened,
        synthesize_plugin_branch,
        validate_remote_url,
    )
except ImportError:
    from check_kde_compliance import check_upstream_branch
    from obs_packaging import build_kdeconnect_bundle, strip_spdx_comments, strip_xml_comments, verify_bundle_manifest
    from upstream_sync import (
        SRCMD5_RE,
        build_scrubbed_env,
        compute_cycle_fingerprint,
        compute_overlay_sha256,
        query_remote_head,
        run_git_hardened,
        synthesize_plugin_branch,
        validate_remote_url,
    )

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".state"

DEFAULT_STAGING_PROJECT = "home:dwolfF:kdeconnect:staging"
DEFAULT_STABLE_PROJECT = "home:dwolfF:kdeconnect"
DEFAULT_DISTRO = "xUbuntu_26.04"
DEFAULT_ARCH = "x86_64"
ANDROID_UPSTREAM_URL = "https://invent.kde.org/network/kdeconnect-android.git"

CTEST_SUMMARY_RE = re.compile(r"(\d+)% tests passed,\s*(\d+)\s+tests?\s+failed out of\s*(\d+)")
LINTIAN_ERROR_RE = re.compile(r"^(?:\[\s*\d+s\]\s*)?(E:\s+\S+:.*)$")
APK_VERIFIED_RE = re.compile(r"(?m)(?:^|\s)APK_VERIFIED=1\s*$")
APKSIGNER_OK_RE = re.compile(r"(?m)(?:^|\s)Verifies\s*$")
IN_PROGRESS_STATES = {"scheduled", "dispatching", "blocked", "building", "finished", "unknown"}
SETTLED_REPO_CODES = {"published", "unpublished", "broken"}


def build_osc_command(*args: str, state_dir: Path = STATE) -> list[str]:
    """Construct an osc command with keyring disabled and per-runner cookiejar isolation."""
    return ["osc", "--no-keyring", "--setopt", f"cookiejar={state_dir / 'osc-cookiejar'}", *args]


@dataclass(frozen=True)
class EnvCheckResult:
    ok: bool
    errors: list[str]
    details: dict[str, str]


@dataclass(frozen=True)
class PromotionDecision:
    ready: bool
    reasons: list[str]


@dataclass(frozen=True)
class BuildLogMetrics:
    ctest_ran: int
    ctest_failed: int
    lintian_errors: int
    apk_verified: bool
    lintian_error_lines: tuple[str, ...] = ()


def parse_obs_build_log(log_text: str) -> BuildLogMetrics:
    """Extract ctest counts, lintian errors, and APK signature verification from an OBS log."""
    ctest_matches = CTEST_SUMMARY_RE.findall(log_text)
    if ctest_matches:
        _, failed_s, total_s = ctest_matches[-1]
        ctest_ran = int(total_s)
        ctest_failed = int(failed_s)
    else:
        ctest_ran = 0
        ctest_failed = 0

    err_lines: list[str] = []
    for raw_line in log_text.splitlines():
        match = LINTIAN_ERROR_RE.match(raw_line.strip())
        if match:
            err_lines.append(match.group(1))

    apk_verified = bool(APK_VERIFIED_RE.search(log_text) and APKSIGNER_OK_RE.search(log_text))
    return BuildLogMetrics(
        ctest_ran=ctest_ran,
        ctest_failed=ctest_failed,
        lintian_errors=len(err_lines),
        apk_verified=apk_verified,
        lintian_error_lines=tuple(err_lines),
    )


def parse_obs_result_states(xml_text: str) -> list[dict]:
    """Parse OBS /build/<project>/_result XML into repository/package status records."""
    root = ET.fromstring(xml_text)
    rows: list[dict] = []
    for res in root.findall("result"):
        repo = res.attrib.get("repository", "")
        arch = res.attrib.get("arch", "")
        repo_code = res.attrib.get("code", "")
        dirty = res.attrib.get("dirty", "false") == "true"
        for status in res.findall("status"):
            rows.append(
                {
                    "repository": repo,
                    "arch": arch,
                    "repo_code": repo_code,
                    "dirty": dirty,
                    "package": status.attrib.get("package", ""),
                    "code": status.attrib.get("code", "unknown"),
                }
            )
    return rows


def _resolve_osc_config(explicit: str | None) -> Path | None:
    if explicit:
        return Path(explicit).expanduser()
    xdg = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    candidate = xdg / "osc" / "oscrc"
    if candidate.is_file():
        return candidate
    legacy = Path.home() / ".oscrc"
    if legacy.is_file():
        return legacy
    return None


def verify_controller_environment(
    tmpdir: Path,
    osc_config: Path | None,
    osc_binary: str | None = None,
    check_non_tmpfs: bool = True,
) -> EnvCheckResult:
    """Verify disk-backed TMPDIR, osc binary availability, and readable oscrc."""
    errors: list[str] = []
    details: dict[str, str] = {}

    if osc_binary is None:
        resolved_osc = shutil.which("osc")
    elif osc_binary and os.access(osc_binary, os.X_OK):
        resolved_osc = osc_binary
    else:
        resolved_osc = None

    if not resolved_osc:
        errors.append("osc executable not found in PATH")
    else:
        details["osc"] = resolved_osc

    if not tmpdir.is_dir():
        errors.append(f"TMPDIR does not exist: {tmpdir}")
    else:
        try:
            with tempfile.NamedTemporaryFile(dir=tmpdir, delete=True):
                pass
        except OSError as exc:
            errors.append(f"TMPDIR is not writable ({tmpdir}): {exc}")
        if check_non_tmpfs:
            fstype = subprocess.run(
                ["findmnt", "-n", "-o", "FSTYPE", "-T", str(tmpdir)],
                capture_output=True,
                text=True,
                check=False,
            )
            if fstype.returncode == 0 and fstype.stdout.strip() == "tmpfs":
                errors.append(f"TMPDIR must be disk-backed, got tmpfs: {tmpdir}")
            elif fstype.returncode == 0:
                details["tmpdir_fstype"] = fstype.stdout.strip()

    if osc_config is None or not osc_config.is_file() or not os.access(osc_config, os.R_OK):
        errors.append("Readable oscrc configuration not found")
    else:
        details["osc_config_present"] = "true"

    return EnvCheckResult(ok=not errors, errors=errors, details=details)


def evaluate_promotion_readiness(
    staging_state: str,
    ctest_ran: int,
    ctest_failed: int,
    lintian_errors: int,
    deb_verified: bool,
    require_ctest: bool = True,
) -> PromotionDecision:
    """Enforce mandatory Gate 2 and Gate 3 evidence before promoting staging to stable."""
    reasons: list[str] = []
    if staging_state != "succeeded":
        reasons.append(f"OBS staging build state is '{staging_state}', expected 'succeeded'")
    if require_ctest and ctest_ran <= 0:
        reasons.append("No ctest test cases executed in OBS staging build log")
    if ctest_failed != 0:
        reasons.append(f"ctest reported {ctest_failed} failing test(s)")
    if lintian_errors != 0:
        reasons.append(f"lintian reported {lintian_errors} error(s)")
    if not deb_verified:
        reasons.append("Isolated .deb install/contents check has not passed")
    return PromotionDecision(ready=not reasons, reasons=reasons)


def evaluate_package_promotion_readiness(
    package: str,
    staging_state: str,
    ctest_ran: int,
    ctest_failed: int,
    lintian_errors: int,
    deb_verified: bool,
) -> PromotionDecision:
    """Enforce Gate 2 and Gate 3 readiness rules for a named OBS package."""
    return evaluate_promotion_readiness(
        staging_state=staging_state,
        ctest_ran=ctest_ran,
        ctest_failed=ctest_failed,
        lintian_errors=lintian_errors,
        deb_verified=deb_verified,
        require_ctest=(package == "kdeconnect"),
    )


def _ensure_state_tmp() -> Path:
    tmp = STATE / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    return tmp


def _run_osc(*args: str, cwd: Path | None = None, binary: bool = False) -> subprocess.CompletedProcess:
    cmd = build_osc_command(*args)
    return subprocess.run(
        cmd,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=not binary,
    )


def _osc_api(path: str, method: str = "GET", xml_text: str | None = None, out_file: Path | None = None) -> str:
    tmp = _ensure_state_tmp()
    if xml_text is not None:
        with tempfile.NamedTemporaryFile(dir=tmp, suffix=".xml", delete=False, mode="w", encoding="utf-8") as handle:
            handle.write(xml_text)
            payload_path = Path(handle.name)
        try:
            proc = _run_osc("api", "-X", method, "-T", str(payload_path), path)
            return proc.stdout
        finally:
            payload_path.unlink(missing_ok=True)

    if out_file is not None:
        part = out_file.with_suffix(out_file.suffix + ".part")
        proc = _run_osc("api", "-X", method, path, binary=True)
        part.write_bytes(proc.stdout)
        os.replace(part, out_file)
        return ""

    return _run_osc("api", "-X", method, path).stdout


def _save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(".new")
    part.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(part, path)


def _cmd_verify_env() -> int:
    tmpdir = Path(os.environ.get("TMPDIR", ".state/tmp")).resolve()
    osc_config = _resolve_osc_config(os.environ.get("OSC_CONFIG"))
    result = verify_controller_environment(tmpdir=tmpdir, osc_config=osc_config)
    if not result.ok:
        for err in result.errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1
    fstype = result.details.get("tmpdir_fstype", "unknown")
    print(f"Controller environment OK (osc={result.details['osc']}, tmpdir_fstype={fstype}, oscrc=readable)")
    return 0


def _cmd_ensure_projects(staging_proj: str, stable_proj: str) -> int:
    operations = [
        (f"/source/{stable_proj}/_meta", ROOT / "obs" / "project.xml"),
        (f"/source/{staging_proj}/_meta", ROOT / "obs" / "staging.xml"),
        (f"/source/{staging_proj}/libei/_meta", ROOT / "obs" / "packages" / "libei.xml"),
        (f"/source/{staging_proj}/kdeconnect/_meta", ROOT / "obs" / "packages" / "kdeconnect.xml"),
        (
            f"/source/{staging_proj}/kdeconnect-android-probe/_meta",
            ROOT / "obs" / "packages" / "kdeconnect-android-probe.xml",
        ),
        (f"/source/{stable_proj}/libei/_meta", ROOT / "obs" / "packages" / "libei-stable.xml"),
        (f"/source/{stable_proj}/kdeconnect/_meta", ROOT / "obs" / "packages" / "kdeconnect-stable.xml"),
    ]
    ensured: list[str] = []
    for api_path, xml_path in operations:
        xml_body = strip_xml_comments(xml_path.read_text(encoding="utf-8"))
        _osc_api(api_path, method="PUT", xml_text=xml_body)
        ensured.append(api_path)

    prjconf_operations = [
        (f"/source/{stable_proj}/_config", ROOT / "obs" / "stable.prjconf"),
        (f"/source/{staging_proj}/_config", ROOT / "obs" / "staging.prjconf"),
    ]
    for api_path, conf_path in prjconf_operations:
        conf_body = strip_spdx_comments(conf_path.read_text(encoding="utf-8"))
        _osc_api(api_path, method="PUT", xml_text=conf_body)
        ensured.append(api_path)

    print(json.dumps({"ensured": ensured}, indent=2))
    return 0


def _cmd_upload_bundle(project: str, package: str, bundle_dir: Path) -> int:
    manifest = verify_bundle_manifest(bundle_dir)
    new_names = [entry["filename"] for entry in manifest["files"]]
    new_set = set(new_names)

    tmp_root = _ensure_state_tmp()
    work_parent = tmp_root / f"checkout-{package}"
    if work_parent.exists():
        shutil.rmtree(work_parent)
    work_parent.mkdir(parents=True)

    _run_osc("checkout", project, package, cwd=work_parent)
    pkg_dir = work_parent / project / package

    for candidate in sorted(pkg_dir.iterdir()):
        if not candidate.is_file():
            continue
        if (candidate.suffix == ".dsc" or ".tar." in candidate.name) and candidate.name not in new_set:
            _run_osc("rm", candidate.name, cwd=pkg_dir)

    for name in new_names:
        dest = pkg_dir / name
        existed = dest.exists()
        shutil.copy2(bundle_dir / name, dest)
        if not existed:
            _run_osc("add", name, cwd=pkg_dir)

    _run_osc("commit", "-m", f"chore: update {package} {manifest['version']} bundle", cwd=pkg_dir)
    dir_xml = _osc_api(f"/source/{project}/{package}")
    srcmd5 = ET.fromstring(dir_xml).attrib.get("srcmd5", "")

    record = {
        "project": project,
        "package": package,
        "version": manifest["version"],
        "dsc": manifest["dsc"],
        "srcmd5": srcmd5,
        "uploaded_at": int(time.time()),
    }
    _save_json(STATE / "candidates" / f"{package}.json", record)
    shutil.rmtree(work_parent, ignore_errors=True)
    print(json.dumps(record, indent=2, sort_keys=True))
    return 0


def _lookup_package_state(project: str, package: str, distro: str, arch: str) -> dict | None:
    xml_text = _osc_api(f"/build/{project}/_result?package={package}")
    for row in parse_obs_result_states(xml_text):
        if row["repository"] == distro and row["arch"] == arch and row["package"] == package:
            return row
    return None


def _cmd_inspect_build(
    project: str,
    package: str,
    distro: str,
    arch: str,
    wait: bool,
    timeout_sec: int,
) -> int:
    deadline = time.monotonic() + timeout_sec
    state_row = _lookup_package_state(project, package, distro, arch)
    while wait:
        if (
            state_row
            and not state_row["dirty"]
            and state_row["repo_code"] in SETTLED_REPO_CODES
            and state_row["code"] not in IN_PROGRESS_STATES
        ):
            break
        if time.monotonic() >= deadline:
            print(f"ERROR: Timed out waiting for {project}/{package}", file=sys.stderr)
            return 1
        cur = state_row["code"] if state_row else "pending"
        repo_code = state_row["repo_code"] if state_row else "pending"
        dirty = state_row["dirty"] if state_row else True
        print(
            f"Waiting for {package} ({distro}/{arch}): code={cur} repo_code={repo_code} dirty={dirty}",
            flush=True,
        )
        time.sleep(10)
        state_row = _lookup_package_state(project, package, distro, arch)

    if state_row is None:
        print(f"ERROR: No build result found for {project}/{package} ({distro}/{arch})", file=sys.stderr)
        return 1

    srcmd5 = ET.fromstring(_osc_api(f"/source/{project}/{package}")).attrib.get("srcmd5", "unknown")
    log_text = ""
    try:
        log_text = _osc_api(f"/build/{project}/{distro}/{arch}/{package}/_log")
    except subprocess.CalledProcessError:
        log_text = ""

    log_path = STATE / "logs" / f"{package}-{srcmd5}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(log_text, encoding="utf-8")

    metrics = parse_obs_build_log(log_text)
    binaries_downloaded: list[dict] = []
    if state_row["code"] == "succeeded":
        bin_xml = ET.fromstring(_osc_api(f"/build/{project}/{distro}/{arch}/{package}"))
        bin_dir = STATE / "binaries" / package / srcmd5
        bin_dir.mkdir(parents=True, exist_ok=True)
        for elem in bin_xml.findall("binary"):
            fname = elem.attrib.get("filename", "")
            if not fname.endswith(".deb"):
                continue
            target = bin_dir / fname
            _osc_api(f"/build/{project}/{distro}/{arch}/{package}/{fname}", out_file=target)
            sha = hashlib.sha256(target.read_bytes()).hexdigest()
            binaries_downloaded.append({"filename": fname, "path": str(target), "sha256": sha})

    summary = {
        "project": project,
        "package": package,
        "distro": distro,
        "arch": arch,
        "srcmd5": srcmd5,
        "code": state_row["code"],
        "dirty": state_row["dirty"],
        "ctest_ran": metrics.ctest_ran,
        "ctest_failed": metrics.ctest_failed,
        "lintian_errors": metrics.lintian_errors,
        "lintian_error_lines": list(metrics.lintian_error_lines),
        "apk_verified": metrics.apk_verified,
        "log_path": str(log_path),
        "binaries": binaries_downloaded,
    }
    _save_json(STATE / "binaries" / package / "latest.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))

    passed = state_row["code"] == "succeeded" and metrics.lintian_errors == 0
    if package == "kdeconnect":
        passed = passed and metrics.ctest_ran > 0 and metrics.ctest_failed == 0
    elif package == "kdeconnect-android-probe":
        passed = passed and metrics.apk_verified
    return 0 if passed else 1


def _cmd_promote(
    staging: str,
    stable: str,
    package: str,
    distro: str,
    arch: str,
    deb_verified: bool,
    wait: bool,
    timeout_sec: int,
    expected_srcmd5: str | None = None,
) -> int:
    summary_path = STATE / "binaries" / package / "latest.json"
    if not summary_path.is_file():
        print(f"ERROR: Missing staging inspection summary: {summary_path}", file=sys.stderr)
        return 1

    latest = json.loads(summary_path.read_text(encoding="utf-8"))
    staged_srcmd5 = str(latest.get("srcmd5", ""))
    if not SRCMD5_RE.match(staged_srcmd5):
        print(f"ERROR: Invalid staged srcmd5 in inspection summary: {staged_srcmd5!r}", file=sys.stderr)
        return 1
    if expected_srcmd5 is not None and staged_srcmd5 != expected_srcmd5:
        print(
            f"ERROR: TOCTOU srcmd5 mismatch: verified={expected_srcmd5} latest={staged_srcmd5}",
            file=sys.stderr,
        )
        return 1

    decision = evaluate_package_promotion_readiness(
        package=package,
        staging_state=str(latest.get("code", "unknown")),
        ctest_ran=int(latest.get("ctest_ran", 0)),
        ctest_failed=int(latest.get("ctest_failed", 0)),
        lintian_errors=int(latest.get("lintian_errors", 0)),
        deb_verified=deb_verified,
    )
    if not decision.ready:
        print(f"ERROR: Promotion blocked for {package}:", file=sys.stderr)
        for reason in decision.reasons:
            print(f"  - {reason}", file=sys.stderr)
        return 1

    live_dir_xml = _osc_api(f"/source/{staging}/{package}")
    live_srcmd5 = ET.fromstring(live_dir_xml).attrib.get("srcmd5", "")
    target_srcmd5 = expected_srcmd5 or staged_srcmd5
    if live_srcmd5 != target_srcmd5:
        print(
            f"ERROR: Live staging source srcmd5 '{live_srcmd5}' does not match verified '{target_srcmd5}'",
            file=sys.stderr,
        )
        return 1

    _run_osc(
        "release",
        staging,
        package,
        "-r",
        distro,
        "-a",
        arch,
        "--target-project",
        stable,
        "--target-repository",
        distro,
        "--no-delay",
    )

    deadline = time.monotonic() + timeout_sec
    state_row = _lookup_package_state(stable, package, distro, arch)
    while wait:
        if state_row and not state_row["dirty"] and state_row["repo_code"] == "published":
            break
        if time.monotonic() >= deadline:
            print(f"ERROR: Timed out waiting for {stable}/{package} repository publish", file=sys.stderr)
            return 1
        repo_code = state_row["repo_code"] if state_row else "pending"
        dirty = state_row["dirty"] if state_row else True
        print(
            f"Waiting for {stable}/{package} ({distro}/{arch}): repo_code={repo_code} dirty={dirty}",
            flush=True,
        )
        time.sleep(5)
        state_row = _lookup_package_state(stable, package, distro, arch)

    record = {
        "staging": staging,
        "stable": stable,
        "package": package,
        "distro": distro,
        "arch": arch,
        "srcmd5": target_srcmd5,
        "promoted_at": int(time.time()),
        "repo_code": state_row["repo_code"] if state_row else "unknown",
        "dirty": state_row["dirty"] if state_row else True,
        "package_code": state_row["code"] if state_row else "unknown",
        "binaries": [item.get("filename") for item in latest.get("binaries", [])],
    }
    _save_json(STATE / "promotions" / f"{package}.json", record)
    print(json.dumps(record, indent=2, sort_keys=True))
    return 0


def _ensure_state_permissions(state_dir: Path | None = None) -> None:
    target = state_dir or STATE
    target.mkdir(parents=True, exist_ok=True)
    os.chmod(target, 0o700)


def prune_old_artifacts(
    package: str,
    keep: int = 2,
    keep_srcmd5s: set[str] | None = None,
    state_dir: Path | None = None,
    sweep_tmp_age_sec: int = 7200,
) -> dict[str, list[str]]:
    """Remove older binary dirs, logs, and stale temp/bundle files while keeping promoted revisions."""
    target = state_dir or STATE
    protected: set[str] = set(keep_srcmd5s or ())

    for meta_path in (
        target / "binaries" / package / "latest.json",
        target / "promotions" / f"{package}.json",
    ):
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                src = meta.get("srcmd5")
                if isinstance(src, str) and src:
                    protected.add(src)
            except json.JSONDecodeError:
                pass

    removed_dirs: list[str] = []
    bin_root = target / "binaries" / package
    if bin_root.is_dir():
        subdirs = sorted(
            (p for p in bin_root.iterdir() if p.is_dir()),
            key=lambda p: (p.stat().st_mtime, p.name),
            reverse=True,
        )
        for idx, sub in enumerate(subdirs):
            if idx < keep or sub.name in protected:
                continue
            shutil.rmtree(sub, ignore_errors=True)
            removed_dirs.append(sub.name)

    removed_logs: list[str] = []
    logs_root = target / "logs"
    if logs_root.is_dir():
        prefix = f"{package}-"
        log_files = sorted(
            (p for p in logs_root.glob(f"{prefix}*.log") if p.is_file()),
            key=lambda p: (p.stat().st_mtime, p.name),
            reverse=True,
        )
        for idx, log_file in enumerate(log_files):
            srcmd5 = log_file.stem[len(prefix) :]
            if idx < keep or srcmd5 in protected:
                continue
            log_file.unlink(missing_ok=True)
            removed_logs.append(log_file.name)

    removed_tmp: list[str] = []
    now_ts = time.time()
    for scratch_name in ("tmp", "bundles", "incoming"):
        scratch_dir = target / scratch_name
        if not scratch_dir.is_dir():
            continue
        for entry in sorted(scratch_dir.iterdir()):
            try:
                age_sec = now_ts - entry.stat().st_mtime
            except OSError:
                continue
            if age_sec < sweep_tmp_age_sec:
                continue
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)
            removed_tmp.append(f"{scratch_name}/{entry.name}")

    return {"removed_dirs": removed_dirs, "removed_logs": removed_logs, "removed_tmp": removed_tmp}


def _cmd_cleanup(keep: int = 2, tmp_age_sec: int = 7200) -> int:
    _ensure_state_permissions(STATE)
    result = prune_old_artifacts("kdeconnect", keep=keep, state_dir=STATE, sweep_tmp_age_sec=tmp_age_sec)
    repo_dir = STATE / "repos" / "kdeconnect-kde"
    git_gc_ran = False
    if (repo_dir / ".git" / "HEAD").is_file():
        run_git_hardened(repo_dir, "gc", "--auto", check=False)
        git_gc_ran = True
    payload = {**result, "git_gc_ran": git_gc_ran}
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def check_android_upstream_drift(state_dir: Path | None = None) -> dict:
    """Poll kdeconnect-android upstream HEAD without blocking the desktop OBS cycle."""
    target = state_dir or STATE
    base_path = target / "sync" / "android_base.txt"
    last_base = base_path.read_text(encoding="utf-8").strip() if base_path.is_file() else None
    try:
        upstream_sha = query_remote_head(ANDROID_UPSTREAM_URL, "refs/heads/master")
        record = {
            "upstream_master_sha": upstream_sha,
            "last_built_base_sha": last_base,
            "needs_local_rebuild": bool(last_base and upstream_sha != last_base),
            "checked_at": int(time.time()),
            "status": "ok",
        }
        _save_json(target / "sync" / "android.json", record)
        if record["needs_local_rebuild"]:
            print(
                f"Notice: kdeconnect-android origin/master moved to {upstream_sha[:12]} "
                f"(local base {last_base[:12]}); run bounded Gradle rebuild on workstation.",
                flush=True,
            )
        return record
    except Exception as exc:
        print(f"WARNING: Android upstream check skipped ({exc})", file=sys.stderr)
        return {
            "last_built_base_sha": last_base,
            "needs_local_rebuild": False,
            "checked_at": int(time.time()),
            "status": "error",
            "error": str(exc),
        }


def _wait_for_staging_settlement(
    project: str,
    package: str,
    distro: str,
    arch: str,
    wait_sec: int,
) -> tuple[bool, dict | None]:
    deadline = time.monotonic() + max(0, wait_sec)
    state_row = _lookup_package_state(project, package, distro, arch)
    while True:
        settled = (
            state_row is not None
            and not state_row["dirty"]
            and state_row["repo_code"] in SETTLED_REPO_CODES
            and state_row["code"] not in IN_PROGRESS_STATES
        )
        if settled:
            return True, state_row
        if time.monotonic() >= deadline:
            return False, state_row
        cur = state_row["code"] if state_row else "pending"
        repo_code = state_row["repo_code"] if state_row else "pending"
        dirty = state_row["dirty"] if state_row else True
        print(
            f"Waiting for staging settlement {project}/{package} ({distro}/{arch}): "
            f"code={cur} repo_code={repo_code} dirty={dirty}",
            flush=True,
        )
        time.sleep(min(15, max(1, int(deadline - time.monotonic()))))
        state_row = _lookup_package_state(project, package, distro, arch)


def _find_libei_debs_dir(state_dir: Path) -> Path | None:
    latest_path = state_dir / "binaries" / "libei" / "latest.json"
    if not latest_path.is_file():
        return None
    meta = json.loads(latest_path.read_text(encoding="utf-8"))
    srcmd5 = str(meta.get("srcmd5", ""))
    if not srcmd5:
        return None
    candidate = state_dir / "binaries" / "libei" / srcmd5
    return candidate if candidate.is_dir() else None


def _cmd_cycle(wait_sec: int = 2400, no_promote: bool = False, dry_run: bool = False) -> int:
    if _cmd_verify_env() != 0:
        return 1
    _ensure_state_permissions(STATE)
    check_android_upstream_drift(STATE)

    repo_dir = STATE / "repos" / "kdeconnect-kde"
    base_file = STATE / "sync" / "plugin_base.txt"
    if not (repo_dir / ".git").exists() or not base_file.is_file():
        print(
            "ERROR: Runner desktop mirror not initialized; run tools/sync-runner.sh from workstation first.",
            file=sys.stderr,
        )
        return 1

    plugin_base_sha = base_file.read_text(encoding="utf-8").strip()
    origin_url = run_git_hardened(repo_dir, "remote", "get-url", "origin").stdout.strip()
    validate_remote_url(origin_url)
    run_git_hardened(repo_dir, "fetch", "origin", "master")

    upstream_sha = run_git_hardened(repo_dir, "rev-parse", "refs/remotes/origin/master").stdout.strip()
    plugin_sha = run_git_hardened(repo_dir, "rev-parse", "refs/heads/plugin/screenstream").stdout.strip()
    overlay_dir = ROOT / "obs" / "kdeconnect" / "debian"
    overlay_sha = compute_overlay_sha256(overlay_dir)
    fingerprint = compute_cycle_fingerprint(upstream_sha, plugin_sha, overlay_sha)

    sync_path = STATE / "sync" / "kdeconnect.json"
    sync_state = json.loads(sync_path.read_text(encoding="utf-8")) if sync_path.is_file() else {}
    return _execute_cycle_with_cleanup(
        repo_dir=repo_dir,
        overlay_dir=overlay_dir,
        plugin_base_sha=plugin_base_sha,
        upstream_sha=upstream_sha,
        plugin_sha=plugin_sha,
        overlay_sha=overlay_sha,
        fingerprint=fingerprint,
        sync_path=sync_path,
        sync_state=sync_state,
        wait_sec=wait_sec,
        no_promote=no_promote,
        dry_run=dry_run,
    )


def _execute_cycle_with_cleanup(
    *,
    repo_dir: Path,
    overlay_dir: Path,
    plugin_base_sha: str,
    upstream_sha: str,
    plugin_sha: str,
    overlay_sha: str,
    fingerprint: str,
    sync_path: Path,
    sync_state: dict,
    wait_sec: int,
    no_promote: bool,
    dry_run: bool,
) -> int:
    if dry_run:
        if sync_state.get("promoted_fingerprint") == fingerprint:
            print(f"[dry-run] Up to date (fingerprint={fingerprint[:12]}, upstream={upstream_sha[:12]})")
            return 0
        cand = sync_state.get("active_candidate")
        if isinstance(cand, dict) and cand.get("fingerprint") == fingerprint:
            action = f"reconcile {cand.get('stage')}"
        else:
            action = "synthesize and upload"
        print(f"[dry-run] Would {action} for fingerprint={fingerprint[:12]} upstream={upstream_sha[:12]}")
        return 0

    keep_hashes: set[str] = set()
    if isinstance(sync_state.get("promoted_srcmd5"), str) and sync_state["promoted_srcmd5"]:
        keep_hashes.add(sync_state["promoted_srcmd5"])
    try:
        if sync_state.get("promoted_fingerprint") == fingerprint:
            print(f"Up to date (fingerprint={fingerprint[:12]}, upstream={upstream_sha[:12]})")
            return 0

        candidate = sync_state.get("active_candidate")
        if isinstance(candidate, dict) and candidate.get("fingerprint") == fingerprint:
            stage = str(candidate.get("stage", ""))
            if stage in ("failed_gate", "rebase_conflict"):
                print(
                    f"Circuit breaker: skipping previously failed fingerprint {fingerprint[:12]} (stage={stage})"
                )
                return 0
        else:
            candidate = None

        if candidate is None:
            synth = synthesize_plugin_branch(
                repo_dir=repo_dir,
                upstream_ref="refs/remotes/origin/master",
                plugin_ref="refs/heads/plugin/screenstream",
                plugin_base_sha=plugin_base_sha,
                target_branch="obs/kdeconnect-staging",
            )
            if not synth.ok or not synth.synthesized_sha:
                sync_state["active_candidate"] = {
                    "fingerprint": fingerprint,
                    "stage": "rebase_conflict" if synth.status == "rebase_conflict" else "failed_gate",
                    "status": synth.status,
                    "conflict_files": list(synth.conflict_files),
                    "error": synth.error,
                    "updated_at": int(time.time()),
                }
                _save_json(sync_path, sync_state)
                print(f"ERROR: Plugin synthesis failed ({synth.status}): {synth.error}", file=sys.stderr)
                return 1

            compliance_issues = check_upstream_branch(repo_dir, "refs/remotes/origin/master")
            if compliance_issues:
                sync_state["active_candidate"] = {
                    "fingerprint": fingerprint,
                    "stage": "failed_gate",
                    "status": "compliance_failed",
                    "error": "; ".join(compliance_issues),
                    "updated_at": int(time.time()),
                }
                _save_json(sync_path, sync_state)
                print(f"ERROR: KDE Invent compliance failed: {compliance_issues}", file=sys.stderr)
                return 1

            bundle_dir = _ensure_state_tmp() / f"bundle-{synth.synthesized_sha[:12]}"
            if bundle_dir.exists():
                shutil.rmtree(bundle_dir)
            try:
                bundle_info = build_kdeconnect_bundle(repo_dir, overlay_dir, bundle_dir, obs_rev=1)
                if _cmd_upload_bundle(DEFAULT_STAGING_PROJECT, "kdeconnect", bundle_dir) != 0:
                    sync_state["active_candidate"] = {
                        "fingerprint": fingerprint,
                        "stage": "failed_gate",
                        "status": "upload_failed",
                        "updated_at": int(time.time()),
                    }
                    _save_json(sync_path, sync_state)
                    return 1
            finally:
                shutil.rmtree(bundle_dir, ignore_errors=True)

            cand_meta = json.loads((STATE / "candidates" / "kdeconnect.json").read_text(encoding="utf-8"))
            candidate = {
                "fingerprint": fingerprint,
                "stage": "staging_uploaded",
                "upstream_sha": upstream_sha,
                "plugin_sha": plugin_sha,
                "overlay_sha": overlay_sha,
                "synthesized_sha": synth.synthesized_sha,
                "srcmd5": cand_meta["srcmd5"],
                "version": bundle_info["version"],
                "updated_at": int(time.time()),
            }
            sync_state["active_candidate"] = candidate
            _save_json(sync_path, sync_state)

        if candidate.get("stage") == "staging_uploaded":
            settled, row = _wait_for_staging_settlement(
                DEFAULT_STAGING_PROJECT,
                "kdeconnect",
                DEFAULT_DISTRO,
                DEFAULT_ARCH,
                wait_sec,
            )
            if not settled:
                print(f"Staging build still in progress ({row}); will reconcile on next cycle.", flush=True)
                return 0

            inspect_rc = _cmd_inspect_build(
                DEFAULT_STAGING_PROJECT,
                "kdeconnect",
                DEFAULT_DISTRO,
                DEFAULT_ARCH,
                wait=False,
                timeout_sec=60,
            )
            latest = json.loads((STATE / "binaries" / "kdeconnect" / "latest.json").read_text(encoding="utf-8"))
            if inspect_rc != 0 or latest.get("srcmd5") != candidate.get("srcmd5"):
                candidate["stage"] = "failed_gate"
                candidate["status"] = "inspect_or_srcmd5_failed"
                candidate["updated_at"] = int(time.time())
                _save_json(sync_path, sync_state)
                print("ERROR: Staging build/ctest/lintian gate failed or srcmd5 changed", file=sys.stderr)
                return 1

            debs_dir = STATE / "binaries" / "kdeconnect" / candidate["srcmd5"]
            libei_dir = _find_libei_debs_dir(STATE)
            if not debs_dir.is_dir() or libei_dir is None:
                candidate["stage"] = "failed_gate"
                candidate["status"] = "missing_deb_artifacts"
                candidate["updated_at"] = int(time.time())
                _save_json(sync_path, sync_state)
                print("ERROR: Missing kdeconnect or libei binary directory for Docker check", file=sys.stderr)
                return 1

            verify_proc = subprocess.run(
                [str(ROOT / "tools" / "verify-deb.sh"), str(debs_dir), str(libei_dir), str(candidate["version"])],
                env=build_scrubbed_env(),
                check=False,
            )
            if verify_proc.returncode != 0:
                candidate["stage"] = "failed_gate"
                candidate["status"] = "docker_verify_failed"
                candidate["updated_at"] = int(time.time())
                _save_json(sync_path, sync_state)
                print("ERROR: Air-gapped .deb verification failed", file=sys.stderr)
                return 1

            candidate["stage"] = "deb_verified"
            candidate["updated_at"] = int(time.time())
            _save_json(sync_path, sync_state)

        if candidate.get("stage") == "deb_verified":
            if no_promote:
                print(f"Verified staging build {candidate['srcmd5']} (--no-promote set); skipping stable promote.")
                return 0

            promote_rc = _cmd_promote(
                DEFAULT_STAGING_PROJECT,
                DEFAULT_STABLE_PROJECT,
                "kdeconnect",
                DEFAULT_DISTRO,
                DEFAULT_ARCH,
                deb_verified=True,
                wait=True,
                timeout_sec=600,
                expected_srcmd5=candidate["srcmd5"],
            )
            if promote_rc != 0:
                return 1

            candidate["stage"] = "promoted"
            candidate["updated_at"] = int(time.time())
            keep_hashes.add(candidate["srcmd5"])
            sync_state["promoted_fingerprint"] = fingerprint
            sync_state["promoted_srcmd5"] = candidate["srcmd5"]
            sync_state["promoted_version"] = candidate["version"]
            sync_state["promoted_at"] = int(time.time())
            _save_json(sync_path, sync_state)
            return 0

        return 0
    finally:
        prune_old_artifacts("kdeconnect", keep=2, keep_srcmd5s=keep_hashes, state_dir=STATE)
        if (repo_dir / ".git" / "HEAD").is_file():
            run_git_hardened(repo_dir, "gc", "--auto", check=False)


def _cmd_status(project: str, package: str | None) -> int:
    if not package:
        print(
            f"OBS targets: staging={DEFAULT_STAGING_PROJECT}, "
            f"stable={DEFAULT_STABLE_PROJECT}, distro={DEFAULT_DISTRO}, arch={DEFAULT_ARCH}"
        )
        return 0
    cmd = build_osc_command("results", project, package, "-r", DEFAULT_DISTRO, "-a", DEFAULT_ARCH)
    proc = subprocess.run(cmd, check=False)
    return proc.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="KDE Connect OBS controller")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("verify-env", help="Verify TMPDIR, osc binary, and oscrc readability")

    cycle_p = sub.add_parser("cycle", help="Run automated upstream sync, OBS build, Docker check, and promote cycle")
    cycle_p.add_argument("--wait-sec", type=int, default=2400)
    cycle_p.add_argument("--no-promote", action="store_true")
    cycle_p.add_argument("--dry-run", action="store_true")

    cleanup_p = sub.add_parser("cleanup", help="Prune old staged artifacts, temp scratch dirs, and run git gc")
    cleanup_p.add_argument("--keep", type=int, default=2)
    cleanup_p.add_argument("--tmp-age-sec", type=int, default=7200)

    ensure_p = sub.add_parser("ensure-projects", help="Ensure OBS staging/stable projects and package metadata")
    ensure_p.add_argument("--staging", default=DEFAULT_STAGING_PROJECT)
    ensure_p.add_argument("--stable", default=DEFAULT_STABLE_PROJECT)

    upload_p = sub.add_parser("upload-bundle", help="Verify and upload a source bundle to OBS")
    upload_p.add_argument("--project", default=DEFAULT_STAGING_PROJECT)
    upload_p.add_argument("--package", required=True)
    upload_p.add_argument("--bundle-dir", type=Path, required=True)

    inspect_p = sub.add_parser("inspect-build", help="Inspect OBS build status, logs, and fetch .deb artifacts")
    inspect_p.add_argument("--project", default=DEFAULT_STAGING_PROJECT)
    inspect_p.add_argument("--package", required=True)
    inspect_p.add_argument("--distro", default=DEFAULT_DISTRO)
    inspect_p.add_argument("--arch", default=DEFAULT_ARCH)
    inspect_p.add_argument("--wait", action="store_true")
    inspect_p.add_argument("--timeout-sec", type=int, default=2400)

    promote_p = sub.add_parser("promote", help="Promote verified staging binaries to stable repository")
    promote_p.add_argument("--staging", default=DEFAULT_STAGING_PROJECT)
    promote_p.add_argument("--stable", default=DEFAULT_STABLE_PROJECT)
    promote_p.add_argument("--package", required=True)
    promote_p.add_argument("--distro", default=DEFAULT_DISTRO)
    promote_p.add_argument("--arch", default=DEFAULT_ARCH)
    promote_p.add_argument("--deb-verified", action="store_true")
    promote_p.add_argument("--wait", action="store_true")
    promote_p.add_argument("--timeout-sec", type=int, default=600)
    promote_p.add_argument("--expected-srcmd5", default=None)

    status_p = sub.add_parser("status", help="Show OBS configuration or query package results")
    status_p.add_argument("--project", default=DEFAULT_STAGING_PROJECT)
    status_p.add_argument("--package", default=None)

    args = parser.parse_args()
    if args.command == "verify-env":
        return _cmd_verify_env()
    if args.command == "cycle":
        return _cmd_cycle(wait_sec=args.wait_sec, no_promote=args.no_promote, dry_run=args.dry_run)
    if args.command == "cleanup":
        return _cmd_cleanup(keep=args.keep, tmp_age_sec=args.tmp_age_sec)
    if args.command == "ensure-projects":
        return _cmd_ensure_projects(args.staging, args.stable)
    if args.command == "upload-bundle":
        return _cmd_upload_bundle(args.project, args.package, args.bundle_dir.resolve())
    if args.command == "inspect-build":
        return _cmd_inspect_build(
            args.project,
            args.package,
            args.distro,
            args.arch,
            args.wait,
            args.timeout_sec,
        )
    if args.command == "promote":
        return _cmd_promote(
            args.staging,
            args.stable,
            args.package,
            args.distro,
            args.arch,
            args.deb_verified,
            args.wait,
            args.timeout_sec,
            expected_srcmd5=args.expected_srcmd5,
        )
    if args.command == "status":
        return _cmd_status(args.project, args.package)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
