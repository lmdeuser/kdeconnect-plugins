<!-- SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com> -->
<!-- SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL -->

# Feature Specification: Automatic 2FA/OTP Notification Clipboard Copy (`otp_autofill`)

## 1. Metadata and Scope

- **Plugin ID:** `otp_autofill` (`upstream_enhancement` to desktop `kdeconnect_notifications` and Android `SMSPlugin`)
- **Human owner / maintainer:** `Dmitriy Volkov <dennshield@duck.com>`
- **Desktop target:** `https://invent.kde.org/network/kdeconnect-kde.git` (branch `work/otp_autofill`, base SHA `27568643beb5395d2fbb81416a32b18bb5a33c13`)
- **Android target:** `https://invent.kde.org/network/kdeconnect-android.git` (branch `work/otp_autofill`, base SHA `6a6f7cd6cf7b3d576f653b3cfeba6eaad438faaa`)
- **OBS packaging targets:** `home:dwolfF:kdeconnect:staging` -> `home:dwolfF:kdeconnect` (`xUbuntu_26.04`, `x86_64`)

## 2. User-Visible Goal and Platform Matrix

- **Goal:** Automatically copy one-time verification codes (2FA / OTP) from incoming non-silent phone notifications to the desktop clipboard without requiring a manual button click, mark them as secret for clipboard managers, clear them after 60 seconds if still active, and fall back to real-time incoming inbox SMS packets when Android 15+ (API 35+) redacts sensitive OTP notifications to `"Sensitive notification content hidden"`.
- **Supported desktop environments:** Ubuntu 26.04 LTS Wayland (`x86_64`) via `KSystemClipboard`.
- **Supported Android API levels:** All Android versions supported by `NotificationsPlugin`, plus Android 15+ (`API 35+`) sensitive-notification fallback via `SMSPlugin` when the optional `READ_SMS` permission is already granted by the user.

## 3. Reuse and Architecture Boundaries

- **Existing plugins reused:**
  - `NotificationsPlugin` (`plugins/notifications/notificationsplugin.{h,cpp}`, `notification.{h,cpp}`, `kdeconnect_notifications.json`) and its existing `copyAuthCodeIfPresent` / `KSystemClipboard` integration.
  - Per-device plugin config (`autoCopyAuthCode`, default `true`).
  - Android `SMSPlugin` (`plugins/sms/SMSPlugin.kt`, `SMSHelper.kt`) reusing `kdeconnect.sms.messages` (`type == 1` inbox messages) without requesting any new Android manifest permissions or sending historical message dumps.
- **New / modified plugin components (`work/otp_autofill`):**
  - Desktop (`kdeconnect-kde`):
    - `plugins/notifications/kdeconnect_notifications.json`: subscribes `kdeconnect_notifications` to `kdeconnect.sms.messages` so `Device::privateReceivedPacket` delivers real-time incoming SMS packets alongside `kdeconnect_sms`.
    - `plugins/notifications/otpcodeextractor.{h,cpp}`: pure, deterministic extraction helper unifying action-button code extraction, title/body keyword + token matching across English and Russian notifications, and `extractFromSmsBatch` (`batch <= 16`, `type == 1` inbox-only, `120 s` freshness window).
    - `plugins/notifications/notification.h` (`actions()` getter), `notificationsplugin.{h,cpp}` (`maybeAutoCopyAuthCode`, `maybeAutoCopyAuthCodeFromSms`, shared 15-second cross-channel deduplication state, 60 s conditional clear), `plugins/notifications/CMakeLists.txt`.
    - `tests/otpextractortest.cpp` registered in `tests/CMakeLists.txt`.
  - Android (`kdeconnect-android`):
    - `src/main/java/org/kde/kdeconnect/plugins/sms/SmsRealtimePushHelper.kt` + `SMSPlugin.kt`: offloads update triggers onto `MessageLooper.handler` (never running SQLite on the UI thread), uses strictly-newer `date > previousTimestamp` bounds (`queryFloorTimestamp`) with a safe startup baseline on empty DBs, and pushes incoming inbox SMS deltas automatically when `READ_SMS` is granted without triggering bulk history sync.
    - `src/test/java/org/kde/kdeconnect/plugins/sms/SmsRealtimePushHelperTest.kt`.
- **Session and capability isolation:** Runs strictly inside `kdeconnect_notifications` / `SMSPlugin`, honors `autoCopyAuthCode` config toggle, and avoids historical SMS flooding by rejecting batches larger than 16 messages or timestamps older than 120 seconds.

## 4. Protocol, Security, and Consent Model

- **Packet types and schema:** No new wire protocol types invented (`kdeconnect.notification` and existing `kdeconnect.sms.messages` only).
- **Untrusted input validation:**
  - Enforce bounded title/text lengths before regex evaluation and bounded SMS batch checks (`1..16` messages, `type == 1` inbox-only, timestamp within `120000 ms` of wall clock).
  - Strip U+2063 invisible separators used by Android Messages.
  - Require either an explicit digit-bearing notification action or OTP keyword context (`code`, `otp`, `verification`, `verify`, `passcode`, `2fa`, `one-time`, `pin`, `auth`, or Russian equivalents encoded as ASCII `\uXXXX` escapes) before extracting a `4..8` digit token.
  - Reject standalone 4-digit years (`1990..2039`), currency-suffixed/prefixed amounts (`RUB`, `USD`, `EUR`, `$`, ruble sign), and phone-number prefixes (`+7...`).
  - Ignore `silent` notifications (such as initial connect dump of pre-existing notifications) and avoid re-copying duplicate codes across notification updates and notification/SMS channels within a 15-second window.
  - Attach `x-kde-passwordManagerHint = "secret"` so desktop clipboard history tools avoid persisting OTPs, and clear after `60000 ms` only if clipboard text still equals that OTP.

## 5. Acceptance Evidence and Test Plan

- **Build-time tests (`ctest` and JUnit):**
  - `otpextractortest`: positive action extraction with U+2063 separators, English OTP, Russian OTP, hyphenated `123-456` normalization, fresh inbox SMS batch fallback; negative rejection of phone numbers, years, currency amounts, non-OTP numbers, sent (`type == 2`) SMS, stale (`> 120 s`) SMS, conversation sync batches (`> 16`), and oversized inputs.
  - Existing `notificationstest` continues passing under `QT_QPA_PLATFORM=offscreen`.
  - Android `SmsRealtimePushHelperTest`: empty DB baseline, non-duplication on `date <= previousTimestamp`, inbox-only unsolicited push vs requested full push.
- **OBS package checks:** `plugins.toml` verifies `kdeconnect_notifications.so` in staged `.deb`.
- **Live device matrix:** Receive real SMS/app 2FA notification (and redacted Android 15 SMS) on Android -> paste code directly on Ubuntu Wayland (`Ctrl+V`) -> verify secret hint, cross-channel deduplication, conditional auto-clear after 60 s, and no false positives on bank balance/order updates.
- **Open design questions:** None.
