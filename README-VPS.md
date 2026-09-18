# Установка и работа на сервере (VPS)

Этот файл описывает **целевой пошаговый путь** запуска Site Panel на арендованном Linux-сервере.
Он написан понятным языком, но текущий репозиторий ещё нельзя безопасно разворачивать в production без исправлений ниже.

> **Статус проверки 2026-09-17: single-user VPS release candidate.** Автоматические API-регрессии и production build панели проходят; production Compose изолирует служебные сервисы. Docker/VPS runtime, PostgreSQL/RLS, Caddy/TLS, worker и browser smoke не проверены в этой среде. Не размещайте панель в публичном интернете, пока не выполните release gate из [`docs/ХОД-РАБОТ.md`](./docs/ХОД-РАБОТ.md).

Связанные файлы:

- Свой компьютер → [`README-LOCAL.md`](./README-LOCAL.md)  
- Краткий обзор → [`README.md`](./README.md)  
- Единый паспорт, блокеры и план → [`docs/ХОД-РАБОТ.md`](./docs/ХОД-РАБОТ.md)
- Предлагаемое развитие операторского продукта (не текущие функции) → [`docs/ROADMAP-OPERATOR-PRODUCT.md`](./docs/ROADMAP-OPERATOR-PRODUCT.md)
- Авария / восстановление → [`docs/runbooks/disaster-recovery.md`](./docs/runbooks/disaster-recovery.md)
- GitHub-обновления, rollback и backup/restore → [`docs/runbooks/github-deploy-recovery.md`](./docs/runbooks/github-deploy-recovery.md)

---

## 0. Чем VPS отличается от «домашнего» запуска

| | Дома (LOCAL) | Сервер (VPS) |
|---|--------------|--------------|
| Кто видит | Обычно только вы | Весь интернет |
| Цель | Разработка, тесты | Реальная панель и сайты клиентов |
| Пароли | Можно demo | **Demo запрещён** |
| Секреты в `.env` | Достаточно сгенерированных | Только сильные, уникальные |
| MFA (код из телефона) | По желанию | **Обязательно** для админов |
| Открытые порты | Можно 8000/5173 | Лучше только **22, 80, 443** |
| Бэкапы | Желательны | **Обязательны** |

Если вы ещё учитесь — сначала пройдите [`README-LOCAL.md`](./README-LOCAL.md). На публичный VPS не выкладывайте demo-логин.

---

## 1. Что такое VPS простыми словами

**VPS** — удалённый компьютер в дата-центре, который вы арендуете.  
Вы подключаетесь к нему по SSH (чёрное окно с командами) и ставите туда нашу систему.

Нужны:

1. Сам VPS (Ubuntu 22.04/24.04 или Debian 12).  
2. Доменное имя (желательно) — например `panel.ваш-сайт.ru`.  
3. Доступ root или пользователь с `sudo`.

---

## 2. Какой сервер купить

### Минимум (тест, 1–2 клиента)

| Ресурс | Значение |
|--------|----------|
| ОС | Ubuntu 22.04 / 24.04 LTS или Debian 12 |
| CPU | 2 ядра |
| RAM | 4 ГБ |
| Диск | 40 ГБ SSD |
| IP | Публичный IPv4 (IPv6 желателен) |

### Нормальный прод (десятки сайтов)

| Ресурс | Значение |
|--------|----------|
| CPU | 4+ ядра |
| RAM | 8–16 ГБ |
| Диск | 100+ ГБ SSD (сайты + бэкапы) |
| Swap | 2–4 ГБ (страховка, не вместо RAM) |

**Совет:** берите провайдера с удобным снимком диска (snapshot) — это упрощает откат.

---

## 3. Карта системы на сервере

```text
Интернет
   │
   ▼
Caddy (порты 80 и 443)  ← сайты клиентов + (желательно) прокси panel/api
   │
   ├── готовый HTML сайтов (том Docker sites_data)
   ├── Panel (админка)
   ├── API (мозг)
   ├── Worker (фоновая доставка лидов и повторные попытки webhook)
   ├── PostgreSQL (база)
   └── Redis (очереди)
```

Рекомендуемый способ: **Docker Compose** из файла  
`infra/docker/docker-compose.yml`.

