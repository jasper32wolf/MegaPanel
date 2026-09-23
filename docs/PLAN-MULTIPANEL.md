# Site Panel — актуальный индекс развития

> **Фактическое состояние, результаты проверок и release gate находятся в [ХОД-РАБОТ.md](./ХОД-РАБОТ.md).** Этот файл помогает выбрать документ и не является доказательством реализации.

## Текущий статус

Site Panel — кандидат self-hosted VPS-релиза для одного оператора. В дополнение к базовому проектному workflow в working tree добавлен первый AI-срез: encrypted VPS-wide provider connections (GLM/Zhipu + OpenAI-compatible gateways), versioned Markdown prompts/evals, pricing snapshot, architecture proposal, ручное approval и отдельный импорт draft PagePlan. Генерация SEO/content/block slots, pricing discovery, гарантированное upstream spend enforcement, browser E2E и PostgreSQL/RLS runtime пока не завершены.

Полный API suite: **189 passed**; panel build проходит. Docker/VPS, PostgreSQL/RLS, Caddy/TLS, worker runtime, authenticated browser flow и restore drill ещё не доказаны end-to-end. До их выполнения продукт нельзя объявлять production-ready.

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
2. Завершить незакрытые части уже реализованного operator workflow: idempotent backfill legacy sites, production worker orchestration, accessible dialogs, Russian slug UX и закрытие legacy direct-publish bypass.
3. Довести AI vertical slice: provider/model catalog и pricing freshness, browser workflow tests, structured page-plan approval/import, затем отдельные SEO/content/block prompt chains и deterministic QA.
4. Автоматизировать доказанные PostgreSQL/Docker/browser проверки в CI, наблюдаемость и восстановление.
5. Только затем рассматривать внешние источники, аналитику и масштабирование.

> Текущий AI-код ещё не подтверждён runtime-проверкой провайдеров или реальным браузерным сценарным тестом. Статус и остаток работ ведутся в [ХОД-РАБОТ.md](./ХОД-РАБОТ.md) и [ROADMAP-OPERATOR-PRODUCT.md](./ROADMAP-OPERATOR-PRODUCT.md).
