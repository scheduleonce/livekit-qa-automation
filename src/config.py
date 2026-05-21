import json
import os
from pathlib import Path
from typing import Any, Dict, Tuple

import yaml


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config JSON not found at: {path}")
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _read_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"YAML config not found at: {path}")
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def resolve_credentials(env: str, json_path: Path, yaml_path: Path) -> Tuple[str, str, str, str]:
    """Resolve LIVEKIT credentials and bot id value for the given environment.

    Logic:
    - If `json_path` exists, use it (require `env` to be provided).
    - Parse `yaml_path` to find `target_bot_id` for the environment.
    - Find matching bot id entry in the JSON `BOT_ID` list and return its `value`.

    Returns:
        (LIVEKIT_URL, API_KEY, API_SECRET, BOT_ID_VALUE)

    Raises:
        ValueError with clear message on any missing data.
    """
    # If JSON exists, require env selection
    if json_path.exists():
        if not env:
            raise ValueError("ENV environment variable is required when livekit_bot_config.json is present")

        config = _read_json(json_path)
        if env not in config:
            raise ValueError(f"Environment '{env}' not found in {json_path}")

        env_cfg = config[env]
        try:
            livekit_url = env_cfg["LIVEKIT_URL"]
            api_key = env_cfg["API_KEY"]
            api_secret = env_cfg["API_SECRET"]
            bot_list = env_cfg.get("BOT_ID", [])
        except KeyError as exc:
            raise ValueError(f"Missing key {exc} in {json_path} for environment {env}")

        # Parse YAML to get target_bot_id
        yaml_data = _read_yaml(yaml_path)
        environments = yaml_data.get("environments") or {}
        if env not in environments:
            raise ValueError(f"Environment '{env}' not defined in {yaml_path}")
        target_bot_id = str(environments[env].get("target_bot_id"))
        if not target_bot_id:
            raise ValueError(f"No target_bot_id configured for environment '{env}' in {yaml_path}")

        # Find matching bot entry
        for entry in bot_list:
            if str(entry.get("id")) == target_bot_id:
                bot_value = entry.get("value")
                if not bot_value:
                    raise ValueError(f"BOT_ID entry with id={target_bot_id} has no 'value' in {json_path}")
                return livekit_url, api_key, api_secret, bot_value

        raise ValueError(f"No BOT_ID entry with id={target_bot_id} found in {json_path} for environment {env}")

    # Fallback: use environment variables if JSON not present
    livekit_url = os.getenv("QA_LIVEKIT_URL") or os.getenv("LIVEKIT_URL")
    api_key = os.getenv("QA_API_KEY") or os.getenv("API_KEY")
    api_secret = os.getenv("QA_API_SECRET") or os.getenv("API_SECRET")
    bot_id = os.getenv("QA_BOTID") or os.getenv("BOT_ID")

    if not (livekit_url and api_key and api_secret and bot_id):
        missing = [k for k, v in (
            ("LIVEKIT_URL", livekit_url), ("API_KEY", api_key), ("API_SECRET", api_secret), ("BOT_ID", bot_id)
        ) if not v]
        raise ValueError(f"Missing configuration and no livekit_bot_config.json present. Missing: {', '.join(missing)}")

    return livekit_url, api_key, api_secret, bot_id