---

## 4. Подготовка сервера (один раз)

Подключайтесь с вашего компьютера:

```bash
ssh root@IP_ВАШЕГО_СЕРВЕРА
```

(или пользователь, который дал хостер).

### 4.1. Обновление системы

```bash
apt update && apt upgrade -y
```

### 4.2. Пользователь под проект (рекомендуется)

Не работайте постоянно под `root`.

```bash
adduser --disabled-password sitepanel
usermod -aG sudo sitepanel
mkdir -p /opt/site-panel
chown sitepanel:sitepanel /opt/site-panel
```

Дальше:

```bash
su - sitepanel
cd /opt/site-panel
```

### 4.3. Docker

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker sitepanel
```

**Выйдите из SSH и зайдите снова**, чтобы группа `docker` применилась.

Проверка:

```bash
docker --version
docker compose version
```

### 4.4. Firewall (очень важно)

Открываем только то, что нужно снаружи:

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status
```

**Не открывайте наружу** (даже «на время»):

| Порт | Почему нельзя |
|------|----------------|
| 5432 | База данных — украдут всё |
| 6379 | Redis — очередь и сессии |
| 2019 | Caddy Admin — можно перехватить сайты |
| 8000 / 5173 | Лучше спрятать за доменом через Caddy, а не торчать в мир |

Сейчас compose **всё же пробрасывает** 5432/6379/2019/8000/5173 на хост. Это удобно для отладки. На проде:

1. Либо уберите эти `ports:` из compose (оставьте только 80/443).  
2. Либо firewall режет всё, кроме 22/80/443, а с хоста ходит только localhost.

### 4.5. SSH по ключу

Отключите вход по паролю root, когда ключи настроены. Это отдельная тема хостера — сделайте до открытия панели миру.

### 4.6. Скопировать проект на сервер

**Вариант A — git:**

```bash
cd /opt/site-panel
git clone <URL_ВАШЕГО_РЕПОЗИТОРИЯ> .
```

**Вариант B — с Windows (PowerShell), с вашего ПК:**

```powershell
scp -r "e:\РАБОТА\ПАНЕЛЬ ДЛЯ ГЕНЕРАЦИИ САЙТОВ\*" sitepanel@IP:/opt/site-panel/
```

(или WinSCP / rsync).

На сервере в `/opt/site-panel` должны быть видны: `scripts/`, `apps/`, `infra/`, `.env.example`.

---

## 5. Текущий bootstrap одной командой (только staging/разработка)

### Интерактивный мастер (проще всего)

```bash
cd /opt/site-panel
chmod +x УСТАНОВКА.sh scripts/*.sh
./УСТАНОВКА.sh
```

В меню: **`2` → режим vps**, укажите URL панели/API (или Enter и допишите `.env` позже).
Демо-seed на VPS по умолчанию **выключен**.

Без меню:

```bash
./scripts/install.sh --mode vps --skip-seed
```

### Что делает режим `vps`

1. Создаёт/обновляет `.env`.  
2. Ставит `APP_ENV=production`.  
3. Генерирует **сильный** `POSTGRES_PASSWORD` и секреты приложения.  
4. Собирает и поднимает весь Docker Compose.  
5. Ждёт, пока API ответит на порту 8000.

В конце installer ожидает доступность порта 8000 и печатает адреса `http://127.0.0.1:8000` и `:5173`. Это **не подтверждает** browser login, работу worker, выдачу SSG через Caddy или безопасность deployment; текущие расхождения перечислены в едином паспорте.

### Сразу после install — обязательно вручную

1. Откройте `.env` и пропишите свои домены (см. §6).  
2. Пересоздайте api/panel.  
3. Создайте **своего** админа (не demo).  
4. Включите MFA.  
5. Закройте лишние порты firewall / compose.  
6. Настройте бэкапы (§11).

### Другие режимы (не путать)

```bash
./scripts/install.sh --mode docker   # полный стек, но «как для разработки» (слабже для прода)
./scripts/install.sh                 # гибрид как LOCAL (нужен npm на хосте)
```

На Windows PowerShell режима `-Mode vps` **нет** — VPS = Linux + `install.sh`.

---

