# Site Panel — с чего начать

Простыми словами: это **панель**, в которой агентство создаёт много сайтов услуг (ремонт, клининг и т.д.), собирает их в готовый HTML и принимает заявки (лиды).

Полное ТЗ: [`Промпт для генерации проекта.md`](./Промпт%20для%20генерации%20проекта.md) (версия 5.6).  
Единый паспорт проекта — промпт, структура, аудит, изменения, ограничения и план: [`docs/ХОД-РАБОТ.md`](./docs/ХОД-РАБОТ.md).
Предлагаемое развитие операторского продукта — [`docs/ROADMAP-OPERATOR-PRODUCT.md`](./docs/ROADMAP-OPERATOR-PRODUCT.md); это не перечень уже реализованных функций.

> **Текущий статус:** single-user release candidate. API unit-тесты и production-сборка панели проходят; безопасный доступ, домены, лиды и webhook delivery реализованы. Docker/VPS, PostgreSQL/Redis worker и browser smoke ещё не были проверены в этом окружении, поэтому перед production-развёртыванием выполните release gate из единого паспорта. План проектов/страниц, контролируемая генерация и проверка качества, обратная связь по лидам и расширенный контур надёжности описаны только в [предлагаемой дорожной карте](./docs/ROADMAP-OPERATOR-PRODUCT.md).

---

## Какой файл читать?

| Ваша ситуация | Откройте |
|---------------|----------|
| Компьютер дома / в офисе (Windows, Mac, Linux) | **[`README-LOCAL.md`](./README-LOCAL.md)** ← начните здесь |
| Арендованный сервер (VPS) в интернете | **[`README-VPS.md`](./README-VPS.md)** |
| Нужен единый документ: промпт, структура, аудит, изменения и план | [`docs/ХОД-РАБОТ.md`](./docs/ХОД-РАБОТ.md) |
| Предлагаемое развитие операторского продукта | [`docs/ROADMAP-OPERATOR-PRODUCT.md`](./docs/ROADMAP-OPERATOR-PRODUCT.md) — не текущий статус |
| Краткий индекс текущего статуса и дальнейшей работы | [`docs/PLAN-MULTIPANEL.md`](./docs/PLAN-MULTIPANEL.md) |
| Восстановление после аварии | [`docs/runbooks/disaster-recovery.md`](./docs/runbooks/disaster-recovery.md) |
| GitHub-обновления и rollback | [`docs/runbooks/github-deploy-recovery.md`](./docs/runbooks/github-deploy-recovery.md) |

---

## Самая быстрая установка (кратко)

> Это текущий путь для разработки, а не подтверждённый clean-install сценарий: локальная миграция Alembic и DB/RLS flow требуют исправления. Диагностика и актуальный статус — в [`docs/ХОД-РАБОТ.md`](./docs/ХОД-РАБОТ.md#6-установка-и-режимы-запуска-фактический-статус).

### Windows (самый простой путь)

1. Установите **Python 3.12+**, **Node.js 20+**, желательно **Docker Desktop**.  
2. Дважды щёлкните файл **`УСТАНОВКА.cmd`** в корне проекта (или `INSTALL.cmd`).
3. В меню нажмите **`1`** — экспресс-установка.
4. Создайте оператора локально: `python scripts\bootstrap_operator.py --email you@example.com`
5. Откройте http://127.0.0.1:5173 и войдите с созданными данными.

Без меню (как раньше):

```powershell
.\scripts\install.ps1
```

### Linux / Mac / VPS

```bash
chmod +x УСТАНОВКА.sh scripts/*.sh
./УСТАНОВКА.sh
```

В меню: `1` (локально) или `2` → режим `vps` для сервера.

Неинтерактивно: `python3 scripts/setup_wizard.py --express --yes`

Подробности, ошибки, ручной режим, FAQ — в **README-LOCAL** / **README-VPS**.

---

## Что получите после установки

| Что | Адрес |
|-----|--------|
| Панель (сайт админки) | http://127.0.0.1:5173 |
| API (для программ) | http://127.0.0.1:8000 |
| Документация API (Swagger) | http://127.0.0.1:8000/docs |
| Проверка «жив ли сервер» | http://127.0.0.1:8000/api/v1/health |

После установки создайте единственного оператора локальной командой `scripts/bootstrap_operator.py`; пароль не передаётся через HTTP и не записывается в документацию.

---

## Частые вопросы (коротко)

Полные FAQ с нюансами — в [`README-LOCAL.md`](./README-LOCAL.md) (§12) и [`README-VPS.md`](./README-VPS.md) (§16).

**Нужен ли Docker?**  
Желателен. Без него Postgres и Redis должны уже работать (порты 5432 и 6379). На VPS для режима `--mode vps` Docker обязателен.

**Это готовый продукт «на 100% по ТЗ»?**  
Нет. Каркас и основные модули есть; часть вещей — заглушки (SERP mock, FIAS sample и др.). Правда: [`docs/ХОД-РАБОТ.md`](./docs/ХОД-РАБОТ.md) §5.

**Где пароли и ключи?**  
В файле `.env` в корне. **Не** коммитьте и не публикуйте. На VPS — `chmod 600 .env`.

**Какой логин после установки?**
Создайте его локальной командой: `python scripts/bootstrap_operator.py --email you@example.com`. Пароль будет запрошен интерактивно.

**Как остановить локально?**

```powershell
.\scripts\stop.ps1
.\scripts\stop.ps1 -Deps   # ещё и базу в Docker
```

**Как поставить на VPS?**

```bash
./scripts/install.sh --mode vps
```

Дальше — домены, MFA, firewall: весь сценарий в [`README-VPS.md`](./README-VPS.md).

**Куда писать про изменения?**  
В журнал [`docs/ХОД-РАБОТ.md`](./docs/ХОД-РАБОТ.md) — «Журнал изменений».
---

## Статус (кратко)

| Блок | Фактический статус |
|------|--------------------|
| Мастер и install-скрипты | no-demo путь и bootstrap оператора реализованы; clean install на новой машине ещё не подтверждён |
| API, модели, миграции | Alembic head: **`0016_project_workflow`**; PostgreSQL/RLS end-to-end ещё не подтверждён |
| Python tests / panel build | **155 passed** / production build **OK** (2026-09-18) |
| Безопасность доступа | public registration, tenant/API-key/plugin endpoints закрыты; TOTP и отзыв сессий доступны в панели |
| Домены, лиды и delivery | DNS/TLS states, redirects, encrypted lead inbox, per-site encrypted webhook settings и durable retries/DLQ реализованы; public deployment smoke не выполнен |
| Docker / VPS / Caddy / worker | конфигурация и документы подготовлены; проверка в Docker/VPS blocked локальной средой |
| SERP / FIAS / IaC | mock, stub или post-release scope; не входят в подтверждённый release workflow |

Миграционный head в коде: **`0016_project_workflow`**. Full-repository Ruff пока не чист из-за legacy backlog; новые route-regression и project workflow файлы проходят targeted checks.

---

## Дальше

→ Откройте **[`README-LOCAL.md`](./README-LOCAL.md)** и следуйте шагам от «нуля» до первого входа.
