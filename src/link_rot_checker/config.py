"""Settings and the TOML configuration file."""

from __future__ import annotations

import dataclasses
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

from link_rot_checker.fetcher import DEFAULT_USER_AGENT
from link_rot_checker.model import Category

CONFIG_TABLE = "link-rot-checker"
DEFAULT_FAIL_ON = ("broken", "redirect_loop")


class ConfigError(ValueError):
    pass


@dataclass
class Settings:
    """Every option of a scan. CLI flags override the config file, which overrides these defaults."""

    # what to crawl
    start_urls: list[str] = field(default_factory=list)
    sitemaps: list[str] = field(default_factory=list)
    crawl: bool = True
    max_pages: int = 500
    max_depth: int = 10
    include_subdomains: bool = False
    extra_hosts: list[str] = field(default_factory=list)
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    ignore: list[str] = field(default_factory=list)
    respect_robots: bool = True
    follow_nofollow: bool = True
    # what to check
    check_external: bool = True
    check_assets: bool = True
    content_checks: bool = True
    use_head: bool = True
    soft_404: bool = True
    parked: bool = True
    chain_threshold: int = 2
    blocked_statuses: list[int] = field(default_factory=lambda: [401, 403, 429, 999])
    # how
    concurrency: int = 10
    per_host_concurrency: int = 2
    rate_per_host: float = 5.0
    host_rates: dict[str, float] = field(default_factory=dict)
    timeout: float = 15.0
    retries: int = 2
    backoff: float = 0.5
    max_redirects: int = 10
    max_body_bytes: int = 262_144
    verify_tls: bool = True
    user_agent: str = DEFAULT_USER_AGENT
    #: Extra request headers as {header: NAME_OF_ENVIRONMENT_VARIABLE}; values are read from the environment.
    header_env: dict[str, str] = field(default_factory=dict)
    # Wayback Machine
    wayback: bool = True
    wayback_timestamp: str | None = None
    wayback_cache: str | None = None
    #: Requests per second to the availability API.
    wayback_rate: float = 1.0
    # CI
    fail_on: list[str] = field(default_factory=lambda: list(DEFAULT_FAIL_ON))
    fail_threshold: int = 1

    def validate(self) -> None:
        if self.max_pages < 1:
            raise ConfigError("max_pages must be at least 1")
        if self.max_depth < 0:
            raise ConfigError("max_depth must be 0 or more")
        if self.concurrency < 1 or self.per_host_concurrency < 1:
            raise ConfigError("concurrency and per_host_concurrency must be at least 1")
        if self.rate_per_host < 0 or self.wayback_rate < 0 or any(v < 0 for v in self.host_rates.values()):
            raise ConfigError("rates must be 0 (unlimited) or positive")
        if self.timeout <= 0:
            raise ConfigError("timeout must be positive")
        if self.retries < 0 or self.max_redirects < 1:
            raise ConfigError("retries must be >= 0 and max_redirects >= 1")
        if self.chain_threshold < 2:
            raise ConfigError("chain_threshold must be at least 2")
        if self.fail_threshold < 1:
            raise ConfigError("fail_threshold must be at least 1")
        valid = {c.value for c in Category}
        bad = [c for c in self.fail_on if c not in valid and c != "none"]
        if bad:
            raise ConfigError(f"unknown category in fail_on: {', '.join(bad)} (valid: {', '.join(sorted(valid))})")
        for pattern in (*self.include, *self.exclude, *self.ignore):
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ConfigError(f"invalid regular expression {pattern!r}: {exc}") from exc
        if self.wayback_timestamp is not None and not re.fullmatch(r"\d{1,14}", self.wayback_timestamp):
            raise ConfigError("wayback_timestamp must be 1 to 14 digits (YYYYMMDDhhmmss)")
        for header, env in self.header_env.items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", env):
                raise ConfigError(f"header_env[{header!r}] must name an environment variable, got {env!r}")

    def fail_categories(self) -> set[Category]:
        return {Category(c) for c in self.fail_on if c != "none"}

    def extra_headers(self) -> dict[str, str]:
        """Resolve ``header_env``; unset variables are skipped."""
        out: dict[str, str] = {}
        for header, env in self.header_env.items():
            value = os.environ.get(env)
            if value:
                out[header] = value
        return out

    def public_dict(self) -> dict[str, Any]:
        """Settings for the report (header values are never included, only variable names)."""
        data = dataclasses.asdict(self)
        return data


_FIELDS = {f.name: f for f in dataclasses.fields(Settings)}


def _coerce(name: str, value: Any) -> Any:
    default = getattr(Settings(), name)
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise ConfigError(f"{name} must be true or false")
        return value
    if isinstance(default, int) and not isinstance(default, bool):
        if not isinstance(value, int) or isinstance(value, bool):
            raise ConfigError(f"{name} must be an integer")
        return value
    if isinstance(default, float):
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ConfigError(f"{name} must be a number")
        return float(value)
    if isinstance(default, list):
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            raise ConfigError(f"{name} must be a list")
        return list(value)
    if isinstance(default, dict):
        if not isinstance(value, dict):
            raise ConfigError(f"{name} must be a table")
        return dict(value)
    if value is not None and not isinstance(value, str):
        raise ConfigError(f"{name} must be a string")
    return value


def settings_from_mapping(data: dict[str, Any], base: Settings | None = None) -> Settings:
    settings = dataclasses.replace(base) if base else Settings()
    for key, value in data.items():
        name = key.replace("-", "_")
        if name not in _FIELDS:
            raise ConfigError(f"unknown setting: {key}")
        setattr(settings, name, _coerce(name, value))
    return settings


def load_config_file(path: Path, base: Settings | None = None) -> Settings:
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    table = data.get(CONFIG_TABLE, data.get(CONFIG_TABLE.replace("-", "_")))
    if table is None:
        raise ConfigError(f"{path}: no [{CONFIG_TABLE}] table")
    if not isinstance(table, dict):
        raise ConfigError(f"{path}: [{CONFIG_TABLE}] must be a table")
    return settings_from_mapping(table, base)
