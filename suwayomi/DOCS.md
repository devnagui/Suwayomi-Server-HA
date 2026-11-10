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
   Passwords must not have leading/trailing whitespace: upstream trims the
   built-in administrator bootstrap password.
3. Set `storage_path` to the chosen storage folder and configure the internal
   URL of your existing FlareSolverr service.
4. Save, start the app, enable its sidebar entry and open it through HA ingress.

The versioned `suwayomi-amd64` and `suwayomi-aarch64` images must be publicly
pullable from `ghcr.io/devnagui/suwayomi-server-ha` before installation. Check
the [build/publish workflow](../.github/workflows/suwayomi.yml) for a successful
run for both architectures. The HA host pulls an image; it does not compile
Kotlin/Gradle or run a second FlareSolverr browser.
Release 0.1.4 passed the native two-architecture pipeline. Both images were
fully pulled anonymously and verified to contain server v2.4.2435, WebUI r3569
and the expected WebUI SHA-256. App 0.1.9 adds opt-in CEF after repeated native
AMD64/ARM64 JavaScript and USB restart validation; it remains off by default.

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
  webUI/                 the selected packaged or runtime browser reader
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
It also exposes `webui_channel` and `webui_update_check_interval`.

- `jvm_heap_mb` defaults to 768 MiB. This limits Java heap, not total app RAM;
  native/JVM overhead, HA and the shared solver need additional headroom.
  Existing explicit values (including 512 MiB) are retained.
- Source concurrency defaults to two for small ARM hosts.
- `flaresolverr_url` is the service root, **without `/v1`**. The default
  `http://db21ed7f-flaresolverr:8191` suits the commonly used existing HA
  FlareSolverr app; adjust it to your installed app's internal hostname.
  The app reports an unavailable solver but never restarts or replaces it.
- Use a dedicated `flaresolverr_session_name` to avoid sharing another app's
  browser session. No second solver is bundled or launched here. The optional
  embedded browser is separate and is not a replacement for FlareSolverr.
- No third-party extension store is installed automatically. Supply current
  `extension_stores` JSON index URLs, not GitHub repository landing pages or
  the obsolete upstream `extensionRepos` setting.

### WebUI updates and CEF-dependent sources

- `webui_channel: bundled` is the safe default: use the independently verified UI
  included in the image, with no runtime downloads.
- Select `stable` or `preview` to enable upstream WebUI downloads. The server uses
  its compatibility mapping and integrity checks rather than blindly installing
  an incompatible UI. `webui_update_check_interval` accepts 1-23 hours; zero
  disables scheduled checks but leaves manual runtime updates available.
- Changing channels preserves the previous managed `webUI` directory. Runtime
  mode is remembered outside that directory, so upgrades/restarts do not replace
  a downloaded UI with an older package. Unknown custom UI trees are not deleted.
- Runtime UI versions can change without a new HA image and therefore are not
  all covered by the package release tests. Return to `bundled` for the
  image-tested UI. An incompatible initial ingress HTML contract fails explicitly.
- `CEF is disabled` is a backend embedded-browser error, not an Android/WebUI
  rendering error. FlareSolverr handles Cloudflare HTTP responses but cannot
  replace all extensions' WebViews.
- App 0.1.9 exposes `embedded_browser_enabled: true` for those
  extensions. It remains off by default, including when upgrading an existing
  configuration without the option. Change it in HA Configuration and restart
  only this app. Versions before 0.1.9 do not expose this option.
- CEF downloads its native components into the server's storage tree on first
  initialization. Budget additional download/disk space and browser RAM **outside**
  `jvm_heap_mb`; check available memory first on a 4 GiB Pi. Existing
  FlareSolverr settings, source concurrency and library storage remain unchanged.
- The app runs an authenticated, non-networked Xvfb display as the
  unprivileged server user. Java is supervised directly, and the display stays
  alive until Java's browser/database shutdown hooks finish. Display failures
  are explicit app failures, not silent fallback to disabled CEF.
