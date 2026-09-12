"""Generate kit assets for site_panel_blocks."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "packages/block-library/src/site_panel_blocks/kits"

BLOCKS_ORDER = [
    "hero",
    "trust_bar",
    "services_grid",
    "pricing_table",
    "calculator",
    "process_steps",
    "team",
    "portfolio",
    "reviews",
    "guarantee",
    "faq",
    "cta_banner",
    "lead_form",
    "contacts",
    "footer",
]

CRO = {
    "hero": ["offer", "utp", "cta_above_fold", "trust_line"],
    "trust_bar": ["trust_line"],
    "services_grid": ["names", "prices_from", "sla", "guarantee"],
    "pricing_table": ["transparent", "compare", "cta_per_plan"],
    "calculator": ["min_fields"],
    "process_steps": ["sla"],
    "team": ["trust_line"],
    "portfolio": ["trust_line"],
    "reviews": ["trust_line"],
    "guarantee": ["guarantee"],
    "faq": ["5_to_10_questions", "schema_faq", "no_water"],
    "cta_banner": ["cta_above_fold"],
    "lead_form": ["min_fields", "phone_mask", "pdn_consent", "alt_channels"],
    "contacts": ["alt_channels"],
    "footer": [],
}


def css(t: str, extra: str = "") -> str:
    return f"""
