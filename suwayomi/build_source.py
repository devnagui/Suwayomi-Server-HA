import io
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


def package(source: Path, output: Path) -> None:
    jars = list((source / "server" / "build").glob("*.jar"))
    if len(jars) != 1:
        raise SystemExit("Expected exactly one built Suwayomi server JAR")
    with zipfile.ZipFile(jars[0]) as jar:
        reference = jar.read("server-reference.conf").decode("utf-8")
        settings = re.findall(r"^server\.(\w+)\s*=", reference, re.MULTILINE)
        version = re.search(r"^Specification-Version: (.+)$", jar.read("META-INF/MANIFEST.MF").decode("utf-8"), re.MULTILINE)
        if version is None:
            raise SystemExit("Built server JAR has no version provenance")
        with zipfile.ZipFile(io.BytesIO(jar.read("WebUI.zip"))) as webui:
            index = webui.read("index.html").decode("utf-8")
            if index.count('<base href="/">') != 1:
                raise SystemExit("Bundled WebUI base-tag contract changed; review ingress before publishing")
            if re.search(r'(?:src|href|data-src)=["\']/assets/', index):
                raise SystemExit("Bundled WebUI has root-relative assets; review ingress before publishing")
            revision = webui.read("revision").decode("utf-8").strip()
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
        "server_settings": sorted(settings),
    }
    (output / "build-info.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    shutil.copyfile(source / "LICENSE", output / "SERVER-LICENSE")


if __name__ == "__main__":
    package(Path(sys.argv[1]), Path(sys.argv[2]))
