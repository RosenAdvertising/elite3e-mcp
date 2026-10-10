"""Configuration and clean-startup failure tests."""

from __future__ import annotations

import pytest

from elite3e_mcp.config import ConfigError, Settings


def test_missing_credentials_name_every_required_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "ELITE3E_BASE_URL",
        "ELITE3E_TOKEN_URL",
        "ELITE3E_CLIENT_ID",
        "ELITE3E_CLIENT_SECRET",
        "ELITE3E_SCOPE",
        "ELITE3E_AUDIENCE",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ConfigError) as exc:
        Settings.from_env()

    message = str(exc.value)
    assert "ELITE3E_BASE_URL" in message
    assert "ELITE3E_TOKEN_URL" in message
    assert "ELITE3E_CLIENT_ID" in message
    assert "ELITE3E_CLIENT_SECRET" in message


def test_startup_without_credentials_runs_stdio(monkeypatch, capsys) -> None:
    from elite3e_mcp import server
    from elite3e_mcp.runtime import MCPServer

    seen = []
    monkeypatch.setattr(MCPServer, "run", lambda self, **kwargs: seen.append(kwargs))
    server.main()
    assert seen == [{"transport": "stdio"}]
    assert "Traceback" not in capsys.readouterr().err
