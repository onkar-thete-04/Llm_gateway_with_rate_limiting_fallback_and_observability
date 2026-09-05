"""YAML configuration loading and hot-reload.

Config lives in two files (``teams.yaml`` and ``providers.yaml``) inside a
config directory. A ``ConfigStore`` holds the current snapshot in memory and
can be swapped atomically on reload.

``api_key_ref`` on a provider is resolved against environment variables (or a
``secrets`` mapping), so API keys are never committed to YAML.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Callable, Mapping

import yaml

from .schema import GatewayConfig, ProviderConfig, TeamConfig


class ConfigError(Exception):
    """Raised when configuration cannot be loaded or parsed."""


class ConfigStore:
    """Holds the current config snapshot plus resolved secrets."""

    def __init__(self, config: GatewayConfig, secrets: Mapping[str, str]) -> None:
        self._config = config
        self._secrets = dict(secrets)

    @property
    def config(self) -> GatewayConfig:
        return self._config

    def team_by_key(self, api_key: str) -> TeamConfig | None:
        for team in self._config.teams:
            if team.api_key == api_key:
                return team
        return None

    def team_by_name(self, name: str) -> TeamConfig | None:
        for team in self._config.teams:
            if team.name == name:
                return team
        return None

    def provider_by_name(self, name: str) -> ProviderConfig | None:
        for provider in self._config.providers:
            if provider.name == name:
                return provider
        return None

    def resolve_secret(self, ref: str | None) -> str | None:
        if not ref:
            return None
        return self._secrets.get(ref)


def _resolve_secrets() -> dict[str, str]:
    return {key: value for key, value in os.environ.items()}


def load_config(
    config_dir: str | Path,
    secrets: Mapping[str, str] | None = None,
) -> ConfigStore:
    """Load ``teams.yaml`` + ``providers.yaml`` from ``config_dir``."""
    config_dir = Path(config_dir)
    secrets = dict(secrets) if secrets is not None else _resolve_secrets()

    teams_path = config_dir / "teams.yaml"
    providers_path = config_dir / "providers.yaml"

    teams_raw = _load_yaml(teams_path) or {}
    providers_raw = _load_yaml(providers_path) or {}

    teams = [TeamConfig.model_validate(item) for item in teams_raw.get("teams", [])]
    providers = [ProviderConfig.model_validate(item) for item in providers_raw.get("providers", [])]

    config = GatewayConfig(teams=teams, providers=providers)
    return ConfigStore(config, secrets)


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"expected a mapping in {path}")
    return data


class ConfigReloader:
    """Background thread that polls config files for mtime changes and swaps
    the store in place when they change."""

    def __init__(
        self,
        store: ConfigStore,
        config_dir: str | Path,
        secrets: Mapping[str, str] | None = None,
        poll_interval: float = 2.0,
        on_reload: Callable[[], None] | None = None,
    ) -> None:
        self._store = store
        self._config_dir = Path(config_dir)
        self._secrets = dict(secrets) if secrets is not None else _resolve_secrets()
        self._poll_interval = poll_interval
        self._on_reload = on_reload
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._mtimes: dict[Path, float] = {}

    def start(self) -> None:
        if self._thread is not None:
            return
        self._mtimes = self._snapshot_mtimes()
        self._thread = threading.Thread(target=self._run, name="config-reloader", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

    def _snapshot_mtimes(self) -> dict[Path, float]:
        snap: dict[Path, float] = {}
        for name in ("teams.yaml", "providers.yaml"):
            path = self._config_dir / name
            snap[path] = path.stat().st_mtime if path.exists() else 0.0
        return snap

    def _run(self) -> None:
        while not self._stop.wait(self._poll_interval):
            current = self._snapshot_mtimes()
            if current != self._mtimes:
                try:
                    new_store = load_config(self._config_dir, self._secrets)
                except ConfigError:
                    continue
                self._store._config = new_store.config
                self._store._secrets = dict(new_store._secrets)
                self._mtimes = current
                if self._on_reload is not None:
                    try:
                        self._on_reload()
                    except Exception:  # noqa: BLE001 - reload callback must not kill the thread
                        continue
