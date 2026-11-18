<!-- SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com> -->
<!-- SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL -->

# Open Build Service (OBS) Runner & Packaging Guide

Desktop builds for Ubuntu 26.04 (`xUbuntu_26.04`, `x86_64`) use a two-project OBS pipeline driven from a dedicated runner host (`~/runners/kdeconnect-plugins`), with no local C++/Qt compilation and no GitHub Actions.

## 1. Two-Contour OBS Setup

| Contour | OBS Project | Packages | Role |
| --- | --- | --- | --- |
| Staging | `home:dwolfF:kdeconnect:staging` | `libei`, `kdeconnect`, `kdeconnect-android-probe` | Builds from uploaded `3.0 (quilt)` bundles (`obs/staging.xml`, `obs/staging.prjconf`), runs `ctest` (`xvfb-run` + `dbus-run-session`), and fails on any `lintian` error. Publishing is disabled here. |
| Stable | `home:dwolfF:kdeconnect` | `libei`, `kdeconnect` | Direct builds disabled (`obs/project.xml`, `obs/stable.prjconf`). Receives binaries only when promoted after staging + offline Docker verification pass. Publishes the public Ubuntu APT repository. |

## 2. How Packaging Works

- `obs/kdeconnect/debian/` -- Debian packaging overlay for upstream `kdeconnect-kde` (`master`) with `BUILD_TESTING=ON`, GStreamer video/audio runtime recommendations, `libei-dev (>= 1.6.0~)`, and `lintian --fail-on error`. Never committed into upstream Git branches.
- `obs/android-probe/debian/` -- Diagnostic Android probe packaging for OBS staging (`kdeconnect-android-probe`, never promoted to stable).
- `obs/verifier.Dockerfile` -- Builds the clean `kdeconnect-verifier:26.04` Rootless Docker image with Ubuntu runtime dependencies pre-installed so `.deb` tests run offline (`--network none`).
- `tools/obs_packaging.py` -- Packs deterministic `.orig.tar.xz` (excluding any top-level `debian/` dir) and `.debian.tar.xz` bundles with SHA-256 `manifest.json` verification and monotonic UTC Git version stamps (`+gitYYYYMMDDHHMMSS.<sha12>`).

## 3. Runner Commands & Hardening

All commands run directly on the runner host via `tools/run-controller.sh` (which enforces a disk-backed `TMPDIR` on `.state/tmp` and isolates `OSC_CONFIG` and cookie jars):

- `verify-env` -- Verify disk-backed `TMPDIR`, `osc` binary, and readable `OSC_CONFIG`.
- `ensure-projects` -- Sync `obs/*.xml` and `obs/*.prjconf` metadata to OBS.
- `cycle [--wait-sec N] [--no-promote] [--dry-run]` -- Full automated loop:
  1. Checks `kdeconnect-android` `origin/master` drift and saves `.state/sync/android.json` (informational; warns if Android local rebuild is needed).
  2. Fetches `https://invent.kde.org/network/kdeconnect-kde.git` `master` using hardened Git (`core.hooksPath=/dev/null`, `core.symlinks=false`, `submodule.recurse=false`, `protocol.ext.allow=never`, `fsckObjects=true`, scrubbed credentials).
  3. Computes a composite fingerprint (`upstream_sha + plugin_sha + overlay_sha`). Exits immediately if already promoted (`Up to date`) or tripped by an earlier failure for the exact same fingerprint.
  4. Cherry-picks `plugin/screenstream` onto `obs/kdeconnect-staging` cleanly, checks tree cleanliness and `check_kde_compliance.py`, builds the OBS bundle, and uploads it to `home:dwolfF:kdeconnect:staging`.
  5. Waits for OBS staging, downloads logs/binaries (`inspect-build`), and checks `ctest > 0`, `ctest_failed == 0`, and `lintian_errors == 0`.
  6. Runs `tools/verify-deb.sh`: static `dpkg-deb` path/mode/SUID/control checks on host + `--network none --cap-drop=ALL --security-opt=no-new-privileges:true` Rootless Docker install/purge + `readelf -d` (no `ldd`) + `gst-inspect-1.0`.
  7. Promotes to `home:dwolfF:kdeconnect` with an `expected_srcmd5` TOCTOU lock check right before `osc release`.
- `cleanup [--keep 2] [--tmp-age-sec 7200]` -- Prunes older binary directories and build logs (always keeping the latest `keep` plus active promoted revisions), sweeps stale `.state/{tmp,bundles,incoming}` entries, and runs `git gc --auto`. Runs automatically at the end of every `cycle`.
- `upload-bundle`, `inspect-build`, `promote`, `status` -- Lower-level manual commands for individual pipeline stages.

## 4. Cron Schedule

Run `python3 tools/install_cron.py` on the runner host to install only the managed hourly slot (`# BEGIN KDECONNECT OBS` at `47 * * * *` via `tools/cron-cycle.sh` with `flock -n` locking and `controller.log` rotation). Existing unrelated cron blocks remain untouched. Use `--remove` to uninstall only this runner's cron block.
