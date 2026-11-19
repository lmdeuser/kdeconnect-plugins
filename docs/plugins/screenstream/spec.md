<!-- SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com> -->
<!-- SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL -->

# Feature Specification: Android Screen and Playback Audio Streaming (`screenstream`)

## 1. Metadata and Scope

- **Plugin ID:** `screenstream`
  - Desktop CMake plugin target: `kdeconnect_screenstream`
  - Desktop D-Bus interface: `org.kde.kdeconnect.device.screenstream`
  - Desktop viewer helper binary: `kdeconnect-screenstream-viewer`
  - Android plugin class: `org.kde.kdeconnect.plugins.screenstream.ScreenStreamPlugin`
  - Android foreground capture service: `org.kde.kdeconnect.plugins.screenstream.ScreenStreamService`
  - Android consent trampoline activity: `org.kde.kdeconnect.plugins.screenstream.ScreenStreamPermissionActivity`
- **Human owner / maintainer:** `Dmitriy Volkov <dennshield@duck.com>`
- **Desktop target:** `https://invent.kde.org/network/kdeconnect-kde.git` (base branch `master`, verified snapshot SHA `1d05b8e07804066b20ba32dbc7cec875188008c0`)
- **Android target:** `https://invent.kde.org/network/kdeconnect-android.git` (base branch `master`, verified snapshot SHA `d0534acc70cd9403fb56d78f645f228e21097492`)
- **Authorized Android build and verification route:** Local Android Studio (bundled JBR) and resource-bounded `./gradlew assembleDebug lintDebug testDebugUnitTest` against Android SDK Platform 37 (`--no-daemon --max-workers=2`, `-Xmx2048m -XX:MaxMetaspaceSize=640m`, disk-backed `TMPDIR` and `GRADLE_USER_HOME`, no duplicate caches), supplemented by zero-dependency OBS `kdeconnect-android-probe` diagnostic APK builds. Zero local desktop C++/CMake compilation on workstations or runners.
- **OBS packaging targets:** `home:dwolfF:kdeconnect:staging` -> `home:dwolfF:kdeconnect` (`xUbuntu_26.04`, `x86_64`)
- **Observed reference test hardware (2026-11-15):** DOOGEE V Max Play, Android 15 (API 35), ADB over USB; Ubuntu 26.04.1 LTS desktop on Wayland, `x86_64`. Establishes one device/OS baseline only.

## 2. User-Visible Goal and Platform Matrix

- **Goal:** View an Android device screen on an Ubuntu 26.04 Wayland desktop over Wi-Fi through KDE Connect, hear eligible playback audio alongside video, and control the phone from the desktop viewer window while reusing KDE Connect's existing input, clipboard, and file-transfer plugins:
  - Stream Android screen video and eligible playback audio over KDE Connect's authenticated LAN TLS transport.
  - Keep video viewing functional and report audio unavailability visibly when audio capture permission is denied, audio focus/usage is ineligible, or an app disallows `AudioPlaybackCapture`.
  - Provide viewer toolbar actions for explicit start/stop, audio mute/unmute, keyframe refresh, wake screen, clipboard push, and file sharing without merging those stacks into `screenstream`, plus Drag & Drop of local files onto `ViewerWindow` forwarded through `SharePlugin` (`shareUrls`, up to 32 files per drop) and an "Open on Phone Screen" desktop notification action when `hasContentIntent` is advertised.
  - Support stationary left-button long-press hold (`180 ms` hold delay, `< 8 px` drag deadzone, release on `focusOutEvent`) alongside drag/click/scroll/keyboard forwarding.
  - Support auto-start on device connect (when enabled in plugin config) honoring required Android consent dialogs, bounded automatic reconnection after unintended Wi-Fi/socket loss, and persistent explicit-stop state across disconnections and reconnections until the user manually restarts streaming.
