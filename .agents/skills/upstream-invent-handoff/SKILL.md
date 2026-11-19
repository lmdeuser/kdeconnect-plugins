---
# SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com>
# SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL
name: upstream-invent-handoff
description: >-
  Execute Gate 5 upstream KDE Invent preparation on work/<plugin_id>: verify
  ASCII-only code, REUSE 3.0 headers, clean diff without packaging overlays,
  commit hygiene, and produce an untracked factual technical dossier in
  .state/dossiers/ without writing MR prose or pushing to invent.kde.org.
---

# Gate 5: Upstream KDE Invent Readiness and Human Handoff

Use this skill when preparing verified upstream `work/<plugin_id>` branches for human review and submission on `invent.kde.org`.

## Mandatory Upstream Compliance

1. Verify each individual `work/<plugin_id>` branch registered in `plugins.toml` is rebased cleanly onto the verified `origin/master` of `kdeconnect-kde` (or `kdeconnect-android`) with coherent, single-purpose commits (never submit `work/continuity-stack` or `obs/kdeconnect-staging`).
2. Run the upstream branch checker per branch from the harness repository:
   ```bash
   python3 tools/check_kde_compliance.py \
     --upstream-repo "$KDECONNECT_DESKTOP_SRC" \
     --base origin/master \
     --ref "refs/heads/work/<plugin_id>"
   ```
3. Confirm each branch check verifies:
   - Zero non-ASCII characters in source/CMake/Kotlin/QML files
   - Valid REUSE 3.0 SPDX headers on all added files
   - Zero `debian/`, `obs/`, `.agents/`, `prototype/`, or `.state/` files in the upstream diff
   - Zero AI `Co-authored-by:` commit trailers (`Assisted-by:` is allowed only when the human asked the agent to commit)

## Prohibited Agent Actions

- **Do NOT write Merge Request descriptions or reviewer responses.** Upstream maintainers immediately close AI-written MR prose.
- **Do NOT run `git push` or create an MR on `invent.kde.org`.** Automated MR submission risks a project ban.

## Deliverable: Untracked Technical Dossier

Write a concise, strictly factual engineering dossier in untracked `.state/dossiers/<plugin_id>.md` containing only verified facts for the human author to review and write their own MR:

1. Upstream base branch, commit SHAs, and clean diffstat
2. Protocol packet types, capability names, and backward-compatibility summary
3. Security boundaries, permissions, and failure modes verified
4. Exact OBS staging build revision, `ctest` count, `lintian` result, and `.deb` verification summary
5. Live hardware verification matrix results and remaining unverified devices/limitations
6. Human comprehension checklist (key functions, lifetime boundaries, and design trade-offs the author must be able to explain to reviewers)
