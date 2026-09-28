from __future__ import annotations

from pathlib import Path

KITS_ROOT = (
    Path(__file__).resolve().parents[3]
    / "packages"
    / "block-library"
    / "src"
    / "site_panel_blocks"
    / "kits"
)


def test_curated_kits_do_not_ship_unconfirmed_commercial_promises():
    forbidden = (
        "Письменная гарантия на работы",
        "Повторный выезд при гарантийном случае",
        "Гарантия до 12 мес.",
        "Срочный выезд",
        "от 0 при заказе",
        "Срочный заказ",
        "Обычно в день обращения.",
        "Оплата после приёмки работ.",
        "Приехали в день обращения.",
        "Мастер, 9 лет",
    )

    for kit in ("service-local-v1", "home-repair-v1"):
        content = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((KITS_ROOT / kit / "blocks").glob("*.html"))
        )
        assert all(value not in content for value in forbidden)