- **Supported desktop environments:**
  - Ubuntu 26.04 LTS desktop running Wayland (`x86_64`). X11 compatibility is out of first-release verification scope and not claimed until explicitly tested.
  - Distinct from existing `virtualmonitor` (`plugins/virtualmonitor/`), which exposes a virtual desktop monitor to remote devices over `krdpserver`; `screenstream` receives Android display and audio on the desktop.
- **Supported Android API levels:**
  - Android 11 (API 30) through Android 15+ (API 35+, matching upstream `kdeconnect-android` `compileSdk`/`targetSdk`).
  - No production dependency on Android developer mode, USB debugging (`adb`), or wireless ADB.
  - Honor protected surfaces (`WindowManager.LayoutParams.FLAG_SECURE`) and per-app `ALLOW_CAPTURE_BY_NONE` audio restrictions without bypass attempts.

## 3. Reuse and Architecture Boundaries

- **Existing KDE Connect transport reused (zero `core/` edits):**
  - `kdeconnect-android` `NetworkPacket` (`payload = NetworkPacket.Payload(inputStream, -1L)`) and `LanLink.sendPackage` stream arbitrary-length payloads over a dynamic TCP `ServerSocket` upgraded with mutual TLS (`SslHelper.convertToSslSocket(..., isDeviceTrusted = true, clientMode = false)`).
  - `kdeconnect-kde` `LanDeviceLink::dataReceived()` connects a pinned-certificate `QSslSocket` via `LanLinkProvider::configureSslSocket(socket.data(), deviceId(), true)` and delivers `NetworkPacket` with `payloadSize() == -1` to `KdeConnectPlugin::receivePacket()`.
- **Existing plugins reused via D-Bus / native flows (zero duplicate stacks):**
  - Pointer/navigation input: `RemoteControlPlugin` (`org.kde.kdeconnect.device.remotecontrol::sendCommand(QVariantMap)`) -> `kdeconnect.mousepad.request` -> Android `MouseReceiverPlugin` + `KdeConnectAccessibilityService`. Because upstream `MouseReceiverPlugin.kt` guards absolute positioning with `if (x != 0 || y != 0)` and interprets missing `(x, y)` on non-action packets as pointer hide, the desktop viewer clamps mapped Android coordinates to `[1, displayWidth - 1]` x `[1, displayHeight - 1]`.
  - Keyboard input: `RemoteKeyboardPlugin` (`org.kde.kdeconnect.device.remotekeyboard::sendKeyPress(...)`, `sendQKeyEvent(...)`) -> `kdeconnect.mousepad.request` -> Android `RemoteKeyboardIMEPlugin` or `RemoteKeyboardPlugin` (`AccessibilityNodeInfo` / `ACTION_SET_TEXT`), documenting IME vs Accessibility focus limits accurately.
  - Clipboard, file transfer, & notifications: `ClipboardPlugin` (`org.kde.kdeconnect.device.clipboard::sendClipboard()`), `SharePlugin` (`org.kde.kdeconnect.device.share::shareUrls(QStringList)` for both toolbar `Share File...` and canvas/window Drag & Drop), and `NotificationsPlugin` (`org.kde.kdeconnect.device.notifications::openNotification(QString)` -> `kdeconnect.notification.action` with `{"key": ..., "open": true}` + D-Bus `wakeScreen()` / `startStream()`). Preserves Android 10+ manual clipboard send flow when `READ_LOGS` is unavailable.
