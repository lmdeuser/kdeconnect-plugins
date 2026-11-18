<!-- SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com> -->
<!-- SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL -->

# KDE Connect Screen Stream

View and control your Android phone screen (with phone audio) directly inside a desktop window on **Ubuntu 26.04 (Wayland)** using KDE Connect — over regular Wi-Fi, with no USB cable, no ADB, and no separate third-party server app.

---

## What Does This Software Do?

This project adds a built-in **Screen Stream** plugin to KDE Connect (`kdeconnect-kde` on Linux desktop and `kdeconnect-android` on Android 11+):

- **Mirror your phone screen on Linux**: Shows a live H.264 video stream of your Android display inside a desktop window over your already-paired, encrypted KDE Connect connection.
- **Control your phone with mouse and keyboard**:
  - **Left-click** to tap and **click-and-drag** to swipe or scroll on the phone screen.
  - **Right-click** inside the window or press the toolbar buttons (**Back**, **Home**, **Recents**) to navigate Android.
  - **Type directly** from your PC keyboard into apps on the phone.
- **Stream phone audio to desktop speakers**: Forwards playback audio from apps that allow audio capture (music, video, games, podcasts) to Linux PipeWire/PulseAudio. If microphone/audio permission is not granted on the phone, video streaming still works silently.
- **Automatic window resizing on rotation**: When you rotate the phone between portrait and landscape, the desktop window automatically resizes and refits to match (and you can press **Fit Window** anytime).
- **Explicit consent and privacy protection**:
  - Android always displays the system **"Start recording or casting..."** dialog before starting a stream.
  - An active notification with a one-tap **Stop screen stream** button stays visible while streaming.
  - Sensitive/protected apps (`FLAG_SECURE` such as banking apps, password screens, or DRM video) stay automatically masked by Android.
  - Stopping the stream manually persists cleanly: the phone will never restart streaming behind your back on reconnect.
- **Zero disruption to standard KDE Connect**: Clipboard sync, file sharing, notifications, SMS, ping, and standalone remote input continue working normally.

---

## How You Use It (Day-to-Day)

1. Open **KDE Connect** on Ubuntu and select your paired Android phone.
2. Click **Start Screen Stream** (or trigger it from the device page / tray menu).
3. On your phone, choose **Entire screen** in the Android prompt and tap **Start**.
4. A desktop window opens showing your phone screen. Click, swipe, type, and listen directly from your PC.
5. When finished, either close the viewer window on Linux or tap **Stop screen stream** in the phone notification bar.

---

## Part 1: Install (For Users)

### Ubuntu 26.04 (`x86_64`)

Run these four commands to add our OBS package repository and upgrade Ubuntu's stock `25.12.3` KDE Connect packages to our tested `27.03.70` build:

```bash
sudo install -d -m 0755 /etc/apt/keyrings
curl -fsSL https://download.opensuse.org/repositories/home:/dwolfF:/kdeconnect/xUbuntu_26.04/Release.key \
  | sudo tee /etc/apt/keyrings/kdeconnect-obs.asc >/dev/null
echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/kdeconnect-obs.asc] https://download.opensuse.org/repositories/home:/dwolfF:/kdeconnect/xUbuntu_26.04/ /" \
  | sudo tee /etc/apt/sources.list.d/kdeconnect-obs.list >/dev/null
sudo apt update && sudo apt install kdeconnect kdeconnect-libs qml6-module-org-kde-kdeconnect libei1
```

Restart your background KDE Connect daemon:

```bash
pkill -x kdeconnectd || true
```

Normal `sudo apt update && sudo apt upgrade` will automatically keep your PC updated with verified builds from `home:dwolfF:kdeconnect` without being overwritten by Ubuntu's older archive packages.

#### Return to Stock Ubuntu Packages

If you want to remove the OBS build and restore Ubuntu's default version:

```bash
sudo rm -f /etc/apt/sources.list.d/kdeconnect-obs.list /etc/apt/keyrings/kdeconnect-obs.asc
sudo apt update
sudo apt install --allow-downgrades kdeconnect=25.12.3-0ubuntu1 kdeconnect-libs=25.12.3-0ubuntu1 qml6-module-org-kde-kdeconnect=25.12.3-0ubuntu1 libei1=1.5.0-3
pkill -x kdeconnectd || true
```

