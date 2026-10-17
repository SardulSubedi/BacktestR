"""Download a pinned, hash-verified portable compiler for local verification only.

This optional helper is not needed to run BacktestR. It installs nothing globally.
"""

import hashlib
import json
from pathlib import Path
from urllib.request import urlopen
import zipfile

root = Path(__file__).resolve().parents[1]
destination = root / ".tools"
destination.mkdir(exist_ok=True)
with urlopen("https://ziglang.org/download/index.json", timeout=30) as response:
    release = json.load(response)["0.14.1"]["x86_64-windows"]
archive = destination / "zig.zip"
if not archive.is_file() or hashlib.sha256(archive.read_bytes()).hexdigest() != release["shasum"]:
    with urlopen(release["tarball"], timeout=60) as response, archive.open("wb") as output:
        while block := response.read(1024 * 1024):
            output.write(block)
if hashlib.sha256(archive.read_bytes()).hexdigest() != release["shasum"]:
    raise SystemExit("Compiler archive checksum mismatch.")
with zipfile.ZipFile(archive) as zipped:
    # Reject archive members that could escape the verification directory.
    for info in zipped.infolist():
        if not (destination / info.filename).resolve().is_relative_to(destination.resolve()):
            raise SystemExit("Invalid archive path.")
    zipped.extractall(destination)
print(next(destination.glob("*/zig.exe")))
