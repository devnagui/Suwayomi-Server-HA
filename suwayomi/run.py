import base64
import hashlib
import json
import math
import os
import re
import signal
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any


OPTION_SETTINGS = {
    "embedded_browser_enabled": "kcefEnabled",
    "downloads_path": "downloadsPath",
    "backup_path": "backupPath",
    "local_source_path": "localSourcePath",
    "extension_stores": "extensionStores",
    "download_as_cbz": "downloadAsCbz",
    "max_sources_in_parallel": "maxSourcesInParallel",
    "global_update_interval": "globalUpdateInterval",
    "backup_time": "backupTime",
    "backup_interval": "backupInterval",
    "backup_ttl": "backupTTL",
    "debug_logs_enabled": "debugLogsEnabled",
    "flaresolverr_enabled": "flareSolverrEnabled",
    "flaresolverr_url": "flareSolverrUrl",
    "flaresolverr_timeout": "flareSolverrTimeout",
    "flaresolverr_session_name": "flareSolverrSessionName",
    "flaresolverr_session_ttl": "flareSolverrSessionTtl",
    "flaresolverr_as_response_fallback": "flareSolverrAsResponseFallback",
}
FIXED_SETTINGS = {
    "ip": "127.0.0.1",
    "port": 4568,
    "webUIEnabled": True,
    "webUIFlavor": "CUSTOM",
    "webUIChannel": "BUNDLED",
    "webUIUpdateCheckInterval": 0,
    "webUISubpath": "",
    "initialOpenInBrowserEnabled": False,
    "systemTrayEnabled": False,
    "authMode": "BASIC_AUTH",
}
WEBUI_DEFAULTS = {"webui_channel": "bundled", "webui_update_check_interval": 23}
OPTION_DEFAULTS = WEBUI_DEFAULTS | {"embedded_browser_enabled": False}
EXTRA_OPTIONS = {"auth_username", "auth_password", "direct_access_enabled", "jvm_heap_mb", "advanced_server_settings", "storage_path", "ssl", "certfile", "keyfile"} | set(WEBUI_DEFAULTS)
RESERVED_SETTINGS = set(OPTION_SETTINGS.values()) | set(FIXED_SETTINGS) | {
    "rootDir", "authUsername", "authPassword", "basicAuthEnabled", "basicAuthUsername", "basicAuthPassword"
}


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON setting: {key}")
        result[key] = value
    return result


def validate_json_value(value: Any) -> None:
    if value is None or isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Settings must not contain null or non-finite numbers")
    if isinstance(value, dict):
        for child in value.values():
            validate_json_value(child)
    elif isinstance(value, list):
        for child in value:
            validate_json_value(child)


def storage_paths(options: dict[str, Any]) -> dict[str, str]:
    root = options["storage_path"]
    paths = {"storage_path": root}
    for key, name in (("downloads_path", "downloads"), ("backup_path", "backups"), ("local_source_path", "local")):
        value = options[key]
        if not isinstance(value, str):
            raise ValueError(f"{key} must be an absolute path or empty")
        paths[key] = str(PurePosixPath(root) / name) if value == "" and isinstance(root, str) else value
    for key, value in paths.items():
        if not isinstance(value, str) or not value or any(ord(c) < 32 for c in value):
            raise ValueError(f"{key} must be an absolute path below /data or /media")
        path = PurePosixPath(value)
        if not path.is_absolute() or ".." in path.parts or not (value.startswith("/data/") or value.startswith("/media/")):
            raise ValueError(f"{key} must be an absolute path below /data or /media without parent traversal")
        paths[key] = str(path)
    return paths


