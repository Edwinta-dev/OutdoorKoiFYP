# Linux Flutter SDK at one exact version, for recording and checking the
# mobile app's golden images (tools/update_goldens.py). Goldens are compared
# pixel by pixel, so they must be rendered by the same Flutter version and OS
# family as CI (.github/workflows/ci.yml pins the version).
#
# Built locally by tools/update_goldens.py as koi-flutter:<version>; the first
# build takes a few minutes (the image is about 4 GB); later runs reuse it.
FROM ubuntu:24.04

ARG FLUTTER_VERSION
RUN test -n "$FLUTTER_VERSION" || (echo "FLUTTER_VERSION build arg is required" && exit 1)

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git unzip xz-utils zip libglu1-mesa \
    && rm -rf /var/lib/apt/lists/*

# The release tag, not a branch: the image is the same whenever it is built.
RUN git clone --depth 1 --branch "$FLUTTER_VERSION" https://github.com/flutter/flutter.git /opt/flutter
ENV PATH="/opt/flutter/bin:/opt/flutter/bin/cache/dart-sdk/bin:${PATH}" \
    FLUTTER_SUPPRESS_ANALYTICS=true \
    PUB_CACHE=/opt/pub-cache

# Tests only: no Android, iOS, web or desktop toolchains.
RUN git config --global --add safe.directory /opt/flutter \
    && flutter config --no-analytics --no-cli-animations \
    && flutter precache \
    && flutter --version