### Android (Android 11+)

The phone also needs the matching `ScreenStream` plugin:

1. Install the debug APK (`Debug KDE Connect`, package `org.kde.kdeconnect_tp.debug`) built from our `kdeconnect-android` `work/screenstream` branch. Because it uses `.debug` as its package suffix, it installs **side-by-side** with official Google Play KDE Connect and will not overwrite it.
2. Pair **Debug KDE Connect** with your Ubuntu PC.
3. Grant **Notifications** (Android 13+) so the active streaming controls stay visible.
4. Optional permissions:
   - **Record Audio**: enables desktop playback of phone app audio (`AudioPlaybackCapture`). Denying it keeps video working without audio.
   - **Accessibility Service**: enables mouse clicks, swipes, navigation buttons, and keyboard typing from the desktop viewer window.

Once the upstream Merge Requests are accepted into official KDE Connect, this plugin will arrive in regular Google Play updates and you can uninstall `Debug KDE Connect`.

---

## Part 2: What This Repository Contains & How Auto-Updates Work

This repository (`lmdeuser/kdeconnect-plugins`) is **not** a duplicate fork of the C++/Kotlin apps themselves. Instead, it is the **engineering hub, Debian/OBS packaging, test harness, and automated rebuild controller**:

- The actual C++/Qt/GStreamer and Kotlin plugin source code lives cleanly on `work/screenstream` branches against official KDE Invent (`kdeconnect-kde` and `kdeconnect-android`), ready for upstream review without any Debian packaging or runner scripts mixed in.
- Whenever upstream `kdeconnect-kde` commits new changes on `master`, our self-hosted runner automatically rebuilds and tests the combined package before touching the public APT repository.

### Automated Rebuild & Security Pipeline

1. **Hardened Git Sync (`tools/upstream_sync.py`)**: Fetches `origin/master` from `https://invent.kde.org/network/kdeconnect-kde.git` with Git hooks, symlinks, submodules, LFS filters, and `ext::` protocols disabled, and strips `OSC_CONFIG` and SSH agent variables from Git.
2. **Safe Branch Synthesis + Compliance**: Cherry-picks only our plugin commits onto a temporary staging branch (`obs/kdeconnect-staging`) without touching `plugin/screenstream`, rejects symlinks/submodules/`debian/` leaks, and checks `check_kde_compliance.py`. Also monitors `kdeconnect-android` `origin/master` drift in `.state/sync/android.json`.
3. **Remote Compilation & Tests (`home:dwolfF:kdeconnect:staging`)**: Uploads a deterministic `3.0 (quilt)` bundle via `tools/obs_packaging.py` to openSUSE Build Service, runs `ctest` under `xvfb-run` + `dbus-run-session`, and fails on any `lintian` error.
4. **Offline Sandboxed Docker Check (`tools/verify-deb.sh` + `obs/verifier.Dockerfile`)**:
   - Checks `.deb` archives statically before running Docker: blocks SUID/SGID binaries, `..` path traversal, unexpected control scripts, and files outside `/usr/{bin,lib,libexec,share,include}` and `/etc/{ufw,xdg}`.
   - Installs the staged `.deb`s inside an offline Rootless Docker container (`--network none`, `--cap-drop=ALL`, `--security-opt=no-new-privileges:true`, `--pids-limit=256`).
   - Checks shared library linkage statically with `readelf -d` (ensuring `Qt6Multimedia` is absent and GStreamer is present), checks GStreamer plugins, and purges cleanly.
5. **TOCTOU-Locked Promotion (`home:dwolfF:kdeconnect`)**: Verifies the exact staging `srcmd5` revision matches what Docker tested right before promoting to stable APT. A per-fingerprint circuit breaker stops broken upstream commits from retrying endlessly.
6. **Automatic Cleanup**: Every run prunes older binary caches/logs (keeping the newest 2 plus active promoted builds), sweeps stale temp/bundle files, runs `git gc --auto`, and prunes dangling Docker layers on verifier rebuilds.

