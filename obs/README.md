<!-- SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com> -->
<!-- SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL -->

# Open Build Service (OBS) Packaging Architecture

Desktop KDE Connect plugins and test builds targeting Ubuntu 26.04 (`xUbuntu_26.04`, `x86_64`) use a strict two-contour OBS pipeline without local compilation or GitHub Actions.

## OBS Projects

| Role | OBS Project | Configuration Invariants |
| --- | --- | --- |
| Staging | `home:dwolfF:kdeconnect:staging` | `sourceaccess=disable`; repository publishing disabled; compiles CMake/Qt6/KF6 sources, runs `ctest`, and runs `lintian`. |
| Stable | `home:dwolfF:kdeconnect` | Direct builds disabled; receives binary promotion only after staging builds, tests, and `.deb` checks pass. |

## Controller Layout (`~/runners/kdeconnect-plugins`)

- Runs host Python and `osc` directly via `tools/run-controller.sh` and `tools/cron-cycle.sh` without a controller Docker wrapper.
- Enforces a disk-backed `TMPDIR` (`.state/tmp`, rejecting `tmpfs`) and readable `OSC_CONFIG`.
- Keeps `.state/`, `.oscrc`, source caches, and logs outside Git.
- Uses managed cron slot `# BEGIN KDECONNECT OBS` (`47 * * * *`) via `tools/install_cron.py`, keeping unrelated cron jobs untouched.

## Promotion Gate Invariants

`tools/obs_controller.py::evaluate_promotion_readiness` blocks promotion from `home:dwolfF:kdeconnect:staging` to `home:dwolfF:kdeconnect` unless:

1. Staging build state for the exact source revision is `succeeded`.
2. `ctest` actually executed > 0 test cases (`ctest_ran > 0`) and reported 0 failures (`ctest_failed == 0`).
3. `lintian` reported 0 errors (`lintian_errors == 0`).
4. Isolated `.deb` inspection and install/remove verification passed.