## 6. Файл `.env` на проде — что обязательно поменять

```bash
cd /opt/site-panel
nano .env    # или vim / mcedit
chmod 600 .env
```

| Переменная | Что поставить |
|------------|----------------|
| `APP_ENV` | `production` |
| `PANEL_PUBLIC_URL` | `https://panel.ваш-домен.ru` |
| `API_PUBLIC_URL` | `https://api.ваш-домен.ru` |
| `CORS_ORIGINS` | **точно** `https://panel.ваш-домен.ru` (без слэша в конце) |
| `POSTGRES_PASSWORD` | сильный (скрипт vps уже сгенерировал) |
| `DATABASE_URL` | пароль должен **совпадать** с `POSTGRES_PASSWORD` |
| `DEEPSEEK_API_KEY` и др. | по желанию (без них — локальный текст) |
| `SENTRY_DSN` | если подключено наблюдение |

**Не меняйте без плана перешифровки:**

- `FIELD_ENCRYPTION_KEY`  
- `BLIND_INDEX_PEPPER`  
- `APP_PEPPER`  

Иначе старые зашифрованные лиды/хэши могут «сломаться».

После правок URL/CORS:

```bash
docker compose --env-file .env -f infra/docker/docker-compose.yml up -d --force-recreate api panel
```

Compose подставляет пароль БД из переменных окружения `${POSTGRES_PASSWORD}` — держите `.env` согласованным.

---

## 7. Первый запуск вручную (если без install.sh)

```bash
cd /opt/site-panel
cp .env.example .env
chmod 600 .env
python3 scripts/prepare_env.py --mode vps

docker compose --env-file .env -f infra/docker/docker-compose.yml up -d --build
docker compose -f infra/docker/docker-compose.yml ps
docker compose -f infra/docker/docker-compose.yml logs migrate
```

Ожидаемо: сервис `migrate` завершился с кодом 0, `api` / `worker` / `caddy` / `panel` работают.

Проверки с сервера:

```bash
curl -fsS http://127.0.0.1:8000/api/v1/health
curl -fsS -o /dev/null -w "%{http_code}\n" http://127.0.0.1:5173/
```

---

## 8. Домены, DNS и HTTPS

### 8.1. DNS у регистратора

Создайте записи типа **A** (и при наличии **AAAA**):

| Имя | Куда |
|-----|------|
| `panel.ваш-домен.ru` | IP VPS |
| `api.ваш-домен.ru` | IP VPS |
| клиентские домены сайтов | IP VPS (когда будете публиковать сайты) |

Подождите 5–60 минут (иногда дольше), пока DNS «разъедется».

Проверка с вашего ПК:

```bash
nslookup panel.ваш-домен.ru
```

### 8.2. Прокси panel и api через Caddy

Сейчас стоковый `infra/caddy/Caddyfile` упрощённый: отдаёт файлы сайтов с `:80`, Admin слушает `0.0.0.0:2019`, `auto_https` ограничен.

Для прода **нужно доработать** Caddyfile примерно так:

```caddyfile
{
  admin 127.0.0.1:2019
}

panel.example.com {
  reverse_proxy panel:80
  encode gzip
  header {
    X-Content-Type-Options nosniff
    Referrer-Policy strict-origin-when-cross-origin
    Strict-Transport-Security "max-age=31536000; includeSubDomains"
  }
}

api.example.com {
  reverse_proxy api:8000
  encode gzip
  header {
    X-Content-Type-Options nosniff
    Referrer-Policy strict-origin-when-cross-origin
  }
}

:80 {
  root * /srv/sites
  encode zstd gzip
  file_server
}
```

Затем:

1. Уберите publish `2019:2019` из compose (или firewall).  
2. Перезапустите caddy.  
3. Обновите `.env` URL/CORS.  
4. Откройте `https://panel...` в браузере.

**Важно:** пока Caddyfile не доработан под ваши домены, HTTPS «из коробки» может не завестись автоматически — это известный нюанс (см. журнал §5).

---

## 9. Первый администратор (без demo)

### 9.1. Создать единственного оператора

Публичной регистрации в production нет. После успешных миграций создайте первую и единственную учётную запись **изнутри Docker-сети**. Пароль не передавайте в командной строке и не сохраняйте в shell history:

