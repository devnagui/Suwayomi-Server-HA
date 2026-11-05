import io
import hashlib
import json
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

SERVER_PATCHES = ("bundled-webui-version.patch", "scoped-manga-cancellation.patch")


def pin_webui(source: Path, revision: str, minimum_server: int, digest: str) -> None:
    if not re.fullmatch(r"r\d+", revision) or minimum_server < 1 or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise SystemExit("Invalid pinned WebUI compatibility metadata")
    count = int(subprocess.check_output(
        ["git", "-C", str(source), "rev-list", "--count", "HEAD"], text=True
    ).strip())
    if count < minimum_server:
        raise SystemExit("Pinned WebUI requires a newer upstream server")
    constants = source / "buildSrc/src/main/kotlin/Constants.kt"
    content = constants.read_text(encoding="utf-8")
    pattern = r'^val webUIRevisionTag = "r\d+"$'
    if len(re.findall(pattern, content, re.MULTILINE)) != 1:
        raise SystemExit("Upstream bundled WebUI build-pin contract changed")
    url = f"https://github.com/Suwayomi/Suwayomi-WebUI-preview/releases/download/{revision}/Suwayomi-WebUI-{revision}.zip"
    with urllib.request.urlopen(url, timeout=60) as response:
        archive = response.read()
    if hashlib.sha256(archive).hexdigest() != digest:
        raise SystemExit("Downloaded WebUI SHA-256 does not match its pinned release")
    destination = source / "server/src/main/resources/WebUI.zip"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(archive)
    override = source / "ha-webui.gradle"
    override.write_text(
        "gradle.beforeProject { project ->\n"
        "    project.repositories.withType(org.gradle.api.artifacts.repositories.MavenArtifactRepository).configureEach { repository ->\n"
        "        if (repository.url.toString() == 'https://github.com/Suwayomi/Suwayomi-Server/raw/android-jar/') {\n"
        "            repository.url = project.uri('https://raw.githubusercontent.com/Suwayomi/Suwayomi-Server/android-jar/')\n"
        "            repository.content { descriptor ->\n"
        "                descriptor.includeModule('com.github.Suwayomi', 'android-jar')\n"
        "            }\n"
        "        }\n"
        "    }\n"
        "}\n"
        "\n"
        "gradle.projectsEvaluated {\n"
        "    def server = gradle.rootProject.findProject(':server')\n"
        "    if (server == null) return\n"
        f"    server.extensions.getByName('buildConfig').buildConfigField('String', 'HA_WEBUI_TAG', '\"{revision}\"')\n"
        "    server.tasks.named('downloadWebUI') {\n"
        "        enabled = false\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )


def package(source: Path, output: Path, expected_webui: str | None = None, expected_digest: str | None = None) -> None:
    jars = list((source / "server" / "build").glob("*.jar"))
    if len(jars) != 1:
        raise SystemExit("Expected exactly one built Suwayomi server JAR")
    with zipfile.ZipFile(jars[0]) as jar:
        reference = jar.read("server-reference.conf").decode("utf-8")
        settings = re.findall(r"^server\.(\w+)\s*=", reference, re.MULTILINE)
        version = re.search(r"^Specification-Version: (.+)$", jar.read("META-INF/MANIFEST.MF").decode("utf-8"), re.MULTILINE)
        if version is None:
            raise SystemExit("Built server JAR has no version provenance")
        webui_bytes = jar.read("WebUI.zip")
        digest = hashlib.sha256(webui_bytes).hexdigest()
        if expected_digest is not None and digest != expected_digest:
            raise SystemExit("Downloaded bundled WebUI SHA-256 does not match its pinned release")
        with zipfile.ZipFile(io.BytesIO(webui_bytes)) as webui:
            index = webui.read("index.html").decode("utf-8")
            if index.count('<base href="/">') != 1:
                raise SystemExit("Bundled WebUI base-tag contract changed; review ingress before publishing")
            if re.search(r'(?:src|href|data-src)=["\']/assets/', index):
                raise SystemExit("Bundled WebUI has root-relative assets; review ingress before publishing")
            revision = webui.read("revision").decode("utf-8").strip()
            if expected_webui is not None and revision != expected_webui:
                raise SystemExit("Built WebUI revision does not match its pinned release")
            webui_output = output / "webui"
            for entry in webui.infolist():
                destination = (webui_output / entry.filename).resolve()
                if not destination.is_relative_to(webui_output.resolve()) or entry.is_dir():
                    if entry.is_dir() and destination.is_relative_to(webui_output.resolve()):
                        continue
                    raise SystemExit("Bundled WebUI contains an unsafe archive path")
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(webui.read(entry))
    if len(settings) < 40:
        raise SystemExit("Could not extract the upstream global settings contract")
    output.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(jars[0], output / "suwayomi.jar")
    metadata = {
        "server_version": version[1].strip(),
        "source_revision": subprocess.check_output(
            ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
        ).strip(),
        "webui_revision": revision,
        "webui_sha256": digest,
        "webui_build_override": "SERVER-WEBUI-OVERRIDE.gradle",
        "server_patches": list(SERVER_PATCHES),
        "server_patch_sha256": {
            name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in SERVER_PATCHES
        },
        "server_settings": sorted(settings),
    }
    (output / "build-info.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    shutil.copyfile(source / "LICENSE", output / "SERVER-LICENSE")
    shutil.copyfile(source / "ha-webui.gradle", output / "SERVER-WEBUI-OVERRIDE.gradle")
    for name in SERVER_PATCHES:
        shutil.copyfile(source / name, output / name)


if __name__ == "__main__":
    if sys.argv[1] == "--pin-webui":
        pin_webui(Path(sys.argv[2]), sys.argv[3], int(sys.argv[4]), sys.argv[5])
    else:
        package(Path(sys.argv[1]), Path(sys.argv[2]), *sys.argv[3:])
