<!-- SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com> -->
<!-- SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL -->

# KDE Connect Plugins Engineering Harness

Public engineering harness, protocol specifications, verification tools, and Ubuntu 26.04 OBS controller scaffolding for developing KDE Connect plugins intended for upstream submission to [KDE Invent](https://invent.kde.org/network/kdeconnect-kde).

## Purpose and Contour Separation

- **This repository (`lmdeuser/kdeconnect-plugins`)** holds the agent engineering constitution (`AGENTS.md`), progressive-disclosure gate skills (`.agents/skills/`), plugin design specifications (`docs/`), diagnostic probes (`prototype/`), OBS packaging automation (`obs/`), and deterministic verification checks (`tools/`, `tests/`).
- **Upstream KDE Connect repositories (`kdeconnect-kde`, `kdeconnect-android`)** are kept in external checkouts on clean `work/<plugin_id>` branches. Official Merge Requests go exclusively to KDE Invent; packaging overlays and agent scaffolding never enter upstream branches.
- **Build and test execution:** Desktop compilation, `ctest`, and `.deb` packaging run remotely on Open Build Service (`home:dwolfF:kdeconnect:staging` -> `home:dwolfF:kdeconnect`, `xUbuntu_26.04`, `x86_64`), orchestrated by a self-hosted controller (`~/runners/kdeconnect-plugins`) without local compilation or GitHub Actions.

## Repository Layout

- `AGENTS.md` -- Engineering constitution, six-gate workflow, and upstream KDE Invent contribution rules.
- `.agents/skills/` -- Gate runbooks (`plugin-design-gate`, `obs-staging-pipeline`, `live-acceptance-matrix`, `upstream-invent-handoff`).
- `docs/WORKFLOW.md` -- Full gate criteria and verification reporting format.
- `docs/templates/plugin-spec-template.md` -- Standard Code-Complete plugin specification template.
- `docs/plugins/screen-stream/spec.md` -- Feature specification for Android screen and eligible playback audio streaming.
- `prototype/` -- Diagnostic Android capture probe (`MediaProjection` + `AudioPlaybackCapture`) and upstream boundary notes.
- `obs/` -- Two-contour OBS staging/stable architecture notes and future packaging overlays.
- `tools/` and `tests/` -- Public-tree leak scanner, REUSE 3.0 / KDE Invent upstream branch validator, OBS controller helpers, and Python `unittest` suite.
- `LICENSES/` -- Verbatim REUSE 3.0 license texts matching KDE Connect.

## Local Verification Commands

Configure `.state/local.env` from `.state.env.example` (`chmod 600 .state/local.env`), export a disk-backed `TMPDIR`, and run:

```bash
python3 tools/check_public_tree.py --worktree
python3 tools/check_kde_compliance.py
python3 -m unittest discover -s tests -v
```

Before proposing an external `work/<plugin_id>` branch for human MR review on KDE Invent:

```bash
python3 tools/check_kde_compliance.py --upstream-repo /path/to/kdeconnect-kde --base origin/master
```

## Licensing

Licenses follow KDE Connect REUSE 3.0 conventions:

- Documentation, skills, Python tooling, tests, shell scripts, and Android probe Java/XML sources: `GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL`
- Build scripts (`*.gradle.kts`, `CMakeLists.txt`, `*.cmake`): `BSD-2-Clause`
- Configuration and ignore manifests (`.gitignore`, `.editorconfig`, `REUSE.toml`, `.state.env.example`): `CC0-1.0`

License texts are located in `LICENSES/`.
