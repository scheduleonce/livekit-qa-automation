import json
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from dotenv import load_dotenv
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCAL_ENV = "App3"

# Load local values without replacing variables injected by Jenkins or the shell.
load_dotenv(PROJECT_ROOT / ".env", override=False)


def get_target_environment() -> str:
    """Return the selected environment, preferring CI-friendly variable names."""
    return (
        os.getenv("APP_ENV")
        or os.getenv("TEST_ENV")
        or os.getenv("ENV")
        or DEFAULT_LOCAL_ENV
    ).strip()


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


def resolve_credentials(
    env: Optional[str],
    json_path: Path,
    yaml_path: Path,
) -> Tuple[str, str, str, str]:
    """Resolve LIVEKIT credentials and bot id value for the given environment.

    Environment credentials take precedence over JSON. This supports Jenkins
    credentials bindings while retaining JSON/.env fallback for local runs.

    Returns:
        (LIVEKIT_URL, API_KEY, API_SECRET, BOT_ID_VALUE)

    Raises:
        ValueError with clear message on any missing data.
    """
    target_env = (env or get_target_environment()).strip()
    config = _read_json(json_path) if json_path.exists() else {}
    env_cfg = config.get(target_env, {})

    livekit_url = os.getenv("LIVEKIT_URL") or os.getenv("QA_LIVEKIT_URL")
    api_key = os.getenv("API_KEY") or os.getenv("QA_API_KEY")
    api_secret = os.getenv("API_SECRET") or os.getenv("QA_API_SECRET")
    credential_values = (livekit_url, api_key, api_secret)

    if any(credential_values):
        missing = [
            name for name, value in zip(
                ("LIVEKIT_URL", "API_KEY", "API_SECRET"), credential_values
            ) if not value
        ]
        if missing:
            raise ValueError(
                f"Incomplete environment credentials for '{target_env}'. "
                f"Missing: {', '.join(missing)}"
            )
    else:
        if not env_cfg:
            raise ValueError(
                f"Environment '{target_env}' not found in {json_path} and "
                "LIVEKIT_URL/API_KEY/API_SECRET are not set"
            )
        try:
            livekit_url = env_cfg["LIVEKIT_URL"]
            api_key = env_cfg["API_KEY"]
            api_secret = env_cfg["API_SECRET"]
        except KeyError as exc:
            raise ValueError(
                f"Missing key {exc} in {json_path} for environment {target_env}"
            ) from exc

    bot_id = os.getenv("BOT_ID") or os.getenv("QA_BOTID")
    if bot_id:
        return livekit_url, api_key, api_secret, bot_id

    bot_list = env_cfg.get("BOT_ID", [])
    yaml_data = _read_yaml(yaml_path)
    environments = yaml_data.get("environments") or {}
    if target_env not in environments:
        raise ValueError(f"Environment '{target_env}' not defined in {yaml_path}")
    target_bot_id = str(environments[target_env].get("target_bot_id", ""))
    if not target_bot_id:
        raise ValueError(
            f"No target_bot_id configured for environment '{target_env}' in {yaml_path}"
        )

    for entry in bot_list:
        if str(entry.get("id")) == target_bot_id:
            bot_value = entry.get("value")
            if not bot_value:
                raise ValueError(
                    f"BOT_ID entry with id={target_bot_id} has no 'value' in {json_path}"
                )
            return livekit_url, api_key, api_secret, bot_value

    raise ValueError(
        f"No BOT_ID entry with id={target_bot_id} found in {json_path} for environment {target_env}"
    )
