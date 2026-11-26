# syntax=docker/dockerfile:1
FROM --platform=$BUILDPLATFORM eclipse-temurin:25-jdk-noble AS builder

ARG SOURCE_REF=4081b05f02456ec099c85127dfa5253e968d48a7
ARG WEBUI_REF=r3601
ARG WEBUI_SHA256=9d254119aaff38b1058f9d1bec3bd0fc93f4e88369b0df380741a9f9367143c5
ARG WEBUI_MIN_SERVER=2388
RUN apt-get update && apt-get install -y --no-install-recommends git python3 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /source
RUN git clone https://github.com/Suwayomi/Suwayomi-Server.git . \
    && git checkout --detach "$SOURCE_REF" \
    && test "$(git rev-parse HEAD)" = "$SOURCE_REF"
COPY build_source.py /source/build_source.py
COPY gradle_retry.py /gradle_retry.py
COPY bundled-webui-version.patch scoped-manga-cancellation.patch webview-navigation-snapshot.patch android-evaluation-semantics.patch ci-github-metadata.patch /source/
RUN python3 /source/build_source.py --apply-patches /source
RUN python3 /source/build_source.py --pin-webui /source "$WEBUI_REF" "$WEBUI_MIN_SERVER" "$WEBUI_SHA256"
RUN --mount=type=cache,target=/root/.gradle \
    cp .github/runner-files/ci-gradle.properties /root/.gradle/gradle.properties \
    && python3 /gradle_retry.py -- ./gradlew --no-daemon --max-workers=2 --init-script /source/ha-webui.gradle \
       :AndroidCompat:test --tests xyz.nulldev.androidcompat.webkit.KcefNavigationSnapshotTest \
       --tests xyz.nulldev.androidcompat.webkit.KcefEvaluationTest \
       --tests xyz.nulldev.androidcompat.webkit.HAAllMangaEvaluationTest \
       :server:test --tests suwayomi.tachidesk.manga.impl.update.UpdaterTest \
       --tests suwayomi.tachidesk.graphql.UpdateMutationTest \
       --tests suwayomi.tachidesk.graphql.HABundledWebUIVersionTest \
       --tests suwayomi.tachidesk.server.util.HAGithubMetadataRequestTest \
       :server:downloadWebUI :server:shadowJar
RUN python3 /source/build_source.py /source /output "$WEBUI_REF" "$WEBUI_SHA256"

FROM eclipse-temurin:25-jre-noble
ARG SOURCE_REF=4081b05f02456ec099c85127dfa5253e968d48a7
ARG WEBUI_REF=r3601
ARG BUILD_VERSION=0.1.13
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
