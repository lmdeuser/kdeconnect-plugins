<!-- SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com> -->
<!-- SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL -->

# End-to-End Plugin Engineering Workflow

Every plugin developed under `lmdeuser/kdeconnect-plugins` moves sequentially through six verification gates before upstream submission to KDE Invent.

## Branch and Repository Topology

| Contour | Location | Purpose | Allowed Contents |
| --- | --- | --- | --- |
| Public Hub | `lmdeuser/kdeconnect-plugins` (`main`) | Engineering harness, `plugins.toml` registry, specs, patches, lockfile, OBS overlay, Python gate checks | `AGENTS.md`, `README.md`, `plugins.toml`, `stack.lock.json`, `patches/`, `.agents/skills/`, `docs/`, `prototype/`, `obs/`, `tools/`, `tests/`, `LICENSES/` |
| Desktop Upstream | External `kdeconnect-kde` checkout (`work/<plugin_id>`) | Independent upstream C++/Qt/QML branches for `invent.kde.org` | Plugin code, CMake integration, Qt tests, REUSE headers; zero packaging/harness files |
| Android Upstream | External `kdeconnect-android` checkout (`work/<plugin_id>`) | Independent upstream Kotlin/Android branches; ephemeral `work/continuity-stack` for APK testing | Android plugin code, manifest/permissions, unit tests, REUSE headers |
| OBS Staging | `home:dwolfF:kdeconnect:staging` (`xUbuntu_26.04`, `x86_64`) | Synthesized `obs/kdeconnect-staging` build, `ctest`, `lintian`, `sourceaccess=disable`, publish disabled | Rebased active `work/<plugin_id>` stack + Debian/OBS overlay |
| OBS Stable | `home:dwolfF:kdeconnect` (`xUbuntu_26.04`, `x86_64`) | Public APT distribution of verified packages | Promoted staging binaries only |

## Gate Sequence

Apply `/code-complete` construction, scientific debugging, and verification discipline across all six gates (with repository rules and upstream KDE Connect conventions taking precedence).

1. **Gate 0 -- Design and Upstream Inspection** (`.agents/skills/plugin-design-gate/SKILL.md`):
   - Source `.state/local.env` and verify external upstream revisions.
   - Map reusable plugins (`mousereceiver`, `remotekeyboard`, `remotekeyboardime`, `clipboard`, `share`, `mousepad`, `notifications`).
   - Register the plugin in `plugins.toml` (`status = "planned"` during Gate 0, promoted to `"active"` when ready for integration), finalize `docs/plugins/<plugin_id>/spec.md`, threat/consent model, and authorized Android build route.
2. **Gate 1 -- Static and Harness Verification (and Public GitHub Sync)**:
   - Whenever upstream `work/<plugin_id>` branches or specs change, export deterministic patches and update `stack.lock.json`, keeping `README.md` and `docs/plugins/<plugin_id>/spec.md` synchronized:
     ```bash
     python3 tools/export_stack_patches.py
     python3 tools/reproduce_stack.py verify-lock
     ```
   - Execute with disk-backed `TMPDIR`:
     ```bash
     python3 tools/plugin_registry.py validate
     python3 tools/check_public_tree.py --worktree
     python3 tools/check_kde_compliance.py
     python3 -m unittest discover -s tests -v
     ```
   - Dry-run multi-branch stack synthesis (`tools/synthesize_android_stack.py --dry-run` / `--desktop --dry-run`) before tagging or syncing revisions.
   - Commit the verified `lmdeuser/kdeconnect-plugins` changes (`README.md`, `docs/`, `patches/`, `stack.lock.json`, tooling) with author `Dmitriy Volkov <dennshield@duck.com>` and push to `origin/main` on GitHub (`https://github.com/lmdeuser/kdeconnect-plugins.git`), while keeping `invent.kde.org` strictly manual per Gate 5.
