"""Shared offline test configuration."""

from __future__ import annotations

import pytest

from elite3e_mcp.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(
        base_url="https://tenant.example/3eapi",
        token_url="https://login.example/oauth2/token",
        client_id="test-client-id",
        client_secret="test-client-secret",
        auth_field="scope",
        auth_value="api://elite/.default",
        timeout_seconds=5,
        max_retries=2,
        backoff_seconds=0.001,
    )
