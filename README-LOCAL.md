# Установка и работа на своём компьютере (локально)

Этот файл — **пошаговая инструкция «от нуля»** для локальной среды разработки.
Читайте сверху вниз. Если что-то сломалось — сразу в раздел **«Вопросы и ответы»** в конце.

> **Статус проверки 2026-09-26:** 315 API-тестов проходят, 4 Docker-gated/integration tests skipped локально; production build панели проходит. Alembic graph достигает `0021_encrypt_legacy_webhook_secrets`; single-active-operator access, TOTP/session/CSRF contracts, Project-first candidate workflow, encrypted lead PII и webhook delivery, liveness/readiness, safe bulk contacts, CycloneDX SBOM definition и production Compose isolation покрыты regression-тестами. Полный Docker/VPS, hosted CI и authenticated browser execution пока не подтверждены в этой среде. Актуальные границы и release gate: [`docs/ХОД-РАБОТ.md`](./docs/ХОД-РАБОТ.md#6-установка-и-режимы-запуска-фактический-статус).

Связанные файлы:

- Сервер в интернете → [`README-VPS.md`](./README-VPS.md)  
- Единый паспорт: промпт, структура, аудит и план → [`docs/ХОД-РАБОТ.md`](./docs/ХОД-РАБОТ.md)
- Предлагаемое развитие операторского продукта (не текущие функции) → [`docs/ROADMAP-OPERATOR-PRODUCT.md`](./docs/ROADMAP-OPERATOR-PRODUCT.md)
- Краткий обзор → [`README.md`](./README.md)

---

## 0. Что вы ставите и зачем

Система состоит из нескольких частей. Их не обязательно понимать глубоко, но полезно знать названия:

| Часть | Простыми словами | Обычно порт |
|-------|------------------|-------------|
| **PostgreSQL** | База данных (хранит сайты, пользователей, лиды) | 5432 |
| **Redis** | Очередь фоновых задач | 6379 |
| **API** | «Мозг» панели (отвечает на запросы) | 8000 |
| **Worker** | Фоновый работник для доставки лидов и повторов webhook | — |
| **Panel** | Красивый сайт админки в браузере | 5173 |
| **Caddy** | Веб-сервер для готовых сайтов (часто через Docker) | 80 / 2019 |

**Предусмотренный путь на Windows:** `install.ps1` поднимает Postgres+Redis в Docker, ставит Python-пакеты, применяет миграции и запускает API + worker + panel. После установки создайте единственного оператора локальной командой `scripts/bootstrap_operator.py`.

---

## 1. Что нужно установить заранее

### 1.1. Обязательно

| Программа | Версия | Зачем | Откуда взять |
|-----------|--------|-------|--------------|
| **Python** | **3.12 или новее** | API, тесты, скрипты | https://www.python.org/downloads/ |
| **Node.js** | **20 или новее** (лучше 22) | Интерфейс панели | https://nodejs.org/ |
| **Git** (желательно) | любая свежая | обновлять проект | https://git-scm.com/ |

**Важно для Windows при установке Python:**

- Поставьте галочку **«Add python.exe to PATH»**.  
- После установки **закройте и снова откройте** PowerShell.  
- Проверка:

```powershell
python --version
# или
py -3.12 --version
```

Должно показать что-то вроде `Python 3.12.x` или `3.13.x` / `3.14.x`.

**Node.js — проверка:**

```powershell
node --version
npm --version
```

### 1.2. Очень желательно: Docker Desktop

Без Docker скрипт **не сможет сам** поднять Postgres и Redis. Тогда база должна уже быть установлена у вас вручную.

- Скачать: https://www.docker.com/products/docker-desktop/  
- После установки запустите Docker Desktop и дождитесь статуса **Running**.  
- Проверка:

```powershell
docker version
docker compose version
```

Если Docker не установлен — см. FAQ «Нет Docker».

### 1.3. Опционально

| Программа | Зачем |
|-----------|-------|
| **k6** | Нагрузочные тесты API |
| **psql / Redis CLI** | Ручная проверка базы |

---

## 2. Подготовка папки проекта

1. Скопируйте или клонируйте проект на диск.  
2. Путь может содержать кириллицу (у вас так и есть) — это нормально, но команды лучше брать **из этой инструкции**.  
3. Откройте **PowerShell** и перейдите в корень проекта:

```powershell
Set-Location -LiteralPath "e:\РАБОТА\ПАНЕЛЬ ДЛЯ ГЕНЕРАЦИИ САЙТОВ"
```

Подставьте **свой** путь, если папка лежит иначе.

Проверка, что вы в корне — должны видеть файлы:

- `README.md`  
- `scripts\install.ps1`  
- `apps\`  
- `packages\`  
- `.env.example`

---

## 3. Автоматизированная установка (текущий development-путь)

### 3.0. Самый простой способ — мастер с меню

**Windows:** дважды щёлкните **`УСТАНОВКА.cmd`** (или `INSTALL.cmd`) в корне проекта.

**Linux / macOS:**

```bash
chmod +x УСТАНОВКА.sh scripts/*.sh
./УСТАНОВКА.sh
```

В меню:

| Клавиша | Что делает |
|---------|------------|
| `1` | Экспресс: local + автостарт без demo fixtures |
| `2` | Настроить local/docker/deps режим, demo fixtures и тесты |
| `3` | Только запустить уже установленное |
| `4` | Остановить |
| `0` | Выход |

Без вопросов: `python scripts/setup_wizard.py --express --yes`

### 3.1. Обычный режим без меню (движок)

Убедитесь, что **Docker Desktop запущен**, затем:

```powershell
Set-Location -LiteralPath "e:\РАБОТА\ПАНЕЛЬ ДЛЯ ГЕНЕРАЦИИ САЙТОВ"
.\scripts\install.ps1
```

Альтернатива без меню: `./scripts/install.ps1` на Windows или `./scripts/install.sh` на Linux/macOS.

### 3.2. Что делает скрипт (по шагам)

1. Проверяет Python и npm.  
2. Создаёт или дополняет файл **`.env`** (секреты, пути).  
3. Поднимает **Postgres + Redis** через Docker (`docker-compose.deps.yml`).  
4. Ждёт, пока порты `5432` и `6379` ответят.  
5. Создаёт виртуальное окружение `.venv` и ставит пакеты.  
6. Прогоняет автоматические тесты (pytest).  
7. Применяет миграции базы (Alembic → актуальный head).
8. Не создаёт пользователя автоматически; после установки выполните `scripts/bootstrap_operator.py`. Demo fixtures создаются только с `-DemoSeed`.
9. Ставит зависимости панели (`npm ci` в `apps/panel`).
10. Запускает API, worker и panel в фоне.

После успешного завершения:

```text
DONE
  Bootstrap operator: python scripts/bootstrap_operator.py --email you@example.com
  API:    http://127.0.0.1:8000/docs
  Panel:  http://127.0.0.1:5173
```

### 3.3. Полезные варианты запуска install

```powershell
# Весь стек только в Docker (API тоже в контейнере)
.\scripts\install.ps1 -Mode docker

# Только пакеты и .env — база уже своя
.\scripts\install.ps1 -Mode deps -SkipStart

# Установка без автозапуска программ
.\scripts\install.ps1 -SkipStart

# Без тестов (быстрее, но хуже проверка)
.\scripts\install.ps1 -SkipTests

# Demo fixtures только для явного локального теста
.\scripts\install.ps1 -DemoSeed

# Явно без Docker (вы сами отвечаете за Postgres/Redis)
.\scripts\install.ps1 -NoDocker
```

### 3.4. Linux / macOS (локально)

```bash
cd "/path/to/ПАНЕЛЬ ДЛЯ ГЕНЕРАЦИИ САЙТОВ"
chmod +x scripts/*.sh
./scripts/install.sh
```

Флаги:

```bash
./scripts/install.sh --mode docker
./scripts/install.sh --skip-tests --skip-start
./scripts/install.sh --demo # только для явного локального fixture-теста
./scripts/install.sh --no-docker
```

---

## 4. Первый вход в панель

1. Откройте браузер: **http://127.0.0.1:5173**  
2. До первого входа создайте оператора локально:

   ```powershell
   python scripts\bootstrap_operator.py --email you@example.com
   ```

   Скрипт безопасно запросит пароль в терминале. Если это обновление старой установки с уже созданным оператором, выполните ту же команду с тем же email и флагом `--reset-password`: она нормализует единственную учётную запись без смены owner scope.
3. Если страница не открывается — см. FAQ «Panel не открывается».  
4. Swagger API: **http://127.0.0.1:8000/docs**

### Что попробовать в интерфейсе

1. **Обзор** — статус API и сводка.  
2. **Сайты** — список, кнопка «Собрать».  
3. **Блоки** — комплекты шаблонов, «Превью», «Подготовить комплект».
4. **Онбординг** — пошаговое создание сайта.  
5. **Лиды / Домены / Медиатека / Настройки / Статус** — повседневная работа, безопасность и проверяемые состояния.

---

## 5. Повседневная работа

### Запустить снова (если уже ставили)

```powershell
Set-Location -LiteralPath "e:\РАБОТА\ПАНЕЛЬ ДЛЯ ГЕНЕРАЦИИ САЙТОВ"
.\scripts\start.ps1
```

Скрипт:

- поднимет Postgres/Redis в Docker (если Docker доступен);  
- запустит API, worker, panel;  
- логи пишет в `data\runtime\*.log`.

### Остановить

```powershell
.\scripts\stop.ps1           # только API / worker / panel
.\scripts\stop.ps1 -Deps     # + остановить Postgres/Redis в Docker
```

На Linux/macOS:

```bash
./scripts/start.sh
./scripts/stop.sh
./scripts/stop.sh --deps
```

### Browser E2E: операторский workflow до candidate preview

E2E-тест проверяет cookie-login/Dashboard, видимость и подтверждённый отзыв другой cookie-сессии, а также путь `импорт keyword → география → Project → facts → PagePlan → draft/QA/apply → candidate preview` через локальный Vite proxy. Он создаёт только изолированные тестовые данные, не публикует сайт, не вызывает AI/CRM, не отправляет внешний webhook и не проверяет Caddy/TLS.

Для локального запуска нужны доступные PostgreSQL и Redis, применённые миграции, отдельная тестовая БД и запущенные API (`127.0.0.1:8000`) с Vite (`127.0.0.1:5173`). Создайте временного оператора с теми же параметрами, что использует сценарий, затем установите Chromium и запустите тест:

```powershell
Set-Location -LiteralPath "e:\РАБОТА\ПАНЕЛЬ ДЛЯ ГЕНЕРАЦИИ САЙТОВ"
$env:E2E_OPERATOR_EMAIL="e2e@example.test"
$env:E2E_OPERATOR_PASSWORD="e2e-local-password"
$env:E2E_OPERATOR_PASSWORD | python scripts\bootstrap_operator.py --email $env:E2E_OPERATOR_EMAIL --password-stdin

Set-Location -LiteralPath "e:\РАБОТА\ПАНЕЛЬ ДЛЯ ГЕНЕРАЦИИ САЙТОВ\apps\panel"
npm ci
npx playwright install chromium
npm run test:e2e
```

В GitHub Actions отдельный `panel-e2e` job сам поднимает service containers, применяет миграции и создаёт ephemeral CI-оператора. Первый успешный GitHub run является доказательством только этого узкого operator workflow до candidate preview; публичный домен, лиды, webhook receiver, Caddy и restore проверяются отдельно.

### PostgreSQL/Redis integration: delivery, RLS и AI budget reservation

Для проверок worker delivery, RLS и AI budget reservation нужны PostgreSQL и Redis с применёнными миграциями. Тест доставки подменяет только внешний HTTP-dispatch и не отправляет запросов во внешнюю сеть; RLS-тест проверяет, что tenant-scoped сессия выключает maintenance bypass и видит только свои сайты; AI-тест запускает две независимые сессии и допускает одну reservation при общем лимите.

```powershell
$env:WEBHOOK_DELIVERY_INTEGRATION="1"
$env:POSTGRES_RLS_INTEGRATION="1"
$env:AI_BUDGET_INTEGRATION="1"
$env:PYTHONPATH="apps\api;packages\block-library\src;packages\shared\src;packages\security\src;packages\ssg\src"
.\.venv\Scripts\python.exe -m pytest apps/api/tests/test_webhook_delivery.py apps/api/tests/test_postgres_rls.py apps/api/tests/test_ai_budget_reservation.py -q
```

В GitHub Actions это выполняет job `integration-services`. Успешный hosted run подтверждает service-container контракт, но не заменяет проверку production DB-role, Caddy, публичного receiver и restore drill.

### Backfill legacy-сайтов в Projects

Если сайты были созданы до появления Project workflow, сначала выполните только просмотр:

```powershell
python scripts\backfill_site_projects.py
# или ограничить просмотр конкретным tenant
python scripts\backfill_site_projects.py --tenant-id <tenant-uuid>
```

После проверки строк `WOULD CREATE` и `SKIP` повторите тот же scope только с явным `--apply`:

```powershell
python scripts\backfill_site_projects.py --apply
```

Скрипт создаёт только draft Project с именем из домена и техническим slug `site-<site UUID hex>`, затем устанавливает взаимные `Site`/`Project` ссылки. Он не переписывает manifest, не создаёт facts/pages/drafts, не выполняет build/publish, не вызывает Caddy или внешние сервисы и не меняет release state legacy-сайта. Частичные links, конфликты и slug collisions не исправляются автоматически: они выводятся как `SKIP` для ручной проверки. Повторный запуск после успешного применения безопасен и показывает `already_linked` вместо нового создания.

### Где лежат логи

| Файл | Что это |
|------|---------|
| `data/runtime/api.out.log` / `api.err.log` | Вывод API |
| `data/runtime/worker.*.log` | Worker |
| `data/runtime/panel.*.log` | Vite / panel |

### Где лежат собранные сайты

Папка из `.env` → `SITES_ROOT` (обычно `data/sites` или абсолютный путь, который прописал `prepare_env.py`).

Структура примерно:

```text
data/sites/<uuid-сайта>/current/index/index.html
data/sites/<uuid-сайта>/current/robots.txt
data/sites/<uuid-сайта>/current/sitemap.xml
```

---

## 6. Файл `.env` — что внутри (простыми словами)

Скрипт создаёт `.env` из `.env.example` и подставляет секреты.  
**Не публикуйте `.env` и не коммитьте его в git.**

| Переменная | Зачем |
|------------|-------|
| `APP_SECRET_KEY` | Подпись токенов входа |
| `APP_PEPPER` | Доп. секрет для паролей |
| `BLIND_INDEX_PEPPER` | Поиск по зашифрованным телефонам |
| `FIELD_ENCRYPTION_KEY` | Шифрование ПДн в лидах (**не меняйте** на живой базе без миграции данных) |
| `DATABASE_URL` | Строка подключения к Postgres |
| `REDIS_URL` | Строка подключения к Redis |
| `CORS_ORIGINS` | С каких адресов браузеру можно звать API |
| `SITES_ROOT` | Куда писать HTML сайтов |
| `DEEPSEEK_API_KEY` и др. | Ключи ИИ (необязательно; без них работает локальный режим) |

Пересоздать/дополнить env:

```powershell
.\.venv\Scripts\python.exe scripts\prepare_env.py --mode local
```

---

## 7. Ручная установка (если скрипт не подошёл)

Делайте по порядку.

### Шаг A — виртуальное окружение Python

```powershell
Set-Location -LiteralPath "e:\РАБОТА\ПАНЕЛЬ ДЛЯ ГЕНЕРАЦИИ САЙТОВ"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Если PowerShell ругается на политику выполнения:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

### Шаг B — пакеты

```powershell
$env:HTTP_PROXY=""; $env:HTTPS_PROXY=""; $env:NO_PROXY="*"
python -m pip install --upgrade pip
python -m pip install hatchling editables
python -m pip install --no-build-isolation `
  -e packages/shared `
  -e packages/security `
  -e packages/ssg `
  -e packages/block-library `
  -e apps/api `
  pytest pytest-asyncio ruff
```

### Шаг C — `.env`

```powershell
python scripts\prepare_env.py --mode local
```

### Шаг D — Postgres и Redis

**Вариант Docker:**

```powershell
docker compose -f infra/docker/docker-compose.deps.yml up -d
```

**Вариант без Docker:** установите PostgreSQL 16 и Redis 7 сами, создайте БД/пользователя `site_panel` / пароль как в `.env`.

### Шаг E — миграции

```powershell
$env:PYTHONPATH="apps\api"
.\.venv\Scripts\alembic.exe -c apps\api\alembic.ini upgrade head
```

### Шаг F — демо-данные

```powershell
$env:PYTHONPATH="apps\api"
.\.venv\Scripts\python.exe scripts\seed_demo.py
```

### Шаг G — панель

```powershell
cd apps\panel
npm install
npm run dev
```

### Шаг H — API (второй терминал)

```powershell
Set-Location -LiteralPath "e:\РАБОТА\ПАНЕЛЬ ДЛЯ ГЕНЕРАЦИИ САЙТОВ"
.\.venv\Scripts\Activate.ps1
$env:PYTHONPATH="apps\api"
.\.venv\Scripts\uvicorn.exe app.main:app --app-dir apps/api --reload --host 127.0.0.1 --port 8000
```

### Шаг I — worker (третий терминал, желательно)

```powershell
$env:PYTHONPATH="apps\api"
Set-Location -LiteralPath "e:\РАБОТА\ПАНЕЛЬ ДЛЯ ГЕНЕРАЦИИ САЙТОВ"
.\.venv\Scripts\arq.exe app.worker.WorkerSettings
```

Или просто: `.\scripts\start.ps1`.

---

## 8. Полный стек в Docker

> **Сейчас это экспериментальный контур, а не рабочий «всё в контейнерах» сценарий.** Panel не проксирует относительный `/api`, worker-образ не содержит API-код, а пути SSG/Caddy расходятся. См. P0-05…P0-07 в [`docs/ХОД-РАБОТ.md`](./docs/ХОД-РАБОТ.md#p0--блокеры-основного-и-безопасного-сценария).

Если хотите «всё в контейнерах»:

```powershell
.\scripts\install.ps1 -Mode docker
# или
docker compose --env-file .env -f infra/docker/docker-compose.yml up -d --build
```

Тогда:

- API: http://127.0.0.1:8000  
- Panel: http://127.0.0.1:5173  
- Caddy: :80 / Admin :2019  

Остановка:

```powershell
docker compose -f infra/docker/docker-compose.yml down
# с удалением данных БД (осторожно!):
docker compose -f infra/docker/docker-compose.yml down -v
```

Seed демо в режиме полного Docker иногда удобнее гонять **с хоста** на проброшенный `localhost:5432` (см. FAQ).

---

## 9. Проверки «всё ли живо»

### Liveness и readiness API

```powershell
# Доступность процесса API без проверки зависимостей
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health/live

# Готовность для выпуска: bounded PostgreSQL SELECT 1 + Redis PING
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health/ready
```

Оба успешных ответа содержат `status: ok`; `/ready` возвращает generic `503` при недоступности PostgreSQL или Redis. Старый `/api/v1/health` сохранён как compatibility alias liveness.

### Автотесты

```powershell
$env:PYTHONPATH="apps\api;packages\block-library\src;packages\shared\src;packages\security\src;packages\ssg\src"
.\.venv\Scripts\python.exe -m pytest apps/api/tests -q
```

### Сборка панели (как в CI)

```powershell
cd apps\panel
npm run build
```

### Нагрузка k6 (если установлен)

```powershell
k6 run infra/k6/smoke.js
k6 run -e BASE=http://127.0.0.1:8000 infra/k6/leads.js
```

### Логин через API

> Для проверки API используйте оператора, созданного через `scripts/bootstrap_operator.py`. Не подставляйте demo credentials в рабочую установку.

```powershell
$email = Read-Host "Email оператора"
$securePassword = Read-Host "Пароль" -AsSecureString
$password = [System.Net.NetworkCredential]::new("", $securePassword).Password
$body = @{ email = $email; password = $password } | ConvertTo-Json
Invoke-WebRequest http://127.0.0.1:8000/api/v1/auth/login -Method POST -Body $body -ContentType "application/json" -SessionVariable session
$password = $null
```

Если TOTP включён, добавьте в `$body` поле `totp_code` с текущим одноразовым кодом.

---

## 10. Библиотека блоков (кратко для пользователя)

В меню **«Блоки»**:

1. Смотрите комплекты `service-local-v1`, `home-repair-v1`.  
2. **Превью** — как примерно выглядит набор секций.  
3. **Подготовить комплект** — сохраняет выбранную версию блоков для сайтов этого оператора.
4. Дальше сайты собираются уже с этими блоками (тема чуть меняется на каждый сайт).

Обновление исходников разработчиком:

```powershell
.\.venv\Scripts\python.exe scripts\generate_block_kits.py
# затем подготовьте комплект через UI
```

---

## 11. Типичный день разработчика

1. `.\scripts\start.ps1`  
2. Правите код.  
3. API с `--reload` подхватывает Python-изменения сам.  
4. Panel (Vite) обновляется сам.  
5. Перед коммитом: `pytest` + при UI `npm run build`.  
6. Запись в [`docs/ХОД-РАБОТ.md`](./docs/ХОД-РАБОТ.md) §7.

---

## 12. Вопросы и ответы (FAQ)

### Базовые

**В: Что такое «панель» и чем она отличается от «сайта клиента»?**  
О: Панель — админка для вас (порт 5173). Сайты клиентов — готовый HTML в `data/sites`, который потом отдаёт Caddy/хостинг.

**В: Нужен ли интернет?**  
О: Да для первой установки (pip, npm, Docker-образы, шрифты Google). Потом часть работы возможна офлайн, но без ключей LLM и без Docker-pull обновлений.

**В: Это бесплатно запускать локально?**  
О: Сам код — да. Docker/Postgres/Redis — локально бесплатно. Ключи DeepSeek/OpenAI — по тарифам провайдеров (необязательны).

**В: Можно ли на Windows 10?**  
О: Да. Проверено на Windows 10 + PowerShell.

**В: Путь с русскими буквами — проблема?**  
О: Обычно нет. Используйте `Set-Location -LiteralPath "..."` как в инструкции.

### Установка и скрипты

**В: Как поставить максимально просто?**

О: Дважды щёлкните **`УСТАНОВКА.cmd`** → клавиша **`1`**. На Linux: `./УСТАНОВКА.sh`.

**В: `install.ps1` пишет, что нельзя выполнить скрипты.**  
О:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Потом снова `.\scripts\install.ps1`.

**В: Ошибка pip / SOCKS / proxy.**  
О: Скрипт чистит proxy. Если ставили вручную:

```powershell
$env:HTTP_PROXY=""; $env:HTTPS_PROXY=""; $env:ALL_PROXY=""; $env:NO_PROXY="*"
```

**В: `Python 3.12+ required`.**  
О: Установите Python 3.12+ и переоткройте терминал. На Windows удобен `py -3.12`.

**В: `npm` not found.**  
О: Установите Node.js LTS и перезапустите PowerShell.

**В: Docker is installed but not running.**  
О: Запустите Docker Desktop, дождитесь зелёного статуса, повторите install.

**В: Нет Docker. Что делать?**  
О:

1. Поставьте Postgres 16 и Redis 7 вручную.  
2. Создайте пользователя/БД как в `.env`.  
3. `.\scripts\install.ps1 -NoDocker`  
   или `-Mode deps` после ручной настройки URL.

**В: `alembic failed — is Postgres up`.**  
О: База не слушает `localhost:5432` или неверный пароль в `.env`. Проверьте:

```powershell
docker compose -f infra/docker/docker-compose.deps.yml ps
```

**В: Seed падает.**  
О: Сначала успешно пройдите миграции. Проверьте `FIELD_ENCRYPTION_KEY` и что БД пустая/доступна. Лог ошибки — в консоли seed.

**В: Тесты красные — можно продолжать?**  
О: Лучше сначала починить. Можно `-SkipTests`, но это скрывает поломку.

**В: Сколько места на диске нужно?**  
О: Ориентир: несколько ГБ (Docker-образы + `node_modules` + `.venv`). Сайты растут с числом страниц.

### Запуск и браузер

**В: Panel не открывается (5173).**  
О: Проверьте `.\scripts\start.ps1` и `data/runtime/panel.err.log`. Убедитесь, что порт свободен.

**В: API недоступен / «API offline» на обзоре.**  
О: Смотрите `data/runtime/api.err.log`. Часто: не поднята БД, неверный `DATABASE_URL`, порт 8000 занят.

**В: Ошибка CORS в браузере.**  
О: В `.env` в `CORS_ORIGINS` должен быть точный origin, например `http://127.0.0.1:5173` **и/или** `http://localhost:5173`. После правки перезапустите API. В режиме Vite proxy `/api` обычно работает и без CORS.

**В: Логин «Invalid credentials».**  
О: Проверьте email созданного оператора и пароль. Если это старая установка, выполните `python scripts\bootstrap_operator.py --email you@example.com --reset-password` с тем же email. Demo account существует только после явного `-DemoSeed` и не является стандартным путём.

**В: Просит TOTP / MFA.**  
О: При включённом TOTP введите код на странице входа. При утере доступа используйте утверждённую процедуру восстановления, а не редактируйте MFA-поля в БД вручную.

**В: Порт 8000 занят.**  
О: Запустите uvicorn на другом порту и поменяйте proxy в `apps/panel/vite.config.ts`, либо убейте процесс на 8000.

### Docker

**В: `docker compose` или `docker-compose`?**  
О: Нужен современный Docker с плагином `docker compose` (через пробел).

**В: Данные пропали после `down -v`.**  
О: Флаг `-v` удаляет тома (БД). Восстановление — только из бэкапа. Локально обычно делают заново `install`/`seed`.

**В: Как посмотреть логи контейнеров?**  
О:

```powershell
docker compose -f infra/docker/docker-compose.yml logs -f api worker
docker compose -f infra/docker/docker-compose.deps.yml logs -f postgres
```

### Данные, ИИ, блоки

**В: Нужны ли ключи ChatGPT / DeepSeek?**  
О: Нет. Без ключей работает локальный режим генерации текста. С ключами в `.env` и `"use_llm": true` — внешние модели.

**В: Где хранятся лиды и телефоны?**  
О: В Postgres, поля зашифрованы. Pepper/ключ — в `.env`.

**В: Что делает Sync блоков?**  
О: Подготавливает выбранную версию комплекта блоков для сайтов единственного оператора.

**В: Почему превью блоков не идеально совпадает с продакшен-сайтом?**  
О: Превью — упрощённый снимок. Финальный вид зависит от seed сайта, контента и сборки SSG.

**В: Формы на клиентском HTML бьют в `/api/...` — это заработает на чужом домене?**  
О: В сгенерированном сайте форма отправляет данные через same-origin `/api/v1/leads/public`; для опубликованного домена Caddy добавляет этот proxy. Public Docker/Caddy smoke всё ещё обязателен перед production.

### Безопасность (даже локально)

**В: Можно ли открыть панель в интернет «как есть»?**  
О: **Нет.** Для интернета читайте [`README-VPS.md`](./README-VPS.md): секреты, MFA, firewall, без demo-пароля.

**В: `.env` утёк — что делать?**  
О: Считайте все секреты скомпрометированными: сгенерируйте новые, смените пароли БД и пользователей, при необходимости перешифруйте данные (сложная процедура).

### Прочее

**В: Где правда про «что ещё не готово»?**  
О: [`docs/ХОД-РАБОТ.md`](./docs/ХОД-РАБОТ.md) раздел «Известные ограничения». Не верьте рекламным формулировкам без сверки.

**В: Как обновить проект с диска/git?**  
О: Обновите файлы → `./scripts/install.ps1 -SkipStart` или вручную `alembic upgrade head` → `start.ps1`. Если менялись блоки — подготовьте комплект через UI.

**В: Куда писать, что я что-то поменял?**  
О: В `docs/ХОД-РАБОТ.md` → «Журнал изменений».

---

## 13. Чеклист «готово к работе локально»

- [ ] Python 3.12+ и Node 20+ установлены  
- [ ] Docker Desktop запущен **или** свои Postgres/Redis  
- [ ] `УСТАНОВКА.cmd` → `1` (или `.\scripts\install.ps1`) завершился без ошибки
- [ ] http://127.0.0.1:8000/api/v1/health отвечает  
- [ ] http://127.0.0.1:5173 открывается  
- [ ] Вход созданного оператора успешен
- [ ] Знаете, как `start` / `stop` и где логи `data/runtime`  

Если все пункты отмечены — локальная среда готова для текущей разработки и unit/UI-проверок. Это не подтверждает PostgreSQL/RLS, Docker/Caddy и lead end-to-end до закрытия P0.