def build_settings(options: dict[str, Any], supported: set[str]) -> dict[str, Any]:
    options = OPTION_DEFAULTS | options
    expected = set(OPTION_SETTINGS) | EXTRA_OPTIONS
    if set(options) != expected:
        raise ValueError("HA options do not match this app version; check the Configuration tab")
    username = options["auth_username"]
    password = options["auth_password"]
    if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", username):
        raise ValueError("auth_username must contain 1-64 letters, digits, dots, underscores or hyphens")
    if not isinstance(password, str) or not 12 <= len(password) <= 256 or any(ord(c) < 32 or ord(c) == 127 for c in password):
        raise ValueError("Set auth_password to 12-256 characters without control characters before starting")
    for key in ("direct_access_enabled", "download_as_cbz", "debug_logs_enabled", "flaresolverr_enabled", "flaresolverr_as_response_fallback", "ssl", "embedded_browser_enabled"):
        if type(options[key]) is not bool:
            raise ValueError(f"{key} must be a boolean")
    for key, minimum, maximum in (
        ("webui_update_check_interval", 0, 23),
        ("jvm_heap_mb", 256, 1536),
        ("max_sources_in_parallel", 1, 64),
        ("backup_interval", 0, 36500),
        ("backup_ttl", 0, 36500),
        ("flaresolverr_timeout", 1, 600),
        ("flaresolverr_session_ttl", 0, 525600),
    ):
        if type(options[key]) is not int or not minimum <= options[key] <= maximum:
            raise ValueError(f"{key} must be an integer in {minimum}..{maximum}")
    interval = options["global_update_interval"]
    channel = options["webui_channel"]
    if not isinstance(channel, str) or channel not in ("bundled", "stable", "preview"):
        raise ValueError("webui_channel must be bundled, stable or preview")
    if type(interval) not in (int, float) or not math.isfinite(interval) or interval < 0:
        raise ValueError("global_update_interval must be a finite non-negative number")
    if not isinstance(options["backup_time"], str) or not re.fullmatch(r"([01][0-9]|2[0-3]):[0-5][0-9]", options["backup_time"]):
        raise ValueError("backup_time must use HH:MM")
    if not isinstance(options["extension_stores"], list):
        raise ValueError("extension_stores must be a list of index URLs")
    for url in options["extension_stores"]:
        validate_url(url, "extension_stores")
    flare = validate_url(options["flaresolverr_url"], "flaresolverr_url")
    if flare.path not in ("", "/"):
        raise ValueError("flaresolverr_url must be the service root, without /v1")
    if not isinstance(options["flaresolverr_session_name"], str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", options["flaresolverr_session_name"]):
        raise ValueError("flaresolverr_session_name must contain 1-64 safe identifier characters")
    try:
        advanced = json.loads(options["advanced_server_settings"], object_pairs_hook=unique_object)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError("advanced_server_settings must be a JSON object") from error
    if not isinstance(advanced, dict):
        raise ValueError("advanced_server_settings must be a JSON object")
    validate_json_value(advanced)
    for key in advanced:
        if key in RESERVED_SETTINGS:
            raise ValueError(f"{key} is managed by the app; use its HA option instead")
        if key not in supported:
            raise ValueError(f"Unknown or deprecated global server setting: {key}")
    settings = advanced | {setting: options[option] for option, setting in OPTION_SETTINGS.items()} | FIXED_SETTINGS
    settings["webUIFlavor"] = "CUSTOM" if channel == "bundled" else "WEBUI"
    settings["webUIChannel"] = channel.upper()
    settings["webUIUpdateCheckInterval"] = 0 if channel == "bundled" else options["webui_update_check_interval"]
    paths = storage_paths(options)
    settings["rootDir"] = paths["storage_path"]
    for key in ("downloads_path", "backup_path", "local_source_path"):
        settings[OPTION_SETTINGS[key]] = paths[key]
    settings["authUsername"] = username
    settings["authPassword"] = password
    missing = set(settings) - supported - {"rootDir"}
    if missing:
        raise ValueError(f"Upstream settings contract changed: {', '.join(sorted(missing))}")
    return settings


def certificate_paths(options: dict[str, Any], root: Path = Path("/ssl")) -> tuple[Path, Path] | None:
    for key in ("certfile", "keyfile"):
        value = options[key]
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._/-]+", value):
            raise ValueError(f"{key} must be a certificate filename below /ssl")
        relative = PurePosixPath(value)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"{key} must stay below /ssl")
    if not options["ssl"]:
        return None
    paths = tuple(root / options[key] for key in ("certfile", "keyfile"))
    for path in paths:
        if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
            raise ValueError("Configured TLS certificate/key is missing or escapes /ssl")
    return paths[0], paths[1]


def certificate_fingerprint(paths: tuple[Path, Path]) -> tuple[tuple[str, int, int, int], ...]:
    values = []
    for path in paths:
        state = path.stat()
        values.append((str(path.resolve()), state.st_ino, state.st_mtime_ns, state.st_size))
    return tuple(values)


