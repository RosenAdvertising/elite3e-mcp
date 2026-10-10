"""Validation for administrator-configured credential destinations."""

from urllib.parse import urlsplit

import httpx


def require_https(value: str, name: str) -> str:
    """Reject ambiguous authorities and URL components that can carry secrets."""
    try:
        parsed = urlsplit(value)
        _ = parsed.port
        url = httpx.URL(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or not url.host
            or parsed.username is not None
            or parsed.password is not None
            or "?" in value
            or "#" in value
            or "\\" in value
            or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)
            or "%" in parsed.netloc
            or parsed.netloc.endswith(":")
            or url.host.lower() != parsed.hostname.encode("idna").decode().lower()
        ):
            raise ValueError from None
    except (ValueError, UnicodeError, httpx.InvalidURL):
        raise ValueError(
            f"{name} must be a complete HTTPS URL with a valid host and port, "
            "without userinfo, query or fragment."
        ) from None
    return value
