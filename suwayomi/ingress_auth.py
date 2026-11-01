import base64
import http.server
import json
import os
import socketserver
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


BACKEND = "http://127.0.0.1:4568/api/graphql"
BOOTSTRAP = r"""(() => {
    'use strict';
    const base = new URL(document.baseURI);
    const subpath = base.pathname.replace(/\/$/, '');
    if (!/^\/api\/hassio_ingress\/[A-Za-z0-9_-]+$/.test(subpath)) {
        throw new Error('Invalid HA ingress authentication scope');
    }
    const key = `suwayomi_webui_${subpath}_auth-refresh-token`;
    const fail = () => {
        localStorage.removeItem(key);
        console.error('HA ingress token refresh failed; administrator session is unavailable');
        const alert = document.createElement('div');
        alert.id = 'ha-ingress-auth-failure';
        alert.setAttribute('role', 'alert');
        alert.textContent = 'HA ingress authentication failed. Check the redacted app logs and restart the app only after correcting administrator credentials or token expiry.';
        alert.style.cssText = 'position:fixed;inset:0;z-index:2147483647;background:#181818;color:white;padding:2rem;font:1rem sans-serif';
        (document.body || document.documentElement).appendChild(alert);
    };
    localStorage.setItem(key, 'ha-ingress-session');
    localStorage.removeItem(`suwayomi_webui_${subpath}_serverBaseURL`);
    const nativeFetch = window.fetch.bind(window);
    window.fetch = async (input, init) => {
        const url = new URL(typeof input === 'string' ? input : input.url, document.baseURI);
        if (url.origin === base.origin && url.pathname === subpath + '/api/graphql' && init?.body) {
            let operation;
            try {
                operation = JSON.parse(init.body);
            } catch (error) {
                if (!(error instanceof SyntaxError)) throw error;
            }
            if (['REFRESH_ACCESS_TOKEN', 'USER_REFRESH'].includes(operation?.operationName) &&
                operation.variables?.refreshToken === 'ha-ingress-session') {
                let response;
                try {
                    response = await nativeFetch(new URL('__ha_auth/session', base), {
                        method: 'POST', credentials: 'same-origin', cache: 'no-store',
                        headers: { 'Content-Type': 'application/json' }, body: '{}',
                    });
                } catch (error) {
                    fail();
                    throw new Error('HA ingress authentication transport failed (details redacted)', { cause: error });
                }
                if (!response.ok) {
                    fail();
                    throw new Error('HA ingress authentication failed; restart the app after checking its redacted logs');
                }
                return response;
            }
        }
        return nativeFetch(input, init);
    };
})();
"""


def graphql(query, variables=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(
        BACKEND, data=json.dumps({"query": query, "variables": variables or {}}).encode(),
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            result = json.load(response)
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        raise RuntimeError("Native authentication transport or response failed (details redacted)") from error
    if not isinstance(result, dict) or result.get("errors") or not isinstance(result.get("data"), dict):
        raise RuntimeError("Native authentication rejected the request (details redacted)")
    return result["data"]


def validate_admin(token):
    data = graphql("{user{id roles} settings{authMode}}", token=token)
    if data != {"user": {"id": 1, "roles": ["ADMIN"]}, "settings": {"authMode": "UI_LOGIN"}}:
        raise RuntimeError("Native authentication did not verify built-in administrator 1 in UI_LOGIN")


def access_expiry(token):
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        expiry = claims["exp"]
        if type(expiry) is not int or not time.time() < expiry <= time.time() + 330:
            raise ValueError("Invalid expiry")
    except (IndexError, KeyError, ValueError, TypeError) as error:
        raise RuntimeError("Native administrator access token has an invalid or unbounded expiry") from error
    return expiry


def login(username, password):
    data = graphql(
        "mutation($input:LoginInput!){login(input:$input){accessToken refreshToken}}",
        {"input": {"username": username, "password": password}},
    )["login"]
    if not isinstance(data, dict) or not isinstance(data.get("refreshToken"), str) or not data["refreshToken"]:
        raise RuntimeError("Native login did not return an administrator refresh token")
    validate_admin(data["accessToken"])
    access_expiry(data["accessToken"])
    return data


class Session:
    def __init__(self, tokens):
        self.refresh_token = tokens["refreshToken"]
        self.lock = threading.Lock()
        self.failed = False

    def access_token(self):
        with self.lock:
            if self.failed:
                raise RuntimeError("HA ingress authentication is unavailable; restart after checking administrator credentials")
            try:
                token = graphql(
                    "mutation($token:String!){refreshToken(input:{refreshToken:$token}){accessToken}}",
                    {"token": self.refresh_token},
                )["refreshToken"]["accessToken"]
                access_expiry(token)
                validate_admin(token)
                return token
            except (RuntimeError, KeyError, TypeError) as error:
                self.failed = True
                print("ERROR: HA ingress JWT refresh/administrator validation failed; denied, no login fallback (details redacted).",
                      file=sys.stderr, flush=True)
                raise RuntimeError("HA ingress authentication is unavailable") from error


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, _format, *arguments):
        pass

    def reply(self, status, content, content_type="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        if self.path == "/__ha_auth/bootstrap.js":
            self.reply(200, BOOTSTRAP.encode(), "application/javascript")
        else:
            self.reply(404, b'{"error":"Not found"}')

    def do_POST(self):
        if self.path != "/__ha_auth/session":
            self.reply(404, b'{"error":"Not found"}')
            return
        if (self.headers.get("Content-Type") != "application/json"
                or self.headers.get("Sec-Fetch-Site", "same-origin") != "same-origin"
                or self.headers.get("Content-Length") != "2"):
            self.reply(403, b'{"error":"Invalid session request"}')
            return
        origin = self.headers.get("Origin")
        if origin and urllib.parse.urlsplit(origin).netloc != self.headers.get("Host"):
            self.reply(403, b'{"error":"Invalid session origin"}')
            return
        if self.rfile.read(2) != b"{}":
            self.reply(403, b'{"error":"Invalid session request"}')
            return
        try:
            token = self.server.session.access_token()
        except RuntimeError:
            self.reply(503, b'{"error":"Administrator session unavailable; check redacted app logs"}')
            return
        self.reply(200, json.dumps({"data": {"refreshToken": {
            "__typename": "RefreshTokenPayload", "accessToken": token,
        }}}).encode())


def main():
    class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
        daemon_threads = True

    os.umask(0o077)
    runtime = Path("/run/suwayomi/auth")
    tokens = json.loads((runtime / "ingress-session.json").read_text())
    socket = runtime / "ingress-auth.sock"
    socket.unlink(missing_ok=True)
    with Server(str(socket), Handler) as server:
        server.session = Session(tokens)
        socket.chmod(0o600)
        server.serve_forever()


if __name__ == "__main__":
    main()
