"""Small, non-executing .env reader. Existing environment variables win."""

import os
from pathlib import Path


def load_env(path: Path | str = ".env") -> None:
    target = Path(path)
    if not target.is_file():
        return
    for line in target.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in {"OPENAI_API_KEY", "OPENAI_MODEL", "APCA_API_KEY_ID",
                       "APCA_API_SECRET_KEY", "ALPACA_DATA_FEED"}:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)
