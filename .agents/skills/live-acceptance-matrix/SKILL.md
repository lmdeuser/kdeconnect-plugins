---
# SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com>
# SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL
name: live-acceptance-matrix
description: >-
  Execute Gate 4 virtual-first acceptance (Ubuntu 26.04 Wayland VM + AVD) and
  mandatory post-release bug-hunting audit prior to authorized real-hardware
  testing over Wi-Fi, covering MediaProjection consent, AudioPlaybackCapture,
  input boundary isolation, rotation, disconnect/reconnect, explicit stop
  persistence, and existing plugin non-regression.
---

# Gate 4: Virtual-First Acceptance, Post-Release Bug Hunt, and Live Hardware Matrix

Use this skill only after Gate 2 and Gate 3 verify the exact installable artifacts. Always complete Phase 4A (isolated virtual environment + post-release bug hunt) before Phase 4B (real hardware).

## Preconditions

1. Never install released `.deb` packages on the primary host workstation or deploy untested releases to physical Android hardware before Phase 4A passes.
2. Keep VM names, SSH aliases, and AVD names strictly in untracked `.state/local.env`; never commit local infrastructure identifiers or private IPv4 addresses.
3. Record exact Ubuntu version/session (`Wayland`), desktop package revision, AVD system image / Android device model, Android API level, and installed APK revision.
4. Do not treat virtual or single-device verification as proof of universal OEM or Android version compatibility.

## Phase 4A -- Mandatory Virtual Environment (Ubuntu 26.04 VM + AVD) Acceptance

1. Create a pre-test libvirt snapshot of the Ubuntu 26.04 LTS Wayland VM bridged on the LAN, then install the exact released OBS `.deb` packages inside the VM.
2. Boot the local Android Virtual Device (`API 35+`), install the synthesized `work/continuity-stack` `.apk` onto the AVD, and pair the VM and AVD over LAN/IP.
3. Execute every virtual-capable row of the Verification Matrix below (`screenstream`, `clipboard_images`, `otp_autofill`, `continuity_camera` using the emulated camera and VM PipeWire nodes, `remote_auth` via emulated fingerprint/PIN, plus standalone plugin non-regression).

## Mandatory Post-Release Bug-Hunt Checklist (Before Real Hardware)

1. **Static Post-Release Defect Review**: Audit the exact released desktop and Android diffs following `/code-complete` verification and `bug-analysis-review` checks for null/none dereferences, file-descriptor/socket/PipeWire/encoder/cache leaks, concurrency/threading/reconnect races, and boundary or malformed packet inputs.
2. **Runtime Log & Negative-Path Audit on VM + AVD**: Exercise negative paths (denied/revoked permissions, rapid toggle, socket drop/reconnect, oversized payloads, stale tokens) while inspecting VM `journalctl --user`, `kdeconnectd` stderr, PipeWire nodes, and AVD `logcat` for unhandled exceptions, ANRs, StrictMode violations, leaked resources, or stuck states.
3. **Defect Resolution Gate**: Reproduce any confirmed defect, fix the root cause with a regression test, rebuild via OBS/Gradle, and re-verify in VM + AVD before authorizing any real-hardware installation.

## Phase 4B -- Authorized Real-Hardware Verification (Only After Phase 4A Passes)

1. Confirm explicit user authorization and zero open release-blocking defects from Phase 4A.
2. Install the verified `work/continuity-stack` `.apk` strictly to Android `User 0` (`--user 0`, leaving secondary profiles untouched) and run hardware-dependent verification (`bt_audio_switch` with physical Bluetooth A2DP/HFP headsets, OEM camera/`MediaCodec`/`MediaProjection`/`FLAG_SECURE` quirks, and real Wi-Fi handoff).

## Verification Matrix

1. **Pairing and Permissions**: Verify standard KDE Connect pairing, clear explanation of required Android permissions (`RECORD_AUDIO`, `POST_NOTIFICATIONS`, `AccessibilityService`, `CAMERA` / `USE_BIOMETRIC` / `BLUETOOTH_CONNECT` when enabled by active plugins), and graceful fallback when optional permissions are denied.
2. **MediaProjection Single-Session Compliance**: On Android 14+, verify fresh system capture consent is requested per new capture session, projection tokens are never reused, and wake recovery keeps streaming clean.
3. **Restricted Content Handling**: Test protected screen windows (`FLAG_SECURE`) and disallowed playback audio; confirm platform restrictions are honored and explained to the user without bypass attempts.
4. **Control Boundary Isolation**:
   - Verify pointer translation across scaling, letterboxing, and display rotation (including edge/corner coordinates) and stationary long-press hold.
   - Verify input forwards only while the streaming window is focused and an authorized session is active.
   - Verify stopping streaming or revoking consent immediately stops forwarding without breaking standalone `mousereceiver` or `remotekeyboard`.
5. **Session Lifecycle and Reconnection**:
   - Verify unintended Wi-Fi/socket drop stops capture cleanly and triggers bounded reconnect attempts while honoring system confirmations.
   - Verify explicit user stop from either device persists across disconnect/reconnect until the user manually restarts streaming.
6. **Continuity Stack Workflows**:
   - **Drag & Drop & Notification-to-Stream**: Drop local files onto `ViewerWindow` (`SharePlugin`) and trigger notification "Open on Phone Screen" action cleanly.
   - **Image Clipboard**: Send/receive images via `kdeconnect.clipboard.file` without exceeding `20 MiB` or leaking cache entries.
   - **OTP Autofill**: Verify non-silent 2FA code auto-copy marks clipboard secret, excludes phone/year/currency numbers, and clears after timeout if unchanged.
   - **Wave 2 (`continuity_camera`, `remote_auth`, `bt_audio_switch`) when active**: Verify PipeWire virtual camera/mic & scan import, `BiometricPrompt` challenge/replay/TTL checks, and Bluetooth audio handoff hysteresis.
7. **Existing Feature Non-Regression**: Verify standalone `clipboard`, `share`, `mousereceiver`, `notifications`, and `remotekeyboard` continue functioning independently after streaming teardown.

## Reporting

Report verified device/OS pairs, observed pass/fail per matrix item, and all untested Android versions or OEM environments as explicit gaps.
