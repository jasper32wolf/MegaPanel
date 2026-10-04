# Site Panel — панель для одного оператора

Site Panel — self-hosted панель для одного оператора на собственном Linux VPS. Она помогает вести рабочий контур:

```text
Проект → факты бизнеса → семантика/география → план страниц →
черновик и QA → candidate queue → private preview → явная публикация → typed rollback → лиды
```

Это **не публичный SaaS**, не клиентский кабинет и не готовый multi-tenant hosting. Внутренний `tenant_id` пока сохраняется только для совместимости текущей схемы; публичная регистрация, roles, API keys, plugins, client portal и неподтверждённые AI/analytics/DSAR surfaces не входят в release API.

> **Фактический статус на 2026-10-04: single-operator VPS release candidate.** Есть durable candidate queue и автоматический ограниченный Bukvarix HTTPS public-free preview с явным импортом выбранных результатов; ни один из них не публикует сайт автоматически. Реальные PostgreSQL/Redis worker, Docker/VPS, RLS, Caddy/TLS, production-origin browser login, lead delivery и restore drill всё ещё требуют staging-доказательства. Единственный источник фактического статуса и release gate: [docs/ХОД-РАБОТ.md](./docs/ХОД-РАБОТ.md).

## С чего начать

|Ваша ситуация|Откройте|
|---|---|
|Разработка на Windows, macOS или Linux|[README-LOCAL.md](./README-LOCAL.md)|
|Чистый Linux VPS в интернете|[README-VPS.md](./README-VPS.md)|
|Текущие факты, блокеры и проверенные результаты|[docs/ХОД-РАБОТ.md](./docs/ХОД-РАБОТ.md)|
|План развития операторского продукта|[docs/ROADMAP-OPERATOR-PRODUCT.md](./docs/ROADMAP-OPERATOR-PRODUCT.md)|
|GitHub deploy/recovery и immutable releases|[docs/runbooks/github-deploy-recovery.md](./docs/runbooks/github-deploy-recovery.md)|
|Data recovery или перенос на новый VPS|[docs/runbooks/disaster-recovery.md](./docs/runbooks/disaster-recovery.md)|

## Поддерживаемые пути запуска

### Локальная разработка

Используйте [README-LOCAL.md](./README-LOCAL.md). Быстрые точки входа:

```powershell
# Windows
.\УСТАНОВКА.cmd
# или
.\scripts\install.ps1
```

```bash
# Linux/macOS local development
./УСТАНОВКА.sh
# или
./scripts/install.sh
```

Local/docker/deps режимы предназначены для разработки и проверки на своём компьютере. Они не являются публичным VPS deployment path.

### Production VPS

Используйте только подробный сценарий [README-VPS.md](./README-VPS.md) и отдельный Linux installer:

```bash
sudo bash scripts/install-production-vps.sh \
  --source-dir "$PWD" \
  --config-file /root/site-panel-public.env
```

Production installer создаёт защищённый host layout, production `.env`, named volumes, initial immutable release и backup gate. Он не делает опасных предположений о DNS, SSH access, TOTP, GitHub private keys или off-host backup custody.

**Не используйте на public VPS:**

```bash
./scripts/install.sh --mode vps
./scripts/install.ps1 -Mode vps
git pull
docker compose -f infra/docker/docker-compose.yml up
docker compose down -v
```

Generic installers обслуживают только local/docker/deps; production VPS требует Linux-only `install-production-vps.sh`, `docker-compose.production.yml` и immutable release manager.

## Карта репозитория

```text
apps/api/             FastAPI, Alembic, модели, release worker и API tests
apps/panel/           React/Vite operator interface
packages/             shared contracts, security, SSG and curated block kits
scripts/              local installers plus production release/recovery tooling
infra/docker/         local/development and production Docker definitions
infra/caddy/          development and production Caddy configurations
infra/k6/             load-smoke scripts
infra/ansible/        intentional placeholder; not a deployment path
infra/terraform/      intentional provider-neutral placeholder
docs/                 current status, roadmap, security and operational runbooks
observability/        Prometheus configuration
```

