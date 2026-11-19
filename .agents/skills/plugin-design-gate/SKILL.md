---
# SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com>
# SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL
name: plugin-design-gate
description: >-
  Execute Gate 0 plugin architecture design: inspect upstream kdeconnect-kde and
  kdeconnect-android sources via .state/local.env, register the plugin in
  plugins.toml, map reusable plugin interfaces, and finalize
  docs/plugins/<plugin_id>/spec.md before implementation.
---

# Gate 0: Human-Led Design and Upstream Inspection

Use this skill before writing plugin code in an upstream checkout.

## Prerequisites

1. Source `.state/local.env` (`set -a; source .state/local.env; set +a`) and confirm `KDECONNECT_DESKTOP_SRC`, `KDECONNECT_ANDROID_SRC`, and disk-backed `TMPDIR` exist.
2. Record exact upstream base commits (`git -C "$KDECONNECT_DESKTOP_SRC" rev-parse HEAD` and `git -C "$KDECONNECT_ANDROID_SRC" rev-parse HEAD`) and check `AGENTS.md` / `CONTRIBUTING.md` inside those repositories.

## Inspection Steps

1. Inspect existing plugin registration, CMake macros (`KDEConnectMacros.cmake`), plugin metadata schemas (`plugins/kdeconnect.schema.json`), `KdeConnectPlugin` APIs, D-Bus interfaces, and test targets (`tests/CMakeLists.txt`).
2. Inspect reusable capabilities before designing new protocol packets:
   - Pointer/touch/system actions (`plugins/mousereceiver`, `KdeConnectAccessibilityService`)
   - Keyboard and IME input (`plugins/remotekeyboard`, `plugins/remotekeyboardime`)
   - Clipboard and file sharing (`plugins/clipboard`, `plugins/share`)
   - Notifications and actions (`plugins/notifications`)
   - Screen streaming and viewer helper (`plugins/screenstream`)
3. Map required runtime permissions, OS/Wayland/Android API gates, and protocol security boundaries (untrusted `NetworkPacket` validation, consent, teardown).
4. Determine whether the scope warrants prior upstream discussion on the KDE Connect mailing list or Invent before coding.
5. Check touchpoints with active `work/<plugin_id>` branches in `plugins.toml` so clean per-feature cherry-pick hunks do not conflict during stack synthesis.

## Exit Criteria

1. Complete `docs/plugins/<plugin_id>/spec.md` (derived from `docs/templates/plugin-spec-template.md`) with zero unresolved `[to be determined]` blockers for implementation.
2. Register the plugin entry in `plugins.toml` (`status = "planned"` initially, switching to `"active"` when implementation begins) and pass `python3 tools/plugin_registry.py validate`.
3. Confirm the human maintainer understands and approves the architecture, protocol schema, and authorized Android build route.
4. Verify no forbidden local paths or private identifiers entered `docs/plugins/<plugin_id>/spec.md` using `python3 tools/check_public_tree.py --worktree`.
