<!-- SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com> -->
<!-- SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL -->

# Open Build Service (OBS) Runner and Packaging Guide

Ubuntu 26.04 (`xUbuntu_26.04`, `x86_64`) desktop builds run across a two-project OBS setup driven from a dedicated runner host, with no local C++/Qt compilation and no GitHub Actions.

## Two-contour OBS setup

| Contour | OBS project | Packages | Role |
| --- | --- | --- | --- |
| Staging | [home:dwolfF:kdeconnect:staging](https://build.opensuse.org/project/show/home:dwolfF:kdeconnect:staging) | `libei`, `kdeconnect`, `kdeconnect-android-probe` | Compiles uploaded `3.0 (quilt)` bundles defined in [staging.xml](staging.xml) and [staging.prjconf](staging.prjconf), runs `ctest` under `xvfb-run` and `dbus-run-session`, and fails on any `lintian` error. Publishing is disabled here. |
| Stable | [home:dwolfF:kdeconnect](https://build.opensuse.org/project/show/home:dwolfF:kdeconnect) | `libei`, `kdeconnect` | Direct builds are disabled in [project.xml](project.xml) and [stable.prjconf](stable.prjconf). Receives binaries only by promotion after staging tests and offline Docker verification pass, then publishes the public APT repository. |

## Packaging layout

- [kdeconnect/debian/](kdeconnect/debian/) holds the Debian overlay for upstream [kdeconnect-kde](https://invent.kde.org/network/kdeconnect-kde) (`master`), enabling `BUILD_TESTING=ON`, GStreamer video/audio recommendations, `libei-dev (>= 1.6.0~)`, and `lintian --fail-on error`. It stays strictly out of upstream Git branches.
- [android-probe/debian/](android-probe/debian/) builds the diagnostic `kdeconnect-android-probe` package in OBS staging only (never promoted to stable).
- [verifier.Dockerfile](verifier.Dockerfile) builds the clean `kdeconnect-verifier:26.04` Rootless Docker image with Ubuntu runtime dependencies pre-installed so `.deb` tests execute offline (`--network none`).
- [tools/obs_packaging.py](../tools/obs_packaging.py) packs deterministic `.orig.tar.xz` archives (excluding any top-level `debian/` directory) and `.debian.tar.xz` overlays with SHA-256 `manifest.json` manifests and monotonic UTC version stamps (`+gitYYYYMMDDHHMMSS.<sha12>`).

## Runner commands and hardening

Commands execute through [tools/run-controller.sh](../tools/run-controller.sh), which requires a disk-backed `TMPDIR` inside `.state/tmp` and isolates `OSC_CONFIG` and cookie jars:

- `verify-env`: verify disk-backed `TMPDIR`, `osc` binary, and readable `OSC_CONFIG`.
- `ensure-projects`: sync [staging.xml](staging.xml), [project.xml](project.xml), `*.prjconf`, and [packages/](packages/) XML metadata to OBS.
- `cycle [--wait-sec N] [--no-promote] [--dry-run]`: run the full automated loop in [tools/obs_controller.py](../tools/obs_controller.py):
  1. Check [kdeconnect-android](https://invent.kde.org/network/kdeconnect-android) `origin/master` drift and write `.state/sync/android.json` so you know when the local Android APK needs rebuilding.
  2. Fetch `kdeconnect-kde` `master` via [tools/upstream_sync.py](../tools/upstream_sync.py) with hardened Git flags (`core.hooksPath=/dev/null`, `core.symlinks=false`, `submodule.recurse=false`, `protocol.ext.allow=never`, `fsckObjects=true`, scrubbed credentials).
  3. Compute a composite fingerprint (`upstream_sha`, `plugin_sha`, and overlay SHA-256). Exit early if already promoted (`Up to date`) or circuit-broken by a prior failure on the exact same fingerprint.
  4. Cherry-pick `plugin/screenstream` onto a temporary `obs/kdeconnect-staging` branch, verify tree cleanliness and [tools/check_kde_compliance.py](../tools/check_kde_compliance.py), pack the OBS bundle, and upload it to staging.
  5. Wait for OBS staging (`inspect-build`) and verify `ctest_ran > 0`, `ctest_failed == 0`, and `lintian_errors == 0`.
  6. Run [tools/verify-deb.sh](../tools/verify-deb.sh): static `dpkg-deb` path/mode/SUID/control checks on the host, then Rootless Docker (`--network none`, `--cap-drop=ALL`, `--security-opt=no-new-privileges:true`, `--pids-limit=256`) `dpkg -i`, `readelf -d` check (`libgstreamer-1.0.so.0` and `libQt6Widgets.so.6` present, `Qt6Multimedia` absent), `gst-inspect-1.0`, and `dpkg -P`.
  7. Verify an `expected_srcmd5` TOCTOU lock against staging right before `osc release` promotes binaries to stable.
- `cleanup [--keep 2] [--tmp-age-sec 7200]`: prune older binary directories and logs while preserving active promotions and the newest `keep` builds, sweep stale `.state/{tmp,bundles,incoming}` paths, and run `git gc --auto` (also runs automatically at the end of every `cycle`).
- `upload-bundle`, `inspect-build`, `promote`, `status`: manual subcommands for running or inspecting individual stages.

## Cron schedule

Run [tools/install_cron.py](../tools/install_cron.py) on the runner host to add the managed hourly slot (`# BEGIN KDECONNECT OBS` at `47 * * * *`) invoking [tools/cron-cycle.sh](../tools/cron-cycle.sh) with `flock -n` locking and `controller.log` rotation. Existing unrelated cron entries remain untouched. Pass `--remove` to delete only this runner's cron block.
