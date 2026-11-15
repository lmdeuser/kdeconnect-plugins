#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com>
# SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL

"""Host-side OBS controller entrypoint and release-gate checks."""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".state"

DEFAULT_STAGING_PROJECT = "home:dwolfF:kdeconnect:staging"
DEFAULT_STABLE_PROJECT = "home:dwolfF:kdeconnect"
DEFAULT_DISTRO = "xUbuntu_26.04"
DEFAULT_ARCH = "x86_64"


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

    status_p = sub.add_parser("status", help="Show OBS configuration or query package results")
    status_p.add_argument("--project", default=DEFAULT_STAGING_PROJECT)
    status_p.add_argument("--package", default=None)

    args = parser.parse_args()
    if args.command == "verify-env":
        return _cmd_verify_env()
    if args.command == "cycle":
        return _cmd_verify_env()
    if args.command == "status":
        return _cmd_status(args.project, args.package)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
