// SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com>
// SPDX-License-Identifier: BSD-2-Clause

pluginManagement { repositories { google(); mavenCentral(); gradlePluginPortal() } }
dependencyResolutionManagement { repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS); repositories { google(); mavenCentral() } }
rootProject.name = "KdeConnectCaptureProbe"
include(":app")
