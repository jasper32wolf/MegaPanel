import asyncio

from app.services.llm_router import generate_micro_infill


def test_generate_local_no_keys():
    result = asyncio.run(
        generate_micro_infill(
            "test prompt",
            {"service": "ремонт", "city": "Москва", "city_prep": "Москве", "niche": "ремонт"},
            use_llm=False,
            tenant_id="t-test",
        )
    )
    assert result.provider == "local"
    assert result.tokens_out == 0
    assert len(result.data.unique_core) >= 20


def test_generate_llm_falls_back_without_keys():
    result = asyncio.run(
        generate_micro_infill(
            "test",
            {"service": "уборка", "city": "Казань", "city_prep": "Казани", "niche": "клининг"},
            use_llm=True,
            tenant_id="t-test",
        )
    )
    assert result.provider in {"local_fallback", "local"}
    assert result.data.offer
