import argparse
import base64
import json
import os
import secrets
import socket
import ssl
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]
PREFIX = "/api/hassio_ingress/suwayomi-test-session"


def docker(*arguments: str) -> str:
    return subprocess.check_output(["docker", *arguments], text=True).strip()


def request(url: str, authorization: str = "", ingress: str = "") -> tuple[int, bytes]:
    headers = {}
    if authorization:
        headers["Authorization"] = "Basic " + authorization
    if ingress:
        headers["X-Ingress-Path"] = ingress
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def wait_for_app(name: str, url: str, expected: int, authorization: str) -> None:
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        if docker("inspect", "--format", "{{.State.Running}}", name) != "true":
            print(docker("logs", "--tail", "80", name))
            raise RuntimeError("App exited before becoming ready")
        try:
            if request(url, authorization)[0] == expected:
                return
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            pass
        time.sleep(1)
    print(docker("logs", "--tail", "80", name))
    raise RuntimeError("App did not reach its expected HTTP state within 150 seconds")


def make_test_certificate(image: str, directory: Path) -> None:
    docker(
        "run", "--rm", "--entrypoint", "openssl",
        "--mount", f"type=bind,source={directory},target=/ssl", image,
        "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-sha256",
        "-keyout", "/ssl/privkey.pem", "-out", "/ssl/fullchain.pem", "-days", "1",
        "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
    )


