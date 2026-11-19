<!-- SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com> -->
<!-- SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL -->

# KDE Connect Plugin Engineering Harness

This repository (`lmdeuser/kdeconnect-plugins`) is the public engineering harness, protocol specification hub, verification suite, and Ubuntu 26.04 OBS packaging workspace for KDE Connect plugins targeting upstream submission to KDE Invent.

## 1. Repository Scope, Identity, and Confidentiality

- Keep all tracked files, code, scripts, specs, comments, and commit messages in technical English.
- Approved public maintainer and SPDX copyright identity: `Dmitriy Volkov <dennshield@duck.com>`.
- Configure repository-local Git identity (`user.name` and `user.email`) to match the approved maintainer identity before committing.
- Never commit secrets, `.oscrc`, private IPv4 addresses, local hostnames, local usernames, local filesystem paths, `.deb`/`.apk`/archive artifacts, or `.state/` files.
- Keep machine-specific paths and environment values strictly in untracked `.state/local.env` (`chmod 600`; see `.state.env.example`).
- Every tracked file must comply with REUSE 3.0 using license texts stored in `LICENSES/`.

## 2. Two-Contour Repository Architecture

1. **Public Harness and Packaging Hub (`lmdeuser/kdeconnect-plugins`)**:
   - Owns `AGENTS.md`, `plugins.toml`, `.agents/skills/`, `docs/`, `prototype/`, `obs/`, `tools/`, and `tests/`.
   - `plugins.toml` defines every registered plugin/enhancement (`id`, `kind`, `status`, `desktop_branch`, `android_branch`, `spec`, and `.deb` verification metadata).
2. **External Upstream KDE Connect Checkouts (configured in `.state/local.env`)**:
   - Desktop upstream: `https://invent.kde.org/network/kdeconnect-kde.git`
   - Android upstream: `https://invent.kde.org/network/kdeconnect-android.git`
   - GitHub KDE repositories are read-only mirrors; official Merge Requests are accepted only on KDE Invent (`invent.kde.org`).
   - Implement each upstream plugin or enhancement on its own clean `work/<plugin_id>` branch inside the external checkouts. Never mutate individual `work/<plugin_id>` branches during integration: OBS synthesizes all active desktop `work/<plugin_id>` branches onto `obs/kdeconnect-staging` (`tools/obs_controller.py`), and Android APK builds synthesize active `work/<plugin_id>` branches onto an ephemeral `work/continuity-stack` branch (`tools/synthesize_android_stack.py`). Keep `debian/`, `obs/`, `.agents/`, and `prototype/` completely out of `work/<plugin_id>` and `work/continuity-stack`.

## 3. Build, Runner, and CI Boundaries

- **No local desktop C++ compilation or runner builds**: Do not install desktop KDE/Qt C++ build toolchains or run local `cmake`, `ninja`, `make`, or `ctest` builds on workstations or OBS runner hosts.
- **Bounded local Android Studio / Gradle verification**: Android verification (`./gradlew` and Android Studio) may run locally on developer workstations under strict resource discipline: reuse the workstation's existing `GRADLE_USER_HOME`, `ANDROID_SDK_ROOT`, bundled Android Studio JBR, and a disk-backed `TMPDIR` (never `/tmp` or duplicate offline caches), without altering global user Gradle config files, with `--no-daemon --max-workers=2`, bounded JVM heap (`-Xmx2048m -XX:MaxMetaspaceSize=640m -Dkotlin.compiler.execution.strategy=in-process`), and lowered CPU/I/O priority (`nice -n 10 ionice -c 2 -n 7`).
- **No GitHub Actions**: Never add `.github/workflows/` or use GitHub-hosted/self-hosted GitHub runners (paid).
- **Explicit disk-backed `TMPDIR`**: Export a disk-backed `TMPDIR` (never `/tmp` or `tmpfs`) before running tools, tests, or controllers.
- **Self-hosted OBS controller (`~/runners/kdeconnect-plugins`)**:
  - Runs host Python and `osc` directly via `tools/run-controller.sh` and `tools/cron-cycle.sh` without a controller Docker wrapper.
  - Docker is reserved strictly for isolated `.deb` verification checks, never as a controller wrapper or compiler.
  - Never run two OBS publishers concurrently or modify unrelated cron jobs or OBS projects.
