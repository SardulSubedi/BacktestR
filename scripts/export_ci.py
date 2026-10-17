"""Generate the concrete C++ project that CI compiles (no provider calls)."""

import io
from pathlib import Path
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backtestr.codegen import export_bundle
from backtestr.data import demo_dataset
from backtestr.strategy import default_spec

destination = Path(sys.argv[1] if len(sys.argv) > 1 else "artifacts/cpp").resolve()
destination.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(io.BytesIO(export_bundle(default_spec(), demo_dataset()))) as archive:
    archive.extractall(destination)
print(destination)