3. **Gate 2 -- Remote OBS Staging Build and Tests** (`.agents/skills/obs-staging-pipeline/SKILL.md`):
   - Sync active desktop branches from `plugins.toml` via `tools/sync-runner.sh` to `~/runners/kdeconnect-plugins` and confirm `tools/run-controller.sh verify-env` passes.
   - Synthesize active `work/<plugin_id>` branches cleanly onto `obs/kdeconnect-staging`, apply the packaging overlay (`obs/` and `debian/`), and upload to `home:dwolfF:kdeconnect:staging`.
   - Confirm staging status is `succeeded`, `ctest` executed > 0 tests with 0 failures, and `lintian` reported 0 errors.
4. **Gate 3 -- Artifact Inspection and Promotion Gate**:
   - Inspect staged `.deb` file paths and ELF dependencies dynamically generated from `plugins.toml` (`tools/plugin_registry.py deb-checks` inside `tools/verify-deb.sh`).
   - Run isolated Rootless Docker install/uninstall/reinstall verification without network access.
   - Evaluate `evaluate_package_promotion_readiness(...)` / `evaluate_promotion_readiness(...)` before any promotion to `home:dwolfF:kdeconnect`.
5. **Gate 4 -- Virtual-First Acceptance, Post-Release Bug Hunt, and Live Hardware Matrix** (`.agents/skills/live-acceptance-matrix/SKILL.md`):
   - **Gate 4a -- Virtual Environment Acceptance and Post-Release Bug Hunt (Mandatory Before Hardware)**:
     - Synthesize active Android branches onto `work/continuity-stack` via `tools/synthesize_android_stack.py` without mutating individual `work/<plugin_id>` branches, and run resource-bounded Gradle + `apksigner verify`.
     - Install the exact released OBS `.deb` inside a snapshotted Ubuntu 26.04 LTS libvirt/QEMU VM and the synthesized `.apk` onto an Android Virtual Device (AVD, API 35+), pair over LAN/IP, and execute all virtual-capable acceptance scenarios.
     - Run a mandatory post-release bug-hunting pass (`bug-analysis-review` + `/code-complete` verification): static diff review for null/none dereferences, resource/handle leaks, concurrency/reconnect races, and boundary/malformed inputs, plus runtime inspection of VM `journalctl`/stderr/PipeWire and AVD `logcat` across negative paths. Any confirmed defect must be fixed at root cause with a regression test, rebuilt through OBS/Gradle, and re-verified in VM + AVD before authorizing physical hardware.
   - **Gate 4b -- Authorized Real-Hardware Verification**:
     - Run only after Gate 4a passes with zero open release-blocking defects and with explicit user authorization.
     - Install the verified Android APK strictly to `User 0`, verify hardware-only paths (physical Bluetooth A2DP/HFP audio handoff, OEM camera/`MediaCodec`/`MediaProjection`/`FLAG_SECURE` quirks), and record device model, OS/API versions, negative cases, and regression checks on standalone plugins.
6. **Gate 5 -- Upstream KDE Invent Readiness** (`.agents/skills/upstream-invent-handoff/SKILL.md`):
   - Rebase each individual `work/<plugin_id>` branch onto `origin/master` and pass `python3 tools/check_kde_compliance.py --upstream-repo <path> --base origin/master --ref work/<plugin_id>`.
   - Emit untracked `.state/dossiers/<plugin_id>.md` with factual verification records and human comprehension checkpoints.
   - Stop before `git push` or MR submission; the human maintainer authors the MR description and submits to `invent.kde.org`.

## Mandatory Handoff and Status Reporting Format

Separate verified evidence across six explicit sections in every milestone or final report:

1. Source and packaging changes (repositories, branches, exact commit SHAs).
2. Static checks and Python unit suites run, with exact pass/fail counts.
3. OBS staging source/build status, `ctest` count, `lintian`, and `.deb` inspection results.
4. Remote Android build route status and artifact verification (if applicable).
5. Virtual-environment (VM + AVD) acceptance and post-release bug-hunt findings, plus authorized real-hardware verification (with device/OS matrix) when executed.
6. Remaining blockers, unverified behaviors, and next human decision required.
