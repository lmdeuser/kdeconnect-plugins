# SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com>
# SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL

FROM ubuntu:26.04

RUN export DEBIAN_FRONTEND=noninteractive && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
      binutils \
      kdeconnect \
      qml6-module-org-kde-kcmutils \
      gstreamer1.0-tools \
      gstreamer1.0-plugins-base \
      gstreamer1.0-plugins-good \
      gstreamer1.0-plugins-bad \
      gstreamer1.0-libav \
      gstreamer1.0-pipewire && \
    dpkg --purge --force-depends kdeconnect kdeconnect-libs qml6-module-org-kde-kdeconnect libei1 && \
    rm -rf /var/lib/apt/lists/*
