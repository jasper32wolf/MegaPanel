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
- **Первый полезный error:**
  ```text
  caddy-state-init | Usage: chown [-RhLHPcvf]... USER[:[GRP]] FILE...
  service "caddy-state-init" didn't complete successfully: exit 1
  ```
- **Подтверждённая причина:** Compose shell-form `command: "chown 10001:10001 /data /config"` передавал `chown` как отдельную программу для `sh -ec`, а ownership operands становились shell positional parameters. BusyBox запускал `chown` без файлов и печатал usage.
- **Исправление:** `command` заменена на argv-массив `["chown 10001:10001 /data /config"]`; вся shell command передаётся одним аргументом после `-c`.
- **Дополнительное улучшение:** Python dependencies production image теперь собираются один раз в `site-panel-api:local`, а `migrate` и `worker` используют тот же image. CI сохраняет bounded build/ps/service logs в artifact `production-compose-diagnostics` при любой следующей ошибке.
- **Локальная проверка:** production Compose contract tests — 18 passed; Compose YAML parse passed. Docker runtime локально недоступен.
- **Hosted verification:** требуется новый run на commit с argv fix.

### 2026-09-28 — `panel-e2e` / candidate workflow

- **Run:** не указан
- **Commit с исправлением:** ожидает commit текущего рабочего дерева
- **Статус:** `fixed-locally`
- **Ошибка:**
  ```text
  strict mode violation: getByText('E2E город …') resolved to 2 elements
  ```
- **Причина:** первая замена на `getByRole("strong", ...)` оказалась неверной: HTML-тег `strong` не является стабильной ARIA role и locator не находил элемент.
- **Изменение:** E2E находит единственную строку `tbody tr` с именем города и ожидает видимую кнопку «Изменить» внутри неё. Это проверяет созданную запись иерархии без зависимости от duplicate option или несуществующей role.
- **Локальная проверка:** `npm --prefix apps/panel run build` и `npx playwright test --list` проходят. Полный browser scenario требует controlled API/PostgreSQL/Redis stack и здесь не запускался.
- **Hosted verification:** не выполнена; нужен новый GitHub run на commit с исправлением.

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
