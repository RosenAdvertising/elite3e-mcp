"""Environment-only configuration for the Elite 3E connection."""

from __future__ import annotations

import os
from dataclasses import dataclass

from elite3e_mcp.urls import require_https


class ConfigError(ValueError):
    """Raised when required environment configuration is missing or invalid."""


def _positive_float(name: str, default: str) -> float:
    raw = os.getenv(name, default)
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number; received {raw!r}") from exc
    if value <= 0:
        raise ConfigError(f"{name} must be greater than zero")
    return value


def _non_negative_int(name: str, default: str) -> int:
    raw = os.getenv(name, default)
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a whole number; received {raw!r}") from exc
    if value < 0:
        raise ConfigError(f"{name} must be zero or greater")
    return value


def _required_url(name: str, value: str) -> str:
    try:
        require_https(value, name)
    except ValueError as exc:
        raise ConfigError(str(exc)) from None
    return value.rstrip("/")


@dataclass(frozen=True, slots=True)
class Settings:
    """Validated settings loaded exclusively from environment variables."""

    base_url: str
    token_url: str
    client_id: str
    client_secret: str
    auth_field: str
    auth_value: str
    timeout_seconds: float = 30.0
    max_retries: int = 3
    backoff_seconds: float = 0.5

    allowed_user_ids: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        _required_url("ELITE3E_BASE_URL", self.base_url)
        _required_url("ELITE3E_TOKEN_URL", self.token_url)
        object.__setattr__(self, "allowed_user_ids", frozenset(self.allowed_user_ids))

    @classmethod
    def from_env(cls) -> Settings:
        """Load settings and name every missing variable in one actionable error."""
        required_names = (
            "ELITE3E_BASE_URL",
            "ELITE3E_TOKEN_URL",
            "ELITE3E_CLIENT_ID",
            "ELITE3E_CLIENT_SECRET",
        )
        values = {name: os.getenv(name, "").strip() for name in required_names}
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise ConfigError("Missing required environment variable(s): " + ", ".join(missing))

        scope = os.getenv("ELITE3E_SCOPE", "").strip()
        audience = os.getenv("ELITE3E_AUDIENCE", "").strip()
        if bool(scope) == bool(audience):
            raise ConfigError(
                "Set exactly one of ELITE3E_SCOPE or ELITE3E_AUDIENCE, as named in the "
                "tenant-issued credential package"
            )

        auth_field, auth_value = ("scope", scope) if scope else ("audience", audience)
        return cls(
            allowed_user_ids=allowed_user_ids_from_env(),
            base_url=_required_url("ELITE3E_BASE_URL", values["ELITE3E_BASE_URL"]),
            token_url=_required_url("ELITE3E_TOKEN_URL", values["ELITE3E_TOKEN_URL"]),
            client_id=values["ELITE3E_CLIENT_ID"],
            client_secret=values["ELITE3E_CLIENT_SECRET"],
            auth_field=auth_field,
            auth_value=auth_value,
            timeout_seconds=_positive_float("ELITE3E_TIMEOUT_SECONDS", "30"),
            max_retries=_non_negative_int("ELITE3E_MAX_RETRIES", "3"),
            backoff_seconds=_positive_float("ELITE3E_BACKOFF_SECONDS", "0.5"),
        )


def allowed_user_ids_from_env() -> frozenset[str]:
    """Only the administrator's process environment grants delegated identities."""
    return frozenset(
        value.strip()
        for value in os.getenv("ELITE3E_ALLOWED_USER_IDS", "").split(",")
        if value.strip()
    )
