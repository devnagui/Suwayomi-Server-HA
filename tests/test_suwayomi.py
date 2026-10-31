import ast
import base64
import copy
import importlib.util
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml


ROOT = Path(__file__).parents[1]
APP = ROOT / "suwayomi"
spec = importlib.util.spec_from_file_location("suwayomi_runner", APP / "run.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
MANIFEST = yaml.safe_load((APP / "config.yaml").read_text(encoding="utf-8"))
SUPPORTED = set(runner.OPTION_SETTINGS.values()) | set(runner.FIXED_SETTINGS) | {
    "authUsername", "authPassword", "socksProxyEnabled", "socksProxyHost",
    "socksProxyPassword", "socksProxyPort", "databaseType", "databaseUrl", "databaseUsername", "databasePassword",
}


def options():
    value = copy.deepcopy(MANIFEST["options"])
    value["auth_password"] = 'test-only "password" with \\ escapes'
    return value


class SuwayomiTests(unittest.TestCase):
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

    def test_source_pin_is_consistent(self):
        pin = yaml.safe_load((APP / "build.yaml").read_text())["args"]["SOURCE_REF"]
        self.assertRegex(pin, r"^[0-9a-f]{40}$")
        docker = (APP / "Dockerfile").read_text()
        self.assertEqual(re.findall(r"ARG SOURCE_REF=([0-9a-f]+)", docker), [pin, pin])
        self.assertIn(":server:downloadWebUI :server:shadowJar", docker)
        self.assertIn("git rev-parse HEAD", docker)
        self.assertIn(f"ARG BUILD_VERSION={MANIFEST['version']}", docker)

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

    def test_daily_upstream_check_skips_unchanged_and_updates_changed_source(self):
        current_source = yaml.safe_load((APP / "build.yaml").read_text())["args"]["SOURCE_REF"]
        for source, changed in ((current_source, False), ("b" * 40, True)):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as temporary:
                app = Path(temporary)
                for name in ("build.yaml", "Dockerfile", "config.yaml", "CHANGELOG.md"):
                    shutil.copyfile(APP / name, app / name)
                code = (
                    "const cp=require('node:child_process');"
                    "cp.execFileSync=()=>process.argv[3]+'\\trefs/heads/master\\n';"
                    "const result=require(process.argv[1]).latest(process.argv[2]);"
                    "if(String(result)!==process.argv[4])process.exit(2);"
                )
                result = subprocess.run(
                    ["node", "-e", code, str(ROOT / "scripts" / "update-suwayomi.cjs"), str(app), source, str(changed).lower()],
                    capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                pins = yaml.safe_load((app / "build.yaml").read_text())
                self.assertEqual(pins["args"]["SOURCE_REF"], source)
                version = yaml.safe_load((app / "config.yaml").read_text())["version"]
                parts = [int(part) for part in MANIFEST["version"].split(".")]
                parts[2] += int(changed)
                self.assertEqual(version, ".".join(str(part) for part in parts))
                if changed:
                    self.assertIn(source, (app / "CHANGELOG.md").read_text())

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
        self.assertEqual(settings["authMode"], "BASIC_AUTH")
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
        rendered = runner.render_nginx(template, configured["auth_username"], configured["auth_password"], False)
        authorization = base64.b64encode(f'{configured["auth_username"]}:{configured["auth_password"]}'.encode()).decode()
        ingress, direct = rendered.split("listen 4567;")
        self.assertIn('Authorization "Basic ' + authorization + '"', ingress)
        self.assertIn("allow 172.30.32.2;", ingress)
        self.assertIn("deny all;", ingress)
        self.assertIn("return 403;", direct)
        self.assertIn('auth_basic "Suwayomi";', direct)
        self.assertNotIn(authorization, direct)
        self.assertNotIn("return 403;", runner.render_nginx(template, "user", "password", True).split("listen 4567;")[1])
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


if __name__ == "__main__":
    unittest.main()
