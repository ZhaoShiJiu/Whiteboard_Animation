"""
Loads and validates gateway.yaml, resolving ${ENV_VAR} placeholders.
"""

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

# Pattern: ${VAR_NAME} or ${VAR_NAME:-default}
_ENV_VAR_PATTERN = re.compile(r"\$\{(\w+)(?::-(\S*))?\}")


def _resolve_env(value: str) -> str:
    """Replace ${ENV_VAR} or ${ENV_VAR:-default} placeholders in a string."""
    def _replacer(match: re.Match) -> str:
        var_name = match.group(1)
        default = match.group(2)
        env_val = os.getenv(var_name)
        if env_val:
            return env_val
        if default is not None:
            return default
        return match.group(0)  # keep literal if env var not set & no default
    return _ENV_VAR_PATTERN.sub(_replacer, value)


def _resolve_recursive(obj: Any) -> Any:
    """Walk dicts / lists and resolve env-var placeholders in all string values."""
    if isinstance(obj, dict):
        return {k: _resolve_recursive(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_recursive(item) for item in obj]
    if isinstance(obj, str):
        return _resolve_env(obj)
    return obj


def load_config(
    config_path: str | None = None,
    logger: Optional[logging.Logger] = None,
) -> Dict[str, Any]:
    """
    Load gateway.yaml, validate required keys, resolve env vars, and return
    the configuration dictionary.

    Args:
        config_path: Path to gateway.yaml. If None, looks next to this file.
        logger: Optional Python logger for warnings.

    Returns:
        Fully-resolved configuration dict.

    Raises:
        FileNotFoundError: If gateway.yaml is missing.
        ValueError: If required keys are absent.
    """
    log = logger or logging.getLogger("ai_gateway")

    if config_path is None:
        config_path = Path(__file__).parent / "gateway.yaml"

    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(
            f"gateway.yaml not found at {path}. "
            f"Create one from the template in the ai_gateway directory."
        )

    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    config = _resolve_recursive(raw)

    # ---- Basic validation ---------------------------------------------------
    _validate(config, log)

    return config


def _validate(config: Dict[str, Any], log: logging.Logger) -> None:
    """Ensure required top-level keys exist and basic invariants hold."""

    required_keys = ["providers", "routes", "retry", "database", "pricing"]
    for key in required_keys:
        if key not in config:
            raise ValueError(f"gateway.yaml is missing required key: '{key}'")

    providers = config["providers"]
    if not isinstance(providers, dict) or len(providers) == 0:
        raise ValueError("gateway.yaml: 'providers' must be a non-empty dict.")

    routes = config["routes"]
    if not isinstance(routes, dict) or len(routes) == 0:
        raise ValueError("gateway.yaml: 'routes' must be a non-empty dict.")

    # Every route must reference a known provider
    known_providers = set(providers.keys())
    for task, route_cfg in routes.items():
        provider_name = route_cfg.get("provider")
        if provider_name not in known_providers:
            raise ValueError(
                f"Route '{task}' references unknown provider '{provider_name}'. "
                f"Known providers: {known_providers}"
            )

    # Check API keys are resolvable (warn, don't fail — test envs may use mocks)
    for name, provider in providers.items():
        if "api_key_env" in provider:
            env_var = provider["api_key_env"]
            if not os.getenv(env_var):
                log.warning(
                    "Environment variable '%s' (for provider '%s') is not set. "
                    "Calls to this provider will fail.",
                    env_var, name,
                )
