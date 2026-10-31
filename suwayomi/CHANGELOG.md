# Changelog

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
