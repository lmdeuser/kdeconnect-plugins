<!-- SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com> -->
<!-- SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL -->

# Feature Specification: Automatic 2FA/OTP Notification Clipboard Copy (`otp_autofill`)

## 1. Metadata and Scope

- **Plugin ID:** `otp_autofill` (`upstream_enhancement` to desktop `kdeconnect_notifications`)
- **Human owner / maintainer:** `Dmitriy Volkov <dennshield@duck.com>`
- **Desktop target:** `https://invent.kde.org/network/kdeconnect-kde.git` (branch `work/otp_autofill`, base SHA `69ce9c5c49ce209600873bf10ddf61bfe4dfab1c`)
- **Android target:** None required (reuses Android `NotificationsPlugin` notification forwarding without extra Android permissions such as `READ_SMS`).
- **OBS packaging targets:** `home:dwolfF:kdeconnect:staging` -> `home:dwolfF:kdeconnect` (`xUbuntu_26.04`, `x86_64`)

## 2. User-Visible Goal and Platform Matrix

- **Goal:** Automatically copy one-time verification codes (2FA / OTP) from incoming non-silent phone notifications to the desktop clipboard without requiring a manual button click, mark them as secret for clipboard managers, and clear them after 60 seconds if still active.
- **Supported desktop environments:** Ubuntu 26.04 LTS Wayland (`x86_64`) via `KSystemClipboard`.
- **Supported Android API levels:** All Android versions supported by `NotificationsPlugin` (no Android changes required).

## 3. Reuse and Architecture Boundaries

- **Existing plugins reused:**
  - `NotificationsPlugin` (`plugins/notifications/notificationsplugin.{h,cpp}`, `notification.{h,cpp}`) and its existing `copyAuthCodeIfPresent` / `KSystemClipboard` integration.
  - Per-device plugin config (`autoCopyAuthCode`, default `true`).
- **New / modified plugin components (`work/otp_autofill`):**
  - `plugins/notifications/otpcodeextractor.{h,cpp}`: pure, deterministic extraction helper unifying action-button code extraction and title/body keyword + token matching across English and Russian notifications.
  - `plugins/notifications/notification.h` (`actions()` getter), `notificationsplugin.{h,cpp}` (`maybeAutoCopyAuthCode`, deduplication state, 60 s conditional clear), `plugins/notifications/CMakeLists.txt`.
  - `tests/otpextractortest.cpp` registered in `tests/CMakeLists.txt`.
- **Session and capability isolation:** Runs strictly inside `kdeconnect_notifications` and honors `autoCopyAuthCode` config toggle and upstream notification privacy filters on Android.

## 4. Protocol, Security, and Consent Model

- **Packet types and schema:** No wire protocol changes (`kdeconnect.notification` only).
- **Untrusted input validation:**
  - Enforce bounded title/text lengths before regex evaluation.
  - Strip U+2063 invisible separators used by Android Messages.
  - Require either an explicit digit-bearing notification action or OTP keyword context (`code`, `otp`, `verification`, `verify`, `passcode`, `2fa`, `one-time`, `pin`, `auth`, or Russian equivalents encoded as ASCII `\uXXXX` escapes) before extracting a `4..8` digit token.
  - Reject standalone 4-digit years (`1990..2039`), currency-suffixed/prefixed amounts (`RUB`, `USD`, `EUR`, `$`, ruble sign), and phone-number prefixes (`+7...`).
  - Ignore `silent` notifications (such as initial connect dump of pre-existing notifications) and avoid re-copying the same `(notificationId, code)` pair on notification updates.
  - Attach `x-kde-passwordManagerHint = "secret"` so desktop clipboard history tools avoid persisting OTPs, and clear after `60000 ms` only if clipboard text still equals that OTP.

## 5. Acceptance Evidence and Test Plan

- **Build-time tests (`ctest`):**
  - `otpextractortest`: positive action extraction with U+2063 separators, English OTP, Russian OTP, hyphenated `123-456` normalization; negative rejection of phone numbers, years, currency amounts, non-OTP numbers, and oversized inputs.
  - Existing `notificationstest` continues passing under `QT_QPA_PLATFORM=offscreen`.
- **OBS package checks:** `plugins.toml` verifies `kdeconnect_notifications.so` in staged `.deb`.
- **Live device matrix:** Receive real SMS/app 2FA notification on Android -> paste code directly on Ubuntu Wayland (`Ctrl+V`) -> verify conditional auto-clear after 60 s and no false positives on bank balance/order updates.
- **Open design questions:** None blocking Wave 1 implementation.
