<!-- SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com> -->
<!-- SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL -->

# KDE Connect Continuity Plugins

This project extends [KDE Connect](https://kdeconnect.kde.org/) into a six-plugin continuity stack between Android (11-15+) and Ubuntu 26.04 LTS ([Wayland](https://wayland.freedesktop.org/), `x86_64`) over KDE Connect's paired LAN mutual-[TLS](https://datatracker.ietf.org/doc/html/rfc8446) connection. It requires no USB cable, no ADB in daily use, and no third-party screen or webcam server.

This repository (`lmdeuser/kdeconnect-plugins`) does not fork the desktop or Android codebases. It owns the multi-plugin registry ([plugins.toml](plugins.toml)), protocol and security specifications under `docs/plugins/`, Ubuntu 26.04 packaging overlays under [obs/](obs/), build and compliance automation under [tools/](tools/), and the Python test suite under [tests/](tests/). The C++/Qt desktop plugins and Kotlin Android plugins live on single-purpose `work/<plugin_id>` branches targeting upstream [kdeconnect-kde](https://invent.kde.org/network/kdeconnect-kde) and [kdeconnect-android](https://invent.kde.org/network/kdeconnect-android).

## Included plugins and enhancements

All six active entries declared in [plugins.toml](plugins.toml):

| ID | Kind | Spec | Summary |
| --- | --- | --- | --- |
| `screenstream` | `new_plugin` | [docs/plugins/screenstream/spec.md](docs/plugins/screenstream/spec.md) | Live Android [H.264](https://www.itu.int/rec/T-REC-H.264) screen mirroring ([MediaProjection](https://developer.android.com/media/grow/media-projection) + [MediaCodec](https://developer.android.com/reference/android/media/MediaCodec)) and eligible playback audio (`pcm_s16le` 48 kHz mono via [AudioPlaybackCapture](https://developer.android.com/media/platform/av-capture)) rendered in an isolated [Qt 6 Widgets](https://doc.qt.io/qt-6/qtwidgets-index.html) + [GStreamer](https://gstreamer.freedesktop.org/) helper (`kdeconnect-screenstream-viewer`). Reuses KDE Connect input, clipboard, share (up to 32 files by drag-and-drop), and notification actions ("Open on Phone Screen"), clamps viewer geometry on Wayland rotation via [KWin Scripting](https://develop.kde.org/docs/plasma/kwin/api/), honors [`FLAG_SECURE`](https://developer.android.com/reference/android/view/WindowManager.LayoutParams#FLAG_SECURE), persists explicit stop across reconnects, and toggles screencast vs Bluetooth audio live via [AudioDeviceCallback](https://developer.android.com/reference/android/media/AudioDeviceCallback). |
| `continuity_camera` | `new_plugin` | [docs/plugins/continuity_camera/spec.md](docs/plugins/continuity_camera/spec.md) | Streams phone [Camera2](https://developer.android.com/media/camera/camera2) video and optional [AudioRecord](https://developer.android.com/reference/android/media/AudioRecord) mic audio to Linux through `kdeconnect-continuitycamera-bridge` and [PipeWire](https://pipewire.org/) [`pipewiresink`](https://gitlab.freedesktop.org/pipewire/pipewire/-/tree/master/src/gst) (`kdeconnect_continuity_camera` / `kdeconnect_continuity_mic`), running strictly in userspace without a [`v4l2loopback`](https://github.com/v4l2loopback/v4l2loopback) kernel module, plus on-demand photo capture and multi-page PDF scans built with Android [PdfDocument](https://developer.android.com/reference/android/graphics/pdf/PdfDocument). |
| `bt_audio_switch` | `new_plugin` | [docs/plugins/bt_audio_switch/spec.md](docs/plugins/bt_audio_switch/spec.md) | Coordinates [Bluetooth A2DP / HFP](https://www.bluetooth.com/specifications/specs/) headset ownership between Ubuntu ([BlueZ `org.bluez.Device1`](https://git.kernel.org/pub/scm/bluetooth/bluez.git/tree/doc/org.bluez.Device.rst) + PipeWire) and Android for user-allowlisted MAC addresses, with a 3000 ms anti-oscillation hysteresis cooldown and shared audio arbitration that suppresses automatic phone `media` claims while PC screencast audio is active. |
| `clipboard_images` | `upstream_enhancement` | [docs/plugins/clipboard_images/spec.md](docs/plugins/clipboard_images/spec.md) | Bidirectional image clipboard synchronization over `kdeconnect.clipboard.file` (capped at 20 MiB on Android, shared via [FileProvider](https://developer.android.com/reference/androidx/core/content/FileProvider) with 5-file cache pruning, and exposed as both `QImage` and URL `QMimeData` on Linux Wayland via [KSystemClipboard](https://invent.kde.org/frameworks/kguiaddons/-/blob/master/src/systemclipboard/ksystemclipboard.h)). |
| `otp_autofill` | `upstream_enhancement` | [docs/plugins/otp_autofill/spec.md](docs/plugins/otp_autofill/spec.md) | Automatically copies 4-8 digit 2FA/OTP codes from non-silent English or Russian notifications into the desktop clipboard via [KSystemClipboard](https://invent.kde.org/frameworks/kguiaddons/-/blob/master/src/systemclipboard/ksystemclipboard.h), tags them with `x-kde-passwordManagerHint=secret` (honored by [Klipper](https://invent.kde.org/plasma/plasma-workspace/-/blob/master/klipper/klipper.cpp)), clears after 60 s if unchanged, filters out years, currency amounts, and phone numbers, and falls back to real-time incoming inbox SMS deltas (`SMSPlugin`, <= 16 messages, <= 120 s old) when [Android 15 sensitive-notification redaction](https://developer.android.com/about/versions/15/behavior-changes-all#sensitive-notifications) hides OTP text from third-party [NotificationListenerService](https://developer.android.com/reference/android/service/notification/NotificationListenerService) apps lacking system-only [`RECEIVE_SENSITIVE_NOTIFICATIONS`](https://developer.android.com/reference/android/Manifest.permission#RECEIVE_SENSITIVE_NOTIFICATIONS). |
| `remote_auth` | `new_plugin` | [docs/plugins/remote_auth/spec.md](docs/plugins/remote_auth/spec.md) | Prompts interactive Android [BiometricPrompt](https://developer.android.com/reference/android/hardware/biometrics/BiometricPrompt) confirmation for desktop [D-Bus](https://dbus.freedesktop.org/doc/dbus-specification.html) requests (`org.kde.kdeconnect.device.remoteauth`) and opt-in [`org.freedesktop.login1`](https://www.freedesktop.org/software/systemd/man/latest/org.freedesktop.login1.html) session unlock (`unlockAllowed` defaults to `false`), using single-use 256-bit CSPRNG nonces, a 30 s TTL, and max 5/min rate limiting. System-wide PAM/polkit replacement is out of scope. |

## Install on Ubuntu 26.04 (x86_64, Wayland)

Add the published [home:dwolfF:kdeconnect](https://build.opensuse.org/project/show/home:dwolfF:kdeconnect) APT repository ([xUbuntu_26.04 binaries](https://download.opensuse.org/repositories/home:/dwolfF:/kdeconnect/xUbuntu_26.04/)) on [Open Build Service](https://openbuildservice.org/) and upgrade Ubuntu's stock `25.12.3` KDE Connect packages alongside [`libei`](https://libinput.pages.freedesktop.org/libei/) `1.6.0` (`gstreamer1.0-pipewire` is declared directly in `kdeconnect` `Depends`):

```bash
sudo install -d -m 0755 /etc/apt/keyrings
curl -fsSL https://download.opensuse.org/repositories/home:/dwolfF:/kdeconnect/xUbuntu_26.04/Release.key \
  | sudo tee /etc/apt/keyrings/kdeconnect-obs.asc >/dev/null
echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/kdeconnect-obs.asc] https://download.opensuse.org/repositories/home:/dwolfF:/kdeconnect/xUbuntu_26.04/ /" \
  | sudo tee /etc/apt/sources.list.d/kdeconnect-obs.list >/dev/null
sudo apt update && sudo apt install kdeconnect kdeconnect-libs qml6-module-org-kde-kdeconnect libei1
pkill -x kdeconnectd || true
```

After setup, `sudo apt update && sudo apt upgrade` pulls newer verified builds from OBS.

To remove the OBS repository and revert to Ubuntu's archive packages:

```bash
sudo rm -f /etc/apt/sources.list.d/kdeconnect-obs.list /etc/apt/keyrings/kdeconnect-obs.asc
sudo apt update
sudo apt install --allow-downgrades kdeconnect=25.12.3-0ubuntu1 kdeconnect-libs=25.12.3-0ubuntu1 qml6-module-org-kde-kdeconnect=25.12.3-0ubuntu1 libei1=1.5.0-3
pkill -x kdeconnectd || true
```

## Build and install on Android (11-15+)

Desktop OBS builds do not compile the Android app. Build the side-by-side debug APK (`Debug KDE Connect`, application ID `org.kde.kdeconnect_tp.debug`) by synthesizing the active `work/<plugin_id>` branches from [plugins.toml](plugins.toml) onto `work/continuity-stack` inside your local `kdeconnect-android` checkout, then verify the signed APK with [apksigner](https://developer.android.com/tools/apksigner):

```bash
python3 tools/synthesize_android_stack.py
./gradlew --no-daemon --max-workers=2 assembleDebug lintDebug testDebugUnitTest
apksigner verify --verbose build/outputs/apk/debug/kdeconnect-android-debug.apk
```

Because of the `.debug` suffix, `Debug KDE Connect` installs alongside the Google Play release without replacing it. Permissions are scoped per feature:

- **Notifications (`POST_NOTIFICATIONS`):** keeps [foreground service](https://developer.android.com/develop/background-work/services/fgs/service-types) stop/consent controls visible on Android 13+.
- **Accessibility ([`AccessibilityService`](https://developer.android.com/reference/android/accessibilityservice/AccessibilityService)):** optional for forwarding clicks, drags, scrolls, and keys from the Screen Stream viewer window.
- **Record Audio (`RECORD_AUDIO`):** optional for [AudioPlaybackCapture](https://developer.android.com/media/platform/av-capture) in Screen Stream and phone mic capture in Continuity Camera.
- **Camera (`CAMERA`):** required for Continuity Camera streaming and photo/PDF capture.
- **Bluetooth Connect (`BLUETOOTH_CONNECT`):** required on Android 12+ for Bluetooth headset handoff (`bt_audio_switch`).
- **Notification Listener + SMS (`READ_SMS`, `SEND_SMS`, `RECEIVE_SMS`, `READ_PHONE_STATE`):** required only if using notifications sync or Android 15 SMS-delta OTP fallback when notifications redact 2FA codes.

## Using the main features

### Screen stream and mutually exclusive audio (PC vs Bluetooth)

1. Pair **Debug KDE Connect** with Ubuntu and run **Start Screen Stream** from KDE Connect on the desktop.
2. Choose **Entire screen** in Android's [MediaProjection](https://developer.android.com/media/grow/media-projection) dialog and tap **Start**.
3. Switch audio routing live without stopping video or re-prompting capture consent:
   - **Desktop viewer toolbar:** toggle between **`Audio: Screencast`** and **`Audio: Bluetooth`**.
   - **Android Screen stream plugin:** tap **`Route audio to Bluetooth`** or **`Route audio to PC screencast`**.
   - **Automatic Bluetooth handoff:** connecting or routing to a Bluetooth audio device switches `ScreenStreamService` to `bluetooth_routed` via [`AudioDeviceCallback`](https://developer.android.com/reference/android/media/AudioDeviceCallback); while PC screencast audio is active, `BtAudioSwitchPlugin` suppresses automatic `media` claims so playback never plays on both routes at once.

### Phone webcam and document scanner (`Continuity Camera`)

1. Start streaming from either side:
   - **From desktop (`kdeconnect-app`):** open your phone page and choose **`Use phone as webcam`** (switches to **`Stop phone webcam`**), **`Switch webcam to front camera`** / **`Switch webcam to rear camera`**, or **`Scan document with phone`**.
   - **From phone (`Continuity Camera`):** choose **`Start webcam stream`** / **`Stop camera stream`**, **`Switch camera lens`**, **`Capture photo / scan`**, or **`Scan document (PDF)`**.
2. Confirm the camera consent prompt on the phone when requested.
3. Select the virtual userspace [PipeWire](https://docs.pipewire.org/) devices in OBS Studio, Zoom, Telegram, Firefox, or Chromium:
   - Video source: **`KDE Connect Camera (<Phone>)`** (`node.name = kdeconnect_continuity_camera`).
   - Microphone (when audio is enabled): **`KDE Connect Mic (<Phone>)`** (`node.name = kdeconnect_continuity_mic`).
   - In Chrome/Chromium on Wayland, enable `chrome://flags/#enable-webrtc-pipewire-capturer` if PipeWire camera sources do not appear by default.

### Image clipboard, OTP auto-copy, Bluetooth handoff, and remote auth

- **Image clipboard:** copy an image on Ubuntu Wayland to push via `kdeconnect.clipboard.file` to Android, or push/copy an image on Android (`<= 20 MiB`) to paste directly as `QImage` / file URL on Linux.
- **OTP auto-copy:** non-silent 2FA notifications (and fresh Android 15 inbox SMS messages containing OTP keywords) are copied to the Wayland clipboard with `x-kde-passwordManagerHint=secret` and cleared automatically after 60 s if unchanged.
- **Bluetooth audio handoff:** configure identical allowlisted headset MAC addresses in `bt_audio_switch` settings on desktop and phone to enable automatic/manual A2DP and HFP ownership transitions.
- **Remote auth:** invoke `requestApproval` on `org.kde.kdeconnect.device.remoteauth` (or opt-in session unlock once `unlockAllowed` is enabled) to display a `BiometricPrompt` confirmation on the paired phone.

## Automated OBS pipeline and self-hosted runner

Upstream [kdeconnect-kde](https://invent.kde.org/network/kdeconnect-kde) and [kdeconnect-android](https://invent.kde.org/network/kdeconnect-android) evolve continuously. A self-hosted runner executes the workflow documented in [obs/README.md](obs/README.md) with zero local desktop C++ compilation:

1. [tools/upstream_sync.py](tools/upstream_sync.py) fetches upstream KDE Invent over HTTPS with hardened Git flags (hooks, symlinks, submodules, LFS filters, and `ext::` protocols disabled) and records `kdeconnect-android` `origin/master` drift in `.state/sync/android.json`.
2. Active `desktop_branch` entries in [plugins.toml](plugins.toml) are synthesized onto `obs/kdeconnect-staging` without mutating individual `work/<plugin_id>` branches, then verified with tree checks and [tools/check_kde_compliance.py](tools/check_kde_compliance.py).
3. [tools/obs_packaging.py](tools/obs_packaging.py) builds deterministic [`3.0 (quilt)`](https://manpages.debian.org/unstable/dpkg-dev/dpkg-source.1.en.html) archives using [obs/kdeconnect/debian/](obs/kdeconnect/debian/) and uploads them to [home:dwolfF:kdeconnect:staging](https://build.opensuse.org/project/show/home:dwolfF:kdeconnect:staging), which compiles `xUbuntu_26.04` `x86_64`, runs [`ctest`](https://cmake.org/cmake/help/latest/manual/ctest.1.html), and enforces [`lintian`](https://salsa.debian.org/lintian/lintian) with zero errors.
4. [tools/verify-deb.sh](tools/verify-deb.sh) runs host `dpkg-deb` path/mode/SUID audits followed by install/purge, ELF `readelf -d`, and `gst-inspect-1.0` element checks derived dynamically from [plugins.toml](plugins.toml) inside an offline [Rootless Docker](https://docs.docker.com/engine/security/rootless/) container built from [obs/verifier.Dockerfile](obs/verifier.Dockerfile).
5. [tools/obs_controller.py](tools/obs_controller.py) verifies an `expected_srcmd5` TOCTOU lock on staging before promoting packages to [home:dwolfF:kdeconnect](https://build.opensuse.org/project/show/home:dwolfF:kdeconnect), backed by a per-fingerprint circuit breaker and automatic pruning.

To configure your own runner host, copy [.state.env.example](.state.env.example) to `.state/local.env` (`chmod 600`), provide OBS credentials on the runner, and sync/run:

```bash
./tools/sync-runner.sh
ssh <runner-host> '~/runners/kdeconnect-plugins/tools/run-controller.sh cycle --dry-run'
ssh <runner-host> '~/runners/kdeconnect-plugins/tools/run-controller.sh cycle --wait-sec 3000'
ssh <runner-host> 'python3 ~/runners/kdeconnect-plugins/tools/install_cron.py'
```

See [obs/README.md](obs/README.md) for `status`, `cleanup`, and `--remove` cron commands.

## Repository layout and local checks

| Path | Purpose |
| --- | --- |
| [AGENTS.md](AGENTS.md) | Engineering standards, CI/build boundaries, six-gate lifecycle, and KDE Invent policy |
| [plugins.toml](plugins.toml) | Authoritative registry of active plugin branches, specs, and `.deb`/ELF/GStreamer checks |
| [docs/WORKFLOW.md](docs/WORKFLOW.md) | Detailed Gate 0 to Gate 5 acceptance criteria and handoff checklist |
| `docs/plugins/` | Per-plugin specifications linked in the plugin table above |
| [obs/](obs/) | Ubuntu 26.04 Debian overlays, OBS project/package XMLs, `*.prjconf`, and [verifier.Dockerfile](obs/verifier.Dockerfile) |
| [tools/](tools/) | Stack synthesis, upstream sync, OBS controller/packaging, Docker `.deb` verifier, and hygiene checks |
| [tests/](tests/) | Python `unittest` suite covering registry schema, packaging, runner safety gates, and tree hygiene |
| [prototype/](prototype/) | Initial standalone Android `MediaProjection` probe; see [prototype/README.md](prototype/README.md) |
| [LICENSES/](LICENSES/) | License texts referenced by [REUSE.toml](REUSE.toml) |

Run Gate 1 checks from the repository root with a disk-backed `TMPDIR` (no desktop C++ toolchain is installed or required here):

```bash
python3 tools/plugin_registry.py validate
python3 tools/check_public_tree.py --worktree
python3 tools/check_kde_compliance.py
python3 -m unittest discover -s tests -v
python3 tools/synthesize_android_stack.py --desktop --dry-run
python3 tools/synthesize_android_stack.py --dry-run
```

## Terms and primary sources

| Term | Description and primary source |
| --- | --- |
| **KDE Connect** | Cross-device integration framework over paired LAN TLS links ([kdeconnect.kde.org](https://kdeconnect.kde.org/), [kdeconnect-kde](https://invent.kde.org/network/kdeconnect-kde), [kdeconnect-android](https://invent.kde.org/network/kdeconnect-android)). |
| **Wayland & `libei`** | Compositor display protocol and emulated input library used by KDE Connect remote pointer/keyboard control on Linux ([Wayland](https://wayland.freedesktop.org/), [`libei`](https://libinput.pages.freedesktop.org/libei/)). |
| **PipeWire & `pipewiresink`** | Linux multimedia server hosting userspace `Video/Source` and `Audio/Source` webcam/mic nodes without kernel modules ([PipeWire](https://pipewire.org/), [PipeWire docs](https://docs.pipewire.org/), [`pipewiresink`](https://gitlab.freedesktop.org/pipewire/pipewire/-/tree/master/src/gst); contrasts with [`v4l2loopback`](https://github.com/v4l2loopback/v4l2loopback)). |
| **GStreamer** | Out-of-process media pipeline decoding H.264/PCM streams cleanly isolated from `kdeconnectd` ([gstreamer.freedesktop.org](https://gstreamer.freedesktop.org/documentation/)). |
| **KWin Scripting & Qt 6 Widgets** | Plasma window-management API used to clamp viewer bounds during rotation and the desktop viewer UI toolkit ([KWin Scripting](https://develop.kde.org/docs/plasma/kwin/api/), [Qt 6 Widgets](https://doc.qt.io/qt-6/qtwidgets-index.html)). |
| **`KSystemClipboard` & Klipper** | KDE Frameworks system clipboard bridge for Wayland and KDE Plasma clipboard manager respecting `x-kde-passwordManagerHint=secret` ([KSystemClipboard](https://invent.kde.org/frameworks/kguiaddons/-/blob/master/src/systemclipboard/ksystemclipboard.h), [Klipper](https://invent.kde.org/plasma/plasma-workspace/-/blob/master/klipper/klipper.cpp)). |
| **D-Bus, BlueZ & `systemd-logind`** | Linux IPC bus, `org.bluez.Device1` Bluetooth profile interface, Bluetooth A2DP/HFP specs, and `org.freedesktop.login1` session unlock API ([D-Bus](https://dbus.freedesktop.org/doc/dbus-specification.html), [`org.bluez.Device1`](https://git.kernel.org/pub/scm/bluetooth/bluez.git/tree/doc/org.bluez.Device.rst), [Bluetooth specs](https://www.bluetooth.com/specifications/specs/), [`org.freedesktop.login1`](https://www.freedesktop.org/software/systemd/man/latest/org.freedesktop.login1.html)). |
| **Android capture & input APIs** | [`MediaProjection`](https://developer.android.com/media/grow/media-projection), [`AudioPlaybackCapture`](https://developer.android.com/media/platform/av-capture), [`MediaCodec`](https://developer.android.com/reference/android/media/MediaCodec), [`AudioDeviceCallback`](https://developer.android.com/reference/android/media/AudioDeviceCallback), [`Camera2`](https://developer.android.com/media/camera/camera2), [`AudioRecord`](https://developer.android.com/reference/android/media/AudioRecord), [`PdfDocument`](https://developer.android.com/reference/android/graphics/pdf/PdfDocument), [`FLAG_SECURE`](https://developer.android.com/reference/android/view/WindowManager.LayoutParams#FLAG_SECURE), [`AccessibilityService`](https://developer.android.org/reference/android/accessibilityservice/AccessibilityService), and [Foreground Service Types](https://developer.android.com/develop/background-work/services/fgs/service-types). |
| **Android security, sharing & OTPs** | [`FileProvider`](https://developer.android.com/reference/androidx/core/content/FileProvider), [`NotificationListenerService`](https://developer.android.com/reference/android/service/notification/NotificationListenerService), [Android 15 Sensitive Notifications](https://developer.android.com/about/versions/15/behavior-changes-all#sensitive-notifications), [`RECEIVE_SENSITIVE_NOTIFICATIONS`](https://developer.android.com/reference/android/Manifest.permission#RECEIVE_SENSITIVE_NOTIFICATIONS), [`BiometricPrompt`](https://developer.android.com/reference/android/hardware/biometrics/BiometricPrompt), and [`apksigner`](https://developer.android.com/tools/apksigner). |
| **Media & transport standards** | ITU-T H.264 video compression and Transport Layer Security ([H.264](https://www.itu.int/rec/T-REC-H.264), [RFC 8446 / TLS 1.3](https://datatracker.ietf.org/doc/html/rfc8446)). |
| **Packaging, CI & compliance** | [Open Build Service](https://openbuildservice.org/), [Debian `3.0 (quilt)`](https://manpages.debian.org/unstable/dpkg-dev/dpkg-source.1.en.html), [`ctest`](https://cmake.org/cmake/help/latest/manual/ctest.1.html), [`lintian`](https://salsa.debian.org/lintian/lintian), [Rootless Docker](https://docs.docker.com/engine/security/rootless/), [REUSE 3.0](https://reuse.software/spec-3.0/), and [SPDX License List](https://spdx.org/licenses/). |

## Verification scope, upstream policy, and license

- **Verification scope:** Virtual Gate 4 acceptance and packaging verification targeted Ubuntu 26.04 LTS (`x86_64`, Wayland) and Android 15 (API 35). X11 desktops and untested OEM Android builds or versions are outside verified claims until explicitly tested.
- **Upstream KDE policy:** Upstream KDE repositories do not accept automated pull requests or AI-authored merge requests. Individual `work/<plugin_id>` branches stay free of packaging overlays, undergo human maintainer review, and are discussed on the [KDE Connect mailing list](https://mail.kde.org/mailman/listinfo/kdeconnect) before manual submission to KDE Invent; see [AGENTS.md](AGENTS.md).
- **License:** Tracked files follow [REUSE 3.0](https://reuse.software/spec-3.0/) backed by [REUSE.toml](REUSE.toml) and [LICENSES/](LICENSES/) (`GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL` for plugin/tool code and docs, `BSD-2-Clause` for build helpers, `Apache-2.0` where upstream Android/Gradle components apply, and `CC0-1.0` for manifests).
