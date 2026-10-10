"""Real configuration-loader regression for the offline verify command."""

import pytest

from elite3e_mcp import config, setup


@pytest.mark.parametrize("variable", ["ELITE3E_BASE_URL", "ELITE3E_TOKEN_URL"])
@pytest.mark.parametrize(
    "url", ["https://[invalid", "https://example.invalid:bad", "https://example.invalid:99999"]
)
def test_verify_malformed_url(monkeypatch, capsys, variable, url):
    for key, value in {
        "ELITE3E_BASE_URL": "https://example.invalid",
        "ELITE3E_TOKEN_URL": "https://example.invalid/token",
        "ELITE3E_CLIENT_ID": "synthetic",
        "ELITE3E_CLIENT_SECRET": "synthetic",
        "ELITE3E_SCOPE": "synthetic",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("ELITE3E_AUDIENCE", raising=False)
    monkeypatch.setenv(variable, url)
    with pytest.raises(config.ConfigError, match=variable):
        config.Settings.from_env()
    assert setup.verify() == 2
    output = capsys.readouterr().err
    assert "elite3e-mcp-setup" in output
    assert variable in output
    assert "Traceback" not in output
    assert url not in output
