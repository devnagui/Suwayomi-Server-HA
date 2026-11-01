# syntax=docker/dockerfile:1
FROM --platform=$BUILDPLATFORM eclipse-temurin:25-jdk-noble AS builder

ARG SOURCE_REF=6b481e8b62938b9c060eb210b934e0fbfdfa466b
ARG WEBUI_REF=r3569
ARG WEBUI_SHA256=d20b8421f4137a5cf7fca24bd8c62e6c0ad72ea3a373d215e396d801ccac145b
ARG WEBUI_MIN_SERVER=2366
RUN apt-get update && apt-get install -y --no-install-recommends git python3 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /source
RUN git clone https://github.com/Suwayomi/Suwayomi-Server.git . \
    && git checkout --detach "$SOURCE_REF" \
    && test "$(git rev-parse HEAD)" = "$SOURCE_REF"
COPY build_source.py /build_source.py
RUN python3 /build_source.py --pin-webui /source "$WEBUI_REF" "$WEBUI_MIN_SERVER" "$WEBUI_SHA256"
RUN --mount=type=cache,target=/root/.gradle \
    cp .github/runner-files/ci-gradle.properties /root/.gradle/gradle.properties \
    && ./gradlew --no-daemon --max-workers=2 --init-script /source/ha-webui.gradle :server:downloadWebUI :server:shadowJar
RUN python3 /build_source.py /source /output "$WEBUI_REF" "$WEBUI_SHA256"

FROM eclipse-temurin:25-jre-noble
ARG SOURCE_REF=6b481e8b62938b9c060eb210b934e0fbfdfa466b
ARG WEBUI_REF=r3569
ARG BUILD_VERSION=0.1.5
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
