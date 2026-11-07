# Changelog

## 0.1.8

- Automatically repackage [upstream Suwayomi server 7b061be09c9b](https://github.com/Suwayomi/Suwayomi-Server/commit/7b061be09c9b62a89a63addd11ac91c40fc2510f) with verified compatible WebUI r3578.
- Publish only after amd64 and ARM64 build, ingress/API/browser and USB library-persistence tests pass.

## 0.1.7

- Automatically repackage [upstream Suwayomi server 7b061be09c9b](https://github.com/Suwayomi/Suwayomi-Server/commit/7b061be09c9b62a89a63addd11ac91c40fc2510f) with verified compatible WebUI r3574.
- Publish only after amd64 and ARM64 build, ingress/API/browser and USB library-persistence tests pass.

## 0.1.6 (local candidate, not published)

- Repackage [upstream Suwayomi server 7b061be09c9b](https://github.com/Suwayomi/Suwayomi-Server/commit/7b061be09c9b62a89a63addd11ac91c40fc2510f)
  (`v2.4.2476`) with unchanged SHA-256-pinned compatible WebUI r3569.
- Add the unmodified licensed upstream 128px icon at HA's `suwayomi/icon.png`
  path for the app store and installed Apps view.
- Wait for real app-asset completion after SPA login/routes in browser tests;
  do not mistake stale Playwright network-idle state for finished lazy imports,
  and do not ignore asset aborts, HTTP errors or JavaScript failures.
- Correct bundled-WebUI update metadata using a separate `HA_WEBUI_TAG` and
  explicit custom/bundled recognition. Do not change upstream `WEBUI_TAG`,
  stable/preview compatibility floors, real upgrade notices or offline startup.
- Support `updateStop(input: {mangaIds: [...]})` for selected pending/running
  manga only, without cancelling unrelated source queues. Omitted/null IDs
  retain stop-all behavior; empty/unknown IDs are no-ops.
- Apply only the explicit metadata and scoped-cancellation overlays; include
  their sources and SHA-256 in packaged provenance. Gate the image build on
  targeted updater/mutation cancellation tests. Keep the independent local
  chapter-page-list patch excluded.
- Publish only after amd64 and ARM64 build, ingress/API/browser and USB library-persistence tests pass.

## 0.1.5

- Replace container-enforced Basic authentication with native UI_LOGIN/JWT;
  reuse HA credentials for upstream built-in administrator 1 without resetting
  accounts or library state.
- Preserve HA administrator-only ingress with a private Unix-socket refresh
  broker and automatic native WebUI sessions in the HA iframe. Shared refresh
  tokens remain in private runtime files; only five-minute access JWTs reach
  the browser. Failures are redacted, explicit and fail closed without fallback.
- Keep direct access disabled/unmapped by default. When enabled, serve the
  native login/static UI publicly but require JWT for library/API/media and
  native WebSocket operations; retain existing HTTPS/renewal guards.
- Verify Basic-auth migration, retained non-admin credentials/roles, real local
  CBZ media protection, browser form/refresh/iframe behavior, private/USB storage,
  runtime UI modes and intentional native refresh failures.
- Retain server pin 6b481e8b (v2.4.2435), WebUI r3569 and its existing SHA-256,
  all existing options, architecture names, heap settings and disabled CEF.
- Keep the release authentication-only: do not apply/package server overlays.
  Gate native AMD64/ARM64 publication on explicit 0.1.4 migration, intentional
  authentication failure and stable WebUI compatibility checks as well.

## Unreleased (independent work, excluded from 0.1.5)

- Reject empty source chapter page lists before changing cached pages, download
  flags or reading progress. Return a clear source-page error instead of an
  empty successful cache response. The proposed source patch remains preserved
  locally, but is excluded from this release's build, JAR and packaged provenance.

## 0.1.4

- Package the latest compatible WebUI independently of the server source pin;
  start with verified preview r3569 and record its SHA-256 and build override.
- Check server and WebUI updates on pushes, daily and manual runs; publish only
  after native AMD64/ARM64 package, browser/API, storage and TLS tests pass.
- Add opt-in stable/preview runtime WebUI downloads, with the tested bundled
  mode preserved by default and previous managed UI trees retained on switches.
- Keep runtime update options separate from ingress and authentication;
  preserve older installations' bundled mode and disabled embedded browser.
- Exclude experimental CEF support after intermittent native restart/persistence
  failures; do not weaken release gates or expose an unvalidated enable switch.
- Preserve direct Java supervision and report backend persistence errors explicitly.

## 0.1.3

- Add optional direct HTTPS using the existing HA certificate/key through a
  read-only SSL mount; keep native ingress and mandatory direct authentication.
- Validate certificate filenames, validity and key pairing without copying or
  displaying private key contents.
- Reload renewed HA certificates in place without restarting the manga server;
  report renewal failures explicitly while retaining the active certificate.
- Test certificate trust/hostname, plaintext rejection and real renewal reload.

## 0.1.2

- Publish as the standalone, unofficial Suwayomi-Server-HA repackaging, with
  upstream attribution/license and dedicated repository/container URLs.
- Add configurable whole-server storage, including the library/H2 database,
  extensions, downloads, backups and local manga under a mounted USB app folder.
- Keep private HA options/runtime credentials separate and reject missing or
  stale media mounts instead of falling back to the SD card.
- Use ordinary packaged-WebUI directories for USB filesystems without symlink
  support; preserve the previous managed UI during upgrades.
- Verify actual library persistence and path defaults with mounted-volume tests.
- Add a daily/manual GitHub upstream check with automatic patch versioning,
  shared source snapshots, both-architecture publication gates and tested
  metadata/changelog/release updates. Do not overwrite existing released images.
- Retry transient Docker startup connection resets only within the smoke-test
  readiness deadline; still fail explicitly when the app exits or never becomes ready.
- Use an explicit BCP-47 browser-test locale instead of hosted runners' invalid
  `en-US@posix` locale, while retaining all browser and asset error assertions.

## 0.1.1

- Update to current main server v2.4.2435 (source revision `6b481e8b6293`),
  rather than the stock HA image's older v2.3.2243.
- Prepackage the matching WebUI r3518 and serve it through upstream Custom
  mode, with no runtime WebUI downloads or version drift.
- Wait for all initial UI assets before advertising readiness.
- Validate HA manifests against the canonical schema, use cold database backups,
  and provide a Node source-update command compatible with Windows script policy.
- Verify actual server/UI versions, external solver settings, ingress/direct
  browser navigation, WebSockets, disabled direct access and persistent restarts.

## 0.1.0

- Build pinned Suwayomi Server source and its matching bundled WebUI for amd64
  and aarch64; publish tested versioned HA app images.
- Add native HA ingress, streamed GraphQL/API requests and WebSocket support.
- Expose deployment/global settings while retaining upstream per-user settings.
- Reuse an existing external FlareSolverr with persistent state and USB storage.
- Keep direct access unmapped and disabled unless explicitly enabled, with
  authentication separate from the trusted ingress listener.
