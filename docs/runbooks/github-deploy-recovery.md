# GitHub-управляемые обновления и аварийное восстановление

> Этот runbook описывает **целевую production-схему**. Она добавляет безопасный release/recovery-контур, но не отменяет P0-блокеры приложения из [`docs/ХОД-РАБОТ.md`](../ХОД-РАБОТ.md). Пока P0 не закрыты и CI не зелёный, GitHub Actions намеренно не сможет автоматически опубликовать релиз.

## 1. Что решает эта система

| Задача | Как решается |
|---|---|
| Обновить VPS из GitHub | Успешный `ci` для точного SHA в `main` запускает [`deploy-production.yml`](../../.github/workflows/deploy-production.yml). |
| Не развернуть непроверенный код | Deploy workflow принимает только commit из `main`, у которого есть успешный запуск `ci.yml`. |
| Не потерять предыдущий код | На VPS поддерживаются immutable release-каталоги и симлинки `current`/`previous`. |
| Безопасно откатить неудачный релиз | После failed health выполняется code rollback на `previous`; schema downgrade и DB restore не выполняются. |
| Реагировать на аварии | [`recover-production.yml`](../../.github/workflows/recover-production.yml) каждые 10 минут проверяет публичный health и запускает bounded recovery только при подтверждённой внутренней проблеме. |
| Восстановить данные | Только вручную через GitHub `restore` с `confirmation=RESTORE` и конкретным encrypted restic snapshot. |
| Хранить резервные копии | `restic` отправляет зашифрованные PostgreSQL dump, сайты, shared `.env` и release state во внешнее S3-compatible хранилище. |

## 2. Модель безопасности и важные ограничения

1. **GitHub Actions не хранит БД-дампы и `.env`.** В GitHub передаётся только архив исходного кода релиза.
2. **GitHub Actions не доверяет новому host key автоматически.** `DEPLOY_KNOWN_HOSTS` и `RECOVERY_KNOWN_HOSTS` содержат заранее проверенный fingerprint/known_hosts entry.
3. **Ключи deploy и recovery разные.** У них отдельные GitHub Environment secrets и разные forced-command роли на VPS.
4. **Recovery никогда не делает автоматически:** `pg_restore`, `down -v`, удаление volume или переинициализацию `.env`.
5. **Rollback откатывает контейнерный код, а не schema.** Перед добавлением migration разработчик обязан обеспечить backward compatibility на время возможного rollback.
6. **GitHub cron best-effort.** Он может задерживаться или пропускаться. Нужен независимый uptime monitor и alerts; scheduled workflow — дополнительный слой, не SLA.
7. **Наличие workflow не делает приложение production-ready.** Public registration/RLS/lead flow и остальные P0 из единого паспорта должны быть закрыты отдельно.

## 3. Архитектура release-каталогов на VPS

```text
/opt/site-panel/
├── bin/                         # стабильные entry points, не mutable release
│   ├── release-manager.sh
│   ├── backup-production.sh
│   ├── restore-production.sh
│   └── github-deploy-gateway.sh
├── incoming/<SHA>.tar.gz         # archive, доставленный GitHub Actions
├── releases/<SHA>/               # immutable код релиза
│   ├── .site-panel-release       # commit=<SHA>
│   ├── .env -> ../shared/.env    # symlink, секреты не в archive
│   └── infra/docker/docker-compose.production.yml
├── current -> releases/<SHA>     # активный code release
├── previous -> releases/<SHA>    # последний работающий code release
└── shared/
    ├── .env                      # production variables, chmod 600
    ├── backup.env                # restic destination/credentials, chmod 600
    └── release-state/
        ├── state.env             # release, backup and recovery state
        └── audit.log             # timestamped non-secret operational events
```

Named Docker volumes (`site-panel_pgdata`, `site-panel_sites_data`, `site-panel_uploads_data`, `site-panel_dsar_data`, `site-panel_caddy_data`, `site-panel_caddy_config`) не находятся внутри release directory. Поэтому смена кода не уничтожает данные.

