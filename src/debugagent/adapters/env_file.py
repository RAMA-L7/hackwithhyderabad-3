"""Env file loader: KEY=VALUE lines into os.environ. A non-empty value already in the shell wins."""

from __future__ import annotations

import os
from pathlib import Path

from debugagent.domain.errors import InputError


def load_env_file(path: str | Path) -> None:
    file = Path(path)
    if not file.exists():
        raise InputError(f"env file not found: {path}")
    for line in file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if not os.environ.get(key):
            os.environ[key] = value.strip('"').strip("'")