The legacy standalone `apps/worker` package was removed: the only supported worker is [`apps/api/app/worker.py`](./apps/api/app/worker.py), which processes durable lead delivery, approved AI/crawler work and frozen candidate-build UUIDs. It never publishes a candidate automatically.

## Основной операторский workflow

1. Создайте проект; это не создаёт публичный сайт.
2. Добавьте и подтвердите facts бизнеса.
3. Свяжите импортированные keywords и validated geo places с проектом.
4. При необходимости поставьте в очередь ограниченный Bukvarix HTTPS public-free preview, просмотрите результаты и явно импортируйте только выбранные фразы с provenance; URL, endpoint и personal API key не вводятся.
5. Создайте PagePlan и вручную утвердите его.
6. Получите deterministic PageDraft и QA verdict.
7. При `warn` внесите audit override с причиной; `block` исправьте и создайте новый candidate.
8. Примените approved draft в manifest — без build, Caddy или публикации.
9. Поставьте candidate в очередь: snapshot фиксируется до worker, а completion не публикует сайт и не запускает IndexNow.
10. В Releases дождитесь ready, посмотрите authenticated private preview и выполните legal review.
11. Проверьте DNS и явно опубликуйте выбранный build.
12. При необходимости выполните typed rollback только на ранее опубликованный immutable build.
13. Записывайте business outcome лида вручную; он не изменяет content или SEO автоматически.

Подробное фактическое покрытие и непроверенные runtime-гейты: [docs/ХОД-РАБОТ.md](./docs/ХОД-РАБОТ.md).

## Безопасность и данные

- У production один active `superadmin`; bootstrap откажется создать второй owner scope.
- Password оператора не передаётся в HTTP endpoint или command-line arguments.
- Сессии cookie-based: `HttpOnly`, `Secure`, `SameSite`, CSRF и rotation/revocation.
- TOTP требуется включить до реального использования VPS.
- PII лидов шифруются; secrets и backups надо защищать как production data.
- `.env`, `backup.env`, private keys, restic password, database dumps, cookies, TOTP secrets и raw lead PII нельзя публиковать или коммитить.

## Проверки и известные границы

Последний локальный прогон в этом working tree:

- `pytest apps/api/tests -q` — **591 passed, 6 service-gated tests skipped**;
- `ruff check apps/api` и `git diff --check` — проходят;
- panel production build проходит; mocked Chromium Releases suite — **12 passed**;
- Docker/browser execution, hosted CI и реальный VPS staging drill здесь не выполнялись.

Не заменяйте реальное доказательство конфигурационным review или unit tests. До public use выполните checklist из [README-VPS.md](./README-VPS.md) и обновите фактический журнал только подтверждёнными результатами.

## Полезные документы

|Документ|Назначение|
|---|---|
|[README-LOCAL.md](./README-LOCAL.md)|Локальная установка, первый вход и local FAQ.|
|[README-VPS.md](./README-VPS.md)|Подробная безопасная установка VPS для неспециалиста.|
|[docs/ХОД-РАБОТ.md](./docs/ХОД-РАБОТ.md)|Паспорт проекта, структура, release gates и журнал.|
|[docs/PLAN-MULTIPANEL.md](./docs/PLAN-MULTIPANEL.md)|Короткий индекс статуса и последовательности развития.|
|[docs/ROADMAP-OPERATOR-PRODUCT.md](./docs/ROADMAP-OPERATOR-PRODUCT.md)|Предлагаемое развитие после доказательства ядра.|
|[docs/security/](./docs/security/)|Retention, incident response, subprocessors и `security.txt` template.|

## Лицо, отвечающее за production

До реальных клиентов оператор обязан подтвердить: HTTPS, single operator + TOTP, firewall/SSH access, public port exposure, site delivery, lead form/webhook, encrypted off-host backup и restore на отдельном disposable VPS. Пока эти шаги не пройдены, продукт остаётся release candidate.