- **New plugin components:**
  - **Desktop (`kdeconnect-kde`, branch `work/screenstream`):**
    - `plugins/screenstream/` (`kdeconnect_screenstream.json`, `screenstreamplugin.{h,cpp}`, `CMakeLists.txt`): KdeConnectPlugin lifecycle, D-Bus methods (`startStream`, `stopStream`, `wakeScreen`, `requestKeyframe`, `isStreaming`, `state`), stream socket bridging, and process supervision of `kdeconnect-screenstream-viewer` with `QProcess::ForwardedChannels` over an owner-only local socket / passed fd.
    - `plugins/screenstream/viewer/` (`kdeconnect-screenstream-viewer`): dedicated Qt6 (`Widgets`/`Gui`/Wayland + Drag & Drop onto `ViewerWindow`) + GStreamer (`appsrc ! h264parse config-interval=-1 ! decodebin (vah264dec/avdec_h264) ! ...`) + audio playback (`QAudioSink` or `pulsesink`/`autoaudiosink` for `pcm_s16le` 48 kHz mono without `Qt6Multimedia` DSO coupling in the viewer) helper process isolated from `kdeconnectd`, keeping decoder crashes from terminating the KDE Connect daemon.
    - Minimal integration edits outside `plugins/screenstream/`: `plugins/CMakeLists.txt`, `dbusinterfaces/CMakeLists.txt` (`geninterface`), `app/qml/DevicePage.qml` (launch/stop/wake actions), and `plugins/notifications/` (`hasContentIntent` -> `Open on Phone Screen` action triggering Android `contentIntent` + desktop `screenstream` D-Bus wake/start).
  - **Android (`kdeconnect-android`, branch `work/screenstream`):**
    - `plugins/screenstream/` (`ScreenStreamPlugin.kt`, `ScreenStreamService.kt`, `ScreenStreamPermissionActivity.kt`, `StreamMuxer.kt`, `ScreenEncoder.kt`, `AudioPlaybackCapturer.kt`, manifest entries for `FOREGROUND_SERVICE_MEDIA_PROJECTION` and non-exported service).
    - Keep video encoding and audio capture as independent internal modules coordinated under a single `MediaProjection` instance where permitted.
- **Control-session and lifecycle isolation:**
  - The desktop viewer forwards mouse/wheel/keyboard D-Bus calls only while `screenstream` session state is `STREAMING` and remote control permission/capability is enabled on both peers.
  - Input forwarding stops immediately on local stop, peer `stopped` packet, TLS stream EOF, or consent revocation, and never resumes automatically prior to a new authorized streaming session.
  - Unrelated plugins (`mousereceiver`, `remotekeyboard`, `clipboard`, `share`) remain independently usable when `screenstream` is stopped, paused, or disabled.

## 4. Protocol, Security, and Consent Model

- **Capabilities advertised in metadata:**
  - Desktop `X-KdeConnect-SupportedPacketType`: `["kdeconnect.screenstream"]`; `X-KdeConnect-OutgoingPacketType`: `["kdeconnect.screenstream.request"]`.
  - Android `supportedPacketTypes`: `kdeconnect.screenstream.request`; `outgoingPacketTypes`: `kdeconnect.screenstream`.
  - Legacy peers not advertising `kdeconnect.screenstream` in identity capabilities show "Screen streaming not supported by peer" without emitting packets or errors.
- **Control packet schemas (JSON body <= 8192 bytes, no arbitrary URLs/paths):**
  - `kdeconnect.screenstream.request` (Desktop -> Android):
    - `{"action": "query_state"}`
    - `{"action": "start", "maxWidth": int [320..3840], "maxHeight": int [320..3840], "maxFps": int [5..60], "maxBitrateKbps": int [500..20000], "requestAudio": bool}`
    - `{"action": "wake"}`
    - `{"action": "request_keyframe"}`
    - `{"action": "stop", "explicitStop": bool}`
  - `kdeconnect.screenstream` (Android -> Desktop):
    - `{"event": "state", "state": "idle|awaiting_consent|streaming|stopped|error", "explicitStop": bool, "audioAvailable": bool, "audioReason": string, "accessibilityEnabled": bool, "displayWidth": int, "displayHeight": int, "rotation": int (0|90|180|270)}`
    - `{"event": "stream_open", "videoCodec": "h264-annexb", "audioCodec": "pcm_s16le_48000_mono"|"none", "width": int, "height": int, "rotation": int, "fps": int}` plus `payloadSize = -1` and KDE Connect `payloadTransferInfo`.
    - `{"event": "geometry_changed", "width": int, "height": int, "rotation": int}`
    - `{"event": "stopped", "explicitStop": bool, "reason": string}`
