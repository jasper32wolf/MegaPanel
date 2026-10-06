"""Legal page templates (TZ 10) — lawyer review required before production."""

from __future__ import annotations

from html import escape
from typing import Any


def _legal_telemetry_tag(token: str | None) -> str:
    if not token:
        return ""
    return (
        '<script src="/site-panel-telemetry.js" '
        f'data-telemetry-token="{escape(token, quote=True)}" defer></script>'
    )


def render_privacy(
    org: dict[str, Any],
    jurisdiction: str = "152-FZ",
    telemetry_token: str | None = None,
    *,
    raw_days: int = 30,
    aggregate_days: int = 365,
) -> str:
    name = escape(str(org.get("org") or org.get("name") or "Оператор"))
    inn = escape(str(org.get("inn") or "—"))
    email = escape(str(org.get("privacy_email") or "не указан"))
    address = escape(str(org.get("address") or "—"))
    jurisdiction = escape(jurisdiction)
    return f"""<!DOCTYPE html>
<html lang="ru"><head>
<meta charset="utf-8"><meta name="robots" content="noindex, follow">
<title>Политика конфиденциальности</title></head>
<body>
<h1>Политика конфиденциальности</h1>
<p>Оператор: {name}, ИНН {inn}, адрес {address}.</p>
<p>Юрисдикция: {jurisdiction}. Контакт по ПДн: {email}.</p>
<p>Персональные данные обрабатываются на основании согласия / договора.
Субъект вправе запросить доступ, исправление или удаление (DSAR).</p>
<p>При отдельном согласии на аналитику сайт сохраняет тип события, путь страницы,
дату и псевдонимный идентификатор сессии. IP-адрес, текст заявок и параметры URL
в события аналитики не записываются. Сырые события хранятся {raw_days} завершённых
календарных дней UTC (допустимая настройка 7–90 дней) и удаляются ежедневной
задачей. Суточные итоги без идентификатора сессии хранятся {aggregate_days} дней
(допустимая настройка 90–730 дней) и также удаляются ежедневной задачей. При остановке
сервера очистка задерживается до возобновления его работы.</p>
<p>Согласие можно отозвать через «Настройки приватности» на сайте. Сбор прекращается,
доступные сырые события этой сессии удаляются при связи с сервером; суточные
итоги после удаления сырых записей не привязаны к сессии и отдельно не удаляются.
Если нет соединения, сайт повторяет запрос на удаление при следующем посещении
в той же вкладке. Сигналы GPC и DNT отключают аналитику.</p>
<p>Подробнее: <a href="/cookie-policy/">политика cookie</a>.</p>
<p><em>Шаблон — требуется юридическое ревью перед публикацией.</em></p>
<script src="/cookie-banner.js" defer></script>
{_legal_telemetry_tag(telemetry_token)}
</body></html>
"""


def render_terms(org: dict[str, Any], telemetry_token: str | None = None) -> str:
    name = escape(str(org.get("org") or "Исполнитель"))
    return f"""<!DOCTYPE html>
<html lang="ru"><head>
<meta charset="utf-8"><meta name="robots" content="noindex, follow">
<title>Пользовательское соглашение</title></head>
<body>
<h1>Пользовательское соглашение</h1>
<p>Услуги оказывает {name}. Используя сайт, вы соглашаетесь с условиями оказания услуг.</p>
<p><em>Шаблон — требуется юридическое ревью.</em></p>
<script src="/cookie-banner.js" defer></script>
{_legal_telemetry_tag(telemetry_token)}
</body></html>
"""


def render_cookies(org: dict[str, Any], telemetry_token: str | None = None) -> str:
    return f"""<!DOCTYPE html>
<html lang="ru"><head>
<meta charset="utf-8"><meta name="robots" content="noindex, follow">
<title>Политика cookie</title></head>
<body>
<h1>Политика cookie</h1>
<p>Необходимые данные поддерживают работу сайта. Аналитика включается только
после отдельного выбора «Разрешить аналитику». Для этой цели браузер хранит
согласие в localStorage, а случайный идентификатор сессии — в sessionStorage.
На сервер передаётся только криптографический отпечаток идентификатора.</p>
<p>Кнопка «Настройки приватности» доступна на страницах сайта. Выберите
«Только необходимые», чтобы прекратить аналитику и удалить доступные сырые
события этой сессии. Если сервер недоступен, запрос повторяется при следующем
посещении в той же вкладке. Техническая отметка отзыва на сервере хранится
24 часа и затем удаляется ежедневной очисткой. GPC/DNT запрещают сбор независимо
от выбора в браузере.</p>
<p><a href="/privacy/">Политика конфиденциальности</a>.</p>
<script src="/cookie-banner.js" defer></script>
{_legal_telemetry_tag(telemetry_token)}
</body></html>
"""


