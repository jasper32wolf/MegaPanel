# Roadmap операторского продукта

> **Статус документа: предложение, не реализация.** Здесь описано развитие Site Panel после доказательства текущего single-user VPS release-кандидата. Источник фактов о существующем коде, проверках и ограничениях — [ХОД-РАБОТ.md](./ХОД-РАБОТ.md). Этот roadmap не добавляет API, экранов, фоновых задач, сроков или гарантий.

## Реализованный срез 2026-09-20

Следующие части roadmap уже реализованы на уровне API/UI и автоматических проверок, но ещё не прошли PostgreSQL, Docker/Caddy и browser E2E proof:

- `Project`, immutable fact revisions, project-bound keyword/geo selection и PagePlan state machine;
- deterministic PageDraft generation из approved inputs, QA `pass/warn/block`, audited warning override и explicit apply без публикации;
- candidate SSG materialization без activation, authenticated preview, explicit project publish и selected rollback;
- append-only LeadOutcome, lead analysis summary и отдельный outcome history в inbox;
- project-first panel workspace `/projects` и `/projects/:projectId` с loading/error/status пояснениями.

Оставшиеся части этого roadmap — backfill существующих сайтов, полноценный PostgreSQL/RLS migration proof, worker orchestration, Playwright/axe E2E, CI release job и production runtime proof.

## Как читать статусы

| Статус | Значение |
|---|---|
| **Реализовано** | Возможность находится в текущем release-кандидате. |
| **Проверено** | Для возможности есть указанный автоматический тест или реальный runtime-прогон. |
| **Предлагается** | Направление будущей работы; не является доступной функцией. |

На текущем этапе реализованы базовые single-user access, TOTP/cookie sessions, curated sites, encrypted lead inbox, per-site webhooks, durable delivery и ограниченный GitHub-managed update/recovery control plane. Автоматические проверки подтверждают контрактный уровень, но GitHub dispatch, Docker/VPS, PostgreSQL/RLS, Caddy/TLS и browser end-to-end сценарии ещё требуют отдельного доказательства.

## Граница первого релиза

Первый релиз — self-hosted панель на одном VPS для одного оператора. Его обязательный сценарий:

```text
оператор → сайт → домен → сборка/публикация → same-origin форма → зашифрованный лид → подписанный webhook
```

