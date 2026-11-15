<!-- SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com> -->
<!-- SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL -->

# Feature Specification: Android Screen and Audio Streaming

## 1. Metadata and Scope

- **Feature name:** Android screen and audio streaming
- **Proposed plugin ID:** `[to be chosen after checking upstream plugin naming conventions]`
- **Maintainer:** `Dmitriy Volkov <dennshield@duck.com>`
- **Desktop repository and upstream target:** `https://invent.kde.org/network/kdeconnect-kde.git` (observed research snapshot: `master` at `1d05b8e07804066b20ba32dbc7cec875188008c0`; verify current HEAD before implementation)
- **Android repository and upstream target:** `https://invent.kde.org/network/kdeconnect-android.git` (observed research snapshot: `master` at `d0534acc70cd9403fb56d78f645f228e21097492`; verify current HEAD before implementation)
- **Android build route:** Prefer building the Android prototype and app on OBS if compatible SDK, Gradle, dependencies, offline inputs, and recipe are verified; otherwise require a separate authorization decision for an alternative remote Android build route.
- **OBS targets:** Staging `home:dwolfF:kdeconnect:staging` -> Stable `home:dwolfF:kdeconnect` (`xUbuntu_26.04`, `x86_64`).
- **Observed reference test hardware (2026-11-15):** DOOGEE V Max Play, Android 15 (API 35), connected by USB with ADB available; Ubuntu 26.04.1 LTS, Wayland, `x86_64`. This establishes only one device/OS baseline.

## 2. User-Visible Goal and Behavior

View an Android phone screen on an Ubuntu 26.04 Wayland desktop over Wi-Fi through KDE Connect and control the phone from that viewing window while reusing KDE Connect's existing input, clipboard, and file-transfer capabilities:

- **First-release scope:** Stream Android screen video and eligible playback audio to the desktop, and expose access to existing KDE Connect mouse/keyboard control, clipboard sharing, and file transfer without duplicating those stacks.
- **Session start and reconnection lifecycle:**
  - Start viewing/control automatically after device connection when Android permits, honoring required system consent prompts.
  - Stop cleanly from either device and immediately on connection loss.
  - After an unintended connection loss, attempt bounded automatic reconnection while honoring required system confirmations.
  - After an explicit user stop on either device, persist the stopped state across disconnections and reconnections until the user manually restarts streaming.
- **Restrictions and transparency:** Honor Android and per-app restrictions on protected screen (`FLAG_SECURE`) or disallowed audio capture, and clearly explain unavailable content or missing permissions in the UI.
- **Platforms:** Ubuntu 26.04 desktop on Wayland only, and Android 11 (API 30) or later over Wi-Fi.
- **External runtime dependencies:** An external desktop helper/viewer application is acceptable if justified; avoid requiring Android developer mode, USB debugging, or wireless ADB in production unless an unavoidable limitation is documented and approved first.

## 3. Architecture and Component Boundaries

- **New streaming plugin:** Provide desktop and Android counterparts for screen video, eligible playback audio, session state, consent, and negotiated authenticated media transport.
  - Keep video and audio as separable internal components under one streaming plugin.
  - Coordinate screen and audio capture through one Android `MediaProjection` session where supported rather than opening separate projections without a demonstrated platform need.
  - Report audio unavailability visibly without blocking video-only viewing when permitted.
- **Existing control integration:**
  - Reuse KDE Connect's existing `mousereceiver` (`kdeconnect.mousepad.request`), `KdeConnectAccessibilityService`, `remotekeyboard`, and `remotekeyboardime` plugins.
  - Translate viewer pointer coordinates accurately across image scaling, letterboxing, and display rotation (note: upstream `MouseReceiverPlugin.kt` checks `x != 0 || y != 0`, so edge/origin handling must be verified carefully).
  - Verify keyboard focus, shortcuts, IME vs Accessibility trade-offs, and document unsupported behaviors rather than claiming parity or building a parallel remote-input stack.
- **Control-session boundary:**
  - Forward input originating from the streaming viewer only while its authorized streaming session is active and control capability is enabled.
  - Stop forwarding immediately on session stop, disconnection, or consent revocation; never silently resume control prior to a new authorized session.
  - Preserve standalone operation of existing input plugins outside the streaming window.
- **Existing clipboard and file transfer:**
  - Reuse `plugins/clipboard` and `plugins/share` and their established user flows (including manual send on Android 10+ when `READ_LOGS` is absent); keep them independent of the streaming session lifecycle and do not promise unverified automatic sync or extra formats.
