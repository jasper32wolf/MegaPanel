# Roadmap операторского продукта

> **Статус документа: roadmap и остаток предлагаемого развития; не является общим утверждением об отсутствии реализации.** Уже реализованные AI provider/architecture slices и их непроверенные ограничения перечислены только как факты в [ХОД-РАБОТ.md](./ХОД-РАБОТ.md). Здесь описаны дальнейшие направления; roadmap не добавляет API, экранов, фоновых задач, сроков или гарантий.

## Реализованный срез 2026-09-20

Следующие части roadmap уже реализованы на уровне API/UI и автоматических проверок, но ещё не прошли PostgreSQL, Docker/Caddy и browser E2E proof:

- `Project`, immutable fact revisions, project-bound keyword/geo selection и PagePlan state machine;
- deterministic PageDraft generation из approved inputs, QA `pass/warn/block`, audited warning override и explicit apply без публикации;
- candidate SSG materialization без activation, authenticated preview, explicit project publish и selected rollback;
- append-only LeadOutcome, lead analysis summary и отдельный outcome history в inbox;
- project-first panel workspace `/projects` и `/projects/:projectId` с loading/error/status пояснениями.

Оставшиеся части этого roadmap — выполнить safe CLI backfill существующих сайтов на backed-up PostgreSQL, получить первые hosted runs добавленных PostgreSQL/Redis worker smoke и Chromium operator workflow E2E до candidate preview, полноценный PostgreSQL/RLS migration proof, domain/lead/axe E2E, CI release job и production runtime proof.

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

**Базовый контур реализован.** Есть Project, immutable fact revisions, подтверждение оператором и связь с PagePlan. Для legacy sites добавлен idempotent dry-run-first CLI backfill; до фактического запуска на backed-up PostgreSQL остаётся PostgreSQL/RLS runtime proof.

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

**Базовый контур реализован.** PagePlan state machine, frozen inputs и manual approve/reject существуют. Нужны фактический запуск legacy backfill, worker orchestration и runtime/browser proof.

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

**Базовый контур реализован.** Candidate build не активирует release, preview требует аутентификацию, publish/rollback требуют selected hash и explicit confirmation. Legacy direct build/publish/rollback endpoints исключены из release API; нужны Docker/Caddy/domain runtime proof.

- Ошибка сборки или качества не должна заменять текущий release.
- В UI должны быть видны причина, версия, затронутые страницы и следующее действие.
- Автоматическая публикация и IndexNow/drip не являются целью этого этапа.

### 6. Петля исходов лидов

**Базовый контур реализован.** Append-only LeadOutcome, reason/note/history и aggregate analysis отделены от inbox/delivery state. Результат лида не меняет content или SEO автоматически.

Оператор отмечает понятный исход лида и контекст: сайт, страница, статус, причина и заметка. Эти сведения становятся наблюдаемым материалом для следующей проверки page plan или формы. Они не должны автоматически менять текст, ставки, публикацию или маршрутизацию лида.

**Готовность:** результат лида отделён от технического статуса webhook delivery и доступен для осмысленного ручного анализа.

### 7. Ежедневный опыт оператора

**Частично реализован.** Project workspace и остальные operator actions используют accessible native dialogs вместо browser `confirm`/`prompt`; typed confirmation для удаления домена сохранено. Остаются полный keyboard/browser E2E и axe checks.

Приоритеты: очереди задач, статусы и причины блокировки, loading/empty/error/retry состояния, безопасные подтверждения, controlled reveal PII, keyboard navigation и browser-проверки основных действий. Раздел AI включает provider setup, архитектурный proposal→approval→draft PagePlan, отдельные PageDraft/SEO brief предложения и ограниченный server-owned `unique_core` block-slot proposal; произвольная генерация блоков, контента или публикация до отдельной реализации недоступны.

### 8. Наблюдаемость и восстановление

**Частично реализовано.** Безопасный экран panel update/recovery, immutable release scripts, encrypted off-host backup, bounded code rollback и раздельные liveness/readiness probes существуют. Нужны hosted CI, реальный GitHub dispatch, staging readiness/alert proof, регулярный restore drill и зафиксированные RPO/RTO. Пока такие прогоны не выполнены, recovery и SLA нельзя называть подтверждёнными.

### 9. AI-ассистированная структура сайтов, блоки и SEO

**Технический срез реализован частично и не прошёл PostgreSQL/browser/provider runtime proof.** VPS-wide provider registry/configuration, encrypted write-only keys, GLM/Zhipu и OpenAI-compatible gateways, versioned Markdown prompts, pricing quote, architecture proposal с approval→draft PagePlan, validated curated block selection, quote-gated plain-text copy/SEO metadata и отдельный approved `unique_core` block-slot proposal→noindex PageDraft import для утверждённого PagePlan существуют в working tree. Полный SEO/slot generation для произвольных полей, AI block selection, расширенные typed catalog slot schemas, Gemini/Mistral native adapters, provider discovery, async jobs и E2E пока не реализованы.

- Поддерживать прямые native adapters и OpenAI-compatible endpoints для OpenRouter, gateway и operator-configured providers; произвольный endpoint требует серверного egress allowlist.
- Тариф/free metadata должны иметь источник и timestamp; бесплатная модель выбирается только оператором и не является гарантией цены, квоты или uptime.
- Для каждого действия хранить отдельный Markdown prompt или prompt chain с input/output schema, hard constraints, insufficiency behavior и eval fixtures; prompt ID/version/hash сохраняются с run.
- Контекст ограничен подтверждёнными facts, выбранными keywords/geo и curated kit/block IDs. Не отправлять lead PII/secrets; результат модели не может содержать исполняемый HTML/CSS/JS.
- Каждый генеративный запрос требует явного выбора модели, подтверждения передачи данных и стоимости/лимита; результат сначала остаётся proposal. Human approval предшествует draft PagePlan creation; submit-review, approve, content draft, QA, apply, build, preview и publish — отдельные gates.

**Готовность:** provider/secret/egress/pricing tests, mocked adapter tests без платных вызовов в CI, полный page-plan → content/SEO → QA → apply → private preview E2E и staging smoke с подтверждённой стоимостью.

## Порядок и зависимости

1. Сначала завершить release gate текущего VPS-кандидата: PostgreSQL/RLS, Docker/Caddy, browser, public lead journey и restore drill.
2. Затем формализовать данные бизнеса, ручную семантику/географию и page plan inputs.
3. AI может предложить архитектуру и только после ручного approval подготовить draft PagePlan; без активированного провайдера и подтверждения расходов generation не запускается.
4. После утверждения PagePlan связывать с ним content/SEO generation, QA, apply, build и publish — каждый переход отдельно подтверждает оператор.
5. После устойчивой работы лидов добавлять ручную feedback-петлю.
6. Наблюдаемость, backup и проверка восстановления идут сквозным потоком, но становятся фактом только после измеримого proof.

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
