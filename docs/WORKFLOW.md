<!-- SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com> -->
<!-- SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL -->

# End-to-End Plugin Engineering Workflow

Every plugin developed under `lmdeuser/kdeconnect-plugins` moves sequentially through six verification gates before upstream submission to KDE Invent.

## Branch and Repository Topology

| Contour | Location | Purpose | Allowed Contents |
| --- | --- | --- | --- |
| Public Hub | `lmdeuser/kdeconnect-plugins` (`main`) | Engineering harness, specs, probes, OBS overlay, Python gate checks | `AGENTS.md`, `.agents/skills/`, `docs/`, `prototype/`, `obs/`, `tools/`, `tests/`, `LICENSES/` |
| Desktop Upstream | External `kdeconnect-kde` checkout (`work/<plugin_id>`) | Clean upstream C++/Qt/QML plugin branch for `invent.kde.org` | Plugin code, CMake integration, Qt tests, REUSE headers; zero packaging/harness files |
| Android Upstream | External `kdeconnect-android` checkout (`work/<plugin_id>`) | Clean upstream Kotlin/Android plugin branch for `invent.kde.org` | Android plugin code, manifest/permissions, unit tests, REUSE headers |
| OBS Staging | `home:dwolfF:kdeconnect:staging` (`xUbuntu_26.04`, `x86_64`) | Remote compilation, `ctest`, `lintian`, `sourceaccess=disable`, publish disabled | Upstream feature revision + Debian/OBS overlay |
| OBS Stable | `home:dwolfF:kdeconnect` (`xUbuntu_26.04`, `x86_64`) | Public APT distribution of verified packages | Promoted staging binaries only |

## Gate Sequence

1. **Gate 0 -- Design and Upstream Inspection** (`.agents/skills/plugin-design-gate/SKILL.md`):
   - Source `.state/local.env` and verify external upstream revisions.
   - Map reusable plugins (`mousereceiver`, `remotekeyboard`, `remotekeyboardime`, `clipboard`, `share`, `mousepad`).
   - Finalize `docs/plugins/<plugin_id>/spec.md`, threat/consent model, and authorized remote Android build route.
2. **Gate 1 -- Static and Harness Verification**:
   - Execute with disk-backed `TMPDIR`:
     ```bash
     python3 tools/check_public_tree.py --worktree
     python3 tools/check_kde_compliance.py
     python3 -m unittest discover -s tests -v
     ```
   - All Python checks must pass before tagging a source revision for OBS.
3. **Gate 2 -- Remote OBS Staging Build and Tests** (`.agents/skills/obs-staging-pipeline/SKILL.md`):
   - Sync the public runner tree to `~/runners/kdeconnect-plugins` and confirm `tools/run-controller.sh verify-env` passes.
   - Build exact `work/<plugin_id>` revision + packaging overlay in `home:dwolfF:kdeconnect:staging`.
   - Confirm staging status is `succeeded`, `ctest` executed > 0 tests with 0 failures, and `lintian` reported 0 errors.
4. **Gate 3 -- Artifact Inspection and Promotion Gate**:
   - Inspect staged `.deb` file paths, plugin install locations, and runtime dependencies.
   - Run isolated container install/uninstall/reinstall verification if authorized.
   - Evaluate `evaluate_promotion_readiness(...)` before any promotion to `home:dwolfF:kdeconnect`.
5. **Gate 4 -- Authorized Live Hardware Matrix** (`.agents/skills/live-acceptance-matrix/SKILL.md`):
   - Test on real Ubuntu 26.04 Wayland and Android 11+ hardware over Wi-Fi with explicit user authorization.
   - Record device model, OS/API versions, negative cases (denied/revoked consent, protected content, disconnects), and regression checks on standalone plugins.
6. **Gate 5 -- Upstream KDE Invent Readiness** (`.agents/skills/upstream-invent-handoff/SKILL.md`):
   - Rebase `work/<plugin_id>` onto `origin/master` and pass `python3 tools/check_kde_compliance.py --upstream-repo <path> --base origin/master`.
   - Emit untracked `.state/dossiers/<plugin_id>.md` with factual verification records and human comprehension checkpoints.
   - Stop before `git push` or MR submission; the human maintainer authors the MR description and submits to `invent.kde.org`.

## Mandatory Handoff and Status Reporting Format

Separate verified evidence across six explicit sections in every milestone or final report:

1. Source and packaging changes (repositories, branches, exact commit SHAs).
2. Static checks and Python unit suites run, with exact pass/fail counts.
3. OBS staging source/build status, `ctest` count, `lintian`, and `.deb` inspection results.
4. Remote Android build route status and artifact verification (if applicable).
5. Authorized live desktop/Android behavior verified (with device/OS matrix).
6. Remaining blockers, unverified behaviors, and next human decision required.
