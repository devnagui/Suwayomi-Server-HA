# Suwayomi Server for Home Assistant

An **unofficial Home Assistant repackaging** of
[Suwayomi-Server](https://github.com/Suwayomi/Suwayomi-Server), with the upstream
[Suwayomi WebUI](https://github.com/Suwayomi/Suwayomi-WebUI).
The manga server and reader are Suwayomi's work; this repository supplies the
Home Assistant app manifest, container build, ingress proxy, startup configuration,
storage management and update/publish pipeline.

This is **not the Android client**, a new manga engine, or an official Suwayomi
or Home Assistant distribution. Mobile/desktop clients connect separately to
the optional authenticated server API.

## Features

- Native, authenticated Home Assistant ingress with working assets, browser
  routing, GraphQL and WebSockets.
- Native Suwayomi UI login/JWT for separate clients; HA administrators get an
  automatic shared administrator session through a private token broker.
- AMD64 and ARM64/aarch64 images, including Raspberry Pi 4/5.
- Configurable storage root: keep database, library, downloads, backups and
  local manga in an app-specific folder on an existing mounted USB disk.
- Reuse an existing FlareSolverr service; no second solver or embedded Chromium
  is started.
- Optional direct access, disabled and unmapped by default, with mandatory
  native JWT protection for library/API/media (the login/static UI is public).
  Enable HTTPS with the existing HA certificates before
  Internet forwarding, or use a trusted HTTPS reverse proxy.
- Versioned images built from a pinned server commit and separately SHA-256-pinned,
  compatible WebUI, with server/UI update checks and tests before publication.
- Optional compatible stable/preview WebUI downloads at runtime, without waiting
  for a server image update; the tested bundled mode remains the default.

## Install in Home Assistant

Add this repository in **Settings → Apps → App store → Repositories**:

```text
https://github.com/devnagui/Suwayomi-Server-HA
```

Install **Suwayomi Server**, then read its
[configuration and storage documentation](suwayomi/DOCS.md).
Set a unique password and the existing FlareSolverr URL before starting.
For USB storage, set `storage_path` to your mounted app folder, for example
`/media/torrent_disk/suwayomi`. Leave the individual path overrides empty to
keep everything under that root.

[Release 0.1.4](https://github.com/devnagui/Suwayomi-Server-HA/releases/tag/v0.1.4)
has passed the native AMD64/ARM64 pipeline. Both architecture images were
fully pulled from GHCR without registry credentials, and their actual packaged
server/UI versions and archive SHA-256 were verified. CEF is kept disabled
pending [restart-safety validation](https://github.com/devnagui/Suwayomi-Server-HA/issues/2).

The local **0.1.5 candidate is not published**. It migrates container-enforced
Basic authentication to native `UI_LOGIN`, keeping the existing
`auth_username`/`auth_password` as built-in administrator 1 and retaining its
library and other users. See the [login migration guidance](suwayomi/DOCS.md#login-migration-from-014).
No live HA configuration or credentials are changed by preparing this candidate.

Both architecture images for the app version must be published and publicly
pullable from GHCR before Supervisor can install the package. The
[GitHub Actions workflow](.github/workflows/suwayomi.yml) builds, tests and
publishes them; check its latest successful run rather than assuming a source
commit alone means the images are available.

## Updating and contributing

The GitHub pipeline checks upstream master and compatible WebUI releases on
pushes and daily at **04:15 UTC**. When either changes, the pipeline increments
the HA patch version, builds and tests both
architectures, then publishes both images and records the tested source/version
update. Failed candidates are not offered as a new HA version. Released image
versions are not silently overwritten.

You can also use **Actions → Build and publish Suwayomi HA repackaging → Run
workflow**. Keep `update_upstream` enabled to check the latest upstream source.
Runtime downloads can be enabled in HA Configuration with
`webui_channel: preview` (or `stable`) and
`webui_update_check_interval: 23`. The upstream server selects a compatible UI.
CEF-dependent sources still require a separately supported backend: experimental
embedded-browser support did not consistently pass restart/persistence checks
and is not included in this release. A disabled-CEF error can appear in Android
as well as WebUI; runtime WebUI updates do not enable CEF.

No personal token, local compiler or running controller is needed by the
scheduled workflow: it uses GitHub's scoped workflow token.

**For forks or new package names:** verify anonymous pulls after first
publication. If GHCR created a package privately, open it in your GitHub
profile's Packages tab, then use
**Package settings → Change visibility → Public** once. GitHub currently has no
supported API for that visibility change. Later pushes retain the public
setting. This repository's two initial packages are already publicly pullable;
no extra visibility setup is needed for release 0.1.2.

The pipeline does not enable HA auto-updates, restart the installed app, repair
FlareSolverr or change host networking. Upstream incompatibilities still need
attention when the tests report a failure; the existing HA version remains
unchanged.

For a manual source update on a development machine,
clone the upstream server beside this repository, or pass its checkout path:

```powershell
node .\scripts\update-suwayomi.cjs 0.1.6
```

The command fast-forwards a clean upstream checkout and updates the source pin
and the compatible WebUI/digest and app version together. Changes to the manga server itself belong in
[Suwayomi-Server](https://github.com/Suwayomi/Suwayomi-Server); packaging,
ingress and Home Assistant configuration changes belong here.

See [DOCS.md](suwayomi/DOCS.md) for installation, USB backup/migration,
authentication, local validation and release details.

## Attribution and license

Suwayomi-Server and Suwayomi WebUI are copyright their respective contributors.
They are redistributed under the Mozilla Public License 2.0 with their notices
and matching source links preserved. This repository's packaging code uses the
same [MPL-2.0 license](LICENSE).

The initial server source pin is
[`6b481e8b62938b9c060eb210b934e0fbfdfa466b`](https://github.com/Suwayomi/Suwayomi-Server/tree/6b481e8b62938b9c060eb210b934e0fbfdfa466b)
(server `v2.4.2435`, from upstream master/preview). Packages 0.1.4/0.1.5 select WebUI
`r3569` independently through a packaging-only Gradle download override; upstream
server source and its compatibility floor remain unchanged.
Exact source and UI provenance are recorded in each image's
`/app/build-info.json`. This package does not bundle or endorse third-party
extension stores or grant rights to manga content.