- Earlier experiments intermittently failed category-persistence checks.
  The new native release gate must load local HTML, execute JavaScript, and
  verify the same administrator/category and real library after restart in
  three independent trials per architecture.
  Successful initialization alone is not sufficient. Progress is tracked in
  [issue #2](https://github.com/devnagui/Suwayomi-Server-HA/issues/2).
- An enabled browser does not repair unavailable websites or obsolete extensions.
  A later `Timed out waiting for WebView` needs separate source/compatibility
  diagnosis; it is not the same failure as `CEF is disabled`.
- Native full-source validation (2026-11-10):
  [both AMD64 and ARM64 passed all gates](https://github.com/devnagui/Suwayomi-Server-HA/actions/runs/42868678917),
  including three independent CEF/USB restart trials per architecture with real
  JavaScript before and after restart and matching administrator/category/library
  baselines. Default-disabled browser/API, migration, authentication-failure,
  runtime WebUI and TLS checks also passed. The focused suites passed 58 Python
  and nine Chromium regressions. Emulated ARM64 is not used as native evidence.
  The [0.1.9 publication run](https://github.com/devnagui/Suwayomi-Server-HA/actions/runs/42870234361)
  repeated the full gates with the final version before promoting both images.

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
(`172.30.32.2`) with a validated native `X-Ingress-Path`. The proxy changes
the HTML base tag and loads an ingress-only authentication bootstrap script;
the upstream WebUI uses the base for relative assets, lazy imports,
router paths, API/WebSocket URLs, browser storage and service-worker scope.
JSON, JavaScript, CSS and binary content are not rewritten. Streamed requests
and WebSockets remain enabled.

The administrator-only HA panel is explicit. HA administrators using the
sidebar share native Suwayomi administrator **ID 1**, without another password
prompt. This is not HA-to-Suwayomi user synchronization.

The backend uses `UI_LOGIN` in every WebUI mode, with five-minute access JWTs.
Startup signs in through the upstream login mutation and verifies administrator
1/ADMIN and `UI_LOGIN` before exposing either listener. Its refresh JWT stays in
`/run/suwayomi/auth/ingress-session.json` (directory 0700, file/socket 0600);
the Unix socket is not a public listener. The ingress-only broker uses native
refresh and revalidates identity; it returns only short-lived access JWTs.
The browser stores a scoped **non-secret marker**, not that shared refresh JWT.
The bootstrap intercepts the shipped WebUI's native refresh operation only;
normal API, WebSocket and media requests still require native authentication.
Direct access cannot reach the bootstrap/broker, and a marker is not a valid
direct refresh token. No Visitor is promoted, upstream auth is never disabled,
and no Supervisor token, Docker socket or public static secret is used.

Native HA forwarding preserves application Authorization headers; Supervisor's
`panel_admin` check rejects non-admin ingress sessions before proxying. The
local iframe simulator exercises that forwarding contract, not a live HA
authentication session. Verify the real sidebar/login during a later approved
deployment; this migration does not contact or reconfigure HA.

Invalid/expired refresh, unavailable backend or wrong administrator identity
returns an explicit, redacted authentication error (503) and latches the broker
closed: no Basic or automatic-password-login fallback. After fixing the
reported cause, restart the app to establish a new private session. Native
refresh expiry may therefore require an app restart for long-running ingress.
Runtime stable/preview UIs can change their auth contract; use the tested bundle
if a later runtime UI cannot establish its native session.

Direct access needs **both** `direct_access_enabled: true` and a host-port
mapping for `4567/tcp` in the Network section. Both are off by default.
The login page and static WebUI are public when direct access is enabled.
Library queries, mutations, downloads and media remain protected by native JWT,
including native WebSocket connection/subscription authentication. Invalid
tokens and old Basic headers grant no bypass. The backend is loopback-only on
4568. Direct browsers use the native login form and their own native refresh
session; API clients use login/refresh mutations and bearer tokens.

### Login migration from 0.1.4

0.1.5 was published on November 1, 2026, after both native architecture gates
passed. It was authentication-only, without server overlays.
The independent local chapter-page-list patch is preserved,
but excluded from the Docker context, JAR and packaged provenance.
Before a later approved upgrade, take a cold backup of `/data` and the external
USB server tree. Keep the existing HA `auth_username` and `auth_password`:
the pinned upstream migration/startup settings synchronize only built-in
administrator 1's credentials, not the library or other accounts. No accounts,
categories, manga, reading state or storage locations are deleted or reset.
Existing secondary users keep their native credentials and roles.

Use those same HA-configured credentials in the direct WebUI login form or
native API login, not in an HTTP Basic challenge. If startup reports an
administrator conflict, check for another account already using that username;
upstream can rename administrator 1 on a collision, so startup refuses to
advertise a wrongly authenticated session. Do not delete accounts or initialize
a new storage root to work around it. Correct the conflict through an authorized
native administrator session before retrying. Ingress remains shared-admin,
not a way to turn a direct Visitor or ordinary user into an administrator.

### Reuse the actual HA certificates for direct HTTPS

From app 0.1.3, the direct listener can use HA's existing certificate files:

```yaml
direct_access_enabled: true
ssl: true
certfile: fullchain.pem
keyfile: privkey.pem
```

The `/ssl` directory is mapped read-only. Set the filenames to the same chain/key
used by HA Core; never paste or copy the private key into app options, source or
USB storage. An expired/missing certificate, mismatched key or escaping path
fails explicitly. HA ingress stays on its internal HTTP listener and retains
HA's own HTTPS/authentication.

Map `4567/tcp` to an unused HA host port. Connect with
`https://YOUR_HA_CERTIFICATE_HOSTNAME:PORT/`; use the hostname in the certificate,
not a raw IP that cannot pass hostname validation. The same URL and configured
credentials are usable by separate clients.

The app detects HA certificate replacement, including supported symlink renewals,
and validates/reloads Nginx within 60 seconds without restarting Suwayomi.
Renewal errors are shown in the app log; the active certificate remains loaded
until the replacement is valid. Keep HA's normal certificate renewal process
working; the app does not issue or replace HA certificates.

For later router forwarding, forward only this authenticated **HTTPS** listener,
retain strong unique credentials and maintain updates/backups. Consider upstream
access/rate limits or a VPN/reverse proxy for additional protection. Router,
firewall and WAN mappings are not changed by installing this app.

**Never forward plaintext port 4567 directly to the Internet.** If `ssl` is off,
put the app behind a trusted HTTPS reverse proxy with preserved
Authorization/WebSocket headers.
Alternatively, use your existing authenticated HA HTTPS ingress endpoint.
This package does not change your router, firewall, TLS or HA authentication.

## Source updates and publication

### Automatic GitHub pipeline

The [workflow](../.github/workflows/suwayomi.yml) runs daily at **04:15 UTC** and
can be started manually from GitHub Actions.

For packaging-only changes, dispatch with `update_upstream=false` and a newer
`release_version` (for example `0.1.9`). The existing pin helper prepares that
version while retaining the current server and WebUI. The app-store metadata is
still committed only after both tested images have been promoted.

The workflow:

1. Checks upstream master and the latest compatible WebUI; a UI-only change also
   causes a new package build.
2. Prepares source/UI/digest pins, HA patch version and changelog without committing
   untested metadata to the app store.
3. Builds native AMD64 and ARM64 candidates from the exact same prepared source.
4. Runs the HA linter, unit tests, browser/iframe/API/authentication tests and
   actual library persistence tests for both private and USB storage, plus runtime
   WebUI downloads. Publication additionally gates opt-in CEF on real JavaScript
   execution before and after a USB library restart. CEF remains off by default
   and cannot be enabled through unmanaged advanced-setting overrides.
   Both native architecture jobs also explicitly gate 0.1.4→UI_LOGIN migration,
   intentional authentication failure and stable WebUI login/ingress compatibility.
   Subnet smoke checks run sequentially within each job before candidate upload.
5. Promotes both passing images, commits the tested metadata update, and creates
   a versioned GitHub release. A failed architecture blocks publication and the
   app-store version change.

Existing released versions are immutable; documentation-only builds still run
checks but do not overwrite release images. This uses GitHub's built-in,
repository-scoped workflow token and does not require a local always-on machine.
GitHub may disable scheduled runs after prolonged public-repository inactivity;
reenable them in Actions if GitHub reports that condition.

For forks/new package names, verify public pulls after first publication. If a
package is private, make it public once in GitHub's Packages settings.
Repository permission inheritance is not the same as package visibility, and
GitHub has no supported API for that visibility switch. Once public, later
automatic pushes retain it. Both initial packages in this repository are
already publicly pullable for release 0.1.2.

HA app installation/update is separate from GitHub publication. This workflow
never enables Supervisor auto-update, restarts the live app, modifies the USB
mount or touches other household services.

### Manual development update

Clone upstream `Suwayomi-Server` beside `Suwayomi-Server-HA`, or pass a checkout
path as the second argument:

```powershell
node .\scripts\update-suwayomi.cjs 0.1.7
```

The command rejects dirty checkouts and non-increasing app versions,
fast-forwards the server, resolves its full commit SHA, and updates the source
pins and HA app version together. The initial upstream pin is
[`6b481e8b62938b9c060eb210b934e0fbfdfa466b`](https://github.com/Suwayomi/Suwayomi-Server/tree/6b481e8b62938b9c060eb210b934e0fbfdfa466b):
server `v2.4.2435` from upstream master/preview. Releases 0.1.4/0.1.5 bundle
WebUI `r3569`; the local, unpublished 0.1.6 candidate advances the server to
`7b061be09c9b62a89a63addd11ac91c40fc2510f` (`v2.4.2476`) with that same UI.
HA app version and upstream server version are separate identifiers.

The separately pinned compatible WebUI is extracted at image build time and copied into
an app-managed ordinary directory, so USB filesystems need no symlink support.
It is served through upstream Custom UI mode and updates with the app, without
runtime UI downloads or release drift in bundled mode. Stable/preview mode instead
uses the upstream runtime updater. On a UI upgrade the previous managed
directory is retained as `webUI.previous-*`; unknown pre-existing directories or
links are preserved and reported as migration errors, not deleted.
The packaging-only `SERVER-WEBUI-OVERRIDE.gradle` is included with build provenance;
it selects the downloaded bundle and, from 0.1.6, its separate `HA_WEBUI_TAG`.
Upstream `WEBUI_TAG` remains unchanged because the server also uses it as a
stable/preview compatibility floor. The explicit `bundled-webui-version.patch`
makes only bundled-channel checks use the package's revision and recognizes
the managed custom/bundled flavor; it does not change runtime channel selection,
server upgrade checks, automatic download settings or the offline bundled startup.

### Update notices

HA app versions, upstream server versions and WebUI revisions are independent.
The November 2, 2026 upstream preview server `v2.4.2476` is genuinely newer than
the `v2.4.2435` in HA 0.1.5. Update the server through a tested HA app release,
not the upstream JAR download button. Preview WebUI is still `r3569`; stable is
`r3518`, with upstream minimum server revisions `r2366` and `r2320` respectively.
Switching to stable deliberately selects an older, compatible UI.

Before 0.1.6, custom/bundled mode can falsely report `r3518` as an available
update even while serving `r3569`: the independent archive override did not
update the version used by upstream's bundled check, and the custom flavor
differed from its recorded default flavor. The candidate fixes both metadata
contracts, rather than disabling notifications or raising the runtime UI floor.
Stable/preview channels still use upstream's genuine compatible update checks.

The November 2 scheduled run
[`41444378033`](https://github.com/devnagui/Suwayomi-Server-HA/actions/runs/41444378033)
prepared 0.1.6 but failed only the ARM64 stable-UI browser gate: test navigation
aborted unfinished lazy JavaScript imports after native login. AMD64 passed;
publication was correctly blocked. The regression fix tracks actual same-origin
assets before navigating again; a stale SPA `networkidle` load state alone is
insufficient. HTTP/asset/JavaScript failure assertions remain enabled.

The official `icon.png` is a repository presentation asset, separate from the
sidebar `panel_icon` and the WebUI favicon. HA receives it when it refreshes the
app repository; no image rebuild or live controller change alone installs it.

### Individual manga-update cancellation (0.1.6 candidate)

The explicit `scoped-manga-cancellation.patch` adds optional
`UpdateStopInput.mangaIds: [Int!]`. IDs cancel only matching pending/running
jobs, reporting the existing `SKIPPED` terminal state and preserving unrelated
queues and completed jobs. Omitted/null IDs retain the legacy global stop;
`[]` and unknown IDs do nothing. Clients must not substitute stop-all if a
server does not support the new field.

Both named overlays and their SHA-256 digests are recorded in
`/app/build-info.json`, with the exact patch sources in `/app/`.
The image build runs `UpdaterTest`, `UpdateMutationTest` and
`HABundledWebUIVersionTest` before packaging. The last test verifies that the
packaged revision is not offered as an update while a real revision mismatch
is still reported.
The unrelated local `chapter-page-list.patch` is not copied, applied or included
in provenance.

## Local verification and known limits

```powershell
python -m unittest discover -s tests -p test_suwayomi.py
node --test .\tests\browser_ready.test.mjs
docker build -t suwayomi-ha:test .\suwayomi
python .\tests\suwayomi_smoke.py suwayomi-ha:test --browser --usb-storage
python .\tests\suwayomi_smoke.py suwayomi-ha:test --browser --usb-storage --migration-from ghcr.io/devnagui/suwayomi-server-ha/suwayomi-amd64:0.1.4 --auth-failure
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
`--tls` additionally uses its own temporary test CA/key, verifies certificate
trust/hostname and rejection of plaintext, then exercises a real renewal reload.
It does not disable certificate checks. Run `--browser` separately from `--tls`;
the browser/iframe tests and the verified HTTPS client cover distinct contracts.

Local browser tests cover six routes, roughly 100 app-owned assets, ingress iframe
styles/icons, API/WebSocket requests, direct authentication, service-worker
scope and restart persistence. Optional upstream Google Fonts can be blocked
by the test environment and are reported separately. Controller-side ARM64
emulation is not a physical Pi benchmark. Real HA login/sidebar, phone UI and
third-party manga-source downloads require deployment-specific verification.
The standalone browser-readiness fixtures need no Docker or live server; they
reproduce a slow post-login import and verify genuine HTTP/JavaScript failures
remain visible. Bundled smoke tests additionally require the update check to
report the actual packaged UI revision with no false update.

Login checks exercise the real shipped UI in a separate HA-host iframe,
automatic ingress JWT sessions, direct form login/wrong-password/logout/invalid
refresh, real access-token expiry/recovery, route-reload refresh, protected
native WebSocket subscriptions (including anonymous denial) and actual local CBZ
thumbnail/page bytes. The migration flag seeds an existing 0.1.4 library and
non-admin account, stops it gracefully, and verifies the same users/library
after switching images. `--auth-failure` invalidates only the disposable fixture's
JWT audience and checks repeated 503 denial, redacted logs and the frontend's
visible authentication failure. All subnet smoke invocations must run
**sequentially**. Runtime UI checks complement the bundled/default checks.
