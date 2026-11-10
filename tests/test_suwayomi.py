import ast
import base64
import copy
import importlib.util
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import yaml
import time


ROOT = Path(__file__).parents[1]
APP = ROOT / "suwayomi"
sys.path.insert(0, str(APP))
spec = importlib.util.spec_from_file_location("suwayomi_runner", APP / "run.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
MANIFEST = yaml.safe_load((APP / "config.yaml").read_text(encoding="utf-8"))
SUPPORTED = set(runner.OPTION_SETTINGS.values()) | set(runner.FIXED_SETTINGS) | {
    "authUsername", "authPassword", "socksProxyEnabled", "socksProxyHost",
    "socksProxyPassword", "socksProxyPort", "databaseType", "databaseUrl", "databaseUsername", "databasePassword",
}
import ingress_auth as auth


def options():
    value = copy.deepcopy(MANIFEST["options"])
    value["auth_password"] = 'test-only "password" with \\ escapes'
    return value


class SuwayomiTests(unittest.TestCase):
    def test_expiry_smoke_restores_normal_lifetime_before_refresh_on_slow_runners(self):
        smoke_spec = importlib.util.spec_from_file_location("suwayomi_expiry_smoke", ROOT / "tests" / "suwayomi_smoke.py")
        smoke = importlib.util.module_from_spec(smoke_spec)
        smoke_spec.loader.exec_module(smoke)
        replies = [
            {"data": {"settings": {"jwtTokenExpiry": "1s"}}},
            {"data": {"login": {"accessToken": "expired-access", "refreshToken": "test-refresh"}}},
            {"errors": [{"message": "Expired"}]},
            {"data": {"settings": {"jwtTokenExpiry": "5m"}}},
            {"data": {"refreshToken": {"accessToken": "renewed-access"}}},
            {"data": {"user": {"id": 1}}},
        ]
        with patch.object(smoke, "graphql", side_effect=replies) as api, patch.object(
            smoke, "request", return_value=(401, b"")
        ) as media, patch.object(smoke, "wait_for_token_expiry") as expiry:
            result = smoke.verify_native_token_expiry("http://test", "login", options(), "admin-access", "/api/image")
        self.assertEqual(result["accessToken"], "expired-access")
        expiry.assert_called_once_with("expired-access")
        media.assert_called_once_with("http://test/api/image", "expired-access")
        calls = api.call_args_list
        self.assertIn('jwtTokenExpiry:"1s"', calls[0].args[1])
        self.assertEqual(calls[2].kwargs["token"], "expired-access")
        self.assertIn('jwtTokenExpiry:"5m"', calls[3].args[1])
        self.assertEqual(calls[4].kwargs["token"], "expired-access")
        self.assertEqual(calls[5].kwargs["token"], "renewed-access")

    def test_expiry_wait_uses_jwt_claims_and_a_monotonic_deadline(self):
        smoke_spec = importlib.util.spec_from_file_location("suwayomi_expiry_clock", ROOT / "tests" / "suwayomi_smoke.py")
        smoke = importlib.util.module_from_spec(smoke_spec)
        smoke_spec.loader.exec_module(smoke)
        def token(expiry):
            claims = base64.urlsafe_b64encode(json.dumps({"exp": expiry}).encode()).decode().rstrip("=")
            return "header." + claims + ".signature"
        with patch.object(smoke.time, "time", side_effect=[100, 100, 100, 101.1]), patch.object(
            smoke.time, "monotonic", return_value=0
        ), patch.object(smoke.time, "sleep") as sleep:
            smoke.wait_for_token_expiry(token(101))
        sleep.assert_called_once()
        with patch.object(smoke.time, "time", return_value=100), patch.object(
            smoke.time, "monotonic", side_effect=[0, 8]
        ), patch.object(smoke.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "bounded test deadline"):
                smoke.wait_for_token_expiry(token(101))
        for value in ("private-invalid-token", token(True), token(0), token(300)):
            with self.subTest(token=value), patch.object(smoke.time, "time", return_value=100):
                with self.assertRaises(RuntimeError) as error:
                    smoke.wait_for_token_expiry(value)
                self.assertNotIn(value, str(error.exception))
        with patch.object(smoke.time, "time", return_value=200):
            smoke.wait_for_token_expiry(token(101))

    def test_smoke_failure_diagnostics_redact_credentials_and_escaped_passwords(self):
        smoke_spec = importlib.util.spec_from_file_location("suwayomi_safe_logs", ROOT / "tests" / "suwayomi_smoke.py")
        smoke = importlib.util.module_from_spec(smoke_spec)
        smoke_spec.loader.exec_module(smoke)
        password = 'private "password" with \\ escapes'
        smoke.remember_secrets({"password": password, "data": {"login": {"accessToken": "private-access"}}})
        text = "startup failure " + password + " " + json.dumps(password)[1:-1] + " private-access"
        with patch.object(smoke, "docker", return_value=text), patch("sys.stdout", new_callable=io.StringIO) as output:
            smoke.print_failure_logs("fixture", "80")
        self.assertIn("startup failure", output.getvalue())
        self.assertNotIn("private", output.getvalue())
        with patch.object(smoke.subprocess, "check_output", side_effect=subprocess.CalledProcessError(
            1, ["docker", "exec", "--env", "TOKEN=private-access"], output="private-access"
        )):
            with self.assertRaisesRegex(RuntimeError, "exit code 1") as error:
                smoke.docker("exec", "--env", "TOKEN=private-access")
        self.assertNotIn("private-access", str(error.exception))
        with patch.object(smoke.subprocess, "check_output", side_effect=subprocess.TimeoutExpired(
            ["docker", "exec", "--env", "TOKEN=private-access"], 180,
        )) as output:
            with self.assertRaisesRegex(RuntimeError, "180-second deadline") as error:
                smoke.docker("exec", "--env", "TOKEN=private-access")
        self.assertEqual(output.call_args.kwargs["timeout"], 180)
        self.assertNotIn("private-access", str(error.exception))

    def test_smoke_cleanup_attempts_all_resources_and_preserves_primary_failure(self):
        smoke_spec = importlib.util.spec_from_file_location("suwayomi_cleanup", ROOT / "tests" / "suwayomi_smoke.py")
        smoke = importlib.util.module_from_spec(smoke_spec)
        smoke_spec.loader.exec_module(smoke)
        resources = [("container", "app"), ("network", "network"), ("volume", "data")]
        responses = [subprocess.TimeoutExpired("docker", 90),
                     subprocess.CompletedProcess([], 1), subprocess.CompletedProcess([], 0)]
        with patch.object(smoke.subprocess, "run", side_effect=responses) as run, patch(
            "sys.stdout", new_callable=io.StringIO
        ) as output:
            smoke.cleanup_resources(resources, primary_failure=True)
        self.assertEqual(run.call_count, 3)
        self.assertIn("Additional cleanup failures", output.getvalue())
        with patch.object(smoke.subprocess, "run", side_effect=responses) as run:
            with self.assertRaisesRegex(RuntimeError, "cleanup"):
                smoke.cleanup_resources(resources, primary_failure=False)
        self.assertEqual(run.call_count, 3)

    def test_native_login_is_bounded_and_cannot_be_overridden(self):
        settings = runner.build_settings(options(), SUPPORTED)
        self.assertEqual(settings["authMode"], "UI_LOGIN")
        self.assertEqual(settings["jwtTokenExpiry"], "5m")
        self.assertIs(MANIFEST.get("panel_admin", True), True)
        for key in ("authMode", "jwtTokenExpiry", "authUsername", "authPassword", "kcefEnabled"):
            configured = options()
            configured["advanced_server_settings"] = json.dumps({key: "unsafe"})
            with self.assertRaises(ValueError):
                runner.build_settings(configured, SUPPORTED)
        configured = options()
        configured["auth_password"] = " leading-whitespace-password"
        with self.assertRaisesRegex(ValueError, "whitespace"):
            runner.build_settings(configured, SUPPORTED)

    def test_startup_login_failure_is_redacted_and_does_not_fallback(self):
        child = unittest.mock.Mock()
        child.poll.return_value = None
        with patch.object(runner, "login", side_effect=RuntimeError("private-secret")):
            with self.assertRaisesRegex(RuntimeError, "administrator 1") as error:
                runner.verify_ui_login(child, "private-user", "private-password")
        self.assertNotIn("private-secret", str(error.exception))
        self.assertNotIn("private-password", str(error.exception))
        child.poll.return_value = 1
        with patch.object(runner, "login") as login:
            with self.assertRaisesRegex(RuntimeError, "exited"):
                runner.verify_ui_login(child, "user", "password")
            login.assert_not_called()

    def test_broker_rejects_visitor_other_users_and_wrong_auth_mode(self):
        for account in (
            {"user": None, "settings": {"authMode": "UI_LOGIN"}},
            {"user": {"id": 2, "roles": ["ADMIN"]}, "settings": {"authMode": "UI_LOGIN"}},
            {"user": {"id": 1, "roles": ["USER"]}, "settings": {"authMode": "UI_LOGIN"}},
            {"user": {"id": 1, "roles": ["ADMIN"]}, "settings": {"authMode": "NONE"}},
        ):
            with self.subTest(account=account), patch.object(auth, "graphql", return_value=account):
                with self.assertRaisesRegex(RuntimeError, "administrator"):
                    auth.validate_admin("test-only-token")

    def test_broker_expiry_rejects_expired_unbounded_and_malformed_tokens(self):
        def token(expiry):
            claims = base64.urlsafe_b64encode(json.dumps({"exp": expiry}).encode()).decode().rstrip("=")
            return "test-header." + claims + ".test-signature"
        self.assertGreater(auth.access_expiry(token(int(time.time()) + 300)), time.time())
        for value in ("bad-token", "header.bad.signature", token(int(time.time()) - 1),
                      token(int(time.time()) + 3600), token("tomorrow")):
            with self.subTest(value=value), self.assertRaisesRegex(RuntimeError, "expiry"):
                auth.access_expiry(value)

    def test_broker_refresh_failure_logs_redacted_and_latches_denied(self):
        session = auth.Session({"refreshToken": "private-refresh-token"})
        output = io.StringIO()
        with patch.object(auth, "graphql", side_effect=RuntimeError("private-refresh-token")) as request, patch.object(
            auth.sys, "stderr", output,
        ):
            for _ in range(2):
                with self.assertRaisesRegex(RuntimeError, "unavailable"):
                    session.access_token()
            self.assertEqual(request.call_count, 1)
        self.assertIn("denied, no login fallback", output.getvalue())
        self.assertNotIn("private-refresh-token", output.getvalue())

    def test_broker_success_returns_only_validated_native_access_token(self):
        session = auth.Session({"refreshToken": "private-refresh-token"})
        with patch.object(auth, "graphql", return_value={"refreshToken": {"accessToken": "access"}}), patch.object(
            auth, "access_expiry",
        ), patch.object(auth, "validate_admin") as verify:
            self.assertEqual(session.access_token(), "access")
            verify.assert_called_once_with("access")
        self.assertFalse(session.failed)

    def test_ingress_broker_is_private_and_direct_routes_cannot_use_it(self):
        template = runner.render_nginx((APP / "nginx.conf").read_text(), True)
        ingress, direct = template.split("listen 4567;")
        self.assertIn("http://unix:/run/suwayomi/auth/ingress-auth.sock", ingress)
        self.assertNotIn("proxy_pass http://unix:", direct)
        self.assertIn("location ^~ /__ha_auth/", direct)
        self.assertIn("return 404;", direct)
        self.assertIn("ha-ingress-session", auth.BOOTSTRAP)
        self.assertNotIn("auth_password", auth.BOOTSTRAP)
        self.assertNotIn("Basic ", template)
        self.assertIn("window.fetch", auth.BOOTSTRAP)
        self.assertIn("'USER_REFRESH'", auth.BOOTSTRAP)
        self.assertIn("'REFRESH_ACCESS_TOKEN'", auth.BOOTSTRAP)
        self.assertIn("console.error", auth.BOOTSTRAP)

    def test_tls_reuses_readonly_ha_files_and_preserves_http_ingress(self):
        self.assertIn("ssl:ro", MANIFEST["map"])
        self.assertFalse(MANIFEST["options"]["ssl"])
        configured = options()
        self.assertIsNone(runner.certificate_paths(configured))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "fullchain.pem").write_text("test-only public certificate")
            (root / "privkey.pem").write_text("test-only key fixture")
            configured["ssl"] = True
            paths = runner.certificate_paths(configured, root)
            self.assertEqual(paths, (root / "fullchain.pem", root / "privkey.pem"))
            rendered = runner.render_nginx(
                (APP / "nginx.conf").read_text(), True, paths,
            )
            self.assertIn("listen 8080;", rendered)
            self.assertIn("listen 4567 ssl;", rendered)
            self.assertIn("ssl_protocols TLSv1.2 TLSv1.3;", rendered)
            self.assertIn('Authorization $bearer_authorization;', rendered)
            self.assertNotIn("ssl_verify_client off", rendered)
            before = runner.certificate_fingerprint(paths)
            (root / "fullchain.pem").write_text("renewed test-only public certificate")
            self.assertNotEqual(before, runner.certificate_fingerprint(paths))

    def test_tls_paths_reject_missing_files_traversal_and_nginx_injection(self):
        configured = options()
        configured["ssl"] = True
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ValueError, "missing"):
                runner.certificate_paths(configured, root)
            for value in ("../outside.pem", "/ssl/fullchain.pem", 'file.pem"; injected;', "file\n.pem"):
                configured["certfile"] = value
                with self.subTest(value=value), self.assertRaises(ValueError):
                    runner.certificate_paths(configured, root)

    def test_smoke_readiness_handles_docker_startup_reset_without_masking_app_exit(self):
        smoke_spec = importlib.util.spec_from_file_location("suwayomi_smoke", ROOT / "tests" / "suwayomi_smoke.py")
        smoke = importlib.util.module_from_spec(smoke_spec)
        smoke_spec.loader.exec_module(smoke)
        with patch.object(smoke, "docker", return_value="true"), patch.object(
            smoke, "request", side_effect=[ConnectionResetError("not listening yet"), (200, b"ready")]
        ), patch.object(smoke.time, "sleep"):
            smoke.wait_for_app("test-app", "http://127.0.0.1/", 200, "test-only")
        with patch.object(smoke, "docker", side_effect=["false", "explicit app failure"]):
            with self.assertRaisesRegex(RuntimeError, "App exited"):
                smoke.wait_for_app("test-app", "http://127.0.0.1/", 200, "test-only")

    def test_python_sources_parse(self):
        for name in ("run.py", "build_source.py"):
            ast.parse((APP / name).read_text(encoding="utf-8"))

    def test_manifest_contract(self):
        self.assertEqual(set(MANIFEST["options"]), set(MANIFEST["schema"]))
        self.assertEqual(set(MANIFEST["arch"]), {"aarch64", "amd64"})
        self.assertEqual(MANIFEST["ports"], {"4567/tcp": None})
        self.assertFalse(MANIFEST["options"]["direct_access_enabled"])
        self.assertTrue(MANIFEST["ingress"])
        self.assertTrue(MANIFEST["ingress_stream"])
        self.assertEqual(MANIFEST["ingress_port"], 8080)
        self.assertNotIn("ingress_entry", MANIFEST)
        self.assertTrue(MANIFEST.get("panel_admin", True))
        self.assertNotIn("webui", MANIFEST)
        for forbidden in ("docker_api", "full_access", "host_network", "privileged", "hassio_api", "homeassistant_api"):
            self.assertNotIn(forbidden, MANIFEST)

    def test_ha_app_icon_is_the_unmodified_licensed_upstream_asset(self):
        icon = (APP / "icon.png").read_bytes()
        self.assertEqual(icon[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(int.from_bytes(icon[16:20], "big"), 128)
        self.assertEqual(int.from_bytes(icon[20:24], "big"), 128)
        self.assertEqual(hashlib.sha256(icon).hexdigest(),
                         "01f60bf4ccd803028d4559606b0738a39ef6b9bbb452b31b7dafd18e28fac076")
        self.assertIn("faviconlogo-128.png", (ROOT / "README.md").read_text(encoding="utf-8"))

    def test_source_pin_is_consistent(self):
        pins = yaml.safe_load((APP / "build.yaml").read_text())["args"]
        pin = pins["SOURCE_REF"]
        self.assertRegex(pin, r"^[0-9a-f]{40}$")
        docker = (APP / "Dockerfile").read_text()
        self.assertEqual(re.findall(r"ARG SOURCE_REF=([0-9a-f]+)", docker), [pin, pin])
        self.assertIn(":server:downloadWebUI :server:shadowJar", docker)
        self.assertIn(":server:test --tests suwayomi.tachidesk.manga.impl.update.UpdaterTest", docker)
        self.assertIn("--tests suwayomi.tachidesk.graphql.UpdateMutationTest", docker)
        self.assertIn("--tests suwayomi.tachidesk.graphql.HABundledWebUIVersionTest", docker)
        self.assertIn("git rev-parse HEAD", docker)
        self.assertIn(f"ARG BUILD_VERSION={MANIFEST['version']}", docker)
        self.assertEqual(re.findall(r"ARG WEBUI_REF=(r\d+)", docker), [pins["WEBUI_REF"]] * 2)
        self.assertIn("ARG WEBUI_SHA256=" + pins["WEBUI_SHA256"], docker)
        self.assertIn("ARG WEBUI_MIN_SERVER=" + pins["WEBUI_MIN_SERVER"], docker)
        self.assertIn("--init-script /source/ha-webui.gradle", docker)

    def test_standalone_repository_identity_is_consistent(self):
        self.assertEqual(MANIFEST["url"], "https://github.com/devnagui/Suwayomi-Server-HA")
        self.assertEqual(MANIFEST["image"], "ghcr.io/devnagui/suwayomi-server-ha/suwayomi-{arch}")
        repository = yaml.safe_load((ROOT / "repository.yaml").read_text())
        self.assertEqual(repository["url"], MANIFEST["url"])
        self.assertIn(MANIFEST["url"], (APP / "Dockerfile").read_text())

    def test_scheduled_pipeline_promotes_only_after_both_architectures(self):
        workflow = yaml.load((ROOT / ".github" / "workflows" / "suwayomi.yml").read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(workflow["on"]["schedule"][0]["cron"], "15 4 * * *")
        jobs = workflow["jobs"]
        self.assertEqual(jobs["publish"]["needs"], ["prepare", "build"])
        self.assertEqual({value["arch"] for value in jobs["build"]["strategy"]["matrix"]["include"]}, {"amd64", "aarch64"})
        self.assertTrue(any("--usb-storage" in step.get("run", "") for step in jobs["build"]["steps"]))
        steps = jobs["publish"]["steps"]
        promotion = next(index for index, step in enumerate(steps) if step.get("name", "").startswith("Promote both"))
        record = next(index for index, step in enumerate(steps) if step.get("name") == "Record tested upstream update")
        self.assertLess(promotion, record)
        self.assertFalse(any("git commit" in step.get("run", "") for step in jobs["prepare"]["steps"]))

    def test_source_pin_helper_is_complete_and_rejects_non_increasing_versions(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary)
            for name in ("build.yaml", "Dockerfile", "config.yaml", "CHANGELOG.md"):
                shutil.copyfile(APP / name, app / name)
            parts = [int(part) for part in MANIFEST["version"].split(".")]
            parts[2] += 1
            version = ".".join(str(part) for part in parts)
            script = ROOT / "scripts" / "update-suwayomi.cjs"
            result = subprocess.run(
                ["node", "-e", "require(process.argv[1]).pin(process.argv[2],process.argv[3],process.argv[4])", str(script), version, "a" * 40, str(app)],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(yaml.safe_load((app / "config.yaml").read_text())["version"], version)
            self.assertEqual(yaml.safe_load((app / "build.yaml").read_text())["args"]["SOURCE_REF"], "a" * 40)
            self.assertEqual((app / "Dockerfile").read_text().count("ARG SOURCE_REF=" + "a" * 40), 2)
            before = {name: (app / name).read_bytes() for name in ("build.yaml", "Dockerfile", "config.yaml")}
            rejected = subprocess.run(result.args, capture_output=True, text=True)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertEqual(before, {name: (app / name).read_bytes() for name in before})

    def test_native_matrix_gates_auth_migration_failure_and_stable_before_upload(self):
        workflow = yaml.load((ROOT / ".github" / "workflows" / "suwayomi.yml").read_text(), Loader=yaml.BaseLoader)
        build = workflow["jobs"]["build"]
        self.assertEqual({item["runner"] for item in build["strategy"]["matrix"]["include"]},
                         {"ubuntu-24.04", "ubuntu-24.04-arm"})
        steps = build["steps"]
        upload = next(index for index, step in enumerate(steps) if step.get("name", "").startswith("Upload tested"))
        commands = [step.get("run", "") for step in steps[:upload]]
        migration = [command for command in commands if "--migration-from" in command]
        self.assertEqual(len(migration), 1)
        self.assertIn("suwayomi-${{ matrix.arch }}:0.1.4", migration[0])
        self.assertIn("--browser --usb-storage", migration[0])
        self.assertTrue(any("--auth-failure" in command and "--browser --usb-storage" in command for command in commands))
        self.assertTrue(any("--webui-channel stable --browser --usb-storage" in command for command in commands))
        for selector in ("--browser", "--usb-storage", "--webui-channel preview", "--tls"):
            self.assertTrue(any(selector in command for command in commands))
        self.assertTrue(any("--cef --usb-storage" in command for command in commands))

    def test_build_applies_only_the_explicit_approved_overlays(self):
        docker = (APP / "Dockerfile").read_text()
        self.assertNotIn("chapter-page-list.patch", docker)
        self.assertIn("COPY bundled-webui-version.patch scoped-manga-cancellation.patch /source/", docker)
        self.assertIn("git apply --check bundled-webui-version.patch scoped-manga-cancellation.patch", docker)
        self.assertNotIn("ChapterTest", docker)
        self.assertIn("chapter-page-list.patch", (APP / ".dockerignore").read_text().splitlines())
        cancellation = (APP / "scoped-manga-cancellation.patch").read_text()
        self.assertIn("val mangaIds: List<Int>? = null", cancellation)
        self.assertIn("UpdaterTest.kt", cancellation)
        self.assertIn("UpdateMutationTest.kt", cancellation)
        self.assertNotIn("ChapterForDownload", cancellation)

    def test_daily_upstream_check_skips_unchanged_and_updates_changed_source(self):
        pins = yaml.safe_load((APP / "build.yaml").read_text())["args"]
        current_source = pins["SOURCE_REF"]
        current_ui = pins["WEBUI_REF"]
        for source, ui, changed in ((current_source, current_ui, False), ("b" * 40, current_ui, True), (current_source, "r9999", True)):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as temporary:
                app = Path(temporary)
                for name in ("build.yaml", "Dockerfile", "config.yaml", "CHANGELOG.md"):
                    shutil.copyfile(APP / name, app / name)
                code = (
                    "const cp=require('node:child_process');"
                    "cp.execFileSync=()=>process.argv[3]+'\\trefs/heads/master\\n';"
                    "global.fetch=async(url)=>{"
                    "if(url.includes('/commits?'))return new Response(JSON.stringify([{sha:process.argv[3]}]),{headers:{link:'<https://api.github.com/commits?page=2435>; rel=\"last\"'}});"
                    "if(url.includes('versionToServerVersionMapping'))return new Response(JSON.stringify([{uiVersion:'PREVIEW',serverVersion:'r'+process.argv[7]}]));"
                    "return new Response(JSON.stringify({tag_name:process.argv[5],assets:[{name:'Suwayomi-WebUI-'+process.argv[5]+'.zip',digest:'sha256:'+process.argv[6]}]}));};"
                    "require(process.argv[1]).latest(process.argv[2]).then(result=>{if(String(result)!==process.argv[4])process.exit(2)}).catch(e=>{console.error(e);process.exit(3)});"
                )
                result = subprocess.run(
                    ["node", "-e", code, str(ROOT / "scripts" / "update-suwayomi.cjs"), str(app), source, str(changed).lower(), ui, pins["WEBUI_SHA256"], pins["WEBUI_MIN_SERVER"]],
                    capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                updated_pins = yaml.safe_load((app / "build.yaml").read_text())
                self.assertEqual(updated_pins["args"]["SOURCE_REF"], source)
                self.assertEqual(updated_pins["args"]["WEBUI_REF"], ui)
                version = yaml.safe_load((app / "config.yaml").read_text())["version"]
                parts = [int(part) for part in MANIFEST["version"].split(".")]
                parts[2] += int(changed)
                self.assertEqual(version, ".".join(str(part) for part in parts))
                if changed:
                    self.assertIn(source, (app / "CHANGELOG.md").read_text())
                    self.assertIn(ui, (app / "CHANGELOG.md").read_text())

    def test_ui_update_failures_do_not_modify_package_pins(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary)
            for name in ("build.yaml", "Dockerfile", "config.yaml", "CHANGELOG.md"):
                shutil.copyfile(APP / name, app / name)
            before = {name: (app / name).read_bytes() for name in ("build.yaml", "Dockerfile", "config.yaml", "CHANGELOG.md")}
            source = yaml.safe_load((APP / "build.yaml").read_text())["args"]["SOURCE_REF"]
            code = (
                "const cp=require('node:child_process');"
                "cp.execFileSync=()=>process.argv[3]+'\\trefs/heads/master\\n';"
                "global.fetch=async()=>new Response('rate limited',{status:429});"
                "require(process.argv[1]).latest(process.argv[2])"
                ".then(()=>process.exit(2)).catch(e=>{if(!e.message.includes('429'))process.exit(3)});"
            )
            result = subprocess.run(
                ["node", "-e", code, str(ROOT / "scripts" / "update-suwayomi.cjs"), str(app), source],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(before, {name: (app / name).read_bytes() for name in before})

    def test_compatible_ui_selection_rejects_a_missing_digest_and_uses_supported_floor(self):
        code = (
            "const helper=require(process.argv[1]);"
            "const mapping=[{uiVersion:'PREVIEW',serverVersion:'r2500'},{uiVersion:'r3518',serverVersion:'r2320'}];"
            "const urls=[];"
            "const fetcher=async url=>{urls.push(url);return new Response(JSON.stringify("
            "url.includes('Mapping.json')?mapping:{tag_name:'r3518',assets:[{name:'Suwayomi-WebUI-r3518.zip',digest:'sha256:'+'a'.repeat(64)}]}));};"
            "(async()=>{"
            "const result=await helper.compatibleWebUI(2435,fetcher);"
            "if(result.revision!=='r3518'||result.minimumServer!==2320||!urls.some(u=>u.endsWith('tags/r3518')))process.exit(2);"
            "try{await helper.compatibleWebUI(2435,async url=>new Response(JSON.stringify("
            "url.includes('Mapping.json')?mapping:{tag_name:'r3518',assets:[]})));process.exit(3)}"
            "catch(e){if(!e.message.includes('SHA-256'))throw e}"
            "})().catch(e=>{console.error(e);process.exit(4)});"
        )
        result = subprocess.run(
            ["node", "-e", code, str(ROOT / "scripts" / "update-suwayomi.cjs")],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_translations_cover_every_ha_option(self):
        translations = yaml.safe_load((APP / "translations" / "en.yaml").read_text())
        self.assertEqual(set(translations["configuration"]), set(MANIFEST["options"]))

    def test_all_managed_settings_are_wired(self):
        configured = options()
        settings = runner.build_settings(configured, SUPPORTED)
        paths = runner.storage_paths(configured)
        for option, setting in runner.OPTION_SETTINGS.items():
            self.assertEqual(settings[setting], paths[option] if option in paths else configured[option])
        self.assertEqual(settings["rootDir"], configured["storage_path"])
        self.assertEqual(settings["ip"], "127.0.0.1")
        self.assertEqual(settings["port"], 4568)
        self.assertEqual(settings["authMode"], "UI_LOGIN")
        self.assertEqual(settings["webUIChannel"], "BUNDLED")
        self.assertEqual(settings["webUIFlavor"], "CUSTOM")
        self.assertFalse(settings["kcefEnabled"])
        self.assertEqual(settings["webUISubpath"], "")

    def test_unconfigured_password_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "auth_password"):
            runner.build_settings(MANIFEST["options"], SUPPORTED)

    def test_invalid_options_fail_without_disclosing_values(self):
        cases = {
            "auth_username": "unsafe:username",
            "auth_password": "private-password\ninjection",
            "direct_access_enabled": "false",
            "max_sources_in_parallel": True,
            "jvm_heap_mb": 128,
            "global_update_interval": float("nan"),
            "backup_time": "25:99",
            "flaresolverr_url": "http://private:secret@host:8191",
            "flaresolverr_timeout": 0,
            "flaresolverr_session_name": "name\ninjection",
            "extension_stores": "https://example.org",
            "storage_path": "/media/torrent_disk/../escape",
            "downloads_path": "relative/path",
        }
        for key, value in cases.items():
            with self.subTest(key=key):
                configured = options()
                configured[key] = value
                with self.assertRaisesRegex(ValueError, key) as error:
                    runner.build_settings(configured, SUPPORTED)
                if key in ("auth_username", "auth_password", "flaresolverr_url"):
                    self.assertNotIn(str(value), str(error.exception))

    def test_missing_and_unknown_options_fail(self):
        for configured in (options() | {"unknown": True}, {key: value for key, value in options().items() if key != "backup_path"}):
            with self.assertRaisesRegex(ValueError, "Configuration"):
                runner.build_settings(configured, SUPPORTED)

    def test_advanced_global_settings_support_typed_values(self):
        configured = options()
        configured["advanced_server_settings"] = '{"socksProxyEnabled":true,"socksProxyPort":"1080","databaseType":"POSTGRESQL"}'
        settings = runner.build_settings(configured, SUPPORTED)
        self.assertTrue(settings["socksProxyEnabled"])
        self.assertEqual(settings["socksProxyPort"], "1080")
        self.assertEqual(settings["databaseType"], "POSTGRESQL")

    def test_reserved_unknown_deprecated_and_invalid_advanced_settings_fail(self):
        for advanced in (
            '{"ip":"0.0.0.0"}', '{"authMode":"NONE"}', '{"webUISubpath":"/other"}',
            '{"flareSolverrUrl":"http://other:8191"}', '{"autoDownloadNewChapters":true}',
            '{"notASetting":true}', '{"socksProxyEnabled":null}', '{"socksProxyEnabled":NaN}',
            '{"socksProxyEnabled":true,"socksProxyEnabled":false}', "[]", "{",
        ):
            with self.subTest(advanced=advanced):
                configured = options()
                configured["advanced_server_settings"] = advanced
                with self.assertRaises(ValueError):
                    runner.build_settings(configured, SUPPORTED)

    def test_upstream_contract_drift_fails(self):
        with self.assertRaisesRegex(ValueError, "contract changed"):
            runner.build_settings(options(), SUPPORTED - {"flareSolverrEnabled"})

    def test_flaresolverr_is_external_and_uses_root_url(self):
        self.assertEqual(MANIFEST["options"]["flaresolverr_url"], "http://db21ed7f-flaresolverr:8191")
        configured = options()
        configured["flaresolverr_url"] += "/v1"
        with self.assertRaisesRegex(ValueError, "without /v1"):
            runner.build_settings(configured, SUPPORTED)
        docker = (APP / "Dockerfile").read_text().lower()
        self.assertNotIn("chromium", docker)
        self.assertNotIn("flaresolverr", docker)

    def test_ingress_and_direct_authorization_are_separate(self):
        template = (APP / "nginx.conf").read_text()
        configured = options()
        rendered = runner.render_nginx(template, False)
        authorization = base64.b64encode(f'{configured["auth_username"]}:{configured["auth_password"]}'.encode()).decode()
        ingress, direct = rendered.split("listen 4567;")
        self.assertIn('Authorization $bearer_authorization;', ingress)
        self.assertIn("allow 172.30.32.2;", ingress)
        self.assertIn("deny all;", ingress)
        self.assertIn("return 403;", direct)
        self.assertNotIn('auth_basic', rendered)
        self.assertNotIn('htpasswd', rendered)
        self.assertNotIn(configured["auth_password"], rendered)
        self.assertNotIn(authorization, direct)
        self.assertNotIn("return 403;", runner.render_nginx(template, True).split("listen 4567;")[1])
        self.assertIn("proxy_set_header X-Ingress-Path", direct)

    def test_only_html_base_tag_is_rewritten(self):
        template = (APP / "nginx.conf").read_text()
        self.assertIn('sub_filter \'<base href="/">\' \'<base href="$ingress_path/">\';', template)
        self.assertNotIn("application/javascript", template)
        self.assertNotIn("application/json", template)
        self.assertIn("proxy_request_buffering off;", template)
        self.assertIn("proxy_buffering off;", template)

    def test_java_argfile_escapes_password_and_preserves_json_types(self):
        configured = options()
        settings = runner.build_settings(configured, SUPPORTED)
        arguments = runner.java_arguments(settings, configured["jvm_heap_mb"])
        self.assertIn('"-Xmx768m"\n', arguments)
        self.assertEqual(len(arguments.splitlines()), len(settings) + 5)
        self.assertIn('server.flareSolverrEnabled=true"', arguments)
        self.assertIn('server.extensionStores=[]"', arguments)
        self.assertIn('server.authPassword=', arguments)
        self.assertIn(f'"-Dsuwayomi.tachidesk.config.server.rootDir={configured["storage_path"]}"', arguments)
        self.assertNotIn(configured["auth_password"], arguments)

    def test_storage_is_contained_and_created_without_deleting_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            data = root / "data"
            data.mkdir()
            target = data / "downloads"
            with patch.object(runner.os, "chown", create=True):
                runner.prepare_path(str(target), (data,))
                (target / "keep").write_text("existing")
                runner.prepare_path(str(target), (data,))
            self.assertEqual((target / "keep").read_text(), "existing")
            for path in (data, root / "outside", data / ".." / "outside"):
                with self.assertRaises(ValueError):
                    runner.prepare_path(str(path), (data,))

    def test_missing_usb_volume_never_creates_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            media = Path(temporary).resolve() / "media"
            media.mkdir()
            with self.assertRaisesRegex(ValueError, "SD card"):
                runner.prepare_path(str(media / "missing-volume" / "downloads"), (media,))
            self.assertFalse((media / "missing-volume").exists())

    def test_packaged_webui_preserves_existing_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            packaged = root / "packaged"
            packaged.mkdir()
            (packaged / "index.html").write_text('<base href="/">')
            (packaged / "revision").write_text("r-test")
            existing = root / "existing"
            existing.mkdir()
            (existing / "index.html").write_text("keep")
            with self.assertRaisesRegex(ValueError, "preserved"):
                runner.prepare_webui(existing, packaged)
            self.assertEqual((existing / "index.html").read_text(), "keep")
            target = root / "managed"
            runner.prepare_webui(target, packaged)
            self.assertFalse(target.is_symlink())
            runner.prepare_webui(target, packaged)
            (packaged / "revision").write_text("r-next")
            runner.prepare_webui(target, packaged)
            self.assertEqual((target / "revision").read_text(), "r-next")
            previous = list(root.glob("managed.previous-*"))
            self.assertEqual(len(previous), 1)
            self.assertEqual((previous[0] / "revision").read_text(), "r-test")

    def test_usb_root_drives_all_default_directories(self):
        configured = options()
        configured["storage_path"] = "/media/torrent_disk/suwayomi"
        paths = runner.storage_paths(configured)
        self.assertEqual(paths, {
            "storage_path": "/media/torrent_disk/suwayomi",
            "downloads_path": "/media/torrent_disk/suwayomi/downloads",
            "backup_path": "/media/torrent_disk/suwayomi/backups",
            "local_source_path": "/media/torrent_disk/suwayomi/local",
        })
        settings = runner.build_settings(configured, SUPPORTED)
        self.assertEqual(settings["rootDir"], paths["storage_path"])
        configured["downloads_path"] = "/media/other_volume/chapters"
        self.assertEqual(runner.storage_paths(configured)["downloads_path"], "/media/other_volume/chapters")

    def test_stale_media_directory_is_not_a_mounted_volume(self):
        with tempfile.TemporaryDirectory() as temporary:
            media = Path(temporary).resolve() / "media"
            (media / "usb").mkdir(parents=True)
            with self.assertRaisesRegex(ValueError, "not mounted"):
                runner.prepare_path(str(media / "usb" / "suwayomi"), (media,))
            self.assertFalse((media / "usb" / "suwayomi").exists())

    def test_runtime_webui_options_preserve_bundled_defaults_and_enable_upstream_updates(self):
        for channel in ("bundled", "stable", "preview"):
            configured = options() | {"webui_channel": channel}
            settings = runner.build_settings(configured, SUPPORTED)
            self.assertEqual(settings["webUIChannel"], channel.upper())
            self.assertEqual(settings["webUIFlavor"], "CUSTOM" if channel == "bundled" else "WEBUI")
            self.assertEqual(settings["webUIUpdateCheckInterval"], 0 if channel == "bundled" else 23)
            self.assertFalse(settings["kcefEnabled"])
            self.assertEqual(settings["authMode"], "UI_LOGIN")
        legacy = {key: value for key, value in options().items() if key not in runner.WEBUI_DEFAULTS}
        self.assertEqual(runner.build_settings(legacy, SUPPORTED)["webUIChannel"], "BUNDLED")
        configured = options() | {"webui_channel": "preview", "webui_update_check_interval": 0}
        self.assertEqual(runner.build_settings(configured, SUPPORTED)["webUIUpdateCheckInterval"], 0)

    def test_runtime_webui_options_reject_invalid_modes_intervals_and_overrides(self):
        for key, value in (("webui_channel", "other"), ("webui_channel", True), ("webui_update_check_interval", -1), ("webui_update_check_interval", 24), ("webui_update_check_interval", True)):
            with self.subTest(key=key, value=value), self.assertRaisesRegex(ValueError, key):
                runner.build_settings(options() | {key: value}, SUPPORTED)
        for key in ("webUIFlavor", "webUIChannel", "webUIUpdateCheckInterval"):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "managed"):
                runner.build_settings(options() | {"advanced_server_settings": json.dumps({key: "PREVIEW"})}, SUPPORTED)

    def test_embedded_browser_is_opt_in_and_cannot_bypass_the_managed_option(self):
        for channel in ("bundled", "stable", "preview"):
            self.assertFalse(runner.build_settings(options() | {"webui_channel": channel}, SUPPORTED)["kcefEnabled"])
            self.assertTrue(runner.build_settings(options() | {
                "webui_channel": channel, "embedded_browser_enabled": True,
            }, SUPPORTED)["kcefEnabled"])
        legacy = options()
        del legacy["embedded_browser_enabled"]
        self.assertFalse(runner.build_settings(legacy, SUPPORTED)["kcefEnabled"])
        for value in ("true", 1, None):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "must be a boolean"):
                runner.build_settings(options() | {"embedded_browser_enabled": value}, SUPPORTED)
        with self.assertRaisesRegex(ValueError, "managed"):
            runner.build_settings(options() | {"advanced_server_settings": '{"kcefEnabled":true}'}, SUPPORTED)

    def test_cef_display_uses_private_authentication_and_waits_for_readiness(self):
        display = unittest.mock.Mock()
        display.poll.return_value = None
        selector = unittest.mock.MagicMock()
        selector.__enter__.return_value = selector
        selector.select.return_value = [(object(), 1)]
        runtime = Path("/run/suwayomi")
        with patch.object(runner, "private_file") as private, patch.object(
            runner.secrets, "token_hex", return_value="test-cookie",
        ), patch.object(runner.subprocess, "run") as run, patch.object(
            runner.subprocess, "Popen", return_value=display,
        ) as popen, patch.object(runner.selectors, "DefaultSelector", return_value=selector), patch.object(
            runner.os, "read", side_effect=[b"9", b"9\n"],
        ):
            child, environment = runner.start_display(runtime)
        self.assertIs(child, display)
        self.assertEqual(environment["DISPLAY"], ":99")
        self.assertEqual(environment["XAUTHORITY"], str(runtime / "xauthority"))
        private.assert_called_once_with(runtime / "xauthority", "", uid=1001, gid=1001)
        self.assertEqual(run.call_args.kwargs["input"], "add :99 . test-cookie\n")
        self.assertNotIn("test-cookie", str(run.call_args.args))
        arguments = popen.call_args.args[0]
        self.assertIn("-auth", arguments)
        self.assertIn("-nolisten", arguments)
        self.assertNotIn("-ac", arguments)
        display.stdout.close.assert_called_once()

    def test_cef_display_readiness_failure_cleans_up_and_does_not_fallback(self):
        for data, expected in ((None, "10 seconds"), (b"", "exited"), (b"98\n", "readiness")):
            display = unittest.mock.Mock()
            display.poll.return_value = None
            selector = unittest.mock.MagicMock()
            selector.__enter__.return_value = selector
            selector.select.return_value = [] if data is None else [(object(), 1)]
            with self.subTest(data=data), patch.object(runner, "private_file"), patch.object(
                runner.subprocess, "run",
            ), patch.object(runner.subprocess, "Popen", return_value=display), patch.object(
                runner.selectors, "DefaultSelector", return_value=selector,
            ), patch.object(runner.os, "read", return_value=data), patch.object(runner, "stop_child") as stop:
                with self.assertRaisesRegex(RuntimeError, expected):
                    runner.start_display(Path("/run/suwayomi"))
                stop.assert_called_once_with(display)
                display.stdout.close.assert_called_once()

    def test_cef_authentication_timeout_is_explicit_and_never_starts_a_display(self):
        with patch.object(runner, "private_file"), patch.object(
            runner.subprocess, "run", side_effect=subprocess.TimeoutExpired("xauth", 10),
        ), patch.object(runner.subprocess, "Popen") as start:
            with self.assertRaisesRegex(RuntimeError, "authentication exceeded"):
                runner.start_display(Path("/run/suwayomi/auth"))
            start.assert_not_called()

    def test_cef_display_survives_until_the_jvm_shutdown_hooks_finish(self):
        events = []
        java = unittest.mock.Mock()
        display = unittest.mock.Mock()
        for name, child in (("java", java), ("display", display)):
            child.poll.return_value = None
            child.terminate.side_effect = lambda name=name: events.append(name + "-terminate")
            child.wait.side_effect = lambda timeout=None, name=name: events.append(name + "-wait")
        runner.shutdown_children([java], display)
        self.assertLess(events.index("java-wait"), events.index("display-terminate"))
        java.wait.assert_called_once_with(timeout=45)
        display.wait.assert_called_once_with(timeout=45)

    def test_cef_probe_requires_javascript_not_only_a_websocket_or_load_event(self):
        spec = importlib.util.spec_from_file_location("suwayomi_cef_probe", ROOT / "tests" / "suwayomi_probe.py")
        probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(probe)
        event = json.dumps({"type": "consoleMessage", "message": "HA_CEF_JS_RESULT:42"}).encode()
        for payload, succeeds in ((b'{"type":"pong"}', False), (event, True)):
            connection = unittest.mock.MagicMock()
            connection.__enter__.return_value = connection
            stream = io.BytesIO(b"HTTP/1.1 101 Switching Protocols\r\n\r\n" +
                                bytes((0x01, 5)) + payload[:5] +
                                bytes((0x80, len(payload) - 5)) + payload[5:])
            connection.recv.side_effect = stream.read
            with self.subTest(succeeds=succeeds), patch.object(
                probe.socket, "create_connection", return_value=connection,
            ), patch.dict(probe.os.environ, {"SUWAYOMI_TEST_TOKEN": "test-token"}), patch(
                "sys.stdout", new_callable=io.StringIO,
            ) as output:
                if succeeds:
                    probe.cef_webview()
                    self.assertIn("executed JavaScript", output.getvalue())
                else:
                    with self.assertRaisesRegex(RuntimeError, "closed"):
                        probe.cef_webview()
                self.assertNotIn("test-token", output.getvalue())

    def test_child_shutdown_signals_its_directly_supervised_process(self):
        class Child:
            terminated = False
            def poll(self):
                return None
            def terminate(self):
                self.terminated = True
        child = Child()
        runner.terminate_child(child)
        self.assertTrue(child.terminated)

    def test_runtime_mode_transitions_preserve_previous_ui_and_do_not_restore_stale_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            packaged = root / "packaged"
            packaged.mkdir()
            (packaged / "index.html").write_text('<base href="/">')
            (packaged / "revision").write_text("r-bundled")
            target = root / "webUI"
            runner.prepare_selected_webui(target, "bundled", packaged)
            runner.prepare_selected_webui(target, "preview", packaged)
            self.assertFalse(target.exists(), "Runtime first boot must use upstream initial download")
            self.assertEqual(len(list(root.glob("webUI.previous-bundled-*"))), 1)
            target.mkdir()
            (target / "index.html").write_text('<base href="/">')
            (target / "revision").write_text("r-runtime")
            runner.prepare_selected_webui(target, "preview", packaged)
            self.assertEqual((target / "revision").read_text(), "r-runtime")
            runner.prepare_selected_webui(target, "bundled", packaged)
            self.assertEqual((target / "revision").read_text(), "r-bundled")
            previous = list(root.glob("webUI.previous-preview-*"))
            self.assertEqual(len(previous), 1)
            self.assertEqual((previous[0] / "revision").read_text(), "r-runtime")

    def test_runtime_mode_preserves_unmanaged_content_and_invalid_markers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "webUI"
            target.mkdir()
            (target / "keep").write_text("user content")
            with self.assertRaisesRegex(ValueError, "preserved"):
                runner.prepare_selected_webui(target, "preview")
            self.assertEqual((target / "keep").read_text(), "user content")
            (root / ".ha-webui-mode.json").write_text('{"channel":"invalid"}')
            with self.assertRaisesRegex(ValueError, "marker"):
                runner.prepare_selected_webui(target, "preview")
            self.assertEqual((target / "keep").read_text(), "user content")

    def test_switching_to_a_missing_bundle_preserves_the_active_runtime_ui(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "webUI"
            runner.prepare_selected_webui(target, "preview")
            target.mkdir()
            (target / "index.html").write_text("runtime UI")
            with self.assertRaisesRegex(ValueError, "preserved"):
                runner.prepare_selected_webui(target, "bundled", root / "missing")
            self.assertEqual((target / "index.html").read_text(), "runtime UI")
            self.assertEqual(json.loads((root / ".ha-webui-mode.json").read_text())["channel"], "preview")

    def test_ui_build_override_checks_compatibility_and_does_not_modify_upstream_source(self):
        spec = importlib.util.spec_from_file_location("package_builder", APP / "build_source.py")
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            constants = root / "buildSrc/src/main/kotlin/Constants.kt"
            constants.parent.mkdir(parents=True)
            original = 'val webUIRevisionTag = "r3518"\n'
            constants.write_text(original)
            payload = b"test-only webui archive"
            digest = builder.hashlib.sha256(payload).hexdigest()
            with patch.object(builder.subprocess, "check_output", return_value="2435"), patch.object(
                builder.urllib.request, "urlopen", return_value=io.BytesIO(payload)
            ):
                builder.pin_webui(root, "r3569", 2366, digest)
                with self.assertRaisesRegex(SystemExit, "newer"):
                    builder.pin_webui(root, "r3569", 2500, digest)
            self.assertEqual(constants.read_text(), original)
            override = (root / "ha-webui.gradle").read_text()
            self.assertIn("enabled = false", override)
            self.assertIn("'HA_WEBUI_TAG', '\"r3569\"'", override)
            self.assertNotIn("'WEBUI_TAG'", override)
            self.assertEqual((root / "server/src/main/resources/WebUI.zip").read_bytes(), payload)

    def test_packaged_ui_digest_revision_and_provenance_are_verified(self):
        spec = importlib.util.spec_from_file_location("package_builder", APP / "build_source.py")
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            root = Path(temporary)
            source = root / "source"
            jars = source / "server/build"
            jars.mkdir(parents=True)
            (source / "LICENSE").write_text("test-only license fixture")
            (source / "ha-webui.gradle").write_text("test-only override fixture")
            for name in builder.SERVER_PATCHES:
                shutil.copyfile(APP / name, source / name)
            (source / "chapter-page-list.patch").write_text("test-only patch fixture")
            ui = io.BytesIO()
            with zipfile.ZipFile(ui, "w") as archive:
                archive.writestr("index.html", '<base href="/">')
                archive.writestr("revision", "r3569")
            with zipfile.ZipFile(jars / "server.jar", "w") as archive:
                archive.writestr("WebUI.zip", ui.getvalue())
                archive.writestr("server-reference.conf", "\n".join(f"server.setting{index}=true" for index in range(40)))
                archive.writestr("META-INF/MANIFEST.MF", "Specification-Version: v2.4.2435\n")
            with patch.object(builder.subprocess, "check_output", return_value="a" * 40):
                with self.assertRaisesRegex(SystemExit, "SHA-256"):
                    builder.package(source, root / "bad", "r3569", "f" * 64)
                digest = builder.hashlib.sha256(ui.getvalue()).hexdigest()
                with self.assertRaisesRegex(SystemExit, "revision"):
                    builder.package(source, root / "bad", "r9999", digest)
                builder.package(source, root / "output", "r3569", digest)
            metadata = json.loads((root / "output/build-info.json").read_text())
            self.assertEqual(metadata["webui_revision"], "r3569")
            self.assertEqual(metadata["webui_sha256"], digest)
            self.assertTrue((root / "output/SERVER-WEBUI-OVERRIDE.gradle").is_file())
            self.assertEqual(metadata["server_patches"], list(builder.SERVER_PATCHES))
            for name in builder.SERVER_PATCHES:
                self.assertEqual((root / "output" / name).read_bytes(), (APP / name).read_bytes())
                self.assertEqual(metadata["server_patch_sha256"][name],
                                 hashlib.sha256((APP / name).read_bytes()).hexdigest())
            self.assertFalse((root / "output/chapter-page-list.patch").exists())
            self.assertEqual((source / "chapter-page-list.patch").read_text(), "test-only patch fixture")

    def test_ui_login_readiness_requires_builtin_admin_without_exposing_tokens(self):
        java = unittest.mock.Mock()
        java.poll.return_value = None
        expected = {"accessToken": "test-only-access", "refreshToken": "test-only-refresh"}
        with patch.object(runner, "login", return_value=expected):
            self.assertEqual(runner.verify_ui_login(java, "admin", "test-only password"), expected)
        with patch.object(runner, "login", side_effect=RuntimeError("private-token")), self.assertRaisesRegex(RuntimeError, "administrator 1"):
            runner.verify_ui_login(java, "admin", "test-only password")

if __name__ == "__main__":
    unittest.main()
