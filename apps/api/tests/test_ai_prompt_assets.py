from __future__ import annotations

import pytest
from app.services.ai_secrets import encrypt_provider_key, redact_secret
from app.services.prompt_catalog import PromptAsset, list_prompts, load_prompt
from app.services.prompt_evals import prompt_evaluation_fixture, validate_prompt_evaluation_fixture


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


def test_every_packaged_prompt_has_a_typed_local_evaluation_fixture() -> None:
    fixtures = [validate_prompt_evaluation_fixture(prompt) for prompt in list_prompts()]

    assert len(fixtures) == len(list_prompts())
    assert all(path.is_file() and path.suffix == ".jsonl" for path in fixtures)


def test_prompt_evaluation_fixture_rejects_escape_from_canonical_directory(tmp_path) -> None:
    prompt_path = tmp_path / "ai" / "content" / "prompt.md"
    prompt_path.parent.mkdir(parents=True)
    prompt = PromptAsset(
        prompt_id="test.prompt",
        version="1",
        path=prompt_path,
        content="- **Evaluation fixtures:** `../../outside.jsonl`\n",
        content_hash="a" * 64,
    )

    with pytest.raises(ValueError, match="outside"):
        prompt_evaluation_fixture(prompt)


def test_prompt_path_traversal_is_rejected() -> None:
    try:
        load_prompt("../../../../outside.md")
    except ValueError as exc:
        assert "outside" in str(exc)
    else:
        raise AssertionError("path traversal was accepted")


def test_architecture_prompt_marks_competitor_evidence_as_reference_only() -> None:
    prompt = load_prompt("architecture/propose-site-map.md")

    assert "Approved competitor evidence is reference-only" in prompt.content
    assert '"approved_competitor_evidence": []' in prompt.content
