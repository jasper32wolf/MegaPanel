"""Legal page templates (TZ 10) — lawyer review required before production."""

from __future__ import annotations

from html import escape
from typing import Any


def render_privacy(org: dict[str, Any], jurisdiction: str = "152-FZ") -> str:
    name = escape(str(org.get("org") or org.get("name") or "Оператор"))
    inn = escape(str(org.get("inn") or "—"))
    email = escape(str(org.get("email") or "privacy@example.com"))
    address = escape(str(org.get("address") or "—"))
    jurisdiction = escape(jurisdiction)
    return f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><title>Политика конфиденциальности</title></head>
<body>
<h1>Политика конфиденциальности</h1>
<p>Оператор: {name}, ИНН {inn}, адрес {address}.</p>
<p>Юрисдикция: {jurisdiction}. Контакт по ПДн: {email}.</p>
<p>Персональные данные обрабатываются на основании согласия / договора.
Субъект вправе запросить доступ, исправление или удаление (DSAR).</p>
<p><em>Шаблон — требуется юридическое ревью перед публикацией.</em></p>
</body></html>
"""


def render_terms(org: dict[str, Any]) -> str:
    name = escape(str(org.get("org") or "Исполнитель"))
    return f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><title>Пользовательское соглашение</title></head>
<body>
<h1>Пользовательское соглашение</h1>
<p>Услуги оказывает {name}. Используя сайт, вы соглашаетесь с условиями оказания услуг.</p>
<p><em>Шаблон — требуется юридическое ревью.</em></p>
</body></html>
"""


def render_cookies(org: dict[str, Any]) -> str:
    return """<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><title>Cookie Policy</title></head>
<body>
<h1>Политика cookie</h1>
<p>Мы используем необходимые cookie для работы сайта. Аналитика — только после согласия.</p>
<p>GPC/DNT учитываются Consent Management модулем панели.</p>
</body></html>
"""


COOKIE_BANNER_JS = (
    "(function(){if(localStorage.getItem('sp_consent')){"
    "window.__spConsent=JSON.parse(localStorage.getItem('sp_consent'));return;}"
    "var b=document.createElement('div');b.setAttribute('role','dialog');"
    "b.style.cssText='position:fixed;bottom:0;left:0;right:0;padding:12px 16px;"
    "background:#1c1917;color:#fafaf9;z-index:9999;font:14px sans-serif';"
    'b.innerHTML=\'Мы используем cookie. <button id="sp-ok">Принять необходимые</button> '
    '<button id="sp-all">Принять все</button>\';document.body.appendChild(b);'
    "function save(a){var c={necessary:true,analytics:!!a,marketing:!!a};"
    "localStorage.setItem('sp_consent',JSON.stringify(c));window.__spConsent=c;b.remove();}"
    "document.getElementById('sp-ok').onclick=function(){save(false)};"
    "document.getElementById('sp-all').onclick=function(){save(true)};})();"
)


def write_legal_pack(site_dir, org: dict[str, Any]) -> list[str]:
    from pathlib import Path

    site_dir = Path(site_dir)
    written = []
    for slug, html in (
        ("privacy", render_privacy(org)),
        ("terms", render_terms(org)),
        ("cookie-policy", render_cookies(org)),
    ):
        out = site_dir / slug / "index.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html, encoding="utf-8")
        written.append(str(out))
    (site_dir / "cookie-banner.js").write_text(COOKIE_BANNER_JS, encoding="utf-8")
    written.append(str(site_dir / "cookie-banner.js"))
    return written
