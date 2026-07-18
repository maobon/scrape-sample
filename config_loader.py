import json
import os
from pathlib import Path


DEFAULT_CONFIG_PATH = "config.json"


def get_config_path():
    return os.getenv("CONFIG_PATH", DEFAULT_CONFIG_PATH)


def load_app_config(config_path=None):
    path = Path(config_path or get_config_path())
    if not path.exists():
        return {}

    with path.open("r", encoding="utf-8") as f:
        config = json.load(f)

    if not isinstance(config, dict):
        raise ValueError(f"Config root must be a JSON object: {path}")

    return config


def get_config_section(section, config_path=None):
    config = load_app_config(config_path)
    value = config.get(section, {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f'Config "{section}" must be a JSON object')

    return value
