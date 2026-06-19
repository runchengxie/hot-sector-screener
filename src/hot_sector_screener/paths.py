from __future__ import annotations

import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "src"
CONFIGS_DIR = ROOT_DIR / "configs"
EXPERIMENT_CONFIGS_DIR = CONFIGS_DIR / "experiments"
OUTPUTS_DIR = ROOT_DIR / "outputs"
TMP_DIR = ROOT_DIR / ".tmp"


def get_platform_root() -> Path | None:
    """Return DATA_PLATFORM_ROOT if set, else None."""
    env_val = os.environ.get("DATA_PLATFORM_ROOT")
    if env_val:
        return Path(env_val).expanduser().resolve()
    return None


def ensure_output_dir(date_str: str) -> Path:
    """Create and return outputs/<date_str>/."""
    out_dir = OUTPUTS_DIR / date_str
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir
