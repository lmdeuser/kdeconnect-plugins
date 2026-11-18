<!-- SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com> -->
<!-- SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL -->

# KDE Connect Screen Stream Plugin & Automated OBS Builder

## What Is This?

This project adds a **Screen Stream** plugin to KDE Connect so you can view and control your Android phone screen (with optional playback audio) directly in a desktop window on **Ubuntu 26.04 (Wayland)**:

- Shows your Android 11+ screen inside a Qt desktop window with automatic window fitting and portrait/landscape rotation support.
- Streams device playback audio using GStreamer and PipeWire (requires `RECORD_AUDIO` permission; falls back cleanly to video-only if denied).
- Sends mouse clicks, drags, Back, Home, and Recents navigation actions, and keyboard input through KDE Connect's existing input plugins once Android Accessibility is enabled.
- Rebuilds desktop `.deb` packages automatically in [openSUSE Build Service (OBS)](https://build.opensuse.org/) whenever upstream `kdeconnect-kde` updates, checks them in an offline sandboxed Docker container, and publishes verified packages to APT.

---

## Install on Ubuntu 26.04 (`x86_64`)

Run these four commands in your terminal to add our OBS repository and replace the stock Ubuntu package (`25.12.3`) with our tested `27.03.70` build:

```bash
sudo install -d -m 0755 /etc/apt/keyrings
curl -fsSL https://download.opensuse.org/repositories/home:/dwolfF:/kdeconnect/xUbuntu_26.04/Release.key \
  | sudo tee /etc/apt/keyrings/kdeconnect-obs.asc >/dev/null
echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/kdeconnect-obs.asc] https://download.opensuse.org/repositories/home:/dwolfF:/kdeconnect/xUbuntu_26.04/ /" \
  | sudo tee /etc/apt/sources.list.d/kdeconnect-obs.list >/dev/null
sudo apt update && sudo apt install kdeconnect kdeconnect-libs qml6-module-org-kde-kdeconnect libei1
```

Then restart your user KDE Connect background daemon:

```bash
pkill -x kdeconnectd || true
```

From now on, normal `sudo apt update && sudo apt upgrade` will automatically pull new verified builds from `home:dwolfF:kdeconnect` without being overwritten by older Ubuntu stock packages (`27.03.70+git... > 25.12.3`).

### Uninstall or Return to Stock Ubuntu Packages

If you ever want to switch back to the official Ubuntu archive version:

```bash
sudo rm -f /etc/apt/sources.list.d/kdeconnect-obs.list /etc/apt/keyrings/kdeconnect-obs.asc
sudo apt update
sudo apt install --allow-downgrades kdeconnect=25.12.3-0ubuntu1 kdeconnect-libs=25.12.3-0ubuntu1 qml6-module-org-kde-kdeconnect=25.12.3-0ubuntu1 libei1=1.5.0-3
pkill -x kdeconnectd || true
```

---

## Install on Android (Android 11+)

Desktop-only stock KDE Connect cannot stream the phone screen unless the Android app also has the `ScreenStream` plugin:

1. Install the matching APK (`Debug KDE Connect`, package ID `org.kde.kdeconnect_tp.debug`) from our `kdeconnect-android` `work/screenstream` build. Because it uses `.debug` as its package suffix, it installs **side-by-side** with Google Play's KDE Connect without overwriting it.
2. Open **Debug KDE Connect** and pair it with your Ubuntu computer.
3. Grant **Notifications** permission (Android 13+) so the active stream notification and `"Stop screen stream"` button stay visible.
4. Optional permissions:
   - **Microphone (`RECORD_AUDIO`)**: needed for Android `AudioPlaybackCapture` device audio streaming. If you deny it, video streaming still works silently.
   - **Accessibility Service**: needed if you want to click, swipe, and type on your phone from the desktop viewer window.
   - **Screen Capture consent dialog**: Android asks you to confirm screen sharing on every new session for security; protected (`FLAG_SECURE`) screens stay masked by Android automatically.

Once the upstream Merge Requests are accepted into official KDE Connect, this feature will ship in regular Google Play updates and you can remove `Debug KDE Connect`.

---

## How Automatic Updates & Safety Checks Work

We never run untrusted upstream commits directly against your credentials or ship untested builds straight to APT:

1. **Isolated Git Sync (`tools/upstream_sync.py`)**: Fetches `origin/master` from `https://invent.kde.org/network/kdeconnect-kde.git` with Git hooks, symlinks, submodules, LFS filters, and `ext::` protocols disabled, and strips `OSC_CONFIG` and SSH keys from the Git environment.
2. **Clean Rebase + KDE Checks**: Cherry-picks only our plugin commits onto a temporary staging branch (`obs/kdeconnect-staging`) while keeping our base `plugin/screenstream` branch untouched, rejects symlinks/submodules/`debian/` leaks, and runs `check_kde_compliance.py`.
3. **Remote OBS Staging Build (`home:dwolfF:kdeconnect:staging`)**: Uploads a deterministic `3.0 (quilt)` archive via `tools/obs_packaging.py` and builds remotely with `ctest` (`BUILD_TESTING=ON`) and `lintian --fail-on error`.
4. **Offline Rootless Docker Check (`tools/verify-deb.sh` + `obs/verifier.Dockerfile`)**:
   - First inspects `.deb` contents on the runner host to reject SUID/SGID binaries, path traversal (`..`), unexpected control scripts, or files outside `/usr/{bin,lib,libexec,share,include}` and `/etc/{ufw,xdg}`.
   - Next installs the staged `.deb`s inside an air-gapped Rootless Docker container (`--network none`, `--cap-drop=ALL`, `--security-opt=no-new-privileges:true`, `--pids-limit=256`).
   - Checks ELF linkage statically with `readelf -d` (ensuring `Qt6Multimedia` is absent and GStreamer is present) and verifies GStreamer elements, then cleanly purges the test packages.
5. **TOCTOU-Checked Promotion (`home:dwolfF:kdeconnect`)**: Verifies the exact staging `srcmd5` hash matches what Docker checked right before releasing to the public APT repository. Failed commits trip a per-fingerprint circuit breaker so the cron job never loops endlessly on broken upstream code.
6. **Automatic Disk & Docker Cleanup**: Every cycle automatically prunes older staged binaries/logs (keeping the latest 2 plus the active promoted version), sweeps stale scratch files, runs `git gc --auto`, and prunes dangling Docker layers on verifier rebuilds without touching other projects.

---

## Run Your Own Auto-Build Runner (Step-by-Step)

1. Copy `.state.env.example` to `.state/local.env` (`chmod 600 .state/local.env`) and fill in your local checkout paths, disk-backed `TMPDIR` (never `tmpfs`), runner SSH host, and runner directory (`RUNNER_DIR`).
2. Prepare a readable `osc` config (`.state/controller.env` pointing `OSC_CONFIG` to your credentials on the runner host) and make sure Rootless Docker works on the runner.
3. Verify local code checks and bootstrap the runner host from your workstation:

```bash
./tools/sync-runner.sh
```

4. Test what the controller would do next without changing OBS:

```bash
ssh <runner-host> '~/runners/kdeconnect-plugins/tools/run-controller.sh cycle --dry-run'
```

5. Run one full automated sync, build, verification, and promotion cycle manually:

```bash
ssh <runner-host> '~/runners/kdeconnect-plugins/tools/run-controller.sh cycle --wait-sec 3000'
```

6. Enable the hourly cron job on the runner (`47 * * * *`, preserving any existing cron jobs):

```bash
ssh <runner-host> 'python3 ~/runners/kdeconnect-plugins/tools/install_cron.py'
```

7. Useful maintenance commands on the runner:

```bash
# Clean old staged builds, logs, and temporary files manually
~/runners/kdeconnect-plugins/tools/run-controller.sh cleanup --keep 2

# Check OBS staging/stable status
~/runners/kdeconnect-plugins/tools/run-controller.sh status --package kdeconnect

# Remove only this runner's cron entry
python3 ~/runners/kdeconnect-plugins/tools/install_cron.py --remove
```

If an upstream commit breaks cherry-pick cleanly (`rebase_conflict`) or fails `ctest`/`lintian`/`verify-deb.sh` (`failed_gate`), the runner stops retrying that fingerprint and leaves your existing stable APT repository untouched until you rebase your local `work/screenstream` branch and re-run `./tools/sync-runner.sh`.

---

## What Each Folder Does

| Path | What It Contains |
| --- | --- |
| `README.md` | Start-here guide for users and self-hosted runner setup |
| `AGENTS.md` | Strict rules for AI-assisted work and KDE Invent policy |
| `docs/` | Workflow gates (`WORKFLOW.md`), templates, and `screenstream` protocol spec |
| `obs/` | Ubuntu 26.04 Debian packaging files, OBS XML/prjconf configs, and `verifier.Dockerfile` |
| `tools/` | `sync-runner.sh`, `upstream_sync.py`, `obs_controller.py`, `obs_packaging.py`, `verify-deb.sh`, `install_cron.py`, and lint/hygiene checkers |
| `tests/` | Python unit test suite (`unittest`) for packaging, runner gates, sync safety, and tree hygiene |
| `prototype/` | Standalone minimal Android capture probe used during early prototyping |
| `LICENSES/` | Standard REUSE 3.0 license text files (`GPL-2.0-only`, `GPL-3.0-only`, `BSD-2-Clause`, `CC0-1.0`, `LicenseRef-KDE-Accepted-GPL`) |

---

## Local Developer Checks

Always set a disk-backed temporary directory (`TMPDIR`) before running tests or tools:

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

Upstream KDE Connect rejects automated or AI-written Merge Requests (`AGENTS.md` and `CONTRIBUTING.md`). All changes on `work/screenstream` must be reviewed, understood, and submitted manually by a human maintainer to [KDE Invent](https://invent.kde.org/network/kdeconnect-kde) after discussion on the KDE Connect mailing list or community chat.

---

## License

All files follow REUSE 3.0 SPDX headers with license texts stored in `LICENSES/`:

- Docs, skills, Python scripts, shell scripts, tests, and Android probe sources: `GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL`
- Build scripts: `BSD-2-Clause`
- Config and ignore manifests: `CC0-1.0`
