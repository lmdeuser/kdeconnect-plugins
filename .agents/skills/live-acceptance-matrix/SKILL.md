---
# SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com>
# SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL
name: live-acceptance-matrix
description: >-
  Execute Gate 4 authorized live testing on Ubuntu 26.04 Wayland and Android 11+
  over Wi-Fi, covering MediaProjection consent, AudioPlaybackCapture, input boundary
  isolation, rotation, disconnect/reconnect, explicit stop persistence, and
  existing plugin non-regression.
---

# Gate 4: Live Wayland and Android Hardware Acceptance Matrix

Use this skill only after Gate 2 and Gate 3 verify the exact installable artifacts and when authorized to test on real hardware.

## Preconditions

1. Confirm explicit authorization before installing staged `.deb` packages or Android `.apk` builds on test devices.
2. Record exact Ubuntu version/session (`Wayland`), desktop package revision, Android device model, Android API level, and installed APK revision.
3. Do not treat single-device verification as proof of universal OEM or Android version compatibility.

## Verification Matrix

1. **Pairing and Permissions**: Verify standard KDE Connect pairing, clear explanation of required Android permissions (`RECORD_AUDIO`, `POST_NOTIFICATIONS`, `AccessibilityService` when enabled), and graceful video-only fallback when optional permissions are denied.
2. **MediaProjection Single-Session Compliance**: On Android 14+, verify fresh system capture consent is requested per new capture session and projection tokens are never reused.
3. **Restricted Content Handling**: Test protected screen windows (`FLAG_SECURE`) and disallowed playback audio; confirm platform restrictions are honored and explained to the user without bypass attempts.
4. **Control Boundary Isolation**:
   - Verify pointer translation across scaling, letterboxing, and display rotation (including edge/corner coordinates).
   - Verify input forwards only while the streaming window is focused and an authorized session is active.
   - Verify stopping streaming or revoking consent immediately stops forwarding without breaking standalone `mousereceiver` or `remotekeyboard`.
5. **Session Lifecycle and Reconnection**:
   - Verify unintended Wi-Fi/socket drop stops capture cleanly and triggers bounded reconnect attempts while honoring system confirmations.
   - Verify explicit user stop from either device persists across disconnect/reconnect until the user manually restarts streaming.
6. **Existing Feature Non-Regression**: Verify standalone `clipboard`, `share`, `mousereceiver`, and `remotekeyboard` continue functioning independently after streaming teardown.

## Reporting

Report verified device/OS pairs, observed pass/fail per matrix item, and all untested Android versions or OEM environments as explicit gaps.
