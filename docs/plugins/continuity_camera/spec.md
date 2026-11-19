<!-- SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com> -->
<!-- SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL -->

# Feature Specification: Phone Camera, Microphone, and Document Scan (`continuity_camera`)

## 1. Metadata and Scope

- **Plugin ID:** `continuity_camera` (desktop `kdeconnect_continuitycamera` + `kdeconnect-continuitycamera-bridge`)
- **Human owner / maintainer:** `Dmitriy Volkov <dennshield@duck.com>`
- **Desktop target:** `https://invent.kde.org/network/kdeconnect-kde.git` (branch `work/continuity_camera`, base SHA `1d05b8e07804066b20ba32dbc7cec875188008c0`)
- **Android target:** `https://invent.kde.org/network/kdeconnect-android.git` (branch `work/continuity_camera`, base SHA `d0534acc70cd9403fb56d78f645f228e21097492`)
- **Authorized Android build route:** Local bounded `./gradlew assembleDebug lintDebug testDebugUnitTest` + `apksigner verify`
- **OBS packaging targets:** `home:dwolfF:kdeconnect:staging` -> `home:dwolfF:kdeconnect` (`xUbuntu_26.04`, `x86_64`)

## 2. User-Visible Goal and Platform Matrix

- **Goal:** Use the paired Android phone over Wi-Fi as a virtual PipeWire webcam and microphone on Linux desktop apps (browsers, Zoom, OBS Studio, Meet) and import single captured photos or multi-page scanned PDFs on demand from the desktop.
- **Supported desktop environments:** Ubuntu 26.04 LTS Wayland (`x86_64`) with PipeWire + GStreamer (`pipewiresink`, `appsrc`, `h264parse`, `avdec_h264`/`vah264dec`). Virtual camera/mic nodes are created purely in userspace via PipeWire without requiring `v4l2loopback` kernel modules.
- **Supported Android API levels:** Android 11 (API 30) through Android 15+ (API 35+) using Camera2 (`android.hardware.camera2`) and `AudioRecord` (`MediaRecorder.AudioSource.VOICE_COMMUNICATION` / `MIC`).

## 3. Reuse and Architecture Boundaries

- **Existing plugins reused:** KDE Connect mutual-TLS `NetworkPacket` streaming payloads (`payloadSize = -1`) for live camera H.264/PCM transport, `SharePlugin` (`kdeconnect.share.request`) or dedicated result transfer for on-demand photo/PDF scan delivery into the user's configured download directory, and `screenstream`'s out-of-process helper pattern isolating GStreamer crashes from `kdeconnectd`.
- **New plugin components (planned Wave 2):**
  - Desktop `plugins/continuity_camera/`: `kdeconnect_continuitycamera.so` (`KdeConnectPlugin` + D-Bus controls `startCamera`, `stopCamera`, `requestPhotoCapture`, `requestDocumentScan`) and `kdeconnect-continuitycamera-bridge` (GStreamer `appsrc -> h264parse -> decodebin -> videoconvert -> pipewiresink` and audio `appsrc -> pipewiresink` with `media.class = Video/Source` / `Audio/Source`).
  - Android `plugins/continuitycamera/`: `ContinuityCameraPlugin.kt`, `ContinuityCameraService.kt` (`FOREGROUND_SERVICE_CAMERA` / `FOREGROUND_SERVICE_MICROPHONE`), `ContinuityCameraCaptureActivity.kt` (photo/multi-page scan PDF generator using `android.graphics.pdf.PdfDocument`).
- **Session and capability isolation:** Disabled/stopped state tears down PipeWire sources and releases Android camera/mic hardware immediately without affecting `screenstream` or `mpris`.

## 4. Protocol, Security, and Consent Model

- **Packet types and schema:**
  - `kdeconnect.continuitycamera.request`: `{"action": "start_stream"|"stop_stream"|"capture_photo"|"scan_document"|"switch_lens", "maxWidth": int, "maxHeight": int, "fps": int, "includeMic": bool}`.
  - `kdeconnect.continuitycamera`: `{"event": "state"|"stream_open"|"capture_result"|"stopped", ...}` reusing fixed binary header framing (`magic`, `kind`, `flags`, bounded `payload_length`, `pts_us`) for live streams and size-capped JPEG/PDF payloads for capture import.
- **Untrusted input validation:** Strictly clamp resolution (`[320..1920]`), fps (`[10..60]`), control JSON (`<= 8192` bytes), and imported JPEG/PDF payload size (`<= 50 MiB`); reject unsupported paths/filenames.
- **Permissions and consent:** Runtime `CAMERA` and optional `RECORD_AUDIO` permissions on Android; foreground service notifications visible whenever camera/mic are active; manual capture requires user confirmation on phone UI before returning a photo or PDF scan.

## 5. Acceptance Evidence and Test Plan

- **Build-time tests:** `ctest` unit tests for protocol state machine, resolution/fps clamping, binary frame parser bounds, and `plugins.toml` ELF/GStreamer checks (`pipewiresink`, `appsrc`, `h264parse`, `avdec_h264`); Android JUnit packet/capture-request validation.
- **OBS package checks:** Verify `kdeconnect_continuitycamera.so`, `kdeconnect-continuitycamera-bridge`, and `gstreamer1.0-pipewire` runtime integration.
- **Live device matrix:** Stream Android camera+mic into `firefox`/`obs`/`pw-top` on Ubuntu 26.04 Wayland, verify lens switch (front/rear), permission-denied fallback, photo capture import, and clean PipeWire teardown on stop/disconnect.
- **Open design questions:** Upstream package dependency check in OBS `xUbuntu_26.04`: verify `gstreamer1.0-pipewire` is installed/recommended cleanly via `debian/control` before Wave 2 coding.