.blk-{t} {{
  padding: calc(var(--sp-gap, 1rem) * 1.6) calc(var(--sp-gap, 1rem) * 1.2);
  border-radius: var(--sp-radius, 12px);
  background: var(--sp-surface, #fff);
  color: var(--sp-text, #14201c);
  box-shadow: var(--sp-shadow, none);
  margin: calc(var(--sp-gap, 1rem) * 0.8) 0;
  font-family: var(--sp-font, system-ui);
}}
.blk-{t} .sp-btn {{
  display: inline-block;
  background: var(--sp-primary, #0f6e5c);
  color: #fff;
  padding: 0.7rem 1.2rem;
  border-radius: var(--sp-radius-sm, 8px);
  text-decoration: none;
  font-weight: 600;
  border: 0;
  cursor: pointer;
}}
.blk-{t} .sp-muted {{ color: var(--sp-muted, #5f7269); }}
.blk-{t} h2, .blk-{t} h3 {{ margin: 0 0 0.75rem; letter-spacing: -0.02em; }}
{extra}
""".strip()


HTML = {
    "hero": """
<div class="sp-hero" style="background: var(--sp-gradient); border-radius: var(--sp-radius); padding: 2rem 1.5rem;">
  <p class="sp-muted" style="margin:0 0 .5rem; text-transform:uppercase; letter-spacing:.06em; font-size:.8rem;">Услуги в {city_prep}</p>
  <h2 style="font-size:clamp(1.6rem,3vw,2.4rem); margin:0 0 .75rem;">{service}</h2>
  <p class="offer">{unique_core}</p>
  <p style="margin:1rem 0"><a class="sp-btn" href="tel:{phone}">Позвонить {phone}</a></p>
  <p class="sp-muted trust">Выезд · гарантия · расчёт за 15 минут</p>
</div>
""",
    "trust_bar": """
<ul class="sp-trust" style="display:flex;flex-wrap:wrap;gap:1rem;list-style:none;padding:0;margin:0;">
  <li>Лицензированные мастера</li>
  <li>Фикс-смета до работ</li>
  <li>Гарантия до 12 мес.</li>
  <li>Оплата после приёмки</li>
</ul>
""",
    "services_grid": """
<div>
  <h2>Услуги</h2>
  <ul style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:var(--sp-gap);list-style:none;padding:0;">
    <li style="border:1px solid #ddd;border-radius:var(--sp-radius-sm);padding:1rem;"><strong>{service}</strong><p class="sp-muted">от {price}</p></li>
    <li style="border:1px solid #ddd;border-radius:var(--sp-radius-sm);padding:1rem;"><strong>{modifier}</strong><p class="sp-muted">Срочный выезд</p></li>
    <li style="border:1px solid #ddd;border-radius:var(--sp-radius-sm);padding:1rem;"><strong>Диагностика</strong><p class="sp-muted">от 0 при заказе</p></li>
  </ul>
</div>
""",
    "pricing_table": """
<div>
  <h2>Цены</h2>
  <table style="width:100%;border-collapse:collapse;">
    <thead><tr><th style="text-align:left;padding:.5rem;border-bottom:1px solid #ddd;">Услуга</th><th style="text-align:left;padding:.5rem;border-bottom:1px solid #ddd;">От</th></tr></thead>
    <tbody>
      <tr><td style="padding:.5rem;border-bottom:1px solid #eee;">{service}</td><td style="padding:.5rem;border-bottom:1px solid #eee;">{price}</td></tr>
      <tr><td style="padding:.5rem;border-bottom:1px solid #eee;">Выезд мастера</td><td style="padding:.5rem;border-bottom:1px solid #eee;">0</td></tr>
      <tr><td style="padding:.5rem;">Срочный заказ</td><td style="padding:.5rem;">+20%</td></tr>
    </tbody>
  </table>
</div>
""",
    "calculator": """
<div data-calc="service">
  <h2>Калькулятор</h2>
  <label>Объём <input type="range" min="1" max="10" value="3" data-calc-qty></label>
  <p>Ориентир: <strong data-calc-out>от {price}</strong></p>
  <p class="sp-muted">Точная смета после диагностики.</p>
</div>
""",
    "process_steps": """
<div>
  <h2>Как мы работаем</h2>
  <ol style="padding-left:1.2rem;line-height:1.7;">
    <li>Заявка или звонок</li>
    <li>Выезд и диагностика в {city_prep}</li>
    <li>Согласование сметы</li>
    <li>Работы и гарантия</li>
  </ol>
</div>
""",
    "team": """
<div>
  <h2>Команда</h2>
  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:var(--sp-gap);">
    <div><strong>Алексей</strong><p class="sp-muted">Мастер, 9 лет</p></div>
    <div><strong>Ирина</strong><p class="sp-muted">Диспетчер</p></div>
    <div><strong>Павел</strong><p class="sp-muted">Инженер</p></div>
  </div>
</div>
""",
    "portfolio": """
<div>
  <h2>Примеры работ</h2>
  <ul style="display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:var(--sp-gap);list-style:none;padding:0;">
    <li style="min-height:90px;border-radius:var(--sp-radius-sm);background:#eee;padding:1rem;">Кейс · {city_nom}</li>
    <li style="min-height:90px;border-radius:var(--sp-radius-sm);background:#e8e8e8;padding:1rem;">До / после</li>
    <li style="min-height:90px;border-radius:var(--sp-radius-sm);background:#f0f0f0;padding:1rem;">Объект недели</li>
  </ul>
</div>
""",
    "reviews": """
<div>
  <h2>Отзывы</h2>
  <blockquote style="margin:0 0 1rem;padding:1rem;border-left:3px solid var(--sp-primary);">«Приехали в день обращения.» — клиент из {city_gen}</blockquote>
  <blockquote style="margin:0;padding:1rem;border-left:3px solid var(--sp-primary);">«Смета совпала с итогом.»</blockquote>
</div>
""",
    "guarantee": """
<div>
  <h2>Гарантии</h2>
  <ul>
    <li>Письменная гарантия на работы</li>
    <li>Повторный выезд при гарантийном случае — бесплатно</li>
  </ul>
</div>
""",
    "faq": """
<div>
  <h2>Частые вопросы</h2>
  <details><summary>Сколько стоит {service} в {city_prep}?</summary><p>Стоимость зависит от объёма. Тел. {phone}.</p></details>
  <details><summary>Как быстро приедете?</summary><p>Обычно в день обращения.</p></details>
  <details><summary>Нужна ли предоплата?</summary><p>Оплата после приёмки работ.</p></details>
</div>
""",
    "cta_banner": """
<div style="text-align:center;padding:1.5rem;background:var(--sp-gradient);border-radius:var(--sp-radius);">
  <h2 style="margin-top:0;">Нужен {service} в {city_prep}?</h2>
  <p><a class="sp-btn" href="#lead-form">Оставить заявку</a></p>
</div>
""",
    "lead_form": """
<form id="lead-form" method="post" action="/api/v1/leads/public" style="display:grid;gap:.75rem;max-width:420px;">
  <h2>Заявка</h2>
  <label>Имя <input name="name" autocomplete="name"></label>
  <label>Телефон <input name="phone" type="tel" required autocomplete="tel"></label>
  <label>Комментарий <textarea name="message" rows="3"></textarea></label>
  <input type="text" name="website" value="" tabindex="-1" autocomplete="off" aria-hidden="true" style="position:absolute;left:-9999px;">
  <input type="hidden" name="form_ts" value="">
  <label style="font-size:.85rem;"><input type="checkbox" name="consent" required> Согласие на обработку ПДн</label>
  <button class="sp-btn" type="submit">Отправить</button>
  <p class="sp-muted" style="font-size:.8rem;">Или позвоните: <a href="tel:{phone}">{phone}</a></p>
</form>
""",
    "contacts": """
<address style="font-style:normal;">
  <strong>Контакты</strong><br>
  Тел: <a href="tel:{phone}">{phone}</a><br>
  Город: {city_nom}<br>
  Режим: ежедневно 8:00–22:00
</address>
""",
    "footer": """
<footer style="font-size:.85rem;" class="sp-muted">
  <p>© {city_nom} · {service}</p>
  <p><a href="/privacy/">Конфиденциальность</a> · <a href="/terms/">Оферта</a> · <a href="/cookies/">Cookies</a></p>
</footer>
""",
}

HTML2 = dict(HTML)
HTML2["hero"] = """
<div class="sp-hero" style="display:grid;gap:1rem;background:var(--sp-surface);border:2px solid var(--sp-primary);border-radius:var(--sp-radius);padding:1.5rem;">
  <h2 style="margin:0;font-size:clamp(1.5rem,2.8vw,2.2rem);">{service} — {city_nom}</h2>
  <p class="offer">{unique_core}</p>
  <p><a class="sp-btn" href="#lead-form">Вызвать мастера</a> <a href="tel:{phone}" class="sp-muted">{phone}</a></p>
</div>
"""
HTML2["team"] = """
<div>
  <h2>Мастера на объекте</h2>
  <p class="sp-muted">Бригады с допусками и своим инструментом.</p>
  <ul><li>Сантехника / электрика</li><li>Отделка</li><li>Сборка мебели</li></ul>
</div>
"""

EXTRA_CSS = {
    "hero": ".blk-hero .offer{font-size:1.05rem;max-width:36rem;}",
    "faq": ".blk-faq details{margin:.4rem 0;padding:.5rem 0;border-bottom:1px solid #eee;}",
    "lead_form": ".blk-lead_form label{display:grid;gap:.25rem;font-size:.9rem;} .blk-lead_form input,.blk-lead_form textarea{padding:.55rem .7rem;border:1px solid #ccc;border-radius:var(--sp-radius-sm);}",
}


def write_kit(key: str, name: str, desc: str, niches: list[str], html_map: dict, theme: dict) -> None:
    d = ROOT / key
    (d / "blocks").mkdir(parents=True, exist_ok=True)
    (d / "kit.json").write_text(
        json.dumps(
            {
                "key": key,
                "name": name,
                "version": "1.0.0",
                "description": desc,
                "niches": niches,
                "blocks": BLOCKS_ORDER,
                "block_meta": [
                    {"type": t, "name": t.replace("_", " ").title(), "cro": CRO.get(t, [])}
                    for t in BLOCKS_ORDER
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (d / "theme.json").write_text(json.dumps(theme, ensure_ascii=False, indent=2), encoding="utf-8")
    for t in BLOCKS_ORDER:
        (d / "blocks" / f"{t}.html").write_text(html_map[t].strip() + "\n", encoding="utf-8")
        (d / "blocks" / f"{t}.css").write_text(css(t, EXTRA_CSS.get(t, "")) + "\n", encoding="utf-8")
    print("wrote", key)


if __name__ == "__main__":
    write_kit(
        "service-local-v1",
        "Локальные услуги",
        "Полный комплект для локальных service-сайтов: hero->лид->контакты.",
        ["ремонт", "клининг", "услуги"],
        HTML,
        {
            "radius": 14,
            "primary": "#0f6e5c",
            "secondary": "#1a3d34",
            "bg": "#f7f9f8",
            "surface": "#ffffff",
            "text": "#14201c",
            "muted": "#5f7269",
            "gradient": "soft",
            "font_pair": "sans",
        },
    )
    write_kit(
        "home-repair-v1",
        "Домашний ремонт",
        "Акцент на бригады и выезд: альтернативная вёрстка hero/team.",
        ["ремонт", "стройка", "отделка"],
        HTML2,
        {
            "radius": 8,
            "primary": "#8b5a2b",
            "secondary": "#2c1810",
            "bg": "#faf8f5",
            "surface": "#ffffff",
            "text": "#1c1410",
            "muted": "#6b5e54",
            "gradient": "bold",
            "font_pair": "serif_mix",
        },
    )