- **Availability and teardown independence:**
  - Report disabled/unavailable input, audio, clipboard, or file sharing accurately.
  - Releasing streaming media and capture resources must never break unrelated KDE Connect plugins or require clipboard/file transfer to view the screen.

## 4. Android Feasibility Findings and Implementation Gates

These API-level findings are not proof of a completed or multi-device-tested solution; verify against current Android SDK and device behavior:

- **Screen (`MediaProjection`):** Does not require ADB. Apps targeting Android 14+ (API 34+) must obtain fresh user consent for every capture session and never reuse a projection token. Respect projection callbacks, revocation, and protected windows. See [Media projection](https://developer.android.com/media/grow/media-projection) and [Android 14 MediaProjection rules](https://developer.android.com/about/versions/14/behavior-changes-14#user-consent-required-for-each-mediaprojection-capture-session).
- **Control (`AccessibilityService` / IME):** User-enabled `AccessibilityService` can dispatch supported gestures and global actions without ADB, but is not full mouse/keyboard parity across all apps or protected UIs. Verify `ACTION_SET_TEXT`, multi-touch, and shortcuts explicitly. See [AccessibilityService](https://developer.android.com/reference/android/accessibilityservice/AccessibilityService) and [ACTION_SET_TEXT](https://developer.android.com/reference/android/view/accessibility/AccessibilityNodeInfo.AccessibilityAction#ACTION_SET_TEXT).
- **Audio (`AudioPlaybackCapture`):** Requires `RECORD_AUDIO` runtime permission and `MediaProjection` consent. Captures only eligible usages (`USAGE_MEDIA`, `USAGE_GAME`, `USAGE_UNKNOWN`) from apps allowing capture in the same profile; never promise call, system, or DRM-protected audio. See [Capture video and audio playback](https://developer.android.com/media/platform/av-capture).
- **Foreground service and background activity rules:** Declare and start the `mediaProjection` foreground service type in required API order; respect background activity launch limits when reconnecting and persist explicit user stop across reconnections. See [Background activity restrictions](https://developer.android.com/guide/components/activities/secure-bal) and [Foreground service types](https://developer.android.com/about/versions/14/changes/fgs-types-required).
- **Distribution and restricted settings:** Accessibility and sideloading carry Google Play disclosure requirements and Android Restricted Settings restrictions. See [Google Play Accessibility policy](https://support.google.com/googleplay/android-developer/answer/10964491) and [Android restricted settings](https://support.google.com/android/answer/12623953).
- **Current local diagnostic probe:** `prototype/android-probe/` contains an unbuilt local diagnostic probe for `MediaProjection` and `AudioPlaybackCapture` metrics without networking; see `prototype/README.md`.

## 5. Non-Negotiable Engineering Constraints

1. **No local compilation or C++/Gradle test execution:** Desktop compilation, `ctest`, and `.deb` packaging run exclusively in OBS (`home:dwolfF:kdeconnect:staging` -> `home:dwolfF:kdeconnect`). Android builds require an authorized remote build route.
2. **Strict contour separation:** Upstream branches `work/<plugin_id>` in `kdeconnect-kde` and `kdeconnect-android` contain only upstream-ready code and REUSE headers; Debian/OBS packaging and agent harness files stay out of upstream branches.
3. **Untrusted peer and media boundary:** Never stream media over a new unauthenticated socket. Follow KDE Connect TLS pairing, capability negotiation, and explicit packet size/type/bounds validation; document protocol changes and legacy peer fallbacks.
4. **Upstream KDE Invent contribution compliance:** Follow `kdeconnect-kde/AGENTS.md` and `CONTRIBUTING.md` (REUSE 3.0 SPDX headers, ASCII-only punctuation in upstream code/comments, human-planned design, factual `.state/dossiers/screen-stream.md` handoff, and human-authored MR description and submission on `invent.kde.org`).

## 6. Draft Acceptance Criteria and Open Decisions

Before starting implementation on `work/<plugin_id>`:

- Finalize proposed plugin ID and `NetworkPacket` schema after checking upstream `kdeconnect-kde/plugins/` and `kdeconnect-android` plugin registries.
- Define exact negotiated media transport framing, queue/latency bounds, resize/rotation handling, and reconnection consent UX.
- Confirm authorized remote Android build route (OBS Android recipe feasibility check vs authorized alternative).
- Validate prototype and final builds against a documented multi-device Android 11-15 and Ubuntu 26.04 Wayland test matrix covering denied/revoked consent, protected video/audio, coordinate mapping, disconnect/reconnect, explicit stop persistence, and non-regression of `clipboard`, `share`, `mousereceiver`, and `remotekeyboard`.
