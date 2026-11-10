# syntax=docker/dockerfile:1
FROM --platform=$BUILDPLATFORM eclipse-temurin:25-jdk-noble AS builder

ARG SOURCE_REF=7b061be09c9b62a89a63addd11ac91c40fc2510f
ARG WEBUI_REF=r3578
ARG WEBUI_SHA256=63e375b6a49dc3b418d8b9ea63e90d1741eca6c0989fe6456c7d51e2678d3746
ARG WEBUI_MIN_SERVER=2388
RUN apt-get update && apt-get install -y --no-install-recommends git python3 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /source
RUN git clone https://github.com/Suwayomi/Suwayomi-Server.git . \
    && git checkout --detach "$SOURCE_REF" \
    && test "$(git rev-parse HEAD)" = "$SOURCE_REF"
COPY build_source.py /build_source.py
COPY gradle_retry.py /gradle_retry.py
COPY bundled-webui-version.patch scoped-manga-cancellation.patch /source/
RUN git apply --check bundled-webui-version.patch scoped-manga-cancellation.patch \
    && git apply bundled-webui-version.patch scoped-manga-cancellation.patch
RUN python3 /build_source.py --pin-webui /source "$WEBUI_REF" "$WEBUI_MIN_SERVER" "$WEBUI_SHA256"
RUN --mount=type=cache,target=/root/.gradle \
    cp .github/runner-files/ci-gradle.properties /root/.gradle/gradle.properties \
    && python3 /gradle_retry.py -- ./gradlew --no-daemon --max-workers=2 --init-script /source/ha-webui.gradle \
       :server:test --tests suwayomi.tachidesk.manga.impl.update.UpdaterTest \
       --tests suwayomi.tachidesk.graphql.UpdateMutationTest \
       --tests suwayomi.tachidesk.graphql.HABundledWebUIVersionTest \
       :server:downloadWebUI :server:shadowJar
RUN python3 /build_source.py /source /output "$WEBUI_REF" "$WEBUI_SHA256"

FROM eclipse-temurin:25-jre-noble
ARG SOURCE_REF=7b061be09c9b62a89a63addd11ac91c40fc2510f
ARG WEBUI_REF=r3578
ARG BUILD_VERSION=0.1.11
ARG BUILD_ARCH=amd64
LABEL org.opencontainers.image.source="https://github.com/devnagui/Suwayomi-Server-HA" \
    org.opencontainers.image.licenses="MPL-2.0" \
    org.opencontainers.image.version="$BUILD_VERSION" \
    io.suwayomi.server.revision="$SOURCE_REF" \
    io.suwayomi.webui.revision="$WEBUI_REF" \
    io.hass.type="addon" \
    io.hass.name="Suwayomi Server" \
    io.hass.version="$BUILD_VERSION" \
    io.hass.arch="$BUILD_ARCH"
RUN apt-get update && apt-get install -y --no-install-recommends \
    nginx python3 openssl tini ca-certificates \
    xvfb xauth libasound2t64 libatk-bridge2.0-0t64 libatk1.0-0t64 libcups2t64 \
    libdrm2 libgbm1 libgtk-3-0t64 libnss3 libx11-6 libxcomposite1 libxdamage1 \
    libxfixes3 libxi6 libxrandr2 libxrender1 libxss1 libxtst6 libxkbcommon0 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1001 suwayomi \
    && useradd --uid 1001 --gid 1001 --create-home suwayomi \
    && mkdir -p /app /run/suwayomi
COPY --from=builder /output/ /app/
COPY run.py ingress_auth.py nginx.conf /app/
ENV HOME=/home/suwayomi
WORKDIR /app
EXPOSE 8080 4567
ENTRYPOINT ["/usr/bin/tini", "--", "python3", "/app/run.py"]