def certificate_peer(port: int, ca: Path) -> bytes:
    context = ssl.create_default_context(cafile=str(ca))
    with socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
        with context.wrap_socket(connection, server_hostname="localhost") as secured:
            assert secured.version() in ("TLSv1.2", "TLSv1.3")
            return secured.getpeercert(binary_form=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image")
    parser.add_argument("--browser", action="store_true")
    parser.add_argument("--usb-storage", action="store_true")
    parser.add_argument("--tls", action="store_true")
    parser.add_argument("--browser-seconds", type=int, default=0)
    arguments = parser.parse_args()
    if arguments.tls and arguments.browser:
        parser.error("TLS smoke uses a verified private test CA; run browser/iframe checks separately without --tls")
    identifier = "suwayomi-check-" + uuid.uuid4().hex[:12]
    platform = "linux/" + docker("image", "inspect", "--format", "{{.Architecture}}", arguments.image)
    app = identifier + "-app"
    client = identifier + "-supervisor"
    volume = identifier + "-data"
    usb_volume = identifier + "-usb"
    network_created = False
    volume_created = False
    usb_created = False
    app_created = False
    client_created = False
    with tempfile.TemporaryDirectory(prefix="suwayomi-ha-") as temporary:
        data = Path(temporary)
        data.chmod(0o755)
        options = yaml.safe_load((ROOT / "suwayomi" / "config.yaml").read_text())["options"]
        options.update(
            auth_password=secrets.token_urlsafe(24) + ' "quote" \\ #',
            direct_access_enabled=True,
            storage_path="/media/torrent_disk/suwayomi" if arguments.usb_storage else "/data/server",
            downloads_path="",
            backup_path="",
            local_source_path="",
            flaresolverr_enabled=True,
            flaresolverr_url="http://172.30.32.2:8191",
            ssl=arguments.tls,
        )
        tls_directory = data / "ssl"
        tls_mounts = []
        if arguments.tls:
            tls_directory.mkdir(mode=0o755)
            make_test_certificate(arguments.image, tls_directory)
            tls_mounts = ["--mount", f"type=bind,source={tls_directory},target=/ssl,readonly"]
            trusted = tls_directory / "trusted-test-ca.pem"
            trusted.write_bytes((tls_directory / "fullchain.pem").read_bytes())
            urllib.request.install_opener(urllib.request.build_opener(
                urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=str(trusted))),
            ))
        options_path = data / "options.json"
        options_path.write_text(json.dumps(options), encoding="utf-8")
        authorization = base64.b64encode(f'{options["auth_username"]}:{options["auth_password"]}'.encode()).decode()
        try:
            docker("network", "create", "--subnet", "172.30.32.0/24", identifier)
            network_created = True
            docker("volume", "create", volume)
            volume_created = True
            storage_mounts = []
            if arguments.usb_storage:
                docker("volume", "create", usb_volume)
                usb_created = True
                storage_mounts = ["--mount", f"type=volume,source={usb_volume},target=/media/torrent_disk"]
            docker(
                "run", "--detach", "--platform", platform, "--name", client, "--network", identifier, "--ip", "172.30.32.2",
                "--publish", "127.0.0.1::8123", "--entrypoint", "python3",
                "--mount", f"type=bind,source={ROOT / 'tests'},target=/tests,readonly",
                arguments.image, "/tests/suwayomi_probe.py", "--serve",
            )
            client_created = True
            docker(
                "run", "--detach", "--platform", platform, "--name", app, "--network", identifier, "--ip", "172.30.32.3",
                "--publish", "127.0.0.1::4567", "--publish", "127.0.0.1::8080",
                "--mount", f"type=volume,source={volume},target=/data",
                "--mount", f"type=bind,source={options_path},target=/data/options.json,readonly",
                *storage_mounts, *tls_mounts, arguments.image,
            )
            app_created = True
            direct_port = docker("port", app, "4567/tcp").rsplit(":", 1)[1]
            untrusted_port = docker("port", app, "8080/tcp").rsplit(":", 1)[1]
            scheme = "https" if arguments.tls else "http"
            direct_url = scheme + "://127.0.0.1:" + direct_port
            wait_for_app(app, direct_url + "/", 200, authorization)
            assert request(direct_url + "/")[0] == 401, "Direct access must require authentication"
            assert request(direct_url + "/api/graphql", ingress=PREFIX)[0] == 401, "Ingress headers bypassed direct authentication"
            assert request(direct_url + "/", base64.b64encode(b"wrong:wrong").decode())[0] == 401
            assert b'<base href="/">' in request(direct_url + "/", authorization)[1], "Direct HTML was rewritten"
            assert request("http://127.0.0.1:" + untrusted_port + "/", ingress=PREFIX)[0] == 403, "Ingress trusted a non-Supervisor caller"
            ingress_port = docker("port", client, "8123/tcp").rsplit(":", 1)[1]
            ingress_url = "http://127.0.0.1:" + ingress_port + PREFIX + "/"
            print(docker("exec", client, "python3", "/tests/suwayomi_probe.py"), flush=True)
            metadata = json.loads(docker("exec", app, "cat", "/app/build-info.json"))
            query = urllib.request.Request(
                direct_url + "/api/graphql",
                data=json.dumps({"query": "{ aboutServer { version } aboutWebUI { tag } settings { flareSolverrEnabled flareSolverrUrl kcefEnabled webUIFlavor } }"}).encode(),
                headers={"Authorization": "Basic " + authorization, "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(query, timeout=20) as response:
                configured = json.load(response)
            assert configured == {"data": {
                "aboutServer": {"version": metadata["server_version"]},
                "aboutWebUI": {"tag": metadata["webui_revision"]},
                "settings": {
                    "flareSolverrEnabled": True, "flareSolverrUrl": options["flaresolverr_url"],
                    "kcefEnabled": False, "webUIFlavor": "CUSTOM",
                },
            }}, "Actual server, bundled UI or external FlareSolverr settings differ from the packaged contract"
            print(f'PASS: updated backend {metadata["server_version"]}, packaged WebUI {metadata["webui_revision"]} and existing-service FlareSolverr configuration', flush=True)
            if arguments.tls:
                original = certificate_peer(int(direct_port), trusted)
                expected = ssl.PEM_cert_to_DER_cert((tls_directory / "fullchain.pem").read_text())
                assert original == expected, "The HTTPS listener did not reuse the configured certificate"
                assert request("http://127.0.0.1:" + direct_port + "/")[0] == 400, "Plaintext was accepted on the HTTPS listener"
                make_test_certificate(arguments.image, tls_directory)
                with trusted.open("ab") as output:
                    output.write((tls_directory / "fullchain.pem").read_bytes())
                expected = ssl.PEM_cert_to_DER_cert((tls_directory / "fullchain.pem").read_text())
                deadline = time.monotonic() + 80
                while time.monotonic() < deadline:
                    if certificate_peer(int(direct_port), trusted) == expected:
                        break
                    time.sleep(1)
                else:
                    raise RuntimeError("Renewed certificate was not reloaded within the declared interval")
                urllib.request.install_opener(urllib.request.build_opener(
                    urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=str(trusted))),
                ))
                print("PASS: verified HTTPS certificate, hostname/trust, plaintext rejection and renewal reload without a backend restart", flush=True)
            storage = options["storage_path"]
            docker("exec", app, "python3", "-c",
                f"from pathlib import Path; p=Path({storage!r}); assert (p/'database.mv.db').is_file(); "
                "assert (p/'server.conf').is_file(); assert all((p/name).is_dir() for name in ('downloads','backups','local')); "
                + ("assert not Path('/data/server').exists()" if arguments.usb_storage else "assert p==Path('/data/server')"))
            category_request = urllib.request.Request(
                direct_url + "/api/graphql",
                data=json.dumps({"query": 'mutation { createCategory(input:{name:"HA storage persistence check"}) { category { id name } } }'}).encode(),
                headers={"Authorization": "Basic " + authorization, "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(category_request, timeout=20) as response:
                created = json.load(response)
            assert "errors" not in created
            category = created["data"]["createCategory"]["category"]
            assert category["name"] == "HA storage persistence check"
            if arguments.browser:
                environment = os.environ | {"SUWAYOMI_TEST_AUTH": authorization}
                subprocess.run(
                    ["node", str(ROOT / "tests" / "suwayomi_browser.mjs"), ingress_url, direct_url + "/"],
                    env=environment, check=True,
                )
            marker = storage + "/ha-persistence-check"
            docker("exec", "--user", "1001", app, "python3", "-c", f"from pathlib import Path; Path({marker!r}).write_text('preserved')")
            options["direct_access_enabled"] = False
            options_path.write_text(json.dumps(options), encoding="utf-8")
            docker("restart", "--time", "60", app)
            direct_port = docker("port", app, "4567/tcp").rsplit(":", 1)[1]
            direct_url = scheme + "://127.0.0.1:" + direct_port
            wait_for_app(app, direct_url + "/", 403, authorization)
            assert request(direct_url + "/", authorization, PREFIX)[0] == 403, "Disabled direct access accepted a request"
            assert docker("exec", app, "cat", marker) == "preserved"
            persistence_request = urllib.request.Request(
                ingress_url + "api/graphql",
                data=json.dumps({"query": f'{{ category(id:{category["id"]}) {{ id name }} }}'}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(persistence_request, timeout=20) as response:
                assert json.load(response) == {"data": {"category": category}}, "Library database did not persist in the selected storage root"
            logs = docker("logs", app)
            assert "Downloading WebUI" not in logs and "Downloading WebUI zip" not in logs, "Packaged UI unexpectedly required an Internet download"
            print(docker("exec", client, "python3", "/tests/suwayomi_probe.py"), flush=True)
            print("PASS: authentication, trusted ingress, streamed API/WebSockets, disabled direct port and persistent state", flush=True)
            print(f"PASS: {'mounted USB' if arguments.usb_storage else 'private /data'} storage root, actual H2 library persistence, downloads/backups/local paths", flush=True)
            if arguments.browser_seconds:
                print(f"Temporary simulated ingress browser URL: {ingress_url}", flush=True)
                time.sleep(arguments.browser_seconds)
        except (AssertionError, RuntimeError, subprocess.CalledProcessError, urllib.error.URLError, ConnectionError):
            if app_created:
                print(docker("logs", "--tail", "80", app), flush=True)
            if client_created:
                print(docker("logs", "--tail", "30", client), flush=True)
            raise
        finally:
            for name, created in ((client, client_created), (app, app_created)):
                if created:
                    subprocess.run(["docker", "rm", "--force", name], check=True, stdout=subprocess.DEVNULL)
            if network_created:
                subprocess.run(["docker", "network", "rm", identifier], check=True, stdout=subprocess.DEVNULL)
            if volume_created:
                subprocess.run(["docker", "volume", "rm", volume], check=True, stdout=subprocess.DEVNULL)
            if usb_created:
                subprocess.run(["docker", "volume", "rm", usb_volume], check=True, stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
