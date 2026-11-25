<!-- SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com> -->
<!-- SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL -->

# Feature Specification: Bidirectional Image Clipboard (`clipboard_images`)

## 1. Metadata and Scope

- **Plugin ID:** `clipboard_images` (`upstream_enhancement` to `kdeconnect_clipboard` / `ClipboardPlugin`)
- **Human owner / maintainer:** `Dmitriy Volkov <dennshield@duck.com>`
- **Desktop target:** `https://invent.kde.org/network/kdeconnect-kde.git` (branch `work/clipboard_images`, base SHA `27568643beb5395d2fbb81416a32b18bb5a33c13`)
- **Android target:** `https://invent.kde.org/network/kdeconnect-android.git` (branch `work/clipboard_images`, base SHA `6a6f7cd6cf7b3d576f653b3cfeba6eaad438faaa`)
- **Authorized Android build route:** Local bounded `./gradlew assembleDebug lintDebug testDebugUnitTest` + `apksigner verify`
- **OBS packaging targets:** `home:dwolfF:kdeconnect:staging` -> `home:dwolfF:kdeconnect` (`xUbuntu_26.04`, `x86_64`)

## 2. User-Visible Goal and Platform Matrix

- **Goal:** Enable bidirectional clipboard image synchronization between Android 11+ and Ubuntu 26.04 Wayland using KDE Connect's existing `kdeconnect.clipboard.file` packet type.
  - Desktop -> Android: copying an image (`QImage`) or local image file on Linux arrives on Android via `kdeconnect.clipboard.file` and is placed into Android's `ClipboardManager` as a content URI backed by `androidx.core.content.FileProvider`.
  - Android -> Desktop: copying or manually pushing an image URI from Android's clipboard sends `kdeconnect.clipboard.file` to desktop; `ClipboardListener::setFile()` populates both `imageData` (`QImage`) and `urls` in `QMimeData` so pasting works seamlessly in browsers, editors, and image apps on Wayland.
- **Supported desktop environments:** Ubuntu 26.04 LTS Wayland (`x86_64`) via `KSystemClipboard`.
- **Supported Android API levels:** Android 11 (API 30) through Android 15+ (API 35+). Manual "Send Clipboard" works without `READ_LOGS`; automatic background sync continues to depend on upstream `ClipboardListener` permissions.

## 3. Reuse and Architecture Boundaries

- **Existing plugins reused:**
  - Desktop `plugins/clipboard/` (`ClipboardPlugin`, `ClipboardListener`) already implements `kdeconnect.clipboard.file` send/receive.
  - Android `plugins/clipboard/` (`ClipboardPlugin`, `ClipboardListener`) and existing `FileProvider` authority (`${applicationId}.fileprovider`).
- **New / modified plugin components:**
  - **Android (`work/clipboard_images`):** `ClipboardImageHelper.kt` (filename sanitization, MIME/extension mapping, bounded stream copy, cache pruning), `ClipboardListener.kt` (`setImageUri`), `ClipboardPlugin.kt` (`kdeconnect.clipboard.file` receive/send on worker thread), `res/xml/fileprovider_paths.xml` (`clipboard_images/` `<cache-path>` entry), `ClipboardImageHelperTest.kt`.
  - **Desktop (`work/clipboard_images`):** `plugins/clipboard/clipboardlistener.cpp` (`ClipboardListener::setFile()` setting `mime->setImageData(img)` when the downloaded file loads cleanly as `QImage`).
- **Session and capability isolation:** `ClipboardPlugin` still advertises `kdeconnect.clipboard` and `kdeconnect.clipboard.connect` for legacy peers and falls back to plain text when no image URI is present.

## 4. Protocol, Security, and Consent Model

- **Packet types and schema:**
  - `kdeconnect.clipboard.file` (`{"filename": string}` + payload stream <= 20 MiB on Android; desktop honors `maxClipboardFileSizeMB` config).
  - Android advertises `kdeconnect.clipboard.file` in both `supportedPacketTypes` and `outgoingPacketTypes`.
- **Untrusted input validation:**
  - Reject missing/null payloads, missing `filename`, directory separators (`/` or `\`), parent traversal (`..`), NUL bytes, non-image extensions, and payloads exceeding `20 * 1024 * 1024` bytes while streaming (deleting partial temp files on abort).
  - Restrict incoming files strictly to `context.cacheDir/clipboard_images/` and prune old cached images beyond the 5 most recent entries.
- **Permissions and consent:**
  - No new runtime permissions required; shared URIs are granted read-only temporary access via Android's non-exported `FileProvider`.

## 5. Acceptance Evidence and Test Plan

- **Build-time tests:**
  - Android JUnit (`ClipboardImageHelperTest`): verifies safe filename normalization, path-traversal and disallowed-extension rejection, MIME mapping, and cache pruning.
  - Existing desktop `ctest` (`pluginloadtest`, `sendfiletest`) and OBS staging build pass with 0 errors.
- **OBS package checks:** `plugins.toml` verifies `kdeconnect_clipboard.so` in staged `.deb`.
- **Live device matrix:** Copy screenshot on Ubuntu Wayland -> paste image into an Android app; copy/share image on Android -> send clipboard -> paste into LibreOffice/browser on Ubuntu Wayland; test oversized (`> 20 MiB`) and traversal-filename packet rejection.
- **Open design questions:** None blocking Wave 1 implementation.
