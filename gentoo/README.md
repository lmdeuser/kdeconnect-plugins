<!-- SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com> -->
<!-- SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL -->

# Gentoo Linux (Portage EAPI 8) Packaging Guide

This directory provides the Portage repository metadata and profile layout for the `kdeconnect-continuity` overlay targeting Gentoo Linux (systemd or OpenRC, KDE Plasma 6, Wayland-first).

## Generate the complete overlay from `stack.lock.json`

Use a disk-backed `TMPDIR` (never `tmpfs`) and verify the lockfile before emitting the overlay:

```bash
export TMPDIR="${TMPDIR:-$PWD/.state/tmp}"
mkdir -p "$TMPDIR"
python3 tools/reproduce_stack.py verify-lock
```

Emit a standalone `kdeconnect-continuity` Portage overlay alongside a reproduced desktop checkout:

```bash
python3 tools/reproduce_stack.py checkout \
  --target desktop \
  --out ../repro/kdeconnect-kde \
  --emit-gentoo-overlay ../repro/gentoo-overlay
```

Or generate only the Portage overlay directory directly without cloning the upstream Git tree:

```bash
python3 tools/gentoo_packaging.py \
  --output-dir ../repro/gentoo-overlay \
  --ebuild-version 27.03.70_p1
```

If you already have the pinned upstream tarball `kdeconnect-kde-<base_sha>.tar.bz2`, pass `--distfile <path>` (or `--preserve-manifest-from <existing-Manifest>`) to populate a GLEP-74 `Manifest` (`DIST ... BLAKE2B ... SHA512 ...`). Otherwise run `pkgdev manifest` or `ebuild ... manifest` on Gentoo after placing the overlay.

## Generated overlay layout

```text
<output_dir>/
  metadata/layout.conf
  profiles/repo_name
  profiles/package.mask/gentoo-kdeconnect
  kde-misc/kdeconnect/
    kdeconnect-27.03.70_p1.ebuild
    files/patches/<plugin_id>/<NNN>-*.patch
    Manifest                  # when --distfile or matching --preserve-manifest-from is supplied
```

## Portage configuration

1. Register the generated overlay in `/etc/portage/repos.conf/kdeconnect-continuity.conf`:

   ```ini
   [kdeconnect-continuity]
   location = /var/db/repos/kdeconnect-continuity
   priority = 100
   auto-sync = no
   ```

2. Mask the unpatched `::gentoo` package if needed (see `profiles/package.mask/gentoo-kdeconnect`):

   ```bash
   echo "kde-misc/kdeconnect::gentoo" | sudo tee /etc/portage/package.mask/kdeconnect-gentoo
   ```

3. Configure USE flags in `/etc/portage/package.use/kdeconnect`:

   - `pulseaudio` is enabled by default (`+pulseaudio` and `REQUIRED_USE="pulseaudio"` because `bt_audio_switch` links `KF6::PulseAudioQt`).
   - Enable `bluetooth` for `bt_audio_switch` and Screen Stream BlueZ A2DP/HFP arbitration.
   - Optionally enable `telephony` (`kde-frameworks/modemmanager-qt`) or `X` (`x11-libs/libfakekey` / `shareinputdevices`).

   ```text
   kde-misc/kdeconnect bluetooth pulseaudio
   ```

4. Install and verify runtime plugins, binaries, QML modules, and GStreamer elements:

   ```bash
   sudo emerge -av kde-misc/kdeconnect
   bash -euo pipefail -c "$(python3 tools/plugin_registry.py gentoo-checks)"
   ```