Backup policy version 1 сохраняет PostgreSQL, sites, uploads, Caddy data/config, shared `.env` и release state. DSAR exports намеренно не сохраняются и при restore очищаются. Эти policy/version записываются в snapshot manifest; restore принимает только snapshots с поддерживаемой manifest policy.

## 4. Что нужно подготовить до включения workflow

### 4.1. Требования к серверу

- Ubuntu/Debian VPS, Docker Engine + Docker Compose v2;
- `restic`, `python3`, `tar`, `flock`, `curl`, OpenSSH server;
- доступный DNS и HTTPS для panel/API до релиза;
- firewall наружу: только 22, 80 и 443;
- выбранное S3-compatible хранилище для backup, отдельное от VPS;
- закрыты P0 из [`ХОД-РАБОТ.md` §10](../ХОД-РАБОТ.md#p0--блокеры-основного-и-безопасного-сценария), прежде чем открывать публичный доступ.

Production compose публикует только 80/443. PostgreSQL, Redis, API, panel и Caddy Admin остаются внутри `site-panel-internal` Docker network.

### 4.2. Две пары SSH-ключей

На доверенной администраторской машине сгенерируйте **два** ключа. Не выполняйте это на GitHub runner и не добавляйте private keys в репозиторий.

```bash
ssh-keygen -t ed25519 -a 100 -f site-panel-deploy -C "github-site-panel-deploy"
ssh-keygen -t ed25519 -a 100 -f site-panel-recovery -C "github-site-panel-recovery"
```

- `site-panel-deploy` — private key для GitHub Environment `production-deploy`.
- `site-panel-recovery` — private key для GitHub Environment `production-recovery`.
- `.pub` файлы временно безопасно доставьте на VPS для bootstrap. Private keys удалять с VPS нельзя было бы даже случайно: они там вообще не нужны.

### 4.3. Automated VPS bootstrap

Для нового supported VPS используйте единственный installer из [README-VPS.md](../../README-VPS.md). Он устанавливает host dependencies, Docker/Compose, restic, deployment account, volume permissions, production `.env`, initial immutable release и first encrypted backup.

```bash
sudo bash scripts/install-production-vps.sh \
  --source-dir "$PWD" \
  --config-file /root/site-panel-public.env \
  --deploy-public-key-file /root/site-panel-deploy.pub \
  --recovery-public-key-file /root/site-panel-recovery.pub
```

Installer вызывает [`bootstrap-github-deploy.sh`](../../scripts/bootstrap-github-deploy.sh) только при наличии обеих public keys и сохраняет не-secret phase state в `/opt/site-panel/shared/installer-state.json`. Без ключей initial local release возможен, но `github_environment` остаётся pending: не заменяйте этот security boundary unrestricted SSH account.

Firewall и SSH hardening — отдельные opt-in phases: installer не включает их по умолчанию. DNS/TLS, TOTP confirmation, GitHub Environment private-key custody и restore drill также остаются явными operator gates.

### 4.4. Production `.env` и restic

Installer создаёт `/opt/site-panel/shared/.env` с owner `sitepanel-deploy`, mode `0600`, stable cryptographic secrets и согласованными `PANEL_DOMAIN`, `API_DOMAIN`, HTTPS URLs/CORS. [`validate_production_env.py`](../../scripts/validate_production_env.py) запускается до activation; ручная правка не может оставить placeholder, inconsistent origin или group-readable env.

Restic repository/password/backup credentials запрашиваются защищённо или передаются через VPS-local `0600` files. `backup.env` — строго data-file: разрешены только literal allowlisted `KEY=value` rows; shell expressions и unknown keys не выполняются и отклоняются. Storage credentials должны иметь доступ только к выделенному backup bucket/prefix. Не помещайте `backup.env`, restic password или cloud keys в GitHub artifacts, repository variables, исходники или job summary.

Наличие successful `restic init` или initial backup не доказывает restore: installer сохраняет `pending_restore_drill` до восстановления selected snapshot в отдельный disposable VPS.

Если меняется [`github-deploy-gateway.sh`](../../scripts/github-deploy-gateway.sh), повторите gateway bootstrap phase: он намеренно живёт вне mutable release directories.

### 4.5. First-install failure

У первого release нет `previous`, поэтому failed first activation не может безопасно выполнить code rollback. Installer сохраняет logs/state, не удаляет volumes/secrets и требует после исправления причины запустить `--resume`. После первого healthy release existing release manager возвращает code only на `previous`; DB restore всегда требует selected snapshot plus explicit confirmation.

Storage credentials должны иметь доступ только к выделенному backup bucket/prefix. Никогда не помещайте backup credentials в GitHub artifacts, repository variables, исходники или job summary.

## 5. Настройка GitHub

Создайте GitHub Environments:

| Environment | Назначение | Рекомендация |
|---|---|---|
| `production-deploy` | автоматический deploy после green CI | required reviewer, branch policy `main`, отдельные deploy secrets |
| `production-recovery` | schedule/manual restart, rollback, restore | required reviewer как минимум для `restore`; отдельные recovery secrets |

### 5.1. Environment `production-deploy`

**Variables**:

| Variable | Пример |
|---|---|
| `DEPLOY_HOST` | `203.0.113.10` или DNS VPS |
| `DEPLOY_USER` | `sitepanel-deploy` |
| `DEPLOY_PORT` | `22` |
| `DEPLOY_ROOT` | `/opt/site-panel` |

**Secrets**:

| Secret | Содержание |
|---|---|
| `DEPLOY_SSH_KEY` | полный private key `site-panel-deploy` |
| `DEPLOY_KNOWN_HOSTS` | проверенная строка `host keytype base64-key` VPS |

### 5.2. Environment `production-recovery`

**Variables**:

| Variable | Пример |
|---|---|
| `RECOVERY_HOST` | тот же VPS host |
| `RECOVERY_USER` | `sitepanel-deploy` |
| `RECOVERY_PORT` | `22` |

**Secrets**:

| Secret | Содержание |
|---|---|
| `RECOVERY_SSH_KEY` | полный private key `site-panel-recovery` |
| `RECOVERY_KNOWN_HOSTS` | проверенная строка known_hosts того же VPS |

### 5.3. Repository variable

| Variable | Значение |
|---|---|
| `PUBLIC_HEALTH_URL` | `https://api.example.ru/api/v1/health/live` |

Получите host key из доверенного канала: консоль провайдера, уже проверенный SSH fingerprint или out-of-band администраторская сессия. `ssh-keyscan` можно использовать только для формирования кандидата, который затем сверяется с доверенным fingerprint. Workflow не использует TOFU и запускает SSH с `StrictHostKeyChecking=yes`.

## 6. Обновление через GitHub

### 6.1. Автоматический путь

1. Изменение попадает в `main`.
2. [`ci.yml`](../../.github/workflows/ci.yml) должен завершиться success.
3. GitHub запускает `Deploy production` на точном `workflow_run.head_sha`.
4. Workflow собирает archive без `.git`, `.env`, `.claude`, `.roo`, dependencies и runtime.
5. Workflow загружает archive через `scp -O`; forced gateway разрешает только `/opt/site-panel/incoming/<SHA>.tar.gz`.
6. Release manager делает encrypted pre-deploy backup, build, переключение release и health probe.
7. При success `current` указывает на новый SHA, а прежний SHA становится `previous`.
8. При failed health manager возвращает `current` на `previous` и повторно проверяет его health.

Hosted CI result для текущего candidate SHA ещё не зафиксирован. До успешного hosted run публикация остаётся заблокированной; локальные Ruff и YAML проверки не являются заменой GitHub evidence.

### 6.2. Ручный deploy проверенного старого SHA

`Actions → Deploy production → Run workflow` позволяет указать SHA. Workflow проверит два условия:

- SHA является ancestor `main`;
- для него найден хотя бы один successful `ci.yml` run.

Ручной deploy не предназначен для обхода CI и не принимает branch name, tag, произвольный archive или SHA из другой ветки.

### 6.3. Запрос из панели

Экран **Система → Обновления** — дополнительный, а не привилегированный путь. Он доступен только единственному активному superadmin после MFA login и отправляет API только в два жёстко заданных workflow с `ref=main`:

- `deploy-production.yml` — SHA длиной 40/64 символов из списка successful `ci.yml` runs и точное подтверждение `DEPLOY`;
- `recover-production.yml` — `status`, `restart`, `rollback`, `recover` либо `restore`; для последнего обязательны hexadecimal snapshot и точное `RESTORE`.

API создаёт audit-safe запись операции до dispatch, запрещает вторую queued/in-progress операцию и передаёт сгенерированный UUID `request_id`. Для panel-triggered workflow этот UUID является GitHub run name, поэтому панель может показать ссылку и статус конкретного run. Она не получает Docker socket, SSH key, restic credentials, произвольный workflow/ref или raw GitHub response.

Для включения создайте отдельный fine-grained token, ограниченный этим repository: **Actions: Read and write** и **Contents: Read**. Сохраните его только в `/opt/site-panel/shared/.env` как `GITHUB_CONTROL_TOKEN`, вместе с `GITHUB_REPOSITORY=owner/repository`; mode файла — `0600`. После изменения пересоздайте API container. Отсутствие или неверная конфигурация token не ломает manual GitHub Actions path.

GitHub Environment approval по-прежнему обязателен: запрос из панели не обходит required reviewer, branch policy, pinned `known_hosts` или forced-command gateway. При недоступности GitHub/панели используйте ручной workflow из §6.2/§7.2.

### 6.4. Runtime audit

Результат каждого deploy/recovery виден в:

- GitHub Actions Job Summary;
- `/opt/site-panel/shared/release-state/audit.log`;
- `/opt/site-panel/shared/release-state/state.env`.

Логи содержат только SHA, timestamp, snapshot short ID и result. Не добавляйте туда URL с credentials, secret values или PII.

## 7. Автоматическое аварийное восстановление

### 7.1. Scheduled path

Каждые 10 минут `Recover production` запрашивает `PUBLIC_HEALTH_URL`.

| Ситуация | Действие |
|---|---|
| Публичный liveness отвечает | Никакой SSH-команды нет. |
| Public liveness не отвечает, внутренний API readiness отвечает | Нет restart/rollback: вероятны DNS/TLS/маршрутизация или GitHub network issue. Workflow фиксирует результат. |
| Внутренний readiness не отвечает | `auto-recover`: restart `api worker panel caddy`, затем повторный readiness probe. |
| Restart не помог | Code rollback на `previous`; запускается и проверяется его readiness. |
| Previous release не готов | Workflow завершается ошибкой; требуется оператор и manual recovery. |
| Повторная авария в cooldown | Не создаётся циклический restart: действие блокируется на 15 минут по умолчанию. |

Автоматическая DB restore, cleanup volumes и `docker compose down -v` запрещены архитектурно.

### 7.2. Ручные операции

`Actions → Recover production → Run workflow`:

| Operation | Действие |
|---|---|
| `status` | current/previous SHA, API readiness, last backup snapshot |
| `restart` | controlled restart текущего release с readiness acceptance |
| `rollback` | code rollback `current ↔ previous` с readiness probe |
| `recover` | выполнить bounded restart → rollback логику сейчас |
| `restore` | destructive data restore указанного restic snapshot |

Для `restore` обязательно задать:

```text
snapshot: <конкретный hexadecimal restic snapshot ID>
confirmation: RESTORE
```

Перед restore workflow запускает fresh encrypted `pre-restore` backup. Если его нельзя создать, restore останавливается до изменения данных.

## 8. Восстановление данных и новый VPS

### 8.1. Data restore на существующем VPS

1. Используйте `status`, чтобы зафиксировать current/previous SHA и backup snapshot.
2. При необходимости выполните code rollback сначала. Если сервис ожил, data restore не нужен.
3. Выберите проверенный snapshot: выполните `restic snapshots`, передав `RESTIC_REPOSITORY`, `RESTIC_PASSWORD_FILE` и, если нужны, AWS credentials явно через `env` как в §4.5. Не исполняйте `backup.env` через `source`.
4. Запустите manual `restore` только после approval и `confirmation=RESTORE`.
5. Скрипт проверяет manifest policy/version, останавливает application traffic, восстанавливает DB, sites, uploads и Caddy data/config, очищает DSAR exports, применяет migration и проверяет API readiness.
6. Обязательно вручную проверьте login, tenant isolation, sample site, upload path, lead path, TLS/external URLs и Caddy.

### 8.2. Новый VPS после полной потери старого

1. Подготовьте ОС, Docker, firewall, DNS и restic access согласно §4.
2. Выполните bootstrap с **новыми** deploy/recovery SSH keys.
3. Создайте `shared/backup.env` и password file для прежнего restic repository.
4. Через `Deploy production` вручную разверните последний known-good SHA, прошедший CI.
5. После первого deploy выберите snapshot и выполните manual `restore`.
6. Проверьте health, migrations, domains/Caddy, robots/sitemap/IndexNow, lead delivery и webhook.
7. Зафиксируйте фактические RTO/RPO и время инцидента в [`ХОД-РАБОТ.md`](../ХОД-РАБОТ.md).

## 9. Обязательный staging drill

До production и затем не реже одного раза в квартал:

- [ ] Проверить SSH pinned known_hosts и отсутствие interactive shell у обоих GitHub keys.
- [ ] Развернуть green SHA на отдельный staging VPS.
- [ ] Убедиться, что `current` и `previous` указывают на разные release directories.
- [ ] Искусственно остановить API и проверить controlled restart.
- [ ] Подготовить unhealthy staging release и проверить code rollback без DB restore.
- [ ] Создать restic backup и выполнить `restic check`.
- [ ] Восстановить snapshot **только** в disposable VPS/staging.
- [ ] Проверить DB, migration, API, panel, worker, Caddy, site build и lead flow.
- [ ] Зафиксировать measured RTO/RPO, snapshot ID, результаты и follow-up tasks.

## 10. Операторские команды на VPS

Эти команды выполняет только доверенный оператор с console/sudo. GitHub deploy user не получает interactive shell через forced key.

```bash
export SITE_PANEL_ROOT=/opt/site-panel
sudo -u sitepanel-deploy "$SITE_PANEL_ROOT/bin/release-manager.sh" status
sudo -u sitepanel-deploy "$SITE_PANEL_ROOT/bin/release-manager.sh" restart
sudo -u sitepanel-deploy "$SITE_PANEL_ROOT/bin/release-manager.sh" rollback
sudo tail -n 100 "$SITE_PANEL_ROOT/shared/release-state/audit.log"
systemctl status site-panel-backup.timer
```

Не используйте для production update `git pull`, `docker compose down -v` или ручное копирование `.env` в release folders. Все изменения кода должны проходить через immutable release workflow.

## 11. Связанные документы

- [Единый паспорт и P0/P1 roadmap](../ХОД-РАБОТ.md)
- [Общий disaster recovery runbook](./disaster-recovery.md)
- [VPS guide](../../README-VPS.md)
- [Production compose](../../infra/docker/docker-compose.production.yml)
- [Production Caddyfile](../../infra/caddy/Caddyfile.production)
- [Deploy workflow](../../.github/workflows/deploy-production.yml)
- [Recovery workflow](../../.github/workflows/recover-production.yml)