```bash
cd /opt/site-panel/current
read -rs OPERATOR_PASSWORD
printf '\n'
printf '%s\n' "$OPERATOR_PASSWORD" | \
  docker compose --env-file .env -f infra/docker/docker-compose.production.yml \
  run --rm -T api python /app/scripts/bootstrap_operator.py \
  --email 'you@example.com' \
  --name 'Site Panel Operator' \
  --password-stdin
unset OPERATOR_PASSWORD
```

Повторный запуск с тем же email останавливается без изменений. Чтобы осознанно заменить пароль или нормализовать оператора на старой установке, добавьте `--reset-password` с тем же email. Команда не создаёт второй owner scope; если в базе уже несколько пользователей или owner scopes, она останавливается и требует сначала вручную исправить данные. Команда создаёт внутреннего владельца `operator`, который нужен только для совместимости текущей схемы БД и не появляется в интерфейсе.

Проверки, обязательные до публикации панели:

1. `POST /api/v1/auth/register` отсутствует из OpenAPI и возвращает 404;
2. оператор может войти через HTTPS, а API/база/Caddy Admin не открыты наружу;
3. пароль и demo seed не используются на VPS;
4. TOTP включена до постоянной эксплуатации.

### 9.2. Почему нельзя demo на VPS

`admin@demo.local` / `DemoPass123!` — учебный аккаунт.  
Любой, кто читал README, может войти. На публичном сервере это взлом за 10 секунд.

Если случайно сделали seed:

```bash
# НЕ запускайте seed_demo на проде
# Если уже запустили — смените пароль или удалите пользователя из БД
```

### 9.3. Включить MFA (двухфакторку)

Нужен телефон с приложением Google Authenticator / Aegis / 1Password и т.п.

```bash
BASE="https://panel.example.com"
COOKIE_JAR="$(mktemp)"

read -rp 'Email: ' OPERATOR_EMAIL
read -rs -p 'Password: ' OPERATOR_PASSWORD
printf '\n'
curl -fsS -c "$COOKIE_JAR" -X POST "$BASE/api/v1/auth/login" \
  -H 'Content-Type: application/json' \
  -d "$(printf '{\"email\":%s,\"password\":%s}' \
    "$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$OPERATOR_EMAIL")" \
    "$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$OPERATOR_PASSWORD")")"
unset OPERATOR_PASSWORD
CSRF=$(awk '$6 == "site_panel_csrf" { print $7 }' "$COOKIE_JAR")

curl -fsS -b "$COOKIE_JAR" -X POST "$BASE/api/v1/security/totp/setup" \
  -H "X-CSRF-Token: $CSRF"
```

В ответе будет секрет / ссылка `otpauth://...` — внесите в приложение.

Подтверждение кодом из приложения:

```bash
read -rp 'TOTP code: ' TOTP_CODE
curl -fsS -b "$COOKIE_JAR" -X POST "$BASE/api/v1/security/totp/confirm" \
  -H 'Content-Type: application/json' \
  -H "X-CSRF-Token: $CSRF" \
  -d "{\"code\":\"$TOTP_CODE\"}"
rm -f "$COOKIE_JAR"
```

Пока не сделали `confirm`, MFA ещё не включена. После confirm при логине нужен `totp_code`.

Настройка и подтверждение TOTP доступны в панели в разделе **«Настройки»**. Cookie-based сценарий выше остаётся для аварийной диагностики; выполняйте его только по HTTPS и удаляйте временный cookie-файл после работы.

---

## 10. Повседневная работа

### Логи

```bash
cd /opt/site-panel
docker compose -f infra/docker/docker-compose.yml logs -f api worker caddy --tail=200
```

### Статус контейнеров

```bash
docker compose -f infra/docker/docker-compose.yml ps
```

### Обновление кода и аварийный откат

> **Production-путь после настройки release system:** не выполняйте `git pull` непосредственно на работающем VPS. Он не даёт immutable release, проверяемого SHA, pre-deploy backup или безопасного rollback.

После закрытия P0 и настройки GitHub Environments используйте:

1. merge проверенного изменения в `main`;
2. успешный `ci`;
3. workflow **Deploy production**, который доставит точный SHA на VPS;
4. workflow **Recover production** для `status`, `restart`, `rollback` или manual `restore`.

Система хранит `current` и `previous` releases, создаёт encrypted restic backup перед обновлением и автоматически может сделать только restart → code rollback. Восстановление PostgreSQL всегда требует явного `RESTORE` confirmation. Полная настройка: [`docs/runbooks/github-deploy-recovery.md`](./docs/runbooks/github-deploy-recovery.md).

До настройки этого контура обновляйте только закрытый staging вручную, предварительно создавая backup и фиксируя SHA; не используйте это как production process.

### Сборка сайта клиента

Через панель: **Сайты → Собрать**,  
или API:

```bash
curl -fsS -X POST -H "Authorization: Bearer $TOKEN" \
  https://api.ваш-домен.ru/api/v1/sites/<UUID_САЙТА>/build
```

Файлы попадают в Docker-том `sites_data` (внутри api: `/app/dist`, в caddy: `/srv/sites`).

### Домены клиентов

В панели **Домены** или API `POST /api/v1/domains`.  
Проверка DNS/TLS: health endpoint доменов.

### Лиды

Формы отправляют данные same-origin на `POST /api/v1/leads/public`. При подключении клиентского домена dynamic vhost Caddy добавляет proxy только для этого `POST`-пути к API. После привязки домена пересоберите сайт и до production подтвердите отправку на реальном hostname через Docker/Caddy smoke.

### Индексация

Автоматическое продвижение страниц и отправка в IndexNow не входят в подтверждённый workflow первого релиза и не запускаются worker-ом. Не используйте API-only controls индексирования до отдельной end-to-end проверки.

### Остановка

```bash
docker compose -f infra/docker/docker-compose.yml down
```

Данные БД и сайтов в томах **сохраняются**.

### Полное уничтожение (опасно)

```bash
docker compose -f infra/docker/docker-compose.yml down -v
```

Удалит тома Postgres и сайтов. Без бэкапа — всё пропало.

---

## 11. Резервные копии и восстановление

> Для production используйте encrypted off-host restic backups из GitHub release/recovery system. Простые `pg_dump` и tar ниже оставлены только как диагностические примеры для закрытого staging: они сами по себе не обеспечивают шифрование, off-host хранение, retention или проверенный restore.

### Production-путь

1. Настройте `/opt/site-panel/shared/backup.env` и restic repository согласно [`github-deploy-recovery.md`](./docs/runbooks/github-deploy-recovery.md).
2. Bootstrap установит `site-panel-backup.timer`: ежедневный backup + retention + `restic check`.
3. Перед production deploy release manager создаёт дополнительный encrypted pre-deploy snapshot.
4. Для data recovery используйте **Actions → Recover production → restore** с конкретным snapshot ID и `confirmation=RESTORE`.
5. Не храните backup только на одном VPS и не отправляйте PII backups в GitHub Actions artifacts.

### Диагностический PostgreSQL dump (закрытый staging)

```bash
docker compose -f infra/docker/docker-compose.yml exec -T postgres \
  pg_dump -U site_panel -Fc site_panel \
  > /var/backups/site-panel/db-$(date +%F).dump
```

### Диагностический archive sites volume (закрытый staging)

```bash
docker volume ls | grep sites
# имя может быть вроде: site-panel_sites_data или docker_sites_data

docker run --rm \
  -v ИМЯ_ТОМА:/data \
  -v /var/backups/site-panel:/backup \
  alpine tar czf /backup/sites-$(date +%F).tgz -C /data .
```

Полная процедура, обязательный recovery drill и различие code rollback/data restore: [`docs/runbooks/github-deploy-recovery.md`](./docs/runbooks/github-deploy-recovery.md) и [`docs/runbooks/disaster-recovery.md`](./docs/runbooks/disaster-recovery.md).

---

## 12. Альтернатива без Docker (systemd)

Имеет смысл, если Docker нельзя.

1. Установите: `python3.12`, `postgresql`, `redis-server`, `nodejs`, `caddy`.  
2. Повторите ручную установку пакетов как в [`README-LOCAL.md`](./README-LOCAL.md) §7.  
3. `alembic upgrade head`, создайте админа.  
4. Unit-файлы:

`/etc/systemd/system/sitepanel-api.service`:

```ini
[Unit]
Description=Site Panel API
After=network.target postgresql.service redis.service

[Service]
User=sitepanel
WorkingDirectory=/opt/site-panel
EnvironmentFile=/opt/site-panel/.env
Environment=PYTHONPATH=/opt/site-panel/apps/api
ExecStart=/opt/site-panel/.venv/bin/uvicorn app.main:app --app-dir apps/api --host 127.0.0.1 --port 8000 --workers 2
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

`sitepanel-worker.service`:

```ini
[Unit]
Description=Site Panel Worker
After=network.target redis.service

[Service]
User=sitepanel
WorkingDirectory=/opt/site-panel
EnvironmentFile=/opt/site-panel/.env
Environment=PYTHONPATH=/opt/site-panel/apps/api
ExecStart=/opt/site-panel/.venv/bin/arq app.worker.WorkerSettings
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Panel: `cd apps/panel && npm ci && npm run build`, отдайте `dist` через Caddy.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now sitepanel-api sitepanel-worker
```

В `.env` для non-Docker: `POSTGRES_HOST=localhost`, `REDIS_URL=redis://localhost:6379/0`, `CADDY_ADMIN_URL=http://127.0.0.1:2019`.

---

## 13. Мониторинг и безопасность (чек-лист)

| Мера | Как |
|------|-----|
| Health | `GET /api/v1/health` |
| Metrics | `GET /api/v1/metrics` (Prometheus) |
| security.txt | `docs/security/security.txt` — заполните реальные контакты до публикации |
| MFA | у всех админов |
| Firewall | только 22/80/443 снаружи |
| Caddy Admin | не в интернет |
| Postgres/Redis | не в интернет |
| Логи | `docker compose logs` / journald |
| Обновления ОС | `unattended-upgrades` или ручной `apt upgrade` |
| Fail2ban | на SSH |
| Диск | следите за ростом `sites_data` |

Заготовки IaC (`infra/ansible`, `infra/terraform`) сейчас **заглушки** — не считайте готовым прод-оркестратором.

> Этот checklist описывает необходимые ручные проверки, а не уже настроенные SLO, alerts, автоматическое восстановление или подтверждённый runtime. Их предлагаемый контур описан в [`docs/ROADMAP-OPERATOR-PRODUCT.md`](./docs/ROADMAP-OPERATOR-PRODUCT.md); до отдельной проверки он не является функцией VPS-установки.

---

## 14. Масштабирование (ориентиры)

| Когда | Что делать |
|-------|------------|
| Много фоновых задач | `docker compose up -d --scale worker=2` (общий Redis) |
| Много страниц / тяжёлые отчёты | вынести Postgres на отдельный сервер |
| Диск кончается | объектное хранилище или больший диск; чистить старые `previous/` билды |
| TLS/CPU упираются | отдельный edge (Caddy/CDN) |

---

## 15. Тесты на VPS

### Smoke после деплоя

```bash
curl -fsS https://api.ваш-домен.ru/api/v1/health
curl -fsS -o /dev/null -w "%{http_code}\n" https://panel.ваш-домен.ru/
```

### Автотесты

На боевом сервере полный pytest лучше не гонять постоянно. Делайте на staging / в CI / локально (см. README-LOCAL).

### Нагрузка k6 — с другой машины

```bash
k6 run -e BASE=https://api.ваш-домен.ru infra/k6/smoke.js
```

Не долбите продакшен с того же VPS во время пика клиентов.

---

## 16. Вопросы и ответы (FAQ)

### Выбор и покупка

**В: Можно ли поставить на shared-хостинг «за 100 ₽»?**  
О: Нет. Нужен VPS/VDS с Docker или root, не обычный PHP-хостинг.

**В: Подойдёт ли Windows Server?**  
О: Инструкция рассчитана на Linux. Для Windows удобнее LOCAL + отдельный Linux VPS для прода.

**В: Сколько это стоит в месяц?**  
О: Сам софт открыт в репозитории. Платите за VPS, домен, опционально LLM-ключи. Ориентир VPS: от пары долларов/сотен рублей в месяц за тест до заметно большего за прод.

**В: Нужен ли отдельный сервер под базу?**  
О: Для старта — нет. Когда вырастете — да (см. §14).

### Установка

**В: `Permission denied` на `./scripts/install.sh`.**  
О: `chmod +x scripts/*.sh`.

**В: Docker: `permission denied while trying to connect`.**  
О: Пользователь не в группе `docker`. Сделайте `usermod -aG docker $USER`, перелогиньтесь.

**В: `Python 3.12+ required` на VPS.**  
О: Для режима `vps`/`docker` Python на хосте нужен в основном для `prepare_env.py`. Поставьте `python3` ≥ 3.12 или используйте образ/пакет дистрибутива.

**В: `migrate` упал.**  
О: Смотрите `docker compose ... logs migrate`. Чаще всего неверный пароль БД или Postgres ещё не готов. Сверьте `POSTGRES_PASSWORD` и `DATABASE_URL`.

**В: Контейнеры перезапускаются в цикле.**  
О: `docker compose logs api` — читайте traceback. Часто: нет тома, read-only FS, битый `.env`.

**В: Сколько времени занимает первый `up --build`?**  
О: От 5 до 30+ минут в зависимости от CPU и скорости скачивания образов.

### Доступ и безопасность

**В: Открыл панель по IP:5173 — это нормально для прода?**  
О: Для теста да, для прода нет. Сделайте HTTPS-домен и закройте прямой порт.

**В: Что будет, если оставить Caddy Admin на 0.0.0.0:2019 в интернет?**  
О: Злоумышленник может менять маршруты сайтов. Смените на `127.0.0.1` и уберите publish порта.

**В: Утекло `.env`.**  
О: Считайте всё скомпрометированным: новые секреты, новый пароль БД, новые пароли пользователей, ротация ключей шифрования по процедуре (сложно). Перевыпустите доступы.

**В: Можно ли один VPS на несколько агентств?**  
О: Первый релиз рассчитан на одного оператора и один внутренний owner scope. Multi-tenant agency hosting — отдельный post-release redesign, а не поддерживаемый режим этой установки.

**В: Нужен ли Cloudflare?**  
О: Не обязателен. Полезен как CDN/WAF перед Caddy. DNS должен в итоге указывать туда, куда вы задумали (CF proxy или напрямую на VPS).

### Домены и сайты

**В: DNS прописал, сайт не открывается.**  
О: Проверьте: A-запись, firewall 80/443, контейнер caddy жив, Caddyfile, логи caddy. Для panel/api — reverse_proxy настроен?