def validate_url(value: Any, key: str) -> urllib.parse.SplitResult:
    if not isinstance(value, str) or any(c.isspace() for c in value):
        raise ValueError(f"{key} must contain an HTTP(S) URL without whitespace")
    url = urllib.parse.urlsplit(value)
    try:
        port = url.port
    except ValueError as error:
        raise ValueError(f"{key} contains an invalid port") from error
    if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment or port == 0:
        raise ValueError(f"{key} must contain an HTTP(S) URL without credentials, queries or fragments")
    return url


def prepare_path(value: Any, roots: tuple[Path, ...] = (Path("/data"), Path("/media"))) -> Path:
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise ValueError("Storage paths must be absolute paths below /data or /media")
    path = Path(value).resolve()
    root = next((root.resolve() for root in roots if path.is_relative_to(root.resolve()) and path != root.resolve()), None)
    if root is None:
        raise ValueError("Storage paths must stay below /data or /media, including symlink targets")
    if not root.is_dir():
        raise ValueError("The configured storage mount is unavailable")
    if root.name == "media":
        volume = root / path.relative_to(root).parts[0]
        if not os.path.ismount(volume) or path == volume:
            raise ValueError("The media volume is not mounted; refusing to create a fallback directory on the SD card")
    missing = []
    parent = path
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for folder in reversed(missing):
        folder.mkdir(exist_ok=True)
        if os.name == "posix":
            os.chown(folder, 1001, 1001)
    if os.name == "posix":
        os.chown(path, 1001, 1001)
    return path


def java_arguments(settings: dict[str, Any], heap: int) -> str:
    arguments = [
        f"-Xmx{heap}m",
        "-Djava.awt.headless=true",
        "-Djunrar.extractor.thread-keep-alive-seconds=30",
    ]
    arguments.extend(
        f"-Dsuwayomi.tachidesk.config.server.{key}="
        + (value if key == "rootDir" else json.dumps(value, ensure_ascii=True, allow_nan=False))
        for key, value in sorted(settings.items())
    )
    arguments.extend(["-jar", "/app/suwayomi.jar"])
    return "\n".join('"' + argument.replace("\\", "\\\\").replace('"', '\\"') + '"' for argument in arguments) + "\n"


def java_command(embedded_browser: bool) -> list[str]:
    command = ["java", "@/run/suwayomi/java.args"]
    if embedded_browser:
        command = ["xvfb-run", "--auto-servernum", "--server-args=-screen 0 1280x720x24", *command]
    return command


def terminate_child(child: subprocess.Popen) -> None:
    if child.poll() is None:
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def prepare_webui(target: Path, packaged: Path = Path("/app/webui")) -> None:
    if not (packaged / "index.html").is_file():
        raise ValueError("The packaged server WebUI is missing")
    fingerprint = {
        "revision": (packaged / "revision").read_text(encoding="utf-8").strip(),
        "index_sha256": hashlib.sha256((packaged / "index.html").read_bytes()).hexdigest(),
    }
    marker = target / ".ha-webui-package.json"
    if target.is_symlink():
        raise ValueError("Existing WebUI link is preserved; move it aside before using USB-compatible packaged storage")
    if target.exists():
        if not marker.is_file():
            raise ValueError("Existing WebUI directory is preserved; move it aside before using the packaged UI")
        if json.loads(marker.read_text(encoding="utf-8")) == fingerprint:
            return
    staging = target.with_name(f"{target.name}.staging-{time.time_ns()}")
    shutil.copytree(packaged, staging)
    (staging / marker.name).write_text(json.dumps(fingerprint) + "\n", encoding="utf-8")
    (staging / marker.name).chmod(0o644)
    previous = None
    if target.exists():
        previous = target.with_name(f"{target.name}.previous-{time.time_ns()}")
        target.rename(previous)
    try:
        staging.rename(target)
    except OSError:
        if previous is not None:
            previous.rename(target)
        raise


