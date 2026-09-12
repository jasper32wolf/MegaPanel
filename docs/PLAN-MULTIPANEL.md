# План генерации: Мультипанель (краткий статус)

> Полная карта проекта, промпт, структура папок, ограничения и журнал изменений:  
> **[`docs/ХОД-РАБОТ.md`](./ХОД-РАБОТ.md)** ← канон для сверки.

Сверка с ТЗ v5.6 (`Промпт для генерации проекта.md`).

## Фазы 0–9

См. [ХОД-РАБОТ.md §2](./ХОД-РАБОТ.md) — каркас auth/geo/SSG/leads/ops/compliance.  
Часть пунктов **partial** (SERP mock, FIAS stub, IaC stubs) — детали в §5 журнала.

## UI-10 — Редизайн панели (done)

- Hybrid ops UI: светлый workspace + charcoal sidebar, акцент deep teal
- Шрифты: Fraunces + Source Sans 3
- Примитивы: `PageHeader`, `Surface`, `DataTable`, `StatusPill`, `EmptyState`
- Страница `/blocks` — каталог комплектов, превью, sync в tenant

## BLK-11 — Библиотека блоков (done)

- Пакет `packages/block-library` (`LIBRARY_VERSION=1.0.0`)
- Комплекты: `service-local-v1`, `home-repair-v1`
- Theme morph → `--sp-*` + hash-классы
- API kits + mig `0011_block_kits`

## Далее

См. бэклог в [ХОД-РАБОТ.md §6](./ХОД-РАБОТ.md).
