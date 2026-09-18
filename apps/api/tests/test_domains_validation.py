from types import SimpleNamespace

import pytest

from pydantic import ValidationError

from app.api.v1.domains import RedirectCreate, normalize_hostname, validate_redirect


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (" Example.RU. ", "example.ru"),
        ("sub-domain.example.ru", "sub-domain.example.ru"),
    ],
)
def test_normalize_hostname_accepts_valid_public_domains(raw: str, expected: str):
    assert normalize_hostname(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "localhost",
        "127.0.0.1",
        "[::1]",
        "example..ru",
        "-example.ru",
        "example-.ru",
        "https://example.ru",
        "example.ru/path",
    ],
)
def test_normalize_hostname_rejects_non_domains(raw: str):
    with pytest.raises(ValueError):
        normalize_hostname(raw)


def test_validate_redirect_accepts_same_site_https_url():
    site = SimpleNamespace(domain="example.ru")

    assert validate_redirect(site, "/old-page", "https://example.ru/new-page") == (
        "/old-page",
        "https://example.ru/new-page",
    )


@pytest.mark.parametrize(
    ("from_path", "to_url"),
    [
        ("old-page", "https://example.ru/new-page"),
        ("//old-page", "https://example.ru/new-page"),
        ("/old-page", "http://example.ru/new-page"),
        ("/old-page", "https://other.example/new-page"),
        ("/old-page", "https://user@example.ru/new-page"),
        ("/old-page", "https://example.ru:8443/new-page"),
        ("/old\npage", "https://example.ru/new-page"),
        ("/old-page", "https://example.ru/new\npage"),
    ],
)
def test_validate_redirect_rejects_nonlocal_targets(from_path: str, to_url: str):
    with pytest.raises(ValueError):
        validate_redirect(SimpleNamespace(domain="example.ru"), from_path, to_url)


def test_redirect_schema_rejects_non_redirect_status_code():
    with pytest.raises(ValidationError):
        RedirectCreate(site_id="00000000-0000-0000-0000-000000000001", from_path="/old", to_url="https://example.ru/new", code=304)
