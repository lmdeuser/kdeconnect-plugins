<!-- SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com> -->
<!-- SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL -->

# KDE Connect Research Notes and Diagnostic Capture Probe

This directory contains a preparatory diagnostic probe, not a finished plugin or remote-control solution. It has not been compiled, executed, installed, or submitted to OBS.

## Inspected Upstream Sources

Snapshot revisions fetched from official KDE Invent on 2026-11-15:

- `kdeconnect-android`: `d0534acc70cd9403fb56d78f645f228e21097492`
- `kdeconnect-kde`: `1d05b8e07804066b20ba32dbc7cec875188008c0`

Treat these revisions strictly as point-in-time snapshots, not guarantees of current `master` state.

### Observed Existing Capabilities

- **Android input and accessibility:** `kdeconnect-android` already provides `KdeConnectAccessibilityService`, `plugins/mousereceiver/MouseReceiverPlugin.kt`, `plugins/remotekeyboard/RemoteKeyboardPlugin.kt`, and `plugins/remotekeyboardime/RemoteKeyboardIMEPlugin.kt`. The mouse receiver handles `kdeconnect.mousepad.request` via accessibility actions supporting relative movement, absolute positioning, clicks, and system navigation actions. The absolute coordinate branch checks `x != 0 || y != 0`, so `(0, 0)` is not handled separately; coordinate scaling, rotation, and edge mapping must be verified in live sessions. Keyboard input uses separate Accessibility and IME paths with different key coverage across apps.
- **Desktop input:** `kdeconnect-kde` `plugins/remotekeyboard` already sends keys in `kdeconnect.mousepad.request` format; `shareinputdevicesremote` targets remote input sharing and is not an Android screen viewer. Existing desktop UI components are not verified to send viewer-scaled absolute coordinates.
- **Clipboard and file sharing:** Android `plugins/clipboard/ClipboardPlugin.kt` enables automatic sync on Android 10+ only when `READ_LOGS` is granted and otherwise provides manual send. `plugins/share` implements existing bidirectional file transfer. Both behaviors must be verified against actual user flows on target devices.

## Diagnostic Android Probe (`android-probe/`)

`android-probe/` is a standalone minimal Android project designed to test `MediaProjection` and `AudioPlaybackCapture` on Android 11+ (API 30+):

- Requests `RECORD_AUDIO` and `POST_NOTIFICATIONS` (where applicable), then system `MediaProjection` capture consent.
- Starts a `mediaProjection` foreground service, acquires and immediately closes video frames via `ImageReader`, reads eligible playback PCM samples via `AudioRecord`, and reports counters and peak sample amplitude in an ongoing notification.
- Retains no captured frames or audio samples and declares no `INTERNET` or networking permissions. Denying audio permission still allows video-only frame counting. If notifications are disabled, live status may not appear in the shade; the service still supports explicit stop from the activity or system controls.

### Known Scope Limits

- Non-zero frame counts do not prove frames are non-black (for example, `FLAG_SECURE` windows).
- Zero audio peak amplitude can reflect either silence or restricted capture; compare known capturable playback against protected media apps.
- Orientation rotation, display resize, denied/revoked consent recovery, restart behavior, and OEM quirks are unimplemented and untested in this probe.
- Remote mouse/keyboard input, networking, encoding, desktop playback, and KDE Connect plugin integration are intentionally absent.
- Targets Android Gradle Plugin `8.7.3` and Android SDK `35` without a Gradle wrapper; intended strictly for an authorized remote Android build route, not local Gradle execution.

## Device Verification Checklist (After Authorized Remote Build and Install)

1. Run the probe on Android 11 and a current Android version, grant notifications and audio, then accept screen capture consent. Confirm the ongoing indicator appears and frame counts increment.
2. Play known capturable media and verify non-zero audio peak values, then stop playback. Test a capture-restricted app without attempting any bypass.
3. Deny audio and screen permissions, revoke capture via system stop controls, stop via in-app button, restart a session, and rotate the device. Verify clean resource teardown and zero unprompted recapture.
4. Separately validate existing KDE Connect input, clipboard, and share plugins for coordinate accuracy and disconnect behavior; this probe does not exercise them.

## Next Engineering Milestone

After live probe verification, design authenticated peer-to-peer framing over KDE Connect's paired TLS transport, bounded frame/audio queues, coordinate/rotation negotiation, and connection-tied session lifecycle in `docs/plugins/screen-stream/spec.md`.
