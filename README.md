<!-- SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com> -->
<!-- SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL -->

# KDE Connect Continuity Plugins

This project extends [KDE Connect](https://kdeconnect.kde.org/) into a six-plugin continuity stack between Android (11-15+) and Linux desktops (Ubuntu 26.04 LTS and Gentoo Linux Portage EAPI 8, [Wayland](https://wayland.freedesktop.org/)) over KDE Connect's paired LAN mutual-[TLS](https://datatracker.ietf.org/doc/html/rfc8446) connection. It requires no USB cable, no ADB in daily use, and no third-party screen or webcam server.

This repository (`lmdeuser/kdeconnect-plugins`) does not fork the desktop or Android codebases. Instead, it ships the SHA-256-locked patch series under [patches/](patches/), pinned base and result hashes in [stack.lock.json](stack.lock.json), a hardened reproducer ([tools/reproduce_stack.py](tools/reproduce_stack.py)), the multi-plugin registry ([plugins.toml](plugins.toml)), protocol specifications under `docs/plugins/`, Ubuntu 26.04 packaging overlays under [obs/](obs/), Gentoo Portage EAPI 8 overlay tooling under [gentoo/](gentoo/), and verification tests under [tests/](tests/). Anyone cloning this repository can reconstruct the exact 6-plugin desktop and Android source trees from pinned commits on [kdeconnect-kde](https://invent.kde.org/network/kdeconnect-kde) and [kdeconnect-android](https://invent.kde.org/network/kdeconnect-android) without trusting third-party mirrors.

## Included plugins and enhancements

Six active features defined in [plugins.toml](plugins.toml) make up the continuity stack:

| Plugin | Type | Summary | Specification |
| --- | --- | --- | --- |
| [`screenstream`](#screen-stream-screenstream) | New plugin | Live phone screen and audio viewer with mouse/keyboard control | [screenstream](docs/plugins/screenstream/spec.md) |
| [`continuity_camera`](#continuity-camera-continuity_camera) | New plugin | Phone camera and mic as PipeWire devices plus photo/PDF scans | [continuity_camera](docs/plugins/continuity_camera/spec.md) |
| [`bt_audio_switch`](#bluetooth-audio-switch-bt_audio_switch) | New plugin | Coordinated Bluetooth headset handoff between PC and phone | [bt_audio_switch](docs/plugins/bt_audio_switch/spec.md) |
| [`clipboard_images`](#image-clipboard-clipboard_images) | Enhancement | Bidirectional PNG/JPEG clipboard sync over Wi-Fi | [clipboard_images](docs/plugins/clipboard_images/spec.md) |
| [`otp_autofill`](#otp-auto-copy-otp_autofill) | Enhancement | Automatic 2FA/OTP copy with secret hint and Android 15 fallback | [otp_autofill](docs/plugins/otp_autofill/spec.md) |
| [`remote_auth`](#remote-biometric-auth-remote_auth) | New plugin | Phone fingerprint/credential approval for desktop requests | [remote_auth](docs/plugins/remote_auth/spec.md) |

### Screen Stream (`screenstream`)

Streams an Android 11+ display over [H.264](https://www.itu.int/rec/T-REC-H.264) ([MediaProjection](https://developer.android.com/media/grow/media-projection) + [MediaCodec](https://developer.android.com/reference/android/media/MediaCodec)) and eligible playback audio (`pcm_s16le_48000_stereo` 48 kHz stereo with automatic mono fallback via [AudioPlaybackCapture](https://developer.android.com/media/platform/av-capture), prioritized audio muxing, and a continuous sample-clock PTS GStreamer jitter queue) to an isolated [Qt 6 Widgets](https://doc.qt.io/qt-6/qtwidgets-index.html) and [GStreamer](https://gstreamer.freedesktop.org/) viewer (`kdeconnect-screenstream-viewer`).

- **Input & sharing:** Reuses KDE Connect's mouse, long-press, scroll, keyboard, clipboard, drag-and-drop file sharing (up to 32 files via `SharePlugin`), and "Open on Phone Screen" notification action.
- **Wayland window clamping:** Keeps the viewer within monitor work-area bounds across portrait/landscape phone rotations via [KWin Scripting](https://develop.kde.org/docs/plasma/kwin/api/) on Plasma Wayland.
- **Privacy controls:** Honors [`FLAG_SECURE`](https://developer.android.com/reference/android/view/WindowManager.LayoutParams#FLAG_SECURE) windows, requires Android's system capture consent per session, and remembers an explicit stop across reconnects.
- **Zero-config Bluetooth & call handoff:** Switches seamlessly between `Audio: Screencast` and `Audio: Bluetooth` without restarting video. In `Audio: Screencast`, the desktop auto-discovers the paired phone across BlueZ `/org/bluez/hci*` adapters, keeps Bluetooth A2DP (`0000110a`) connected (so Android keeps the phone's loudspeaker silent), and mutes only the PC's PipeWire `bluez_input.<MAC>*` A2DP stream so 48 kHz stereo Wi-Fi audio plays cleanly without doubling or phone-speaker leak. During active phone/VoIP calls (`audioReason = "call_active"`), screencast audio pauses and desktop restores `bluez_input` + `audio-gateway` (HFP-AG `0000111f`) so your PC wired headset mic/earpieces handle the call, resuming Wi-Fi stereo automatically when the call ends and restoring unmuted Bluetooth on stop.

### Continuity Camera (`continuity_camera`)

Exposes the paired phone's camera and microphone to Linux desktop apps over Wi-Fi, alongside on-demand photo capture and multi-page document scanning.

- **Userspace PipeWire webcam & mic:** `kdeconnect-continuitycamera-bridge` normalizes H.264 video to `video/x-raw,format=I420,framerate=30/1` with `node.driver=true` so WebRTC PipeWire clients (Telegram Desktop, Chrome, Firefox, Zoom, OBS) enumerate `KDE Connect Camera (<Phone>)` natively, automatically tees `YUY2` into any writable [`v4l2loopback`](https://github.com/v4l2loopback/v4l2loopback) `/dev/videoN` device for legacy V4L2 apps when present (falling back cleanly to userspace PipeWire-only), and buffers 48 kHz 20 ms `MIC` audio through a sample-clock PTS PipeWire jitter queue (`node.latency = 960/48000`).
- **Camera2 & PDF capture:** Streams [Camera2](https://developer.android.com/media/camera/camera2) video and optional [AudioRecord](https://developer.android.com/reference/android/media/AudioRecord) audio with front/rear lens switching, or builds multi-page scanned PDFs with Android [`PdfDocument`](https://developer.android.com/reference/android/graphics/pdf/PdfDocument).

### Bluetooth Audio Switch (`bt_audio_switch`)

Coordinates [Bluetooth A2DP and HFP](https://www.bluetooth.com/specifications/specs/) headset ownership between Ubuntu and Android for explicitly allowlisted MAC addresses.

- **Handoff with hysteresis:** Uses [BlueZ (`org.bluez.Device1`)](https://git.kernel.org/pub/scm/bluetooth/bluez.git/tree/doc/org.bluez.Device.rst) with automatic BlueZ device path resolution across `/org/bluez/hci*` adapters, `DisconnectProfile` + full link `Disconnect()` on release so single-point headsets reconnect to the phone, and PipeWire sink routing on Ubuntu alongside Android Bluetooth profile proxies, guarded by a 3000 ms cooldown against ping-pong reconnects.
- **Screencast audio arbitration:** Suppresses automatic phone `media` headset claims while Screen Stream audio is active on the PC so sound never plays on both routes at once.

### Image Clipboard (`clipboard_images`)

Adds bidirectional image synchronization over `kdeconnect.clipboard.file`.

- **Android transfer:** Caps received images at 20 MiB, prunes its local cache to 5 recent files, and exposes content URIs safely via [`FileProvider`](https://developer.android.com/reference/androidx/core/content/FileProvider).
- **Wayland paste integration:** Populates both `QImage` pixels and file URLs in `QMimeData` through [`KSystemClipboard`](https://invent.kde.org/frameworks/kguiaddons/-/blob/master/src/systemclipboard/ksystemclipboard.h) so images paste cleanly into browsers and editors.

### OTP Auto-Copy (`otp_autofill`)

Extracts 4-8 digit two-factor authentication codes from incoming phone notifications and places them on the Ubuntu Wayland clipboard.

- **Noise filter & secret hint:** Supports English and Russian OTP phrasing while rejecting years, currency amounts, and phone numbers; attaches `x-kde-passwordManagerHint=secret` for [Klipper](https://invent.kde.org/plasma/plasma-workspace/-/blob/master/klipper/klipper.cpp) and clears after 60 s if unchanged.
- **Android 15 SMS fallback:** When [Android 15 sensitive-notification redaction](https://developer.android.com/about/versions/15/behavior-changes-all#sensitive-notifications) hides OTPs from third-party [`NotificationListenerService`](https://developer.android.com/reference/android/service/notification/NotificationListenerService) apps lacking [`RECEIVE_SENSITIVE_NOTIFICATIONS`](https://developer.android.com/reference/android/Manifest.permission#RECEIVE_SENSITIVE_NOTIFICATIONS), `SMSPlugin` pushes fresh incoming inbox deltas (`<= 16` messages, `<= 120 s`) if `READ_SMS` is granted.

### Remote Biometric Auth (`remote_auth`)

Displays an interactive Android [`BiometricPrompt`](https://developer.android.com/reference/android/hardware/biometrics/BiometricPrompt) confirmation dialog on the phone for desktop D-Bus approval requests.

- **Replay & burst protection:** Uses single-use 256-bit CSPRNG nonces, a 30 s TTL, and a 5 requests/minute cap on its [D-Bus](https://dbus.freedesktop.org/doc/dbus-specification.html) interface (`org.kde.kdeconnect.device.remoteauth`).
- **Opt-in session unlock:** Optionally unlocks an idle desktop session over [`org.freedesktop.login1`](https://www.freedesktop.org/software/systemd/man/latest/org.freedesktop.login1.html) when `unlockAllowed` is enabled (`false` by default). System-wide PAM or polkit replacement is out of scope.

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

## Reproduce and build from source

Anyone with a clone of this repository can verify every patch and reconstruct both codebases directly from pinned official KDE Invent commits. Set a disk-backed `TMPDIR` (never `tmpfs`) before running reproduction commands:

```bash
export TMPDIR="${TMPDIR:-$PWD/.state/tmp}"
mkdir -p "$TMPDIR"
python3 tools/reproduce_stack.py verify-lock
```

### Desktop packages on Ubuntu 26.04 (`x86_64`)

Reproduce the pinned desktop tree (`repro/continuity-stack`) and emit deterministic [`3.0 (quilt)`](https://manpages.debian.org/unstable/dpkg-dev/dpkg-source.1.en.html) Debian source bundles for `kdeconnect` and `libei` `1.6.0` (verified via SHA-256 against upstream archives), then build `.deb` packages locally or inside an Ubuntu 26.04 container without needing maintainer OBS credentials:

```bash
python3 tools/reproduce_stack.py checkout \
  --target desktop \
  --out ../repro/kdeconnect-kde \
  --emit-deb-source ../repro/kdeconnect-deb-src
python3 tools/obs_packaging.py build-libei --output-dir ../repro/libei-deb-src

# Inside an Ubuntu 26.04 build environment (install libei 1.6.0 build deps first):
dpkg-source -x ../repro/libei-deb-src/libei_*.dsc ../repro/build-libei
dpkg-source -x ../repro/kdeconnect-deb-src/kdeconnect_*.dsc ../repro/build-kdeconnect
```

From `../repro/build-libei` and then `../repro/build-kdeconnect`, install build dependencies (`sudo apt build-dep .`) and build binary packages with `dpkg-buildpackage -us -uc`. See [obs/kdeconnect/debian/](obs/kdeconnect/debian/) for exact Debian build flags and `ctest` / `lintian` hooks.

### Desktop packages on Gentoo Linux (Portage EAPI 8, Wayland)

Generate a verified `kdeconnect-continuity` Portage EAPI 8 overlay (with `metadata/layout.conf`, `profiles/repo_name`, `kde-misc/kdeconnect/kdeconnect-27.03.70_p1.ebuild`, and SHA-256-verified `files/patches/`) directly from [stack.lock.json](stack.lock.json):

```bash
python3 tools/reproduce_stack.py checkout \
  --target desktop \
  --out ../repro/kdeconnect-kde \
  --emit-gentoo-overlay ../repro/gentoo-overlay

# Or emit only the Portage overlay without cloning upstream Git history:
python3 tools/gentoo_packaging.py --output-dir ../repro/gentoo-overlay
```

See [gentoo/README.md](gentoo/README.md) for `repos.conf`, `package.mask`, USE flags (`bluetooth`, `+pulseaudio`, optional `telephony`, `X`), `Manifest` handling (`--distfile` / `--preserve-manifest-from`), and post-install runtime verification using `python3 tools/plugin_registry.py gentoo-checks`.

### Android APK (Android 11-15+)

Desktop OBS builds do not compile Android. Reproduce the pinned `kdeconnect-android` tree, compile the side-by-side debug APK (`Debug KDE Connect`, application ID `org.kde.kdeconnect_tp.debug`), run unit tests and lint, and verify the APK signature with [apksigner](https://developer.android.com/tools/apksigner):

```bash
python3 tools/reproduce_stack.py checkout \
  --target android \
  --out ../repro/kdeconnect-android
cd ../repro/kdeconnect-android
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

### Reproducibility and security model

- **What is byte-for-bit reproducible:** Upstream `base_sha` checkout -> per-patch SHA-256 verification -> deterministic Git `HEAD^{tree}` and `HEAD` commits (fixed author/committer metadata, verified against [stack.lock.json](stack.lock.json)) -> byte-for-byte identical Debian `3.0 (quilt)` source bundles (`.orig.tar.xz`, `.debian.tar.xz`, `.dsc`, `manifest.json`). Upstream `libei` archives (`1.6.0`, `munit`, and `1.5.0-3.debian.tar.xz`) are verified over HTTPS with pinned SHA-256 digests.
- **Honest scope limits:** Bit-for-bit binary `.deb` equivalence across independent build machines (`reprotest`) is not yet certified. Android `assembleDebug` APKs are signed with each developer's local debug keystore, and upstream `kdeconnect-android` does not ship Gradle dependency lockfiles.
- **Repository attack-surface defenses:** Only exact HTTPS URLs on `invent.kde.org` are allowed; Git executes with hooks, symlinks, submodules, LFS filters, and `ext::` protocols disabled (`fsckObjects=true`); symlinks and unreferenced files under [patches/](patches/) are rejected; public tree scans block symlinks in Git index and worktree; zero `.github/workflows/` runners exist; and maintainer OBS promotion stays isolated from untrusted external inputs.

## Using the main features

### Screen stream and mutually exclusive audio (PC vs Bluetooth)

1. Pair **Debug KDE Connect** with Ubuntu and run **Start Screen Stream** from KDE Connect on the desktop.
2. Choose **Entire screen** in Android's [MediaProjection](https://developer.android.com/media/grow/media-projection) dialog and tap **Start**.
3. Switch audio routing live without stopping video or re-prompting capture consent:
   - **Desktop viewer toolbar:** toggle between **`Audio: Screencast`** and **`Audio: Bluetooth`**.
   - **Android Screen stream plugin:** tap **`Route audio to Bluetooth`** or **`Route audio to PC screencast`**.
   - **Automatic Bluetooth & call handoff:** zero-config discovery keeps phone-to-PC A2DP (`0000110a`) connected (silencing the phone's internal loudspeaker) while muting the PC's PipeWire `bluez_input.<MAC>*` loopback stream during 48 kHz stereo `Audio: Screencast`; during active phone/VoIP calls (`audioReason = "call_active"`), screencast audio pauses and desktop unmutes `bluez_input` + ensures PipeWire `audio-gateway` (HFP-AG `0000111f`) is active so PC headset microphones and headphones work cleanly for calls; toggling to `Audio: Bluetooth`, closing the viewer, or stopping restores unmuted Bluetooth automatically. Coordinates [`AudioDeviceCallback`](https://developer.android.com/reference/android/media/AudioDeviceCallback) and `BtAudioSwitchPlugin` so playback never plays on both routes at once.

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

## Complete UI Button and Control Reference

Every interactive button, toggle, notification action, and plugin setting exposed on Ubuntu desktop and Android behaves as documented below.

### Desktop Screen Stream Viewer (`kdeconnect-screenstream-viewer`)

All toolbar buttons appear left-to-right in `ViewerWindow`:

| Control | Icon / Shortcut | Exact Behavior |
| --- | --- | --- |
| **`Stop`** | Stop square (`media-playback-stop`) | Stops the local socket, invokes D-Bus `stopStream()` (persisting `explicitStop = true`), unmutes `bluez_input` and releases any claimed Bluetooth headsets, and closes the viewer window. |
| **`Audio: Screencast`** / **`Audio: Bluetooth`** | Speaker (`audio-speakers`) / Bluetooth (`network-bluetooth`, checkable) | **Unchecked (`Audio: Screencast`):** streams 48 kHz stereo PCM over Wi-Fi (`AudioPlaybackCapture`), keeps the phone's BlueZ A2DP (`0000110a`) connected (so the phone loudspeaker stays silent), and mutes the PC's PipeWire `bluez_input.<MAC>*` A2DP stream so audio plays only via Wi-Fi on the PC. **Checked (`Audio: Bluetooth`):** stops Wi-Fi PCM playback (`PcmPlayer`), unmutes `bluez_input.<MAC>*`, restores `audio-gateway` (`0000110a` + `0000111f`), and releases claimed headsets so sound routes purely over Bluetooth (telephone speaker stays silent). **During a VoIP/phone call (`call_active`):** Wi-Fi screencast audio pauses automatically in both positions and your PC wired headset mic/headphones run over Bluetooth HFP-AG until the call ends. |
| **`Mute Audio`** | Muted speaker (`audio-volume-muted`, checkable) | Mutes local `PcmPlayer` output inside the viewer window without stopping `AudioPlaybackCapture` or unmuting the phone loudspeaker. Disabled automatically while **`Audio: Bluetooth`** is active. |
| **`Refresh Keyframe`** | Circular arrows (`view-refresh`) | Sends `requestKeyframe` (`action = "request_keyframe"`) to force immediate SPS/PPS + IDR recovery in Android `ScreenEncoder`. |
| **`Wake Screen`** | Display (`preferences-desktop-display`) | Sends `wakeScreen` (`action = "wake"`) to pulse Android's wake lock (1.5 s) and reinitialize capture after display sleep. |
| **`Fit Window`** | Fit to window (`zoom-fit-best`) | Resizes the window to match the current phone stream aspect ratio and clamps it inside the Wayland monitor work area. |
| **`Send Clipboard`** | Paste (`edit-paste`) | Calls `ClipboardPlugin::sendClipboard()` to push desktop text or images to Android. |
| **`Share File...`** | Document send (`document-send`) | Opens a file picker and transfers selected files to Android via `SharePlugin::shareUrls()` (also triggered by Drag & Drop onto the video canvas, up to 32 files). |
| **`Back`** | Left arrow (`go-previous`) | Sends Android Back (`backclick`) via `RemoteControlPlugin`. Enabled only while streaming and Android `AccessibilityService` is active. |
| **`Home`** | Home (`go-home`) | Sends Android Home (`middleclick`) via `RemoteControlPlugin`. |
| **`Recents`** | Overview grid (`view-grid`) | Sends Android Recents (`forwardclick`) via `RemoteControlPlugin`. |

Canvas interactions inside `ScreenCanvasWidget` (active only while streaming and `AccessibilityService` is enabled): left click sends mapped tap (`singleclick`); holding left mouse `>= 180 ms` or dragging `>= 8 px` sends `singlehold`, continuous pointer moves, and `singlerelease` (auto-released on window focus loss); right click sends Back; middle click sends Home; mouse wheel scrolls; typing sends `RemoteKeyboardPlugin::sendQKeyEvent`; dropping files calls `SharePlugin::shareUrls()`.

### Desktop `kdeconnect-app`, Notifications, and Plugin Settings

- **Device Page Actions (`app/qml/DevicePage.qml`):**
  - **`View device screen` / `Stop screen stream`**: Starts or stops `screenstream` over D-Bus and surfaces any immediate startup error banner.
  - **`Wake device screen`**: Wakes the paired Android display and forces encoder recovery.
  - **`Use phone as webcam` / `Stop phone webcam`**: Starts or stops `continuity_camera` PipeWire streaming (`KDE Connect Camera (<Phone>)` / `KDE Connect Mic (<Phone>)`).
  - **`Switch webcam to front camera` / `Switch webcam to rear camera`** (visible while webcam active): Switches active phone lens live via `ContinuityCameraPlugin::toggleLens()`.
  - **`Take photo with phone`**: Triggers Android capture UI (`MODE_PHOTO`) to shoot and transfer a still JPEG.
  - **`Scan document with phone`**: Triggers multi-page Android document scanner (`MODE_SCAN`) returning a consolidated PDF via `SharePlugin`.
- **Plasma Tray Applet (Plasmoid, `org.kde.kdeconnect`):**
  - **Device header quick toggle (`ScreenStream.qml`):** Icon button alongside battery/signal indicators toggles **`View device screen`** / **`Stop screen stream`**.
  - **Overflow menu (`DeviceDelegate.qml`):** Exposes **`View device screen` / `Stop screen stream`**, **`Wake device screen`** (`screenstream`), **`Use phone as webcam` / `Stop phone webcam`**, **`Switch webcam to front camera` / `Switch webcam to rear camera`** (when webcam active), **`Take photo with phone`**, **`Scan document with phone`** (`continuity_camera`), plus **`Claim headset audio`** and **`Release headset audio`** (`bt_audio_switch`, shown when `allowedHeadsetMacs` is non-empty).
- **Plasma Notification Action:**
  - **`Open on Phone Screen`** (shown on synced Android notifications with `hasContentIntent == true`): Triggers the phone's `contentIntent` and either wakes an active `screenstream` session or launches the viewer.
- **Screen Stream Plugin Settings (`kdeconnect_screenstream_config.qml`):**
  - **`Auto-start screen streaming when this device connects`** (`autoStart`, default `false`): Auto-starts streaming on connect (clears `explicitStop` when enabled).
  - **`Stream playback audio when supported`** (`requestAudio`, default `true`): Requests playback audio capture alongside video.
  - **`Automatically switch audio from Bluetooth to screencast`** (`autoRouteAudio`, default `true`): Keeps phone A2DP silent on PC via `bluez_input` mute during `Audio: Screencast` and restores unmuted Bluetooth on stop/call.
  - **`Maximum frame rate (FPS)`** (`maxFps`, range `5..60`, default `60`) & **`Maximum video bitrate (kbps)`** (`maxBitrateKbps`, range `500..20000`, step `500`, default `8000`).

### Android `Debug KDE Connect` Buttons, Activities, and Settings

- **Screen Stream (`ScreenStreamPlugin`):**
  - Device buttons: **`Start screen stream`** (launches `ScreenStreamPermissionActivity` `MediaProjection` consent), **`Stop screen stream`** (stops capture, releases BlueZ/headset claim, persists `explicitStop = true`), and live toggle **`Route audio to Bluetooth`** / **`Route audio to PC screencast`**.
  - Notifications: ongoing foreground notification exposes **`Stop screen stream`**; background request notification opens system capture consent.
  - Settings (`screenstreamplugin_preferences.xml`): **`Auto-start when connected`** (`false`), **`Include playback audio`** (`true`), **`Auto-switch Bluetooth audio to screencast`** (`true`), **`Keep screen awake while streaming`** (`true`, 4 h safety cap).
- **Continuity Camera (`ContinuityCameraPlugin` & `ContinuityCameraCaptureActivity`):**
  - Device buttons: **`Start webcam stream`** (`1280x720@30fps` + `MIC` audio), **`Stop camera stream`**, **`Switch camera lens`** (toggles front/back live), **`Capture photo / scan`**, **`Scan document (PDF)`**.
  - Capture screen (`ContinuityCameraCaptureActivity`): **`Capture Photo`** (photo mode), **`Add Scan Page (%d)`** & **`Send PDF (%d)`** (scan mode), and **`Cancel`** (closes activity without sending).
- **Bluetooth Audio Switch (`BtAudioSwitchPlugin`):**
  - Device button: **`Claim headset audio`** (requests local headset ownership for connected allowlisted headsets, honoring 3000 ms hysteresis).
  - Settings (`btaudioswitchplugin_preferences.xml`): **`Allowlisted headset MAC addresses`** (`allowedHeadsetMacs`, comma-separated `AA:BB:CC:DD:EE:FF`).
- **Clipboard (`ClipboardPlugin`):**
  - Device button: **`Send clipboard`** (when background clipboard access is restricted on Android 10+, reads text or image URI from clipboard and pushes text or `kdeconnect.clipboard.file` up to 20 MiB to desktop).

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
| [stack.lock.json](stack.lock.json) | Cryptographic lockfile pinning upstream SHAs, patch SHA-256 digests, and expected trees/commits |
| [patches/](patches/) | Exported per-plugin Git patch series for `desktop` and `android` targets |
| [docs/WORKFLOW.md](docs/WORKFLOW.md) | Detailed Gate 0 to Gate 5 acceptance criteria and handoff checklist |
| `docs/plugins/` | Per-plugin specifications linked in the plugin table above |
| [obs/](obs/) | Ubuntu 26.04 Debian overlays, OBS project/package XMLs, `*.prjconf`, and [verifier.Dockerfile](obs/verifier.Dockerfile) |
| [gentoo/](gentoo/) | Gentoo Linux Portage EAPI 8 (`kdeconnect-continuity`) metadata, profile mask notes, and installation guide |
| [tools/](tools/) | Lockfile reproducer, patch exporter, stack synthesis, OBS controller/packaging, Docker verifier, and hygiene checks |
| [tests/](tests/) | Python `unittest` suite covering reproducer integrity, registry schema, packaging, runner safety gates, and tree hygiene |
| [prototype/](prototype/) | Initial standalone Android `MediaProjection` probe; see [prototype/README.md](prototype/README.md) |
| [LICENSES/](LICENSES/) | License texts referenced by [REUSE.toml](REUSE.toml) |

Run Gate 1 checks from the repository root with a disk-backed `TMPDIR` (no desktop C++ toolchain is installed or required here):

```bash
python3 tools/plugin_registry.py validate
python3 tools/reproduce_stack.py verify-lock
python3 tools/check_public_tree.py --worktree
python3 tools/check_kde_compliance.py
python3 -m unittest discover -s tests -v
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

- **Verification scope:** Virtual Gate 4 acceptance and packaging verification targeted Ubuntu 26.04 LTS (`x86_64`, Wayland) and Android 15 (API 35). Gentoo Linux (Portage EAPI 8, Wayland) overlay emission and `gentoo-checks` runtime probes are derived from the same verified patch lockfile; live Gentoo `emerge` verification runs on target Gentoo hosts. X11 desktops and untested OEM Android builds or versions are outside verified claims until explicitly tested.
- **Upstream KDE policy:** Upstream KDE repositories do not accept automated pull requests or AI-authored merge requests. Individual `work/<plugin_id>` branches stay free of packaging overlays, undergo human maintainer review, and are discussed on the [KDE Connect mailing list](https://mail.kde.org/mailman/listinfo/kdeconnect) before manual submission to KDE Invent; see [AGENTS.md](AGENTS.md).
- **License:** Tracked files follow [REUSE 3.0](https://reuse.software/spec-3.0/) backed by [REUSE.toml](REUSE.toml) and [LICENSES/](LICENSES/) (`GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL` for plugin/tool code and docs, `BSD-2-Clause` for build helpers, `Apache-2.0` where upstream Android/Gradle components apply, and `CC0-1.0` for manifests).
