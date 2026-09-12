# Установка и работа на своём компьютере (локально)

Этот файл — **полная инструкция «от нуля»** для человека, который не обязан быть DevOps.  
Читайте сверху вниз. Если что-то сломалось — сразу в раздел **«Вопросы и ответы»** в конце.

Связанные файлы:

- Сервер в интернете → [`README-VPS.md`](./README-VPS.md)  
- Статус проекта → [`docs/ХОД-РАБОТ.md`](./docs/ХОД-РАБОТ.md)  
- Краткий обзор → [`README.md`](./README.md)

---

## 0. Что вы ставите и зачем

Система состоит из нескольких частей. Их не обязательно понимать глубоко, но полезно знать названия:

| Часть | Простыми словами | Обычно порт |
|-------|------------------|-------------|
| **PostgreSQL** | База данных (хранит сайты, пользователей, лиды) | 5432 |
| **Redis** | Очередь фоновых задач | 6379 |
| **API** | «Мозг» панели (отвечает на запросы) | 8000 |
| **Worker** | Фоновый работник (drip, DSAR) | — |
| **Panel** | Красивый сайт админки в браузере | 5173 |
| **Caddy** | Веб-сервер для готовых сайтов (часто через Docker) | 80 / 2019 |

**Рекомендуемый способ на Windows:** скрипт `install.ps1` сам поднимет Postgres+Redis в Docker, поставит Python-пакеты, прогонит тесты, создаст демо-данные и запустит API + worker + panel.

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

## 3. Установка одной командой (рекомендуется)

### 3.1. Обычный режим (лучший для Windows)

Убедитесь, что **Docker Desktop запущен**, затем:

```powershell
Set-Location -LiteralPath "e:\РАБОТА\ПАНЕЛЬ ДЛЯ ГЕНЕРАЦИИ САЙТОВ"
.\scripts\install.ps1
```

Альтернатива: двойной клик / запуск `scripts\install.cmd`.

### 3.2. Что делает скрипт (по шагам)

1. Проверяет Python и npm.  
2. Создаёт или дополняет файл **`.env`** (секреты, пути).  
3. Поднимает **Postgres + Redis** через Docker (`docker-compose.deps.yml`).  
4. Ждёт, пока порты `5432` и `6379` ответят.  
5. Создаёт виртуальное окружение `.venv` и ставит пакеты.  
6. Прогоняет автоматические тесты (pytest).  
7. Применяет миграции базы (Alembic → head `0011_block_kits`).  
8. Заполняет демо-данные (`seed_demo.py`).  
9. Ставит зависимости панели (`npm install` в `apps/panel`).  
10. Запускает API, worker и panel в фоне.

В конце вы увидите примерно:

```text
DONE
  Login:  admin@demo.local / DemoPass123!
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

# Без демо-пользователя
.\scripts\install.ps1 -SkipSeed

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
./scripts/install.sh --skip-seed
./scripts/install.sh --no-docker
```

---

## 4. Первый вход в панель

1. Откройте браузер: **http://127.0.0.1:5173**  
2. Логин:
   - Email: **`admin@demo.local`**  
   - Пароль: **`DemoPass123!`**  
3. Если страница не открывается — см. FAQ «Panel не открывается».  
4. Swagger API: **http://127.0.0.1:8000/docs**

### Что попробовать в интерфейсе

1. **Обзор** — статус API и сводка.  
2. **Сайты** — список, кнопка «Собрать».  
3. **Блоки** — комплекты шаблонов, «Превью», «Sync в tenant».  
4. **Онбординг** — пошаговое создание сайта.  
5. **Лиды / Домены / Ops / Инструменты** — остальные разделы.

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
  -e apps/worker `
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
$env:PYTHONPATH="apps\api;apps\worker"
Set-Location apps\worker
..\..\.venv\Scripts\arq.exe app.worker.WorkerSettings
```

Или просто: `.\scripts\start.ps1`.

---

## 8. Полный стек в Docker

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

### Health API

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health
```

Ожидается поле вроде `status: ok`.

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

```powershell
$body = @{ email = "admin@demo.local"; password = "DemoPass123!" } | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8000/api/v1/auth/login -Method POST -Body $body -ContentType "application/json"
```

---

## 10. Библиотека блоков (кратко для пользователя)

В меню **«Блоки»**:

1. Смотрите комплекты `service-local-v1`, `home-repair-v1`.  
2. **Превью** — как примерно выглядит набор секций.  
3. **Sync в tenant** — копирует шаблоны в вашу организацию.  
4. Дальше сайты собираются уже с этими блоками (тема чуть меняется на каждый сайт).

Обновление исходников разработчиком:

```powershell
.\.venv\Scripts\python.exe scripts\generate_block_kits.py
# затем Sync в UI или POST /api/v1/blocks/kits/{key}/sync
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
О: Сначала выполните seed. Логин именно `admin@demo.local` / `DemoPass123!`. Регистр email важен как в БД (обычно lower).

**В: Просит TOTP / MFA.**  
О: На демо MFA выключена, пока сами не включите. Если включили и потеряли код — правьте поля `totp_secret` / `mfa_enabled` в таблице `users` (только локально).

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
О: Копирует шаблоны комплекта в вашу организацию (tenant), чтобы ими пользоваться при сборке.

**В: Почему превью блоков не идеально совпадает с продакшен-сайтом?**  
О: Превью — упрощённый снимок. Финальный вид зависит от seed сайта, контента и сборки SSG.

**В: Формы на клиентском HTML бьют в `/api/...` — это заработает на чужом домене?**  
О: Локально через панель/прокси — да. На реальном домене сайта нужен абсолютный URL API или прокси (это известное ограничение, см. журнал §5).

### Безопасность (даже локально)

**В: Можно ли открыть панель в интернет «как есть»?**  
О: **Нет.** Для интернета читайте [`README-VPS.md`](./README-VPS.md): секреты, MFA, firewall, без demo-пароля.

**В: `.env` утёк — что делать?**  
О: Считайте все секреты скомпрометированными: сгенерируйте новые, смените пароли БД и пользователей, при необходимости перешифруйте данные (сложная процедура).

### Прочее

**В: Где правда про «что ещё не готово»?**  
О: [`docs/ХОД-РАБОТ.md`](./docs/ХОД-РАБОТ.md) раздел «Известные ограничения». Не верьте рекламным формулировкам без сверки.

**В: Как обновить проект с диска/git?**  
О: Обновите файлы → `.\scripts\install.ps1 -SkipStart` или вручную `alembic upgrade head` → `start.ps1`. Если менялись блоки — Sync kits.

**В: Куда писать, что я что-то поменял?**  
О: В `docs/ХОД-РАБОТ.md` → «Журнал изменений».

---

## 13. Чеклист «готово к работе локально»

- [ ] Python 3.12+ и Node 20+ установлены  
- [ ] Docker Desktop запущен **или** свои Postgres/Redis  
- [ ] `.\scripts\install.ps1` завершился без ошибки  
- [ ] http://127.0.0.1:8000/api/v1/health отвечает  
- [ ] http://127.0.0.1:5173 открывается  
- [ ] Вход `admin@demo.local` / `DemoPass123!` успешен  
- [ ] Знаете, как `start` / `stop` и где логи `data/runtime`  

Если все пункты отмечены — локальная среда готова.
