<!-- SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com> -->
<!-- SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL -->

# KDE Connect Screen Stream

This project adds live Android screen mirroring, app audio playback, and mouse/keyboard control to [KDE Connect](https://kdeconnect.kde.org/) on Ubuntu 26.04 (Wayland). Once your phone and PC are paired over Wi-Fi, you can open a desktop window showing the phone display without plugging in a USB cable, enabling ADB, or installing a third-party screen server.

This repository (`lmdeuser/kdeconnect-plugins`) does not fork or copy the desktop or Android codebases. It holds the [screenstream protocol spec](docs/plugins/screenstream/spec.md), Ubuntu 26.04 packaging overlays under [obs/](obs/), the [verification test suite](tests/), and the automated scripts in [tools/](tools/) that track upstream commits and build `.deb` packages. The actual C++/Qt desktop plugin and Kotlin Android plugin live on clean `work/screenstream` branches ready for review against upstream [kdeconnect-kde](https://invent.kde.org/network/kdeconnect-kde) and [kdeconnect-android](https://invent.kde.org/network/kdeconnect-android).

## What it does

- Displays a live H.264 stream of an Android 11+ screen inside a KDE Connect desktop window over your existing paired TLS connection.
- Forwards mouse clicks and drags as taps, swipes, and scrolls, lets you navigate Android with right-click or toolbar buttons (Back, Home, Recents), and forwards keyboard typing to the phone.
- Streams playback audio from phone apps that allow audio capture (music, video, games, podcasts) to Linux PipeWire/PulseAudio via GStreamer. Denying audio permission on the phone still leaves video streaming working.
- Resizes and refits the viewer window automatically when you rotate the phone, with a Fit Window button when you want to snap back after resizing manually.
- Respects Android privacy rules: every stream starts only after you confirm Android's system screen-cast prompt, keeps a notification with a Stop button on screen, and relies on Android to mask `FLAG_SECURE` windows such as banking apps and password prompts. Stopping manually is remembered so the stream does not restart on reconnect.
- Keeps every built-in KDE Connect feature working as usual (clipboard sync, file sharing, notifications, SMS, ping, and remote input).

## Install on Ubuntu 26.04 (x86_64)

Add the [home:dwolfF:kdeconnect](https://build.opensuse.org/project/show/home:dwolfF:kdeconnect) repository on Open Build Service and upgrade Ubuntu's stock `25.12.3` KDE Connect packages (along with `libei1` `1.6.0` for Wayland input):

```bash
sudo install -d -m 0755 /etc/apt/keyrings
curl -fsSL https://download.opensuse.org/repositories/home:/dwolfF:/kdeconnect/xUbuntu_26.04/Release.key \
  | sudo tee /etc/apt/keyrings/kdeconnect-obs.asc >/dev/null
echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/kdeconnect-obs.asc] https://download.opensuse.org/repositories/home:/dwolfF:/kdeconnect/xUbuntu_26.04/ /" \
  | sudo tee /etc/apt/sources.list.d/kdeconnect-obs.list >/dev/null
sudo apt update && sudo apt install kdeconnect kdeconnect-libs qml6-module-org-kde-kdeconnect libei1
pkill -x kdeconnectd || true
```

After that, regular `sudo apt update && sudo apt upgrade` pulls newer verified builds from OBS without Ubuntu's archive overwriting them.

To remove the OBS repository and go back to Ubuntu's default packages:

```bash
sudo rm -f /etc/apt/sources.list.d/kdeconnect-obs.list /etc/apt/keyrings/kdeconnect-obs.asc
sudo apt update
sudo apt install --allow-downgrades kdeconnect=25.12.3-0ubuntu1 kdeconnect-libs=25.12.3-0ubuntu1 qml6-module-org-kde-kdeconnect=25.12.3-0ubuntu1 libei1=1.5.0-3
pkill -x kdeconnectd || true
```

## Install on Android (11+)

Your phone needs the matching `ScreenStream` plugin built from the `kdeconnect-android` `work/screenstream` branch (`Debug KDE Connect`, package ID `org.kde.kdeconnect_tp.debug`). Because of the `.debug` suffix, it installs side-by-side next to the official Google Play release without replacing it.

1. Install the debug APK on your phone and pair **Debug KDE Connect** with your Ubuntu machine.
2. Allow Notifications on Android 13+ so the foreground service controls stay visible.
3. Grant **Record Audio** if you want phone app sound routed to your PC speakers.
4. Enable the **Accessibility Service** for Debug KDE Connect if you want mouse and keyboard control from the desktop viewer window.

Once the upstream merge requests are accepted and shipped in a new release, you will get Screen Stream in normal Google Play updates and can remove the debug build.

## Using it

1. Open KDE Connect on Ubuntu and pick your paired phone.
2. Click **Start Screen Stream** from the device actions or system tray.
3. On the phone, select **Entire screen** in Android's system dialog and tap **Start**.
4. Close the desktop viewer window or tap **Stop screen stream** in the phone notification whenever you want to end the session.

## How automatic rebuilds work

Upstream [kdeconnect-kde](https://invent.kde.org/network/kdeconnect-kde) moves daily, so manual snapshots go stale quickly. A dedicated runner executes the pipeline documented in [obs/README.md](obs/README.md) whenever new commits land upstream, keeping the stable APT repo untouched unless every check passes.

1. [tools/upstream_sync.py](tools/upstream_sync.py) checks `kdeconnect-kde` (`master`) over HTTPS from KDE Invent using hardened Git flags (hooks, symlinks, submodules, LFS filters, and `ext::` protocols disabled, with `OSC_CONFIG` and SSH agent variables stripped). It also records `kdeconnect-android` `master` drift in `.state/sync/android.json` so you know when the APK needs a local rebuild.
2. New upstream commits are combined onto a temporary `obs/kdeconnect-staging` branch by cherry-picking only the plugin commits cleanly, without rewriting the clean `plugin/screenstream` branch. Tree cleanliness checks block symlinks, submodules, and stray packaging files, and [tools/check_kde_compliance.py](tools/check_kde_compliance.py) runs on the combined diff.
3. [tools/obs_packaging.py](tools/obs_packaging.py) packs a deterministic `3.0 (quilt)` source tarball using the Debian overlay in [obs/kdeconnect/debian/](obs/kdeconnect/debian/) and uploads it to [home:dwolfF:kdeconnect:staging](https://build.opensuse.org/project/show/home:dwolfF:kdeconnect:staging). OBS compiles on `xUbuntu_26.04` `x86_64`, runs `ctest`, and rejects any `lintian` error.
4. Before anything reaches stable APT, [tools/verify-deb.sh](tools/verify-deb.sh) inspects every built `.deb` on the runner (rejecting SUID/SGID binaries, `..` path traversal, unexpected maintainer files, or files outside standard `/usr` and `/etc` targets). It then installs and purges the packages inside an offline Rootless Docker container built from [obs/verifier.Dockerfile](obs/verifier.Dockerfile) (`--network none`, `--cap-drop=ALL`, `--security-opt=no-new-privileges:true`, `--pids-limit=256`), checks ELF dependencies statically with `readelf -d` (verifying `libgstreamer-1.0.so.0` and `libQt6Widgets.so.6` are linked and `Qt6Multimedia` is not), and checks the required GStreamer elements.
5. [tools/obs_controller.py](tools/obs_controller.py) locks onto the verified staging `srcmd5` revision to prevent TOCTOU races before promoting to [home:dwolfF:kdeconnect](https://build.opensuse.org/project/show/home:dwolfF:kdeconnect). A per-fingerprint circuit breaker stops broken upstream commits from looping on every cron tick, and every cycle prunes old build logs, unreferenced `.deb` directories, and stale temporary files automatically.

## Running your own build runner

If you want to host this controller on a build machine:

1. Copy [.state.env.example](.state.env.example) to `.state/local.env` (`chmod 600`), then set your local upstream paths, a disk-backed `TMPDIR` (never `tmpfs`), runner SSH host, and remote directory.
2. Put your OBS credentials on the runner host (`~/.config/osc/oscrc` or `.state/controller.env` with `0600` permissions) and verify Rootless Docker works there.
3. Push updates from your workstation with [tools/sync-runner.sh](tools/sync-runner.sh), which runs Gate 1 checks locally before updating the runner checkout and plugin branch bundle:

```bash
./tools/sync-runner.sh
```

4. Preview what a sync cycle would do, then run a full staging build and promotion cycle using [tools/run-controller.sh](tools/run-controller.sh):

```bash
ssh <runner-host> '~/runners/kdeconnect-plugins/tools/run-controller.sh cycle --dry-run'
ssh <runner-host> '~/runners/kdeconnect-plugins/tools/run-controller.sh cycle --wait-sec 3000'
```

5. Install the managed hourly cron entry (`47 * * * *`) with [tools/install_cron.py](tools/install_cron.py), which edits only its own `# BEGIN KDECONNECT OBS` block and leaves any other cron jobs alone:

```bash
ssh <runner-host> 'python3 ~/runners/kdeconnect-plugins/tools/install_cron.py'
```

If an upstream commit causes a cherry-pick conflict (`rebase_conflict`) or fails a gate (`failed_gate`), the published APT packages stay unchanged until you rebase your local `work/screenstream` branch and re-run [tools/sync-runner.sh](tools/sync-runner.sh). You can inspect OBS state, run manual cleanup, or remove the cron block anytime:

```bash
~/runners/kdeconnect-plugins/tools/run-controller.sh status --package kdeconnect
~/runners/kdeconnect-plugins/tools/run-controller.sh cleanup --keep 2
python3 ~/runners/kdeconnect-plugins/tools/install_cron.py --remove
```

## Repository layout

| Path | Purpose |
| --- | --- |
| [AGENTS.md](AGENTS.md) | Engineering rules, build boundaries, six-gate checklist, and upstream AI policy |
| [docs/WORKFLOW.md](docs/WORKFLOW.md) | Detailed Gate 0 to Gate 5 acceptance criteria and handoff process |
| [docs/plugins/screenstream/spec.md](docs/plugins/screenstream/spec.md) | Screen Stream protocol packets, state machine, input bounds, and audio design |
| [obs/](obs/) | Ubuntu 26.04 Debian overlays, OBS project XML/prjconf files, and [verifier.Dockerfile](obs/verifier.Dockerfile) |
| [tools/](tools/) | Runner automation, packaging, Docker verification, cron manager, and hygiene checks |
| [tests/](tests/) | Python `unittest` suite covering packaging, runner safety gates, and tree hygiene |
| [prototype/](prototype/) | Small standalone Android capture app from initial feasibility testing; see [prototype/README.md](prototype/README.md) |
| [LICENSES/](LICENSES/) | REUSE 3.0 license texts referenced by [REUSE.toml](REUSE.toml) |

## Local checks

Run these from the repository root with a disk-backed `TMPDIR` (no desktop C++ toolchain is needed or expected here):

```bash
python3 tools/check_public_tree.py --worktree
python3 tools/check_kde_compliance.py
python3 -m unittest discover -s tests -v
```

Before submitting an upstream branch manually to KDE Invent, check the clean upstream diff:

```bash
python3 tools/check_kde_compliance.py --upstream-repo /path/to/kdeconnect-kde --base origin/master
```

See [tools/check_public_tree.py](tools/check_public_tree.py) and [AGENTS.md](AGENTS.md) for the exact hygiene checks enforced on every commit.

## Upstream patches and license

Official KDE repositories do not accept automated pull requests or AI-authored merge requests. Changes on `work/screenstream` are kept clean of packaging and automation files, reviewed by the human maintainer, and discussed on the [KDE Connect mailing list](https://mail.kde.org/mailman/listinfo/kdeconnect) before manual submission to KDE Invent.

Every tracked file in this repository carries REUSE 3.0 headers backed by [REUSE.toml](REUSE.toml) and [LICENSES/](LICENSES/) (`GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL` for code, scripts, and documentation, `BSD-2-Clause` for build helpers, and `CC0-1.0` for manifests).
