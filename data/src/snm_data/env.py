"""Minimal ``.env`` loader for local development.

Real environment variables always take precedence; the file only fills in missing values.
Empty values are ignored so that a copied ``.env.example`` never masks real settings.
"""

from __future__ import annotations

import os
from collections.abc import MutableMapping
from pathlib import Path


def load_env_file(path: Path, environ: MutableMapping[str, str] | None = None) -> list[str]:
    """Load ``KEY=VALUE`` lines into ``environ`` without overriding existing keys; return loaded keys."""
    target = os.environ if environ is None else environ
    if not path.is_file():
        return []
    loaded: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        line = line.removeprefix("export ").strip()
        key, value = (part.strip() for part in line.split("=", 1))
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and value and key not in target:
            target[key] = value
            loaded.append(key)
    return loaded
