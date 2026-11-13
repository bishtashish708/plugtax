"""Settings: defaults, then the environment's file, then overrides, then env vars."""

import os
import tomllib
from pathlib import Path

CONFIG = Path(__file__).resolve().parent.parent / "config"
LEGACY_PORTS = {"prod": 443}


def _load(name):
    path = CONFIG / f"{name}.toml"
    return tomllib.loads(path.read_text()) if path.exists() else {}


def _merge(base, extra):
    out = dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_settings(env=None):
    env = os.environ if env is None else env
    app_env = env.get("APP_ENV", "dev")
    settings = _merge(_load("default"), _load(app_env))
    if env.get("APP_LOCAL") == "1":
        settings = _merge(settings, _load(f"{app_env}.local"))
    server = settings["server"]
    if env.get("APP_LEGACY_PORTS") == "1":
        server["port"] = LEGACY_PORTS.get(app_env, server["port"])
    if "PORT" in env:
        server["port"] = int(env["PORT"])
    # Production listens one above the configured port; the configured one is
    # reserved for the health-check sidecar.
    if app_env == "prod":
        server["port"] += 1
    return settings