def prepare_selected_webui(target: Path, channel: str, packaged: Path = Path("/app/webui")) -> None:
    if channel not in ("bundled", "stable", "preview"):
        raise ValueError("Unknown WebUI channel")
    if channel == "bundled" and (
        not (packaged / "index.html").is_file() or not (packaged / "revision").is_file()
    ):
        raise ValueError("The packaged WebUI is missing; existing content is preserved")
    state_file = target.with_name(".ha-webui-mode.json")
    if target.is_symlink() or state_file.is_symlink():
        raise ValueError("Existing WebUI link or mode link is preserved; move it aside before changing modes")
    previous_channel = "bundled"
    if state_file.exists():
        state = json.loads(state_file.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or set(state) != {"channel"} or state["channel"] not in ("bundled", "stable", "preview"):
            raise ValueError("Invalid managed WebUI mode marker; existing content is preserved")
        previous_channel = state["channel"]
    if target.exists() and (
        not target.is_dir() or
        not state_file.exists() and not (target / ".ha-webui-package.json").is_file()
    ):
        raise ValueError("Existing unmanaged WebUI content is preserved; move it aside before selecting a channel")
    previous = None
    if target.exists() and channel != previous_channel:
        previous = target.with_name(f"{target.name}.previous-{previous_channel}-{time.time_ns()}")
        target.rename(previous)
    try:
        if channel == "bundled":
            prepare_webui(target, packaged)
        staging = state_file.with_name(f"{state_file.name}.staging-{time.time_ns()}")
        staging.write_text(json.dumps({"channel": channel}) + "\n", encoding="utf-8")
        staging.replace(state_file)
    except OSError:
        if previous is not None and not target.exists():
            previous.rename(target)
        raise


def render_nginx(template: str, username: str, password: str, direct: bool, tls: tuple[Path, Path] | None = None) -> str:
    authorization = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    directives = ""
    if tls is not None:
        cert, key = tls
        directives = (
            f'ssl_certificate "{cert}";\n        ssl_certificate_key "{key}";\n'
            "        ssl_protocols TLSv1.2 TLSv1.3;\n"
            "        ssl_session_cache shared:TLS:1m;\n"
            "        ssl_session_timeout 10m;\n"
            "        ssl_session_tickets off;"
        )
    return template.replace("__INGRESS_AUTH__", authorization).replace(
        "__DIRECT_ACCESS__", "" if direct else "return 403;"
    ).replace("__DIRECT_LISTEN__", "listen 4567 ssl;" if tls is not None else "listen 4567;").replace("__DIRECT_TLS__", directives)


def private_file(path: Path, content: str, uid: int = 0, gid: int = 0, mode: int = 0o600) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(content)
    os.chown(path, uid, gid)
    os.chmod(path, mode)


def wait_ready(java: subprocess.Popen, authorization: str) -> None:
    request = urllib.request.Request("http://127.0.0.1:4568/", headers={"Authorization": f"Basic {authorization}"})
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if java.poll() is not None:
            raise RuntimeError("Suwayomi exited during startup; inspect the preceding server error")
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                body = response.read()
            if response.status == 200 and b"<base " in body:
                if body.count(b'<base href="/">') != 1 or re.search(rb'(?:src|href|data-src)=["\']/assets/', body):
                    raise RuntimeError("Selected WebUI ingress markup contract changed; no proxy was started")
                assets = set(re.findall(rb'(?:src|href|data-src)="(\./[^"]+)"', body))
                if not assets:
                    raise RuntimeError("Selected WebUI has no recognized initial assets")
                for asset in assets:
                    asset_request = urllib.request.Request(
                        "http://127.0.0.1:4568" + asset.decode("utf-8")[1:],
                        headers={"Authorization": f"Basic {authorization}"},
                    )
                    with urllib.request.urlopen(asset_request, timeout=3) as response:
                        if response.status != 200 or not response.read(1):
                            break
                else:
                    return
        except urllib.error.HTTPError as error:
            if error.code not in (404, 503):
                raise RuntimeError(f"Suwayomi startup returned HTTP {error.code}") from error
        except urllib.error.URLError:
            pass
        time.sleep(1)
    raise RuntimeError("Suwayomi did not become ready within 120 seconds")


def main() -> None:
    os.umask(0o077)
    options = OPTION_DEFAULTS | json.loads(Path("/data/options.json").read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    metadata = json.loads(Path("/app/build-info.json").read_text(encoding="utf-8"))
    settings = build_settings(options, set(metadata["server_settings"]))
    tls = certificate_paths(options)
    if tls is not None:
        subprocess.run(["openssl", "x509", "-in", str(tls[0]), "-noout", "-checkend", "0"], check=True)
    paths = storage_paths(options)
    storage = prepare_path(paths["storage_path"])
    prepare_selected_webui(storage / "webUI", options["webui_channel"])
    for key in ("downloads_path", "backup_path", "local_source_path"):
        prepare_path(paths[key])
    runtime = Path("/run/suwayomi")
    runtime.mkdir(exist_ok=True)
    os.chown(runtime, 0, 1001)
    os.chmod(runtime, 0o750)
    private_file(runtime / "java.args", java_arguments(settings, options["jvm_heap_mb"]), uid=1001, gid=1001)
    hashed = subprocess.run(
        ["openssl", "passwd", "-6", "-stdin"], input=options["auth_password"] + "\n",
        text=True, capture_output=True, check=True,
    ).stdout.strip()
    private_file(runtime / "htpasswd", f'{options["auth_username"]}:{hashed}\n', gid=1001, mode=0o640)
    nginx = render_nginx(Path("/app/nginx.conf").read_text(encoding="utf-8"), options["auth_username"], options["auth_password"], options["direct_access_enabled"], tls)
    private_file(runtime / "nginx.conf", nginx)
    subprocess.run(["nginx", "-t", "-c", str(runtime / "nginx.conf")], check=True)
    print(
        f'Starting Suwayomi {metadata["source_revision"][:12]} / WebUI channel {options["webui_channel"]} '
        f'(bundled fallback {metadata["webui_revision"]})',
        flush=True,
    )
    children: list[subprocess.Popen] = []
    stopping = False

    def stop(_signum: int, _frame: Any) -> None:
        nonlocal stopping
        stopping = True
        for child in children:
            terminate_child(child)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        java = subprocess.Popen(java_command(options["embedded_browser_enabled"]), user=1001, group=1001, start_new_session=True)
        children.append(java)
        authorization = base64.b64encode(f'{options["auth_username"]}:{options["auth_password"]}'.encode("utf-8")).decode("ascii")
        wait_ready(java, authorization)
        if stopping:
            return
        proxy = subprocess.Popen(["nginx", "-c", str(runtime / "nginx.conf"), "-g", "daemon off;"], start_new_session=True)
        children.append(proxy)
        tls_fingerprint = certificate_fingerprint(tls) if tls is not None else None
        tls_check_at = time.monotonic() + 60
        if options["flaresolverr_enabled"]:
            try:
                with urllib.request.urlopen(options["flaresolverr_url"], timeout=5) as response:
                    if response.status != 200:
                        raise RuntimeError("FlareSolverr returned an unexpected status")
            except urllib.error.URLError:
                print("WARNING: External FlareSolverr is unavailable; protected sources need that service restored.", file=sys.stderr, flush=True)
        while not stopping:
            for child in children:
                if child.poll() is not None:
                    raise RuntimeError("A required process exited; inspect the preceding server/proxy error")
            if tls is not None and time.monotonic() >= tls_check_at:
                tls_check_at = time.monotonic() + 60
                try:
                    certificate_paths(options)
                    changed = certificate_fingerprint(tls)
                    if changed != tls_fingerprint:
                        subprocess.run(["openssl", "x509", "-in", str(tls[0]), "-noout", "-checkend", "0"], check=True)
                        subprocess.run(["nginx", "-t", "-c", str(runtime / "nginx.conf")], check=True)
                        proxy.send_signal(signal.SIGHUP)
                        tls_fingerprint = changed
                        print("Reloaded renewed HA TLS certificate without restarting Suwayomi.", flush=True)
                except (ValueError, OSError, subprocess.CalledProcessError) as error:
                    print(f"ERROR: HA TLS certificate reload failed; retaining the active certificate and retrying in 60 seconds: {error}", file=sys.stderr, flush=True)
            time.sleep(0.5)
    finally:
        stop(0, None)
        for child in children:
            try:
                child.wait(timeout=45)
            except subprocess.TimeoutExpired:
                print("WARNING: Process exceeded its shutdown deadline; terminating it.", file=sys.stderr, flush=True)
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait()


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, RuntimeError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"Suwayomi app startup failed: {error}") from error
