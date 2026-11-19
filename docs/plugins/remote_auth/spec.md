<!-- SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com> -->
<!-- SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL -->

# Feature Specification: Phone Biometric Confirmation and Session Unlock (`remote_auth`)

## 1. Metadata and Scope

- **Plugin ID:** `remote_auth` (desktop `kdeconnect_remoteauth`, Android `RemoteAuthPlugin`)
- **Human owner / maintainer:** `Dmitriy Volkov <dennshield@duck.com>`
- **Desktop target:** `https://invent.kde.org/network/kdeconnect-kde.git` (branch `work/remote_auth`, base SHA `1d05b8e07804066b20ba32dbc7cec875188008c0`)
- **Android target:** `https://invent.kde.org/network/kdeconnect-android.git` (branch `work/remote_auth`, base SHA `d0534acc70cd9403fb56d78f645f228e21097492`)
- **Authorized Android build route:** Local bounded `./gradlew assembleDebug lintDebug testDebugUnitTest` + `apksigner verify`
- **OBS packaging targets:** `home:dwolfF:kdeconnect:staging` -> `home:dwolfF:kdeconnect` (`xUbuntu_26.04`, `x86_64`)

## 2. User-Visible Goal and Platform Matrix

- **Goal:** Allow the desktop user to confirm privileged desktop operations (e.g., unlock an idle desktop session or respond to a custom D-Bus/app verification prompt) via an explicit Android `BiometricPrompt` confirmation showing the desktop host name and action summary.
- **Supported desktop environments:** Ubuntu 26.04 LTS Wayland (`x86_64`) with `org.freedesktop.login1.Session` (`Unlock`) and session D-Bus API (`org.kde.kdeconnect.device.remoteauth`). Note: PAM/polkit system-wide replacement is out of scope without a separate root PAM module and is documented explicitly as unverified/excluded unless a follow-up PAM helper is approved.
- **Supported Android API levels:** Android 11 (API 30) through Android 15+ (API 35+) using `androidx.biometric.BiometricPrompt` (`BIOMETRIC_STRONG | DEVICE_CREDENTIAL`).

## 3. Reuse and Architecture Boundaries

- **Existing plugins reused:** Existing `LockDevicePlugin` (`plugins/lockdevice/`) pattern for `org.freedesktop.login1` interaction over system D-Bus and KDE Connect's paired mutual-TLS link (`Device::sendPacket` over TLS).
- **New plugin components (planned Wave 2):**
  - Desktop `plugins/remote_auth/`: `RemoteAuthPlugin` managing cryptographically random challenges, TTL timers, rate-limiting, D-Bus methods (`requestApproval`, `requestSessionUnlock`), and opt-in `login1` unlock (`unlockAllowed` setting, default `false`).
  - Android `plugins/remoteauth/`: `RemoteAuthPlugin.kt`, `RemoteAuthPromptActivity.kt` (`BiometricPrompt` + Keystore/HMAC challenge response).
- **Session and capability isolation:** When `remote_auth` is disabled or `unlockAllowed` is `false`, `login1` session unlock packets are ignored regardless of TLS pairing state.

## 4. Protocol, Security, and Consent Model

- **Packet types and schema:**
  - `kdeconnect.remoteauth.request` (Desktop -> Android): `{"challengeId": string, "nonce": base64 (32 bytes), "purpose": "confirm"|"unlock", "summary": string, "ttlMs": int}`.
  - `kdeconnect.remoteauth` (Android -> Desktop): `{"challengeId": string, "nonce": base64, "approved": bool, "authType": "biometric"|"credential"|"denied"}`.
- **Untrusted input validation and replay defenses:**
  - Desktop generates a 256-bit CSPRNG nonce (`QRandomGenerator::system()`), stores active pending challenge (max 1 pending per device or bounded map <= 4), enforces strict `30000 ms` expiration TTL, single-use consumption (`take()` before evaluation), and exponential cooldown after failed/rejected attempts (`max 5 / minute`).
  - Reject any response with mismatched `challengeId`, mismatched `nonce`, expired timestamp, or `approved != true`.
  - Android refuses background silent approval and always requires an interactive `BiometricPrompt` interaction showing device name and action summary.

## 5. Acceptance Evidence and Test Plan

- **Build-time tests (`ctest` / JUnit):**
  - Desktop `ctest`: valid nonce acceptance, replay of consumed nonce rejected, expired challenge (`> 30 s`) rejected, forged/mismatched nonce rejected, rate limiter blocks bursts, opt-in gate blocks `unlock` when `unlockAllowed == false`.
  - Android JUnit: challenge schema clamping and rejection of oversized/malformed summaries.
- **OBS package checks:** `plugins.toml` verifies `kdeconnect_remoteauth.so` in staged `.deb`.
- **Live device matrix:** Lock Ubuntu Wayland desktop -> trigger `BiometricPrompt` on paired Android 15 phone -> verify explicit approval unlocks session only when enabled, and verify denial/timeout leaves session locked.
- **Open design questions:** Confirm `androidx.biometric:biometric` artifact availability in existing `/staging/gradle` cache or upstream `gradle/libs.versions.toml` before Wave 2 coding.
