<!-- SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com> -->
<!-- SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL -->

# Feature Specification: Seamless Bluetooth Audio Headset Handoff (`bt_audio_switch`)

## 1. Metadata and Scope

- **Plugin ID:** `bt_audio_switch` (desktop `kdeconnect_btaudioswitch`, Android `BtAudioSwitchPlugin`)
- **Human owner / maintainer:** `Dmitriy Volkov <dennshield@duck.com>`
- **Desktop target:** `https://invent.kde.org/network/kdeconnect-kde.git` (branch `work/bt_audio_switch`, base SHA `69ce9c5c49ce209600873bf10ddf61bfe4dfab1c`)
- **Android target:** `https://invent.kde.org/network/kdeconnect-android.git` (branch `work/bt_audio_switch`, base SHA `d0534acc70cd9403fb56d78f645f228e21097492`)
- **Authorized Android build route:** Local bounded `./gradlew assembleDebug lintDebug testDebugUnitTest` + `apksigner verify`
- **OBS packaging targets:** `home:dwolfF:kdeconnect:staging` -> `home:dwolfF:kdeconnect` (`xUbuntu_26.04`, `x86_64`)

## 2. User-Visible Goal and Platform Matrix

- **Goal:** Automatically switch shared Bluetooth headsets/earbuds (A2DP media / HFP call audio) between the Ubuntu desktop and paired Android phone based on active media playback or incoming/active phone calls, plus expose manual one-click audio claim over D-Bus/UI.
- **Supported desktop environments:** Ubuntu 26.04 LTS Wayland (`x86_64`) with BlueZ (`org.bluez.Device1`) on system D-Bus and `PulseAudioQt` / PipeWire Bluetooth profile routing (`bluez_card.*`, `bluez_output.*`).
- **Supported Android API levels:** Android 11 (API 30) through Android 15+ (API 35+) using `BluetoothAdapter` / `BluetoothA2dp` / `BluetoothHeadset` profile proxies and `AudioManager` active playback/call state observers. Note: multipoint headsets stay paired to both hosts; non-multipoint headsets require BlueZ `Disconnect()` / `Connect()` serialization with a debounce window to prevent ping-pong reconnects.

## 3. Reuse and Architecture Boundaries

- **Existing plugins reused:**
  - `PauseMusicPlugin` (`plugins/pausemusic/`) pattern for `PulseAudioQt::Context` and call-state (`kdeconnect.telephony`) / MPRIS playback monitoring.
  - Existing KDE Connect paired mutual-TLS transport for coordination messages (`kdeconnect.btaudioswitch`).
- **New plugin components (planned Wave 2):**
  - Desktop `plugins/bt_audio_switch/`: `BtAudioSwitchPlugin` coordinating BlueZ `org.bluez.Device1::ConnectProfile` / `DisconnectProfile` and PipeWire default sink selection, paired with per-device MAC allowlist configuration and anti-oscillation hysteresis (`3000 ms`).
  - Android `plugins/btaudioswitch/`: `BtAudioSwitchPlugin.kt` monitoring active audio playback / `AudioManager.MODE_IN_CALL` and executing opt-in Bluetooth profile connect/release for allowlisted headset MACs.
- **Session and capability isolation:** Never alters unconnected Bluetooth devices; operates strictly on paired, user-allowlisted headset MAC addresses configured on both sides.

## 4. Protocol, Security, and Consent Model

- **Packet types and schema:**
  - `kdeconnect.btaudioswitch`: `{"action": "claim"|"release"|"status", "mac": "AA:BB:CC:DD:EE:FF", "reason": "call"|"media"|"manual", "timestampMs": int64}`.
- **Untrusted input validation:**
  - Validate canonical uppercase/lowercase IEEE 802 MAC address regex (`^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$`).
  - Reject any `claim`/`release` request for a MAC address not explicitly present in the user's configured allowlist (`allowedHeadsetMacs`).
  - Enforce a `3000 ms` hysteresis cooldown window between ownership transitions to prevent A2DP/HFP connect/disconnect loops when simultaneous notifications occur.
  - Android `BtAudioSwitchPlugin` honors the shared `<package>_audio_arbiter` flag (`screenstream_audio_active`): automatic `media` playback claims on the phone are suppressed while screencast audio is actively streaming to the PC, while `call` and explicit `manual` claims remain permitted; incoming `release` packets immediately abandon any transient yield audio focus.
- **Permissions and consent:** Android runtime `BLUETOOTH_CONNECT` permission (API 31+, degraded cleanly to disabled state with clear explanation if denied); desktop uses unprivileged BlueZ session calls (user already authorized for desktop Bluetooth session control).

## 5. Acceptance Evidence and Test Plan

- **Build-time tests (`ctest` / JUnit):**
  - Desktop `ctest`: MAC validation, allowlist enforcement rejecting unlisted MACs, 3000 ms hysteresis deduplication, tie-break priority (`call` overrides `media`).
  - Android JUnit: packet parsing, MAC canonicalization, and allowlist enforcement.
- **OBS package checks:** `plugins.toml` verifies `kdeconnect_btaudioswitch.so` in staged `.deb`.
- **Live device matrix:** Pair dual-connected Bluetooth headset to Ubuntu desktop and Android phone -> start media on phone while idle on PC -> verify audio routes cleanly without loop; receive call on phone while watching video on PC -> verify HFP call claim; verify permission-denied graceful disable.
- **Open design questions:** Resolved (single-point yield delay fixed at `300 ms` after remote disconnect before initiating BlueZ `Connect()`).