### Run Your Own Auto-Build Runner (Step-by-Step)

1. Copy `.state.env.example` to `.state/local.env` (`chmod 600 .state/local.env`) and set your local upstream paths, a disk-backed `TMPDIR` (never `tmpfs`), runner SSH host, and runner directory.
2. Ensure the runner host has `osc` credentials (`.state/controller.env`) and working Rootless Docker.
3. Run local checks and deploy/sync the runner from your workstation:

```bash
./tools/sync-runner.sh
```

4. Preview the next action without modifying OBS:

```bash
ssh <runner-host> '~/runners/kdeconnect-plugins/tools/run-controller.sh cycle --dry-run'
```

5. Run one full automated sync -> OBS staging -> Docker check -> promote cycle:

```bash
ssh <runner-host> '~/runners/kdeconnect-plugins/tools/run-controller.sh cycle --wait-sec 3000'
```

6. Enable the hourly cron job (`47 * * * *`, preserving existing unrelated cron jobs):

```bash
ssh <runner-host> 'python3 ~/runners/kdeconnect-plugins/tools/install_cron.py'
```

7. Useful maintenance commands on the runner:

```bash
# Clean old staged builds, logs, and scratch files manually
~/runners/kdeconnect-plugins/tools/run-controller.sh cleanup --keep 2

# Query OBS build status
~/runners/kdeconnect-plugins/tools/run-controller.sh status --package kdeconnect

# Remove only this runner's cron block
python3 ~/runners/kdeconnect-plugins/tools/install_cron.py --remove
```

If an upstream commit causes a cherry-pick conflict (`rebase_conflict`) or fails `ctest`/`lintian`/`verify-deb.sh` (`failed_gate`), your existing stable APT packages stay intact until you rebase your local `work/screenstream` branch and re-run `./tools/sync-runner.sh`.

---

## Repository Layout

| Path | What It Contains |
| --- | --- |
| `README.md` | Overview, installation steps, usage guide, and runner instructions |
| `AGENTS.md` | Engineering constitution, six-gate workflow, and KDE Invent AI policy |
| `docs/` | Gate workflow (`WORKFLOW.md`), templates, and `screenstream` protocol spec |
| `obs/` | Ubuntu 26.04 Debian packaging files, OBS XML/prjconf configs, and `verifier.Dockerfile` |
| `tools/` | `sync-runner.sh`, `upstream_sync.py`, `obs_controller.py`, `obs_packaging.py`, `verify-deb.sh`, `install_cron.py`, and compliance checkers |
| `tests/` | Python `unittest` suite covering packaging, runner gates, security hardening, and tree checks |
| `prototype/` | Standalone minimal Android capture probe used during early feasibility testing |
| `LICENSES/` | Verbatim REUSE 3.0 license texts (`GPL-2.0-only`, `GPL-3.0-only`, `BSD-2-Clause`, `CC0-1.0`, `LicenseRef-KDE-Accepted-GPL`) |

---

## Local Developer Checks

Always export a disk-backed `TMPDIR` before running checks or tests:

```bash
python3 tools/check_public_tree.py --worktree
python3 tools/check_kde_compliance.py
python3 -m unittest discover -s tests -v
```

Before reviewing an external `work/<plugin_id>` branch for manual upload to KDE Invent:

```bash
python3 tools/check_kde_compliance.py --upstream-repo /path/to/kdeconnect-kde --base origin/master
```

---

## Upstream KDE Connect Submission

Official KDE Connect rejects automated or AI-written Merge Requests (`AGENTS.md` and `CONTRIBUTING.md`). All changes on `work/screenstream` must be reviewed, understood, and submitted manually by a human maintainer to [KDE Invent](https://invent.kde.org/network/kdeconnect-kde) after pre-discussion on the KDE Connect mailing list or community channel.

---

## License

Every tracked file complies with REUSE 3.0 using license texts in `LICENSES/`:

- Docs, skills, Python scripts, shell scripts, tests, and Android probe Java/XML sources: `GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL`
- Build scripts: `BSD-2-Clause`
- Configuration and ignore manifests: `CC0-1.0`
