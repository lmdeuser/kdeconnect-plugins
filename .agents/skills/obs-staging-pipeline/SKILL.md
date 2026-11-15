---
# SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com>
# SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL
name: obs-staging-pipeline
description: >-
  Execute Gates 2 and 3 for Ubuntu 26.04 x86_64: prepare OBS packaging overlays,
  sync and run the ~/runners/kdeconnect-plugins controller against
  home:dwolfF:kdeconnect:staging, verify ctest and lintian logs, inspect .deb
  artifacts, and enforce promotion gates to home:dwolfF:kdeconnect.
---

# Gates 2 and 3: OBS Staging Build, Tests, and Promotion

Use this skill for all desktop compilation, build-time tests (`ctest`), Debian packaging, and OBS publication checks.

## Strict Boundaries

1. Never compile C++/Qt/CMake projects or run `ctest` locally on the workstation or controller host.
2. Keep Debian/OBS overlays strictly outside `work/<plugin_id>` upstream branches.
3. Staging project `home:dwolfF:kdeconnect:staging` must retain `sourceaccess=disable` and disabled publishing. Never trigger direct builds in stable `home:dwolfF:kdeconnect`.
4. Android builds are separate deliverables; verify an authorized remote Android build route explicitly rather than inferring Android validity from desktop OBS builds.

## Gate 2: Staging Source Upload and Build Verification

1. Verify `tools/run-controller.sh verify-env` succeeds on `~/runners/kdeconnect-plugins` (disk-backed `TMPDIR`, `osc` present, readable `OSC_CONFIG`).
2. Prepare the OBS source archive from an explicit commit revision on `work/<plugin_id>` combined with the separate packaging overlay (`obs/` and `debian/`).
3. Upload to `home:dwolfF:kdeconnect:staging` using `osc` and track exact OBS source revision and target `xUbuntu_26.04` / `x86_64`.
4. Inspect the full staging build log and record exact evidence:
   - Compilation outcome (`succeeded`)
   - Number of `ctest` tests executed (`ctest_ran > 0`) and failed (`ctest_failed == 0`) with required headless/D-Bus environment
   - `lintian` diagnostic output (`lintian_errors == 0`)

## Gate 3: Artifact Inspection and Promotion Gate

1. Fetch and inspect the exact `.deb` artifacts produced by staging: verify installed plugin `.so` paths, metadata/QML/desktop assets, and runtime dependencies.
2. Run isolated container install, uninstall, and reinstall checks where authorized, keeping package inspection separate from live desktop Wayland tests.
3. Evaluate promotion readiness via `tools/obs_controller.py` (`evaluate_promotion_readiness`): block promotion to `home:dwolfF:kdeconnect` unless staging `succeeded`, `ctest_ran > 0`, `ctest_failed == 0`, `lintian_errors == 0`, and `.deb` inspection passed.
