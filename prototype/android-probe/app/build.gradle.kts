// SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com>
// SPDX-License-Identifier: BSD-2-Clause

plugins { id("com.android.application") }

android {
    namespace = "org.example.kdeconnectprobe"
    compileSdk = 35
    defaultConfig {
        applicationId = "org.example.kdeconnectprobe"
        minSdk = 30
        targetSdk = 35
        versionCode = 1
        versionName = "0.1"
    }
}
