# Suwayomi Server repackaged for Home Assistant

This is an **unofficial Home Assistant repackaging**, not a new manga server or
an Android application. The backend is
[Suwayomi-Server](https://github.com/Suwayomi/Suwayomi-Server) and the browser
reader is [Suwayomi WebUI](https://github.com/Suwayomi/Suwayomi-WebUI), maintained
by their upstream contributors. This repository adds the HA manifest, container
build, ingress proxy, storage/configuration adapter and publication workflow.
The upstream projects retain their copyright and MPL-2.0 licensing.

Android, iOS and desktop clients remain separate. They use the optional
authenticated server API address, not a temporary HA ingress session URL.

## Install

1. Add `https://github.com/devnagui/Suwayomi-Server-HA` to the HA app store's
   repositories, reload the store, and install **Suwayomi Server**.
2. Set a unique `auth_password` of 12-256 characters and a simple
   `auth_username` containing letters, digits, dots, underscores or hyphens.
3. Set `storage_path` to the chosen storage folder and configure the internal
   URL of your existing FlareSolverr service.
4. Save, start the app, enable its sidebar entry and open it through HA ingress.

The versioned `suwayomi-amd64` and `suwayomi-aarch64` images must be publicly
pullable from `ghcr.io/devnagui/suwayomi-server-ha` before installation. Check
the [build/publish workflow](../.github/workflows/suwayomi.yml) for a successful
run for both architectures. The HA host pulls an image; it does not compile
Kotlin/Gradle or run a second FlareSolverr browser.

## Configurable storage, including USB

`storage_path` controls **all Suwayomi state**, including its database, library,
server settings, extension state, trackers and cached metadata. The safe default
is `/data/server`, the app's private HA-managed volume.

For a USB disk already mounted in HA under `/media/torrent_disk`, create/use an
app-specific folder and configure:

```yaml
storage_path: /media/torrent_disk/suwayomi
downloads_path: ""
backup_path: ""
local_source_path: ""
```

Empty overrides derive these directories from the selected root:

```text
/media/torrent_disk/suwayomi/
  database.mv.db          library and reading state
  server.conf            upstream server configuration
  extensions/            extension packages and state
  downloads/             downloaded manga
  backups/               Suwayomi's automatic backups
  local/                 local manga
  webUI/                 the matching packaged browser reader
```

The startup adapter creates only the app-specific folder and its children
after verifying the media volume is actually mounted. A missing or stale USB
mount fails explicitly rather than silently storing data on the SD card.
It never mounts/repairs a disk or changes the host's filesystem configuration.
Choose a writable filesystem with reliable file locking for the H2 database.

You can override downloads, backups or local manga independently with absolute
paths below `/data` or an existing mounted `/media` volume. Parent traversal and
symlinks escaping those roots are rejected. The app runs the server as UID/GID
1001 and needs write access to the selected directories.

HA's own `/data/options.json` and generated runtime credential files remain
outside the USB storage tree. However, Suwayomi's database and preferences can
contain source/tracker account details, and server configuration can contain
credentials. **Treat the USB state as private**: do not add the app folder to a
DLNA or public file share. Filesystems such as NTFS can have mount-wide
permissions rather than normal Unix ownership; protect the disk accordingly.

### Backup and migration

- HA app backups include `/data`, **not the external `/media` tree**. When the
  storage root is on USB, back up the entire app-specific USB folder separately.
- HA app backups are cold: Supervisor stops only this app while copying its
  private volume, then restores its prior running state.
- Suwayomi's scheduled backups default to `backups/` below the storage root.
  A backup on the same USB disk is not protection against failure of that disk;
  copy it to another device/location.
- Changing `storage_path` does **not** migrate, merge or delete existing data.
  Stop the app, back up both locations, copy the existing server tree while
  preserving permissions, set the new path, and restart. Retain the old tree
  until the library and downloads have been verified.
- Boot is manual, so arrange startup only after the USB mount is ready. No
  existing HA or household service is reconfigured by this package.

## HA configuration

The Configuration tab exposes the storage root/path overrides, credentials,
direct-access switch, Java heap size, extension store indexes, CBZ downloads,
source concurrency, update interval, backup schedule/retention, debug logs and
all external FlareSolverr connection/session/fallback options.

- `jvm_heap_mb` defaults to 768 MiB. This limits Java heap, not total app RAM;
  native/JVM overhead, HA and the shared solver need additional headroom.
- Source concurrency defaults to two for small ARM hosts.
- `flaresolverr_url` is the service root, **without `/v1`**. The default
  `http://db21ed7f-flaresolverr:8191` suits the commonly used existing HA
  FlareSolverr app; adjust it to your installed app's internal hostname.
  The app reports an unavailable solver but never restarts or replaces it.
- Use a dedicated `flaresolverr_session_name` to avoid sharing another app's
  browser session. No solver/Chromium is bundled or launched here.
- No third-party extension store is installed automatically. Supply current
  `extension_stores` JSON index URLs, not GitHub repository landing pages or
  the obsolete upstream `extensionRepos` setting.

For less common global settings, `advanced_server_settings` accepts a JSON
object with current upstream names and JSON value types, for example:

```json
{"socksProxyEnabled":true,"socksProxyHost":"proxy.local","socksProxyPort":"1080"}
```

This also supports current PostgreSQL, logging, conversion and other global
settings. Unknown/deprecated keys, duplicate keys, null/non-finite values and
overrides of app-managed auth/network/UI/storage settings are rejected.
Keep secrets in HA options, never in committed example configuration.

Reader preferences, automatic download policies, library inclusion, OPDS and
other **per-user** settings are managed through Suwayomi's own UI/GraphQL API.
Their deprecated `server.*` equivalents are not effective HA options.
HA-managed global settings are reapplied on startup; do not change those same
settings in the server UI.

## Ingress and optional direct access

Ingress uses HA's authentication. Internal port 8080 accepts only Supervisor
(`172.30.32.2`) with a validated native `X-Ingress-Path`. The proxy changes only
the HTML base tag; the upstream WebUI uses it for relative assets, lazy imports,
router paths, API/WebSocket URLs, browser storage and service-worker scope.
JSON, JavaScript, CSS and binary content are not rewritten. Streamed requests
and WebSockets remain enabled.

The default administrator-only HA panel is retained: HA administrators using
the sidebar share the configured Suwayomi account. This is not HA-to-Suwayomi
user synchronization. No Supervisor token, Docker socket, host networking,
custom Ingress integration or public static token is required.

Direct access needs **both** `direct_access_enabled: true` and a host-port
mapping for `4567/tcp` in the Network section. Both are off by default.
Every direct route, API, download and WebSocket upgrade requires authentication;
forging ingress headers grants no bypass. The backend is loopback-only on 4568.

**Never forward plaintext port 4567 directly to the Internet.** Use a trusted
HTTPS reverse proxy with a valid certificate, strong unique credentials,
appropriate access/rate limits, and preserved Authorization/WebSocket headers.
Alternatively, use your existing authenticated HA HTTPS ingress endpoint.
This package does not change your router, firewall, TLS or HA authentication.

## Source updates and publication

### Automatic GitHub pipeline

The [workflow](../.github/workflows/suwayomi.yml) runs daily at **04:15 UTC** and
can be started manually from GitHub Actions. It:

1. Checks upstream master; an unchanged source skips the scheduled rebuild.
2. Prepares a new source pin, HA patch version and changelog without committing
   untested metadata to the app store.
3. Builds native AMD64 and ARM64 candidates from the exact same prepared source.
4. Runs the HA linter, unit tests, browser/iframe/API/authentication tests and
   actual library persistence tests for both private and USB storage.
5. Promotes both passing images, commits the tested metadata update, and creates
   a versioned GitHub release. A failed architecture blocks publication and the
   app-store version change.

Existing released versions are immutable; documentation-only builds still run
checks but do not overwrite release images. This uses GitHub's built-in,
repository-scoped workflow token and does not require a local always-on machine.
GitHub may disable scheduled runs after prolonged public-repository inactivity;
reenable them in Actions if GitHub reports that condition.

For **first publication only**, make both architecture packages public in
GitHub's Packages settings. New GHCR packages default to private even when this
repository is public; inheritance of repository permissions is not package
visibility. GitHub has no supported API to automate this visibility switch.
After the two packages are public, later automatic repackagings retain it.

HA app installation/update is separate from GitHub publication. This workflow
never enables Supervisor auto-update, restarts the live app, modifies the USB
mount or touches other household services.

### Manual development update

Clone upstream `Suwayomi-Server` beside `Suwayomi-Server-HA`, or pass a checkout
path as the second argument:

```powershell
node .\scripts\update-suwayomi.cjs 0.1.3
```

The command rejects dirty checkouts and non-increasing app versions,
fast-forwards the server, resolves its full commit SHA, and updates the source
pins and HA app version together. The initial upstream pin is
[`6b481e8b62938b9c060eb210b934e0fbfdfa466b`](https://github.com/Suwayomi/Suwayomi-Server/tree/6b481e8b62938b9c060eb210b934e0fbfdfa466b):
server `v2.4.2435` from upstream master/preview, with WebUI `r3518`.
HA app version and upstream server version are separate identifiers.

The unmodified matching WebUI is extracted at image build time and copied into
an app-managed ordinary directory, so USB filesystems need no symlink support.
It is served through upstream Custom UI mode and updates with the app, without
runtime UI downloads or release drift. On a UI upgrade the previous managed
directory is retained as `webUI.previous-*`; unknown pre-existing directories or
links are preserved and reported as migration errors, not deleted.

## Local verification and known limits

```powershell
python -m unittest discover -s tests -p test_suwayomi.py
docker build -t suwayomi-ha:test .\suwayomi
python .\tests\suwayomi_smoke.py suwayomi-ha:test --browser --usb-storage
```

Browser tests require `npm --prefix tests ci` and
`npx --prefix tests playwright install chromium`; on Linux install the browser
system dependencies too. ARM64 builds add
`--platform linux/arm64 --build-arg BUILD_ARCH=aarch64` to the Docker command.

Smoke tests use task-owned containers/volumes, an ephemeral password, an
external solver stub and a Supervisor-style `172.30.32.0/24` test network.
They fail if that subnet is already in use and clean up their own resources.
They verify real H2 library persistence, not just a storage-path string.
No tests contact/change live HA, the actual USB disk or the real solver.

Local browser tests cover six routes, 98 app-owned assets, ingress iframe
styles/icons, API/WebSocket requests, direct authentication, service-worker
scope and restart persistence. Optional upstream Google Fonts can be blocked
by the test environment and are reported separately. Controller-side ARM64
emulation is not a physical Pi benchmark. Real HA login/sidebar, phone UI and
third-party manga-source downloads require deployment-specific verification.
