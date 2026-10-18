"""Configuration loading and validation."""

from pathlib import Path
from typing import Any, Union

import yaml


DATA_PROCESSING_DIR = Path(__file__).resolve().parent
CONFIG_PATH = DATA_PROCESSING_DIR / "config.yaml"


def load_config(path: Union[Path, str] = CONFIG_PATH) -> dict[str, Any]:
    """Load and validate the embedding configuration."""
    with Path(path).open("r", encoding="utf-8") as config_file:
        config = yaml.safe_load(config_file)

    if not isinstance(config, dict):
        raise ValueError("Configuration must be a YAML mapping.")

    required = {
        "model_name",
        "max_seq_length",
        "batch_size",
        "top_n",
        "vector_dimension",
        "field_map",
    }
    missing = required.difference(config)
    if missing:
        raise ValueError(f"Missing configuration values: {', '.join(sorted(missing))}")

    max_seq_length = int(config["max_seq_length"])
    if not 1 <= max_seq_length <= 8192:
        raise ValueError("max_seq_length must be between 1 and 8192.")
    for name in ("batch_size", "top_n", "vector_dimension"):
        if int(config[name]) < 1:
            raise ValueError(f"{name} must be at least 1.")
    if not isinstance(config["field_map"], dict):
        raise ValueError("field_map must be a YAML mapping.")
    if not str(config["model_name"]).strip():
        raise ValueError("model_name cannot be empty.")

    config["max_seq_length"] = max_seq_length
    for name in ("batch_size", "top_n", "vector_dimension"):
        config[name] = int(config[name])
    return config