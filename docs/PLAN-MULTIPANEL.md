# Site Panel — актуальный индекс развития

> **Фактическое состояние, результаты проверок и release gate находятся в [ХОД-РАБОТ.md](./ХОД-РАБОТ.md).** Этот файл помогает выбрать документ и не является доказательством реализации.

## Текущий статус

Site Panel — кандидат self-hosted VPS-релиза для одного оператора. Реализованы single-operator bootstrap, cookie/TOTP access, project/facts/semantic/geo/PagePlan workflow, deterministic draft/QA/apply, candidate build/preview/publish/rollback, encrypted lead inbox, manual lead outcomes, per-site webhook и durable delivery. Контрактные проверки подтверждают **79 маршрутов OpenAPI / 93 операции** и **155 API-тестов**.

Docker/VPS, PostgreSQL/RLS, Caddy/TLS, worker runtime, браузерный сценарий, публичная форма и restore drill ещё не доказаны end-to-end. До их выполнения продукт нельзя объявлять production-ready.

## Какой документ использовать

| Нужна информация | Документ |
|---|---|
| Что реально есть, что проверено и что блокирует выпуск | [ХОД-РАБОТ.md](./ХОД-РАБОТ.md) |
| Что предлагается развивать после доказательства ядра | [ROADMAP-OPERATOR-PRODUCT.md](./ROADMAP-OPERATOR-PRODUCT.md) |
| Как развернуть локально | [README-LOCAL.md](../README-LOCAL.md) |
| Как подготовить VPS и выполнить release gate | [README-VPS.md](../README-VPS.md) |
| Как восстановить сервис | [runbooks/disaster-recovery.md](./runbooks/disaster-recovery.md) |

## Предлагаемая последовательность развития

1. Доказать текущий release-контур на VPS: миграции, single operator, Caddy/TLS, browser, форма лида, webhook retries/DLQ и restore.
2. Добавить операторский контур «данные бизнеса → семантика/гео → page plan → generation/QA → build/publish → исходы лидов».
3. Автоматизировать доказанные проверки в CI, наблюдаемость и восстановление.
4. Только затем рассматривать внешние источники, генерацию, аналитику и масштабирование.

> Пункты из [ROADMAP-OPERATOR-PRODUCT.md](./ROADMAP-OPERATOR-PRODUCT.md) имеют статус **«предлагается»**, пока не появятся реализация, проверки и запись в фактологическом журнале.
