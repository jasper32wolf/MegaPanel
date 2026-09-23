from __future__ import annotations

from app.services.ai_secrets import encrypt_provider_key, redact_secret
from app.services.prompt_catalog import list_prompts, load_prompt


def test_provider_secret_is_encrypted_and_only_suffix_is_exposed() -> None:
    secret = encrypt_provider_key("provider-secret-1234")
    assert secret.ciphertext != "provider-secret-1234"
    assert secret.last4 == "1234"
    assert redact_secret("provider-secret-1234") != "provider-secret-1234"


def test_prompt_assets_have_identity_and_hash() -> None:
    prompts = list_prompts()
    assert len(prompts) >= 8
    assert len({prompt.prompt_id for prompt in prompts}) == len(prompts)
    assert all(len(prompt.content_hash) == 64 for prompt in prompts)
    assert load_prompt("architecture/propose-site-map.md").prompt_id == "architecture.site-map"


def test_prompt_path_traversal_is_rejected() -> None:
    try:
        load_prompt("../../../../outside.md")
    except ValueError as exc:
        assert "outside" in str(exc)
    else:
        raise AssertionError("path traversal was accepted")
