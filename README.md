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
- Reuse an existing FlareSolverr service; no second solver is started.
- Opt-in embedded CEF browser for extensions that need WebView/JavaScript
  (available from 0.1.9; additional memory is required).
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

[Release 0.1.5](https://github.com/devnagui/Suwayomi-Server-HA/releases/tag/v0.1.5)
passed the native AMD64/ARM64 pipeline and was published on November 1, 2026.
[Release 0.1.9](https://github.com/devnagui/Suwayomi-Server-HA/releases/tag/v0.1.9)
adds `embedded_browser_enabled`, off by default, with a private authenticated
virtual display and direct JVM supervision. Both native AMD64 and ARM64 passed
three independent real-CEF JavaScript and USB library-restart trials before
publication. See [restart-safety validation](https://github.com/devnagui/Suwayomi-Server-HA/issues/2).
[Release 0.1.11](https://github.com/devnagui/Suwayomi-Server-HA/releases/tag/v0.1.11)
extends startup readiness for slow USB-backed libraries with embedded CEF. See [restart-safety validation](https://github.com/devnagui/Suwayomi-Server-HA/issues/2).

Release 0.1.5 migrates container-enforced Basic authentication to native
`UI_LOGIN`, keeping the existing
`auth_username`/`auth_password` as built-in administrator 1 and retaining its
library and other users. See the [login migration guidance](suwayomi/DOCS.md#login-migration-from-014).
The local **0.1.6 candidate is not published**. It packages server `v2.4.2476`,
fixes bundled-WebUI version reporting and the browser release-gate race, adds
the upstream HA app icon, and supports cancelling individual manga updates.
No live HA configuration or credentials are changed by preparing it.

Both architecture images for the app version must be published and publicly
pullable from GHCR before Supervisor can install the package. The
[GitHub Actions workflow](.github/workflows/suwayomi.yml) builds, tests and
publishes them; check its latest successful run rather than assuming a source
commit alone means the images are available.

## Updating and contributing

The browser release gate attributes same-origin GraphQL POST cancellations to
explicit test navigation, including the service-worker reload after subscription
checks. Only `net::ERR_ABORTED` from requests belonging to the previous document
during those navigations is expected; new-document failures, idle API
failures, other network errors, HTTP failures and asset/JavaScript failures still
fail the gate. Readiness is based on visible app content and settled app-owned
assets, not global network idleness: background GraphQL activity must not prevent
an otherwise ready page or iframe from being tested. Fetch-based thumbnails are
included in readiness, delayed old-document cancellation events remain attributed
to their navigation, and the browser must actually render the seeded library
manga instead of merely showing a page shell. Run the Chromium regressions with
`node --test tests/browser_ready.test.mjs` after installing the locked test
dependencies and Playwright Chromium.
Outgoing-document manga-thumbnail GETs use that same explicit-navigation scope:
a late background query may queue an image while navigation awaits headers.
Idle/current-document thumbnail aborts, other image routes and non-abort
failures remain errors.
Newer Apollo-based preview UIs also cancel redundant queries during initialization.
A cancelled query is accepted only when an identical query and variables in the
same document delivered HTTP 200 with actual data and no GraphQL errors, or when
the two known download/WebUI status caches received valid live subscription
states in that same document. The latter matches the upstream Apollo cache
updates, rather than blindly accepting cancelled startup status queries.
Unmatched cancellations, mutations, failed responses and responses from another
document still fail; API cancellation is not blanket-ignored.
The native JWT expiry smoke test expires a one-second fixture token, verifies
that it cannot access user/media data, then restores the normal five-minute
setting before refreshing that expired token. This keeps replacement tokens
usable on slower runners without weakening expiry or refresh authentication.
Expiry waiting uses the issued token's expiry claim and a bounded monotonic
deadline rather than a fixed sleep. Docker operations, browser smoke subprocesses
and cleanup have deadlines. Failure logs redact fixture credentials/JWTs;
cleanup attempts every created resource without replacing the original failure.
Metadata GETs have up to three bounded attempts for transport failures or
HTTP 408/429/5xx; authentication failures, invalid JSON, mismatched release tags
and compatibility errors remain immediate failures. `git ls-remote` has a
thirty-second deadline. The Gradle build gets at most three attempts only when
its output identifies a dependency-download transport failure such as HTTP 504;
compilation, tests, integrity, authentication and TLS failures are not retried.
Each Gradle attempt is limited to eight minutes, with short two/four-second
delays between retries; a timed-out process fails instead of being retried.

Prepare/build/publication jobs are limited to ten/forty/fifteen minutes.
Superseded PR runs can still be cancelled, but main/release runs are not cancelled
mid-publication by a newer push or schedule. Both architecture gates must pass
before release metadata is committed. Registry writes for the two architecture
tags are still sequential, not an atomic transaction; a failed promotion remains
a failed release and needs inspection, never a successful fallback.

### Why a run can still fail

The November 19-25 scheduled candidates failed because upstream added imports
and already imported `coVerify` in `UpdaterTest.kt`. The cancellation overlay now
uses only the coroutine import context and qualified verification calls.
The 0.1.14 candidate pins upstream `4081b05f02456ec099c85127dfa5253e968d48a7`,
rebases navigation callbacks around its new preload/document-script behavior,
and removes backports now supplied by upstream while retaining their regressions.
The newer upstream WebView also streams binary JPEG screenshots. The native
smoke client validates and acknowledges those frames, including fragmentation
and interleaved pings; the real JavaScript console result is still required.
Failed Docker probes now include up to 6,000 characters of redacted output,
instead of hiding the assertion behind a generic exit code.
Runtime WebUI failures report their actual update state; the bounded 300-line
container failure log retains initial download errors even after intentional
authentication-rejection checks have produced their own stack traces.
Preparation checks the complete approved overlay set against the exact selected
server commit before either architecture starts. Docker uses that same helper
and still checks before applying; an incompatible patch remains a hard failure,
not a reason to drop the overlay or publish an untested update.

The November 2026 failures had different causes: cancelled browser requests were
mistaken for errors, a refreshed one-second test JWT could expire on ARM64, and
an upstream Gradle dependency download returned HTTP 504. Old failure
notifications also remain visible after a later commit passes; compare the
notification's commit with the PR's latest completed checks.

Reliability changes must not turn errors into passes. Invalid metadata, broken
patches, compilation/test failures, bad authentication, changed checksums and
incompatible stable/preview WebUIs must still block publication. Upstream APIs,
dependency repositories and runtime WebUI channels are external dependencies,
so no workflow can guarantee every future run will succeed. The goal is bounded,
reproducible checks and diagnostics that distinguish a real regression from a
transient download failure, not retries of whole failed builds or skipped gates.

The GitHub pipeline checks upstream master and compatible WebUI releases daily
at **04:15 UTC**, or on a manual run with `update_upstream` enabled.
Pushes and pull requests test their committed pins. When either upstream pin changes, the pipeline increments
the HA patch version, builds and tests both
architectures, then publishes both images and records the tested source/version
update. Failed candidates are not offered as a new HA version. Released image
versions are not silently overwritten.

You can also use **Actions → Build and publish Suwayomi HA repackaging → Run
workflow**. Keep `update_upstream` enabled to check the latest upstream source.
Runtime downloads can be enabled in HA Configuration with
`webui_channel: preview` (or `stable`) and
`webui_update_check_interval: 23`. The upstream server selects a compatible UI.
For CEF-dependent sources, use app 0.1.9 or newer, set
`embedded_browser_enabled: true` in HA Configuration and restart only this app.
Check available RAM first; native browser memory is outside the Java heap.
A disabled-CEF error can appear in Android as well as WebUI; runtime WebUI
updates and FlareSolverr do not enable the embedded browser.

The `webview-navigation-snapshot.patch` overlay captures CEF main-frame navigation
URLs before Android Handler dispatch. This prevents a null URL from reaching
extensions' concurrent navigation sets. Subframes do not generate Android page
lifecycle callbacks; missing/empty main-frame URLs emit a URL-free warning instead
of an invented URL or successful callback. Genuine `about:blank` URLs remain valid.
Both page-start and page-finish callbacks use the snapshot. Focused AndroidCompat
JVM regressions run during each image build; native CEF gates remain required.
This repair does not turn an empty chapter page list into a valid download.
The 0.1.13 overlay also backported upstream [PR #2535](https://github.com/Suwayomi/Suwayomi-Server/pull/2535)
(`551cb073bb5bb32a6c6f0d39d36cfd2ef634cde9`): the canonical AOSP
`RenderProcessGoneDetail` implementation, including its Apache-2.0 notice.
The 0.1.14 server pin supplies this class itself, so the duplicate backport is
removed. Regression tests still construct both killed/crashed renderer details
without removing renderer-termination reporting or changing Gradle task ordering.

The 0.1.13 compatibility overlay `android-evaluation-semantics.patch` backports upstream
[PR #2517](https://github.com/Suwayomi/Suwayomi-Server/pull/2517)
(`9017f68f3815db6e1804ed3f5712f5e6f2b6a1ff`) and its required callsites/tests.
It replaces the function-body evaluation heuristic with Android-style script
completion via DevTools `Runtime.evaluate`. AllManga's multiline boolean poll
can then report true/false instead of always null; this conditionally restores
its detection logic, not proof of a website challenge or a repaired download.
No source timeout is increased. Images are published only after both native
architecture gates pass.

In 0.1.14, upstream supplies the production evaluation implementation and
`KcefEvaluationTest`; the overlay retains only `HAAllMangaEvaluationTest`.
The original overlay is a [historical test fixture](tests/fixtures/legacy-android-evaluation-semantics.patch),
not a patch applied to the newer server.

`node --test tests/evaluation_completion.test.cjs` exercises the removed wrapper,
the exact AllManga predicate and an ECMAScript reference, with a separate
JavaScript message-hook check. These are not native/provider tests. Canonical
`KcefEvaluationTest` exercises production completion-response conversion in the
image build. `HAAllMangaEvaluationTest` passes the exact predicate's actual
GraalJS completion through that production converter, without claiming native
CDP/provider coverage. Existing native CEF smoke gates exercise the WebSocket renderer,
not the extension's Android WebView provider or its native message bridge.
An actual provider fixture is not added: the current harness has no such entry
point, and this repair adds no public testing endpoint or SDK dependencies.
Verify native provider/bridge behavior and actual source downloads on the target
before declaring a source repaired.

Server updates shown inside Suwayomi refer to upstream server releases, not HA
app releases; install the tested HA image instead of downloading/replacing its
JAR. On November 2, 2026, `v2.4.2476` is a genuine upgrade from `v2.4.2435`;
preview WebUI `r3569` is unchanged. Before 0.1.6, bundled mode can incorrectly
offer the older upstream bundle `r3518`: its custom flavor and overridden archive
did not match the upstream update-check metadata. The candidate corrects that
metadata without hiding genuine server/runtime UI updates or changing stable
WebUI compatibility floors. See [update details](suwayomi/DOCS.md#update-notices).

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
node .\scripts\update-suwayomi.cjs 0.1.7
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

The HA Apps/store image at `suwayomi/icon.png` is an unmodified copy of upstream
[`faviconlogo-128.png`](https://github.com/Suwayomi/Suwayomi-Server/blob/6b481e8b62938b9c060eb210b934e0fbfdfa466b/server/src/main/resources/icon/faviconlogo-128.png)
(128 × 128, MPL-2.0; copyright Suwayomi contributors).
Its SHA-256 is `01f60bf4ccd803028d4559606b0738a39ef6b9bbb452b31b7dafd18e28fac076`.
HA reads this repository asset, not the sidebar's separate `mdi:` panel icon.

The initial server source pin is
[`6b481e8b62938b9c060eb210b934e0fbfdfa466b`](https://github.com/Suwayomi/Suwayomi-Server/tree/6b481e8b62938b9c060eb210b934e0fbfdfa466b)
(server `v2.4.2435`, from upstream master/preview). Packages 0.1.4/0.1.5 select WebUI
`r3569` independently through a packaging-only Gradle download override.
Candidate 0.1.6 pins server
[`7b061be09c9b62a89a63addd11ac91c40fc2510f`](https://github.com/Suwayomi/Suwayomi-Server/tree/7b061be09c9b62a89a63addd11ac91c40fc2510f)
and applies only the explicit bundled-WebUI metadata and scoped manga-cancellation
patches, retaining upstream compatibility floors.
Exact source and UI provenance are recorded in each image's
`/app/build-info.json`, including patch filenames and SHA-256; matching patch
sources are included in `/app/`. This package does not bundle or endorse third-party
extension stores or grant rights to manga content.
