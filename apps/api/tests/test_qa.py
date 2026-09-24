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
