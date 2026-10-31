import base64
import http.client
import http.server
import json
import os
import re
import select
import socket
import struct
import sys
import threading
import urllib.error
import urllib.request


PREFIX = "/api/hassio_ingress/suwayomi-test-session"
BACKEND = "http://172.30.32.3:8080"


def fetch(path: str, prefix: str = PREFIX) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(BACKEND + path, headers={"X-Ingress-Path": prefix})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def websocket() -> None:
    with socket.create_connection(("172.30.32.3", 8080), timeout=10) as connection:
        key = base64.b64encode(os.urandom(16)).decode()
        connection.sendall((
            "GET /api/graphql HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n"
            "Connection: Upgrade\r\nSec-WebSocket-Version: 13\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Protocol: graphql-transport-ws\r\n"
            f"X-Ingress-Path: {PREFIX}\r\n\r\n"
        ).encode())
        headers = bytearray()
        while not headers.endswith(b"\r\n\r\n"):
            part = connection.recv(1)
            if not part:
                raise RuntimeError("WebSocket upgrade closed without a response")
            headers.extend(part)
        assert headers.startswith(b"HTTP/1.1 101"), "GraphQL WebSocket upgrade failed"
        message = b'{"type":"connection_init"}'
        mask = os.urandom(4)
        connection.sendall(bytes((0x81, 0x80 | len(message))) + mask + bytes(c ^ mask[i % 4] for i, c in enumerate(message)))

        def read_exact(length: int) -> bytes:
            result = bytearray()
            while len(result) < length:
                part = connection.recv(length - len(result))
                if not part:
                    raise RuntimeError("WebSocket closed before its connection acknowledgement")
                result.extend(part)
            return bytes(result)

        frame = read_exact(2)
        assert frame[0] & 0x0F == 1, "Expected a WebSocket text acknowledgement"
        length = frame[1] & 0x7F
        if length == 126:
            length = struct.unpack("!H", read_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", read_exact(8))[0]
        assert json.loads(read_exact(length))["type"] == "connection_ack"


def probe() -> None:
    assert fetch("/", "")[0] == 400, "Missing ingress prefix was accepted"
    for invalid in ("/other", PREFIX + "/", PREFIX + '"><script>', "/api/hassio_ingress/../escape"):
        assert fetch("/", invalid)[0] == 400, "Invalid ingress prefix was accepted"
    status, headers, html = fetch("/")
    assert status == 200
    assert f'<base href="{PREFIX}/">'.encode() in html
    assert b'<base href="/">' not in html
    assert "no-store" in headers["Cache-Control"]
    paths = set(re.findall(rb'(?:src|href|data-src)="(\./[^"]+)"', html))
    for path in paths:
        status, headers, body = fetch(path.decode()[1:])
        assert status == 200 and body, f"Initial WebUI asset {path.decode()} returned {status} with {len(body)} bytes"
        content_type = headers["Content-Type"]
        if path.endswith(b".js"):
            assert "javascript" in content_type, "Incorrect JavaScript MIME type"
        if path.endswith(b".css"):
            assert "text/css" in content_type, "Incorrect CSS MIME type"
    manifest = json.loads(fetch("/site.webmanifest")[2])
    assert manifest["start_url"] == "."
    for icon in manifest["icons"]:
        assert icon["src"].startswith("./") and fetch(icon["src"][1:])[0] == 200
    for chunked in (False, True):
        connection = http.client.HTTPConnection("172.30.32.3", 8080, timeout=10)
        payload = b'{"query":"{ __typename }"}'
        connection.request(
            "POST", "/api/graphql", body=iter([payload]) if chunked else payload,
            headers={"Content-Type": "application/json", "X-Ingress-Path": PREFIX},
            encode_chunked=chunked,
        )
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read()) == {"data": {"__typename": "Query"}}
        connection.close()
    websocket()
    print(f"PASS: trusted ingress HTML, {len(paths)} initial assets, manifest/icons, fixed/chunked GraphQL and WebSocket acknowledgement")


class IngressSimulator(http.server.BaseHTTPRequestHandler):
    def log_message(self, _format: str, *arguments: str) -> None:
        pass

    def relay(self) -> None:
        if not self.path.startswith(PREFIX + "/"):
            self.send_error(404)
            return
        path = self.path[len(PREFIX):]
        if self.headers.get("Upgrade", "").lower() == "websocket":
            self.upgrade(path)
            return
        headers = {
            key: value for key, value in self.headers.items()
            if key.lower() not in ("host", "connection", "content-length", "transfer-encoding", "authorization", "x-ingress-path")
        }
        headers["X-Ingress-Path"] = PREFIX
        headers["Host"] = self.headers["Host"]
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else None
        request = urllib.request.Request(BACKEND + path, data=body, headers=headers, method=self.command)
        try:
            response = urllib.request.urlopen(request, timeout=30)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            self.send_response(response.code)
            for key, value in response.headers.items():
                if key.lower() not in ("connection", "transfer-encoding", "content-length"):
                    self.send_header(key, value)
            self.end_headers()
            self.wfile.write(response.read())

    def upgrade(self, path: str) -> None:
        headers = {key: value for key, value in self.headers.items() if key.lower() not in ("authorization", "x-ingress-path")}
        headers["X-Ingress-Path"] = PREFIX
        request = f"GET {path} HTTP/1.1\r\n" + "".join(f"{key}: {value}\r\n" for key, value in headers.items()) + "\r\n"
        self.close_connection = True
        with socket.create_connection(("172.30.32.3", 8080), timeout=10) as upstream:
            upstream.sendall(request.encode())
            while True:
                ready, _, _ = select.select([self.connection, upstream], [], [], 10)
                if not ready:
                    return
                for source in ready:
                    payload = source.recv(65536)
                    if not payload:
                        return
                    (upstream if source is self.connection else self.connection).sendall(payload)

    do_GET = relay
    do_POST = relay
    do_HEAD = relay
    do_OPTIONS = relay
    do_DELETE = relay
    do_PUT = relay


class ExternalSolverStub(http.server.BaseHTTPRequestHandler):
    def log_message(self, _format: str, *arguments: str) -> None:
        pass

    def do_GET(self) -> None:
        body = b'{"msg":"FlareSolverr is ready!","version":"test-only"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    if "--serve" in sys.argv:
        solver = http.server.ThreadingHTTPServer(("0.0.0.0", 8191), ExternalSolverStub)
        threading.Thread(target=solver.serve_forever, daemon=True).start()
        http.server.ThreadingHTTPServer(("0.0.0.0", 8123), IngressSimulator).serve_forever()
    else:
        probe()