- **Multiplexed binary framing inside `payloadSize = -1` TLS payload:**
  - All frames start with a fixed 16-byte big-endian header:
    - Bytes `0..1`: `magic = 0x4B53` (`"KS"`)
    - Byte `2`: `kind` (`0x01` = H.264 Annex-B SPS/PPS codec config, `0x02` = H.264 Annex-B access unit, `0x03` = `pcm_s16le` 48 kHz mono audio chunk, `0x04` = heartbeat / explicit EOS)
    - Byte `3`: `flags` (`bit 0 = keyframe/IDR`, `bit 1 = protected_content_active`, other bits reserved `0`)
    - Bytes `4..7`: `uint32 payload_length` (strictly validated: `<= 4_194_304` bytes for `kind` `0x01`/`0x02`, `<= 65_536` bytes for `kind == 0x03`, `== 0` for `kind == 0x04`)
    - Bytes `8..15`: `uint64 pts_us` (monotonic capture timestamp in microseconds)
  - Malformed headers (`magic != 0x4B53`, unknown `kind`, or out-of-range `payload_length`) immediately close the TLS payload socket, emit an error state, and reset the session cleanly without partial buffer over-reads.
- **Queues, latency bounds, rotation, and wake recovery handling:**
  - Android encoder outputs H.264 baseline/main low-latency Annex-B frames (`BITRATE_MODE_VBR`, `KEY_I_FRAME_INTERVAL = 2` s, `KEY_MAX_B_FRAMES = 0` when low-latency profile is accepted, `KEY_REPEAT_PREVIOUS_FRAME_AFTER = 1_000_000` us, target cap negotiated up to `1920x1080` or device native aspect-scaled within `maxWidth x maxHeight` at `<= 60` fps). Fallback/config timestamps use `System.nanoTime() / 1000L` (`CLOCK_MONOTONIC`) to stay strictly monotonic in the same clock domain as `MediaCodec` surface PTS.
  - Sender muxer queue is bounded (maximum 8 video frames / 16 audio chunks). On video queue pressure, non-IDR frames are dropped and `MediaCodec.PARAMETER_KEY_REQUEST_SYNC_FRAME` is triggered when stream resumes.
  - On display rotation, configuration change, or wake recovery (`Display.STATE_OFF -> Display.STATE_ON`, `Intent.ACTION_SCREEN_ON`, `Intent.ACTION_USER_PRESENT`, or incoming `wake` control request while streaming), `ScreenStreamService` detaches `VirtualDisplay.setSurface(null)`, stops the previous `ScreenEncoder`, starts a fresh `ScreenEncoder` at updated even dimensions (`w`, `h`), calls `VirtualDisplay.resize(...)` if geometry changed, and attaches the new surface (never letting old and new encoders interleave frames in `StreamMuxerQueue`). Rotation also emits `geometry_changed`.
  - Desktop GStreamer pipeline sets `h264parse` `config-interval = -1` so SPS/PPS precedes every IDR access unit, forwards viewer stdout/stderr via `QProcess::ForwardedChannels`, maintains a bounded jitter queue (<= 2 video frames pending decode; audio buffered <= 80 ms), and computes pointer coordinates from actual rendered video rect (`QRectF`) mapped linearly onto `[1, displayWidth - 1]` x `[1, displayHeight - 1]`.
- **Reconnection and persistent explicit stop state machine:**
  - Explicit stop on either side sets persistent `explicitStop = true` (stored per paired `deviceId` in Android plugin preferences and reflected in `kdeconnect.screenstream` `state`).
  - While `explicitStop == true`, auto-start on reconnect is suppressed until the user explicitly triggers Start on desktop or Android.
  - When `explicitStop == false` and auto-start is enabled, unintended connection loss cancels capture immediately and triggers bounded exponential reconnection attempts (e.g., 2s, 4s, 8s, max 3 attempts) upon link restoration; on Android 14+ (API 34+), each fresh `MediaProjection` session requires fresh user consent if a system prompt is mandated, honoring background activity launch limits via a non-intrusive high-priority/ongoing notification action when app is backgrounded.
