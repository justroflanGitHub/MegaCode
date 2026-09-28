# Base testbed for Astra Linux SE 1.7.6 compatibility work.
#
# Astra 1.7.x is built on Debian 10 "buster" (glibc 2.28, system python3 = 3.7),
# so a buster container is the closest freely-available stand-in: whatever runs
# here runs on Astra 1.7.6 with the same interpreter version and older glibc.
#
# python:3.7-slim-buster (not bare debian:10) because the app image ships
# ca-certificates: buster itself moved to archive.debian.org, which is only
# reachable over TLS from this network, and apt cannot bootstrap certs from a
# repo it cannot fetch. The archive's Release files are also past their
# validity date, hence the Check-Valid-Until switch.
#
# The Qt binding (PySide2 5.15.x cp37 vs any cp37-capable PySide6) is
# intentionally NOT installed here: probe PyPI first, then pin in the derived
# image so both stacks can be tried.

FROM python:3.7-slim-buster

RUN printf 'deb https://archive.debian.org/debian buster main\ndeb https://archive.debian.org/debian-security buster/updates main\n' \
        > /etc/apt/sources.list \
    && echo 'Acquire::Check-Valid-Until "false";' > /etc/apt/apt.conf.d/99archive \
    # archive.debian.org (Fastly) intermittently refuses connections from
    # this network; a bare && chain would fail the whole build on one hiccup
    && (apt-get update || (sleep 8 && apt-get update) || (sleep 20 && apt-get update)) \
    && apt-get install -y --no-install-recommends \
        fontconfig \
        fonts-dejavu-core \
        libasound2 \
        libcom-err2 \
        libdbus-1-3 \
        libegl1 \
        libfontconfig1 \
        libfreetype6 \
        libgl1 \
        libglib2.0-0 \
        libgssapi-krb5-2 \
        libk5crypto3 \
        libkeyutils1 \
        libkrb5-3 \
        libnspr4 \
        libnss3 \
        libpcsclite1 \
        libpulse0 \
        libwayland-cursor0 \
        libwayland-egl1 \
        libx11-6 \
        libx11-xcb1 \
        libxcb-cursor0 \
        libxcb-icccm4 \
        libxcb-image0 \
        libxcb-keysyms1 \
        libxcb-randr0 \
        libxcb-render-util0 \
        libxcb-shape0 \
        libxcb-xinerama0 \
        libxcb-xkb1 \
        libxcb1 \
        libxcomposite1 \
        libxi6 \
        libxkbcommon-x11-0 \
        libxkbcommon0 \
        libxkbfile1 \
        libxrandr2 \
        libxrender1 \
        libxtst6 \
    && rm -rf /var/lib/apt/lists/*

# PySide6-Essentials (not the PySide6 metapackage): the app needs
# QtCore/Gui/Widgets/Network only, and Addons drags in another ~200 MB of
# wheels plus system libs (nss, alsa, pulse) an Astra host may not have.
# 6.5.3 is the last release with cp37 wheels:
#   PySide6_Essentials-6.5.3-cp37-abi3-manylinux_2_28_x86_64.whl
# pyte 0.8.2's requires-python hides it from 3.7; 0.8.1 is the pin.
RUN python -m pip install --no-cache-dir \
        "pytest==7.4.4" \
        "PySide6-Essentials==6.5.3" \
        "pyte==0.8.1"

ENV QT_QPA_PLATFORM=offscreen \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /work
CMD ["python", "--version"]
