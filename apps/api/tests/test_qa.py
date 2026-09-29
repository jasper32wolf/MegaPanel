from app.services.qa import run_page_qa


def base_page():
    return {
        "slug": "/repair",
        "title_template": "Ремонт",
        "h1_template": "Ремонт",
        "meta_description_template": "Ремонт техники в городе.",
        "service": "Ремонт",
        "index_state": "noindex",
    }


def base_input():
    return {
        "facts": {"service": "Ремонт", "contacts": {"phone": "+79990000000"}},
        "project_domain": "example.test",
        "geo_snapshot": {"items": [{}]},
        "keyword_snapshot": {"items": []},
    }


def test_ai_provenance_unknown_fact_blocks_qa():
    result = run_page_qa(
        page_manifest=base_page(),
        input_snapshot={
            **base_input(),
            "ai_provenance": {
                "provider_id": "gateway",
                "model_id": "model",
                "prompt_id": "content.page-draft-copy",
                "prompt_version": "1.0.0",
                "prompt_hash": "a" * 64,
                "fact_keys": ["unknown"],
            },
        },
        existing_texts=[],
    )
    assert result["verdict"] == "block"
    assert any(item["rule"] == "ai_fact_provenance" for item in result["findings"])


def test_ai_provenance_requires_noindex():
    page = {**base_page(), "index_state": "indexed"}
    result = run_page_qa(
        page_manifest=page,
        input_snapshot={
            **base_input(),
            "ai_provenance": {
                "provider_id": "gateway",
                "model_id": "model",
                "prompt_id": "content.page-draft-copy",
                "prompt_version": "1.0.0",
                "prompt_hash": "a" * 64,
                "fact_keys": ["service"],
            },
        },
        existing_texts=[],
    )
    assert result["verdict"] == "block"
    assert any(item["rule"] == "ai_index_policy" for item in result["findings"])


def test_deterministic_draft_without_ai_provenance_is_not_rejected_by_ai_rules():
    result = run_page_qa(page_manifest=base_page(), input_snapshot=base_input(), existing_texts=[])
    assert not any(item["rule"].startswith("ai_") for item in result["findings"])


def test_qa_blocks_email_in_rendered_service_field():
    result = run_page_qa(
        page_manifest={**base_page(), "service": "leads@example.test"},
        input_snapshot=base_input(),
        existing_texts=[],
    )

    assert any(item["rule"] == "public_email_leak" for item in result["findings"])


def test_qa_blocks_email_in_custom_schema_org_faq():
    result = run_page_qa(
        page_manifest={
            **base_page(),
            "schema_org": {"faq": [{"q": "Как связаться?", "a": "Пишите leads@example.test"}]},
        },
        input_snapshot=base_input(),
        existing_texts=[],
    )

    assert any(item["rule"] == "public_email_leak" for item in result["findings"])


def test_qa_blocks_email_in_gallery_and_block_media_alt_text():
    asset_id = "11111111-1111-1111-1111-111111111111"
    block_asset_id = "22222222-2222-2222-2222-222222222222"
    result = run_page_qa(
        page_manifest={
            **base_page(),
            "media": [
                {"asset_id": asset_id, "stored_sha256": "a" * 64, "alt": "leads@example.test"}
            ],
            "blocks": [{"type": "hero", "hash_class": "hero", "html": "<p>Hero</p>"}],
            "block_media": {
                "hero": {
                    "asset_id": block_asset_id,
                    "stored_sha256": "b" * 64,
                    "alt": "privacy@example.test",
                }
            },
        },
        input_snapshot=base_input(),
        existing_texts=[],
    )

    assert any(item["rule"] == "public_email_leak" for item in result["findings"])


def test_qa_blocks_claim_slot_mutation_against_the_frozen_fact_snapshot():
    page = {
        **base_page(),
        "blocks": [{"type": "hero", "hash_class": "hero", "html": "<p>{unique_core}</p>"}],
        "unique_core": "Письменная гарантия",
    }
    snapshot = {
        **base_input(),
        "kit_key": "service-local-v1",
        "facts": {
            "service": "Ремонт",
            "contacts": {"phone": "+79990000000"},
            "allowed_claims": ["Письменная гарантия"],
        },
        "claim_slot_bindings": [{"block_id": "hero", "slot": "unique_core", "claim_index": 0}],
    }

    exact = run_page_qa(page_manifest=page, input_snapshot=snapshot, existing_texts=[])
    assert not any(item["rule"] == "claim_slot_contract" for item in exact["findings"])
    altered = run_page_qa(
        page_manifest={**page, "unique_core": "Неподтверждённая гарантия"},
        input_snapshot=snapshot,
        existing_texts=[],
    )
    assert any(item["rule"] == "claim_slot_contract" for item in altered["findings"])


def test_qa_blocks_email_in_public_page_fields():
    from app.services.qa import run_page_qa

    result = run_page_qa(
        page_manifest={
            "slug": "/",
            "title_template": "Ремонт",
            "h1_template": "Ремонт",
            "meta_description_template": "Пишите leads@example.test",
            "service": "Ремонт",
        },
        input_snapshot={
            "facts": {"service": "Ремонт", "contacts": {"phone": "+79990000000"}},
            "project_domain": "example.test",
            "geo_snapshot": {"items": [{"geo_id": "x"}]},
            "keyword_snapshot": {"items": [{"keyword_id": "x"}]},
        },
        existing_texts=[],
    )

    assert result["verdict"] == "block"
    assert any(item["rule"] == "public_email_leak" for item in result["findings"])