**В: SSL не выдаётся.**  
О: Порты 80/443 должны быть доступны с интернета (Let's Encrypt). Имя домена должно резолвиться на этот IP. В стоковом Caddyfile auto_https ограничен — доработайте конфиг.

**В: Собрал сайт — где файлы?**  
О: В Docker volume `sites_data`. Смотреть: `docker compose exec api ls /app/dist`.

**В: Форма заявки на клиентском домене не работает.**  
О: Проверьте, что Caddy dynamic vhost проксирует `POST /api/v1/leads/public` к API, а сайт был пересобран после настройки домена. Этот путь реализован, но должен быть подтверждён public Docker/Caddy smoke на вашем hostname.

**В: Клиентский домен и панель на одном IP — нормально?**  
О: Да. Caddy различает их по имени хоста (Host).

### Данные и обновления

**В: Как часто бэкапить?**  
О: Базу — ежедневно минимум. Перед каждым обновлением с миграциями — обязательно внеочередной бэкап.

**В: Сделал `down -v` — можно откатить?**  
О: Только из бэкапа. Иначе нет.

**В: После `git pull` панель старая.**  
О: Пересоберите образы: `up -d --build`. Для panel это критично.

**В: Нужно ли сидить demo на staging VPS?**  
О: Можно на закрытом staging (IP whitelist / VPN). На публичном — нет.

### ИИ и лиды

**В: Обязательны ключи нейросетей?**  
О: Нет. Экспериментальные AI-generation endpoints не входят в первый release; сайты собираются из подготовленных оператором данных, блоков и manifest. Versioned LLM generation появится после отдельного QA pipeline.

**В: Где лежат персональные данные лидов?**  
О: В PostgreSQL в зашифрованном виде. Ключ — в `.env`. Бэкапы БД = бэкапы ПДн → храните как секреты.

**В: Что такое DSAR?**  
О: Запрос субъекта данных на выгрузку/удаление. Публичный DSAR endpoint намеренно выключен до browser/worker/retention integration proof; обрабатывайте такие запросы по утверждённой ручной процедуре.

### Прочее

**В: Это уже 100% по ТЗ?**  
О: Нет. Читайте [`docs/ХОД-РАБОТ.md`](./docs/ХОД-РАБОТ.md) §5 (SERP mock, FIAS sample, IaC stubs и др.).

**В: Куда писать, что поменяли на проде?**  
О: В журнал `docs/ХОД-РАБОТ.md` §7 + свой внутренний runbook.

**В: Есть ли готовый Ansible «нажал и забыл»?**  
О: Пока заготовка-stub. Основной путь — Docker Compose + эта инструкция.

**В: Как связаться / security.txt?**  
О: Смотрите `docs/security/security.txt`; до публикации замените placeholders реальными контактами. Compliance endpoint в first-release API не публикуется.

---

## 17. Целевой чеклист перед допуском людей (после закрытия P0)

- [ ] Ubuntu/Debian, Docker работает  
- [ ] `./УСТАНОВКА.sh` (режим vps) или `./scripts/install.sh --mode vps` успешен
- [ ] `.env` с сильными секретами, `chmod 600`, `APP_ENV=production`  
- [ ] `PANEL_PUBLIC_URL` / `API_PUBLIC_URL` / `CORS_ORIGINS` = ваши HTTPS  
- [ ] Firewall: снаружи только SSH + 80 + 443  
- [ ] Postgres, Redis, Caddy Admin **не** в открытом интернете  
- [ ] Свой админ создан, **demo нет**  
- [ ] MFA включена и подтверждена  
- [ ] HTTPS panel и api открываются  
- [ ] Создан единственный оператор → сайт → build → домен открывается
- [ ] Тестовый лид доходит в inbox  
- [ ] Ежедневный бэкап БД настроен и проверен restore на учебном стенде  
- [ ] Диск мониторится  
- [ ] Понимаете ограничения из `docs/ХОД-РАБОТ.md` §5  

Даже если операционные пункты отмечены, текущий снимок нельзя пускать к реальным клиентам до закрытия всех P0 из единого паспорта и успешного DB/Docker/browser/lead end-to-end теста.

---

## 18. Целевой план первого production-дня (после закрытия P0)

1. Harden SSH + ufw.  
2. Клон в `/opt/site-panel`.  
3. `./УСТАНОВКА.sh` → режим vps (или `./scripts/install.sh --mode vps --skip-seed`).
4. Дописать домены в `.env`, доработать Caddyfile, закрыть лишние порты.  
5. Админ + MFA.  
6. Бэкап cron.  
7. Первый сайт → build → DNS клиента.  
8. Проверка лида.  
9. Внешний smoke / k6.  
10. Запись в журнал и свой внутренний чеклист.

---

## 19. Ссылки внутри репозитория

| Что | Путь |
|-----|------|
| Локальная установка | [`README-LOCAL.md`](./README-LOCAL.md) |
| Обзор | [`README.md`](./README.md) |
| Журнал / ограничения | [`docs/ХОД-РАБОТ.md`](./docs/ХОД-РАБОТ.md) |
| Disaster recovery | [`docs/runbooks/disaster-recovery.md`](./docs/runbooks/disaster-recovery.md) |
| GitHub deploy/recovery | [`docs/runbooks/github-deploy-recovery.md`](./docs/runbooks/github-deploy-recovery.md) |
| Compose | [`infra/docker/docker-compose.yml`](./infra/docker/docker-compose.yml) |
| Caddy | [`infra/caddy/Caddyfile`](./infra/caddy/Caddyfile) |
| Пример env | [`.env.example`](./.env.example) |
| k6 | [`infra/k6/`](./infra/k6/) |
| security.txt | [`docs/security/security.txt`](./docs/security/security.txt) |