- **Permissions and consent:**
  - Android runtime permissions: `FOREGROUND_SERVICE`, `FOREGROUND_SERVICE_MEDIA_PROJECTION`, `POST_NOTIFICATIONS` (API 33+, degraded gracefully if denied while keeping capture controls), `RECORD_AUDIO` (optional; denial disables audio only with visible banner/state reason).
  - Android 14/15 ordering invariant verified against `prototype/android-probe`: validate user `MediaProjection` consent intent first, call `startForeground(..., ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION)` only after consent is present, and immediately call `getMediaProjection(...)`.
  - On Android 14+ (API 34+), `ScreenStreamPermissionActivity` passes `MediaProjectionConfig.createConfigForDefaultDisplay()` so the system dialog defaults to capturing the entire screen rather than a single app. Incoming `start` and `wake` control requests pulse a bounded screen-wake lock (1.5 s auto-release, 3 s max timeout) to wake the display without flashing or ringing, and active streaming optionally holds a bounded keep-screen-awake lock (default enabled, 4 h safety cap, released immediately on stop, consent revocation, or service destruction).

## 5. Acceptance Evidence and Test Plan

- **Build-time tests (`ctest` / OBS & Android unit tests):**
  - Desktop `ctest` suite (run inside OBS `xUbuntu_26.04` `xvfb-run -a dbus-run-session`):
    - Frame mux/demux parser unit tests: valid SPS/PPS/IDR/PCM frames, truncated header, invalid magic, oversized `payload_length > 4 MiB` video / `> 64 KiB` audio, unknown `kind`, integer overflow boundaries.
    - Coordinate mapper unit tests: portrait, landscape, 90/180/270 rotations, letterbox/pillarbox scaling, `(0, 0)` clamped to `(1, 1)` and `(maxW, maxH)` clamped to `(width - 1, height - 1)`.
    - Session state machine unit tests: explicit stop persistence across disconnect/reconnect, bounded reconnect suppression when `explicitStop == true`, input-gate blocking outside `STREAMING`.
  - Android verification (`./gradlew assembleDebug lintDebug testDebugUnitTest` + `apksigner verify`): JUnit 4 packet validation, bounded queue drop/IDR recovery, `explicitStop` state persistence, and zero `screenstream` Android Lint errors/warnings.
- **Upstream pre-discussion gate (`AGENTS.md` Section 6):**
  - Before proposing `work/screenstream` upstream, maintainer verifies whether a new protocol family (`kdeconnect.screenstream`) + desktop helper binary requires pre-discussion on KDE Invent / KDE Connect mailing list.
- **OBS package checks (Gates 2 & 3):**
  - Staging (`home:dwolfF:kdeconnect:staging`) compiles `kdeconnect`, executes `ctest_ran > 0` with `ctest_failed == 0`, passes `lintian --fail-on error` (`lintian_errors == 0`), verifies `.deb` contents (`kdeconnect_screenstream.so`, `kdeconnect-screenstream-viewer`), and passes isolated container install/uninstall check before any promotion.
  - `kdeconnect-android-probe` builds diagnostic APK remotely in OBS and passes `apksigner verify --verbose`.
- **Live device matrix (Gate 4, authorized Ubuntu 26.04 Wayland + Android 11+ Wi-Fi):**
  - MediaProjection consent grant/deny/system-revoke; audio grant/deny + capturable vs disallowed playback apps; `FLAG_SECURE` transparency; coordinate/drag/scroll/keyboard accuracy across portrait & landscape rotations; disconnect/reconnect recovery; explicit stop persistence across reconnection; non-regression of `clipboard`, `share`, `mousereceiver`, `remotekeyboard`, and standalone D-Bus plugins.
