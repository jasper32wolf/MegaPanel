# Журнал ошибок GitHub CI

> Единое место для ошибок из GitHub Actions. Добавляйте одну карточку на каждый упавший job и вставляйте **финальный полезный фрагмент**, а не весь лог скачивания образов или установки зависимостей.
>
> Этот файл не является доказательством состояния production/VPS. Поля «локальная проверка» и «hosted verification» заполняются только по реально выполненным проверкам.

## Как добавлять новую ошибку

Скопируйте шаблон ниже в начало раздела «Открытые».

```markdown
### YYYY-MM-DD — `<workflow>` / `<job>`

- **Run:** `<GitHub Actions URL или run ID>`
- **Commit:** `<SHA>`
- **Статус:** `open | investigating | fixed-locally | verified-hosted | superseded`
- **Первый полезный error:**
  ```text
  <первая строка ERROR / FAILED / service failed / readiness timeout>
  ```
- **Контекст перед ошибкой:**
  ```text
  <20–80 строк до первого error и весь финальный failure block>
  ```
- **Что job уже успел сделать:** `<build / migration / compose up / readiness / Playwright>`
- **Локальное воспроизведение:** `<команда, результат или «не выполнено»>`
- **Предполагаемая причина:** `<только если подтверждена>`
- **Следующий шаг:** `<конкретная проверка или изменение>`
```

## Открытые

### 2026-09-28 — `CI` / `production-compose-smoke`

- **Run:** не указан
- **Commit:** не указан
- **Статус:** `investigating`
- **Первый полезный error:** пока не получен.
- **Полученный фрагмент:** job успешно скачал базовые images, собрал panel image и продолжал `pip install` для API/worker images. Лог обрывается до `docker compose up` результата, service failure или readiness timeout.
- **Локальное воспроизведение:** не выполнено — Docker CLI в текущем окружении недоступен.
- **Следующий шаг:** добавить сюда хвост job с первой строкой `ERROR`, `service "…" failed`, `API health smoke timed out` или выводом `docker compose logs`, который workflow печатает при отказе.

## Исправлено локально, ожидает hosted verification

### 2026-09-28 — `CI` / Python lint

- **Commit с исправлением:** `d954717`
- **Статус:** `fixed-locally`
- **Ошибка:**
  ```text
  E501 Line too long (106 > 100)
  apps/api/alembic/versions/0027_prompt_revision_lifecycle.py:22

  E501 Line too long (105 > 100)
  apps/api/alembic/versions/0027_prompt_revision_lifecycle.py:24
  ```
- **Причина:** две `op.add_column(...)` строки migration превышали лимит Ruff в 100 символов.
- **Изменение:** оба вызова разбиты на многострочные.
- **Локальная проверка:** `ruff check app tests ../../packages` и `ruff format --check app tests ../../packages` проходят.
- **Hosted verification:** не выполнена; нужен новый GitHub run на commit, содержащем `d954717`.

## Подтверждённые hosted results

> Переносите сюда карточку только после того, как соответствующий GitHub job действительно завершился `success` на указанном commit.
