<!-- SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com> -->
<!-- SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL -->

# Open Build Service (OBS) Packaging Architecture

Desktop KDE Connect builds, prerequisite backports, and remote Android verification APKs targeting Ubuntu 26.04 (`xUbuntu_26.04`, `x86_64`) use a strict two-contour OBS pipeline without local compilation or GitHub Actions.

## OBS Projects and Packages

| Role | OBS Project | Packages | Configuration Invariants |
| --- | --- | --- | --- |
| Staging | `home:dwolfF:kdeconnect:staging` | `libei`, `kdeconnect`, `kdeconnect-android-probe` | Project publishing disabled (`obs/staging.xml`); package `<sourceaccess><disable/></sourceaccess>` (`obs/packages/*.xml`); builds with `ctest` / `apksigner verify` and `lintian`. |
| Stable | `home:dwolfF:kdeconnect` | `libei`, `kdeconnect` | Direct builds disabled (`obs/project.xml`); receives binary promotion only after staging builds, tests, `lintian`, and isolated `.deb` checks pass. `kdeconnect-android-probe` stays staging-only. |

## Packaging Overlays and Bundle Generator

- `obs/kdeconnect/debian/` -- Debian packaging overlay for upstream `kdeconnect-kde` (`master`), adapted from Ubuntu `25.12.3-0ubuntu1` with strict `BUILD_TESTING=ON`, non-ignored `ctest` execution under `xvfb-run` + `dbus-run-session`, `libei-dev (>= 1.6.0~)`, and `lintian --fail-on error`.
- `obs/android-probe/debian/` -- Offline Debian/APK build overlay for `prototype/android-probe/` using `openjdk-21-jdk-headless` and `.orig-sdk.tar.xz` (`aapt2`, `javac`, `d8`, `zipalign`, `apksigner`).
- `tools/obs_packaging.py` -- Builds deterministic `3.0 (quilt)` source bundles (`build-libei`, `build-kdeconnect`, `build-android-probe`) with `manifest.json` SHA-256 verification.

## Controller Layout (`~/runners/kdeconnect-plugins`)

- Runs host Python and `osc` directly via `tools/run-controller.sh` and `tools/cron-cycle.sh` without a controller Docker wrapper.
- Enforces a disk-backed `TMPDIR` (`.state/tmp`, rejecting `tmpfs`) and readable `OSC_CONFIG`.
- Controller commands: `verify-env`, `ensure-projects`, `upload-bundle`, `inspect-build`, `status`, and `cycle`.
- Uses managed cron slot `# BEGIN KDECONNECT OBS` (`47 * * * *`) via `tools/install_cron.py`, keeping unrelated cron jobs untouched.

## Promotion Gate Invariants

`tools/obs_controller.py::evaluate_promotion_readiness` blocks promotion from `home:dwolfF:kdeconnect:staging` to `home:dwolfF:kdeconnect` unless:

1. Staging build state for the exact source revision is `succeeded`.
2. `ctest` actually executed > 0 test cases (`ctest_ran > 0`) and reported 0 failures (`ctest_failed == 0`).
3. `lintian` reported 0 errors (`lintian_errors == 0`).
4. Isolated `.deb` inspection and install/remove verification passed.
