# Site Panel — актуальный индекс развития

> **Фактическое состояние, результаты проверок и release gate находятся в [ХОД-РАБОТ.md](./ХОД-РАБОТ.md).** Этот файл помогает выбрать документ и не является доказательством реализации.

## Текущий статус

Site Panel — кандидат self-hosted VPS-релиза для одного оператора. AI-срез уже включает VPS-wide encrypted provider connections, versioned Markdown prompts/evals, architecture proposal→approved draft PagePlan, quote-gated PageDraft copy и SEO brief→noindex PageDraft, curated block selection, server-owned block-slot contracts и approval-gated `unique_core` block-slot proposal→noindex PageDraft import. Добавлены AI kill switch, дневная/скользящая 30-дневная budget reservation под PostgreSQL advisory lock, conservative accounting для неизвестного usage и audit hash provider pricing; provider-side spending cap всё равно обязателен. Model/pricing discovery, arbitrary/full slot generation и DB/provider/browser runtime proof не завершены.

Полный API suite: **315 passed, 4 Docker-gated/integration skip локально**; migration head — `0021_encrypt_legacy_webhook_secrets`. Public lead intake использует site-scoped atomic idempotency, обязательный form timestamp и shared Redis rate limit; lead message и безопасные legacy webhook secrets шифруются, PII передаётся webhook только transiently через DNS-pinned Host/SNI transport. Liveness/readiness разделены: Compose и внешний edge используют `/health/live`, а release/installer/restore/CI service acceptance требуют bounded PostgreSQL+Redis `/health/ready`. Legacy onboarding bypass удалён в пользу Project-first candidate workflow; refresh rotation/domain health теперь CSRF-protected, session rotation/MFA/revoke имеют API contracts, а CI публикует CycloneDX SBOM artifact. Добавлены GitHub service worker-delivery и PostgreSQL RLS smoke, а также отдельный Chromium `panel-e2e` operator workflow job: login → session revoke → fixtures keyword/geo → Project → facts → PagePlan → draft/QA/apply → candidate preview без publish. Их hosted runs ещё не выполнены. Docker/VPS, PostgreSQL/RLS, Caddy/TLS, worker runtime, domain/lead browser flows и restore drill ещё не доказаны end-to-end. До их выполнения продукт нельзя объявлять production-ready.

## Какой документ использовать

| Нужна информация | Документ |
|---|---|
| Что реально есть, что проверено и что блокирует выпуск | [ХОД-РАБОТ.md](./ХОД-РАБОТ.md) |
| Что предлагается развивать после доказательства ядра | [ROADMAP-OPERATOR-PRODUCT.md](./ROADMAP-OPERATOR-PRODUCT.md) |
| Как развернуть локально | [README-LOCAL.md](../README-LOCAL.md) |
| Как подготовить VPS и выполнить release gate | [README-VPS.md](../README-VPS.md) |
| Как восстановить сервис | [runbooks/disaster-recovery.md](./runbooks/disaster-recovery.md) |

## Предлагаемая последовательность развития

1. Доказать текущий release-контур на staging VPS: миграции, single operator, Caddy/TLS, browser, форма лида, webhook retries/DLQ и restore.
2. Завершить незакрытые части уже реализованного operator workflow: выполнить dry-run и контролируемый `--apply` уже добавленного idempotent legacy-site backfill на backed-up PostgreSQL, production worker orchestration и Russian slug UX. Все подтверждения UI используют accessible dialog или typed confirmation, а legacy direct build/publish/rollback routes исключены из release API.
3. Довести AI vertical slice: provider/model catalog and pricing freshness, authenticated browser + PostgreSQL proof, extend PageDraft copy/metadata slice to separate SEO briefs, block selection/slots and deterministic QA.
4. Выполнить добавленный CI service smoke на hosted runner и затем автоматизировать доказанные Docker/browser проверки, наблюдаемость и восстановление.
5. Только затем рассматривать внешние источники, аналитику и масштабирование.

> Текущий AI-код ещё не подтверждён runtime-проверкой провайдеров или реальным браузерным сценарным тестом. Статус и остаток работ ведутся в [ХОД-РАБОТ.md](./ХОД-РАБОТ.md) и [ROADMAP-OPERATOR-PRODUCT.md](./ROADMAP-OPERATOR-PRODUCT.md).
