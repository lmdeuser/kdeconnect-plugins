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
    from tools.obs_packaging import strip_xml_comments, verify_bundle_manifest
except ImportError:
    from obs_packaging import strip_xml_comments, verify_bundle_manifest

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".state"

DEFAULT_STAGING_PROJECT = "home:dwolfF:kdeconnect:staging"
DEFAULT_STABLE_PROJECT = "home:dwolfF:kdeconnect"
DEFAULT_DISTRO = "xUbuntu_26.04"
DEFAULT_ARCH = "x86_64"

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
) -> PromotionDecision:
    """Enforce mandatory Gate 2 and Gate 3 evidence before promoting staging to stable."""
    reasons: list[str] = []
    if staging_state != "succeeded":
        reasons.append(f"OBS staging build state is '{staging_state}', expected 'succeeded'")
    if ctest_ran <= 0:
        reasons.append("No ctest test cases executed in OBS staging build log")
    if ctest_failed != 0:
        reasons.append(f"ctest reported {ctest_failed} failing test(s)")
    if lintian_errors != 0:
        reasons.append(f"lintian reported {lintian_errors} error(s)")
    if not deb_verified:
        reasons.append("Isolated .deb install/contents check has not passed")
    return PromotionDecision(ready=not reasons, reasons=reasons)


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
    sub.add_parser("cycle", help="Run non-mutating controller readiness check")

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

    status_p = sub.add_parser("status", help="Show OBS configuration or query package results")
    status_p.add_argument("--project", default=DEFAULT_STAGING_PROJECT)
    status_p.add_argument("--package", default=None)

    args = parser.parse_args()
    if args.command == "verify-env":
        return _cmd_verify_env()
    if args.command == "cycle":
        return _cmd_verify_env()
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
    if args.command == "status":
        return _cmd_status(args.project, args.package)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