Это не public SaaS, не клиентский кабинет, не платформа ролей, не автоматический контентный конвейер и не гарантия приёма webhook внешней системой. Полный перечень release gate находится в [ХОД-РАБОТ.md](./ХОД-РАБОТ.md#94-минимальный-release-gate).

## Целевая ручная петля работы

После подтверждения базового release-кандидата продукт развивается в контролируемую операторскую петлю:

```text
данные бизнеса
→ вручную внесённые семантика и география
→ согласованный план страниц
→ контролируемая генерация и QA
→ сборка / публикация
→ исходы лидов
→ оценка следующей итерации оператором
```

Каждый переход требует видимого входного артефакта, статуса, причины блокировки и ручного решения оператора. Автоматизация может помогать готовить черновик, но не должна скрытно менять факты, публиковать сайт или переписывать контент.

## Направления развития

### 1. Карточка проекта и факты бизнеса

**Базовый контур реализован.** Есть Project, immutable fact revisions, подтверждение оператором и связь с PagePlan. Дальше требуется backfill legacy sites и PostgreSQL/RLS runtime proof.

- **Вход:** данные, внесённые оператором.
- **Решение:** оператор подтверждает, какие факты допустимо использовать.
- **Результат:** версия fact set, пригодная для page plan и сборки.
- **Готовность:** изменения фактов видимы, обратимы и связаны с конкретным проектом; неполные данные блокируют только зависимые действия.

### 2. Ручные семантика и география

**Базовый контур реализован.** Project-bound keyword/geo selection, primary geo и blockers существуют; дальнейшая работа — coverage UX, duplicate review и real PostgreSQL proof.

- Семантика импортируется, очищается, группируется по намерению и остаётся черновиком до утверждения.
- География берётся из локального справочника, проверяется оператором и при необходимости получает ручные морфологические исправления.
- Импорт не создаёт страницы, не запускает LLM и не публикует сайт сам по себе.
- Полный GAR/FIAS, live SERP и автоматическое обогащение не входят в это направление.

### 3. Согласованный план страниц

**Базовый контур реализован.** PagePlan state machine, frozen inputs и manual approve/reject существуют. Нужны legacy backfill, worker orchestration и runtime/browser proof.

Каждая страница должна хранить цель, slug, связанные keyword clusters, географию, выбранный kit/blocks, источники фактов, риски и решение оператора (`черновик → на проверке → одобрено → отклонено`). Только одобренная страница может переходить к генерации или сборке.

**Готовность:** оператор видит покрытие ключей и причину существования каждой страницы, может изменить план и не теряет опубликованную версию при отклонении черновика.

### 4. Контролируемая генерация и QA

**Базовый контур реализован.** Deterministic PageDraft generation, immutable inputs, `pass/warn/block`, audited warn override и explicit apply уже есть; provider-backed generation и async worker execution остаются post-proof work.

- Сохранять версию входных данных, prompt/model, стоимость, результат и причину fallback.
- Выполнять детерминированные QA-проверки: обязательные факты, intent/keyword coverage, URL и internal links, SEO limits, дубли, запрещённые claims, PII и XSS.
- Представлять оператору ясный verdict: `pass`, `warn`, `block` или явно зааудитированный override.
- Не публиковать результат автоматически и не использовать LLM как источник неподтверждённых фактов.

**Готовность:** оператор может сравнить черновик с входными фактами, принять/отклонить его и повторить задачу без скрытого изменения текущего опубликованного контента.

### 5. Сборка, публикация и откат

**Базовый контур реализован.** Candidate build не активирует release, preview требует аутентификацию, publish/rollback требуют selected hash и explicit confirmation. Нужны Docker/Caddy/domain runtime proof и закрытие legacy direct-publish bypass.

- Ошибка сборки или качества не должна заменять текущий release.
- В UI должны быть видны причина, версия, затронутые страницы и следующее действие.
- Автоматическая публикация и IndexNow/drip не являются целью этого этапа.

### 6. Петля исходов лидов

**Базовый контур реализован.** Append-only LeadOutcome, reason/note/history и aggregate analysis отделены от inbox/delivery state. Результат лида не меняет content или SEO автоматически.

Оператор отмечает понятный исход лида и контекст: сайт, страница, статус, причина и заметка. Эти сведения становятся наблюдаемым материалом для следующей проверки page plan или формы. Они не должны автоматически менять текст, ставки, публикацию или маршрутизацию лида.

**Готовность:** результат лида отделён от технического статуса webhook delivery и доступен для осмысленного ручного анализа.

### 7. Ежедневный опыт оператора

**Частично реализован.** Project workspace и базовые loading/error/status states есть; остаются accessible confirm dialog, complete keyboard/browser E2E and axe checks.

Приоритеты: очереди задач, статусы и причины блокировки, loading/empty/error/retry состояния, безопасные подтверждения, controlled reveal PII, keyboard navigation и browser-проверки основных действий. Новые разделы навигации нельзя добавлять до появления полноценного workflow.

### 8. Наблюдаемость и восстановление

**Частично реализовано.** Безопасный экран panel update/recovery, immutable release scripts, encrypted off-host backup и bounded code rollback существуют. Нужны измеримые readiness/alerts, реальный GitHub dispatch, регулярный restore drill и зафиксированные RPO/RTO. Пока такие прогоны не выполнены, recovery и SLA нельзя называть подтверждёнными.

## Порядок и зависимости

1. Сначала завершить release gate текущего VPS-кандидата: PostgreSQL/RLS, Docker/Caddy, browser, public lead journey и restore drill.
2. Затем формализовать данные бизнеса, ручную семантику/географию и page plan.
3. После одобряемого page plan связывать с ним generation/QA, build и publish.
4. После устойчивой работы лидов добавлять ручную feedback-петлю.
5. Наблюдаемость, backup и проверка восстановления идут сквозным потоком, но становятся фактом только после измеримого proof.

## Явные не-цели

До отдельного архитектурного, privacy/security и end-to-end решения не открывать:

- public registration, multi-tenant, roles, client portal, billing и plugins;
- live SERP, crawler, FIAS/GAR, IndexNow и drip automation;
- analytics/consent pixel, DSAR automation и compliance-product routes;
- custom block authoring, CRM adapters и broad bulk mutations;
- автоматическую публикацию, самостоятельную корректировку контента и гарантию бизнес-результата лида.

## Как пункт roadmap становится фактом

Каждый пункт переходит в «реализовано» только после:

1. минимальной реализации без лишнего расширения release surface;
2. автоматических регрессионных тестов;
3. Docker/browser/runtime proof, если функция зависит от инфраструктуры или внешнего получателя;
4. обновления [ХОД-РАБОТ.md](./ХОД-РАБОТ.md) с фактическим статусом, ограничениями и результатами проверки.