COOKIE_BANNER_JS = """(() => {
  const consentKey = "sp_consent";
  const sessionKey = "sp_telemetry_session";
  const noTrack = navigator.globalPrivacyControl || navigator.doNotTrack === "1";
  const read = () => {
    try { return JSON.parse(localStorage.getItem(consentKey) || "null"); }
    catch { return null; }
  };
  const stored = read();
  window.__spConsent = {
    necessary: true,
    analytics: !noTrack && stored?.analytics === true,
    marketing: false,
  };
  if (noTrack && stored?.analytics) {
    try { localStorage.setItem(consentKey, JSON.stringify(window.__spConsent)); }
    catch { /* Storage may be unavailable. */ }
  }
  const button = (label, action) => {
    const control = document.createElement("button");
    control.type = "button";
    control.textContent = label;
    control.style.cssText = "margin:4px;padding:8px 12px;cursor:pointer";
    control.addEventListener("click", action);
    return control;
  };
  const settings = button("Настройки приватности", openSettings);
  settings.style.cssText = "position:fixed;bottom:8px;left:8px;z-index:9998;" +
    "padding:8px 12px;background:#1c1917;color:#fafaf9;border:1px solid #fafaf9;" +
    "border-radius:6px;cursor:pointer;font:14px sans-serif";
  document.body.appendChild(settings);
  let dialog;
  function save(analytics) {
    const previous = window.__spConsent?.analytics === true;
    const next = { necessary: true, analytics: !noTrack && !!analytics, marketing: false };
    window.__spConsent = next;
    try { localStorage.setItem(consentKey, JSON.stringify(next)); }
    catch { /* An unavailable store never enables analytics on a later page. */ }
    document.dispatchEvent(new CustomEvent("sp:consent", {
      detail: { analytics: next.analytics, previousAnalytics: previous },
    }));
    if (!next.analytics) {
      try { sessionStorage.removeItem(sessionKey); } catch { /* Storage blocked. */ }
    }
    dialog?.remove();
    dialog = undefined;
    settings.focus();
  }
  function openSettings() {
    if (dialog) { dialog.querySelector("button")?.focus(); return; }
    dialog = document.createElement("div");
    dialog.setAttribute("role", "dialog");
    dialog.setAttribute("aria-label", "Настройки приватности");
    dialog.style.cssText = "position:fixed;bottom:56px;left:8px;right:8px;" +
      "max-width:520px;padding:16px;background:#1c1917;color:#fafaf9;" +
      "border-radius:8px;z-index:9999;font:14px sans-serif";
    const text = document.createElement("p");
    text.textContent = "Аналитика работает только после вашего согласия. " +
      "Вы можете изменить выбор в любое время.";
    dialog.appendChild(text);
    dialog.appendChild(button("Только необходимые", () => save(false)));
    const allow = button("Разрешить аналитику", () => save(true));
    allow.disabled = !!noTrack;
    dialog.appendChild(allow);
    if (noTrack) {
      const note = document.createElement("p");
      note.textContent = "GPC/DNT в браузере запрещает аналитику.";
      dialog.appendChild(note);
    }
    if (read()) dialog.appendChild(button("Закрыть", () => {
      dialog.remove(); dialog = undefined; settings.focus();
    }));
    document.body.appendChild(dialog);
    dialog.querySelector("button")?.focus();
  }
  if (!stored) openSettings();
})();
"""


def write_legal_pack(
    site_dir,
    org: dict[str, Any],
    *,
    telemetry_token: str | None = None,
    raw_days: int = 30,
    aggregate_days: int = 365,
) -> list[str]:
    from pathlib import Path

    if not 7 <= raw_days <= 90 or not 90 <= aggregate_days <= 730:
        raise ValueError("Telemetry retention policy is outside the supported range")
    site_dir = Path(site_dir)
    written = []
    for slug, html in (
        (
            "privacy",
            render_privacy(
                org,
                str(org.get("jurisdiction") or "152-FZ"),
                telemetry_token,
                raw_days=raw_days,
                aggregate_days=aggregate_days,
            ),
        ),
        ("terms", render_terms(org, telemetry_token)),
        ("cookie-policy", render_cookies(org, telemetry_token)),
    ):
        out = site_dir / slug / "index.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html, encoding="utf-8")
        written.append(str(out))
    (site_dir / "cookie-banner.js").write_text(COOKIE_BANNER_JS, encoding="utf-8")
    written.append(str(site_dir / "cookie-banner.js"))
    return written