- **OBS two-contour targets (`xUbuntu_26.04`, `x86_64`)**:
  - **Staging (`home:dwolfF:kdeconnect:staging`)**: `sourceaccess=disable`, publishing disabled; performs CMake compilation, `ctest`, and `lintian`.
  - **Stable (`home:dwolfF:kdeconnect`)**: receives binary promotion only after staging build `succeeded`, `ctest` executes > 0 tests with 0 failures, `lintian` reports 0 errors, and isolated `.deb` checks pass.
- **Android verification targets**: Desktop OBS builds never verify `kdeconnect-android`. Verify Android changes via local resource-bounded `./gradlew assembleDebug lintDebug testDebugUnitTest` + `apksigner verify` against the target SDK platform (and Android Studio inspections/IDE sync), plus optional diagnostic `kdeconnect-android-probe` builds in OBS staging.

## 4. Construction and Security Standards

- Follow the current upstream KDE Connect architecture, `KdeConnectPlugin` contracts, CMake macros, and metadata schemas rather than assuming paths or APIs.
- Reuse existing KDE Connect plugins (`mousereceiver`, `remotekeyboard`, `remotekeyboardime`, `clipboard`, `share`, `mousepad`) instead of building duplicate input, clipboard, or file-transfer stacks.
- Treat paired and unpaired peer `NetworkPacket` payloads and pointer inputs as untrusted boundary data. Validate packet types, fields, sizes, permissions, and session consent at boundaries; handle legacy or disconnected peers safely.
- Apply TDD for Python verification tools, runner scripts, and packaging code. Debug failures from reproducible evidence and falsifiable hypotheses without arbitrary sleeps or symptom-masking fallbacks.

## 5. Mandatory Six-Gate Lifecycle

| Gate | Objective | Primary Skill / Tooling |
| --- | --- | --- |
| **Gate 0** | Human-led architecture, `plugins.toml` registration, upstream API inspection, and completed `spec.md` | [plugin-design-gate](.agents/skills/plugin-design-gate/SKILL.md) |
| **Gate 1** | Public tree hygiene, registry schema, REUSE 3.0 compliance, and Python controller/stack tests | `tools/plugin_registry.py`, `tools/check_public_tree.py`, `tools/check_kde_compliance.py`, `tests/` |
| **Gate 2** | Multi-plugin `work/<plugin_id>` stack synthesis, OBS overlay, remote build, `ctest`, and `lintian` | [obs-staging-pipeline](.agents/skills/obs-staging-pipeline/SKILL.md) |
| **Gate 3** | Registry-driven `.deb` inspection, isolated install/uninstall check, promotion readiness | [obs-staging-pipeline](.agents/skills/obs-staging-pipeline/SKILL.md), `tools/verify-deb.sh`, `tools/obs_controller.py` |
| **Gate 4** | Authorized live Ubuntu 26.04 Wayland + Android 11+ device verification matrix | [live-acceptance-matrix](.agents/skills/live-acceptance-matrix/SKILL.md) |
| **Gate 5** | Rebased `work/<plugin_id>`, per-branch `check_kde_compliance.py --ref`, human dossier in `.state/dossiers/` | [upstream-invent-handoff](.agents/skills/upstream-invent-handoff/SKILL.md) |

Detailed gate criteria and reporting templates live in [docs/WORKFLOW.md](docs/WORKFLOW.md).

## 6. Upstream KDE Connect AI Contribution Policy

Upstream `kdeconnect-kde` (`AGENTS.md` and `CONTRIBUTING.md`) enforces strict human ownership rules. Agents must obey every rule below on upstream branches:

- Only execute designs planned and understood by the human maintainer. Pause for large or pattern-introducing changes and verify maintainer approval for mailing list / Invent pre-discussion.
- Use ASCII-only punctuation in upstream source code and comments (use `-`, `->`, `x`, `...` instead of Unicode dashes, arrows, multiplication signs, or ellipses).
- Keep comments concise; avoid redundant narration and keep patches single-purpose.
- Never add `Co-authored-by:` AI trailers; use `Assisted-by: <assistant name>` only when the user explicitly asks the agent to create a commit.
- Never write Merge Request descriptions or reviewer responses for upstream submission. Produce only an untracked factual technical dossier in `.state/dossiers/<plugin_id>.md` plus human comprehension verification notes.
- Never run `git push` or create a Merge Request against `invent.kde.org` on the user's behalf.
