# Site Panel на VPS: подробная установка для человека без опыта

Этот документ объясняет, как подготовить отдельный Linux-сервер (**VPS**) и установить на него Site Panel — панель для одного оператора, который создаёт и публикует сайты, принимает лиды и управляет ими.

Инструкция намеренно написана подробно. Не нужно разбираться в Docker, Linux или базах данных заранее: просто выполняйте шаги по порядку, не пропускайте предупреждения и не вставляйте пароли или секреты туда, где этого не просит инструкция.

> **Текущий статус на 2026-09-18: release candidate.** Installer автоматизирует большую часть подготовки VPS и первый запуск, но продукт ещё не доказан как готовый к публичной production-эксплуатации. До реальных клиентов нужно пройти staging-проверку: Docker/Compose, PostgreSQL/RLS, Caddy/HTTPS, браузерный вход, клиентский домен, лид-форма, webhook и восстановление из backup. Полный список ограничений: [docs/ХОД-РАБОТ.md](./docs/ХОД-РАБОТ.md).
>
> Успешная команда установки означает только: **скрипт выполнил доступные ему локальные шаги на этом сервере**. Она не означает автоматически, что DNS уже распространился, сертификат выдан, TOTP подтверждён, backup можно восстановить или заявки с клиентских сайтов доходят до CRM.

---

## Содержание

1. [Что вы устанавливаете](#1-что-вы-устанавливаете)
2. [Словарь простых терминов](#2-словарь-простых-терминов)
3. [Что делает installer, а что остаётся за вами](#3-что-делает-installer-а-что-остаётся-за-вами)
4. [Требования к серверу](#4-требования-к-серверу)
5. [Что подготовить до входа на VPS](#5-что-подготовить-до-входа-на-vps)
6. [Шаг 1. Подготовить домены и DNS](#6-шаг-1-подготовить-домены-и-dns)
7. [Шаг 2. Подключиться к серверу](#7-шаг-2-подключиться-к-серверу)
8. [Шаг 3. Скачать код](#8-шаг-3-скачать-код)
9. [Шаг 4. Создать безопасный public-config](#9-шаг-4-создать-безопасный-public-config)
10. [Шаг 5. Запустить автоматическую установку](#10-шаг-5-запустить-автоматическую-установку)
11. [Что будет спрашивать installer](#11-что-будет-спрашивать-installer)
12. [Шаг 6. Открыть панель и включить двухфакторную защиту](#12-шаг-6-открыть-панель-и-включить-двухфакторную-защиту)
13. [Как понять итоговый статус](#13-как-понять-итоговый-статус)
14. [Продолжение после ошибки или DNS-задержки](#14-продолжение-после-ошибки-или-dns-задержки)
15. [Firewall и SSH: включать только после проверки доступа](#15-firewall-и-ssh-включать-только-после-проверки-доступа)
16. [GitHub-обновления и rollback](#16-github-обновления-и-rollback)
17. [Резервные копии и восстановление](#17-резервные-копии-и-восстановление)
18. [Ежедневные команды оператора](#18-ежедневные-команды-оператора)
19. [Проверка перед реальной эксплуатацией](#19-проверка-перед-реальной-эксплуатацией)
20. [Разбор частых проблем](#20-разбор-частых-проблем)
21. [Вопросы и ответы](#21-вопросы-и-ответы)
22. [Что нельзя делать](#22-что-нельзя-делать)

---

## 1. Что вы устанавливаете

После установки на одном VPS будут работать несколько частей системы:

```text
Интернет
   │
   ▼
Caddy — веб-вход на портах 80 и 443
   │
   ├── Панель Site Panel — интерфейс оператора в браузере
   ├── API — серверная часть панели
   ├── Worker — фоновые повторные отправки webhook лидов
   ├── PostgreSQL — база данных
   ├── Redis — внутренняя очередь задач
   └── HTML-файлы опубликованных клиентских сайтов
```

Снаружи должны быть доступны только:

- SSH-порт сервера — обычно `22`, чтобы вы могли администрировать VPS;
- порт `80` — нужен для первичной проверки домена и выпуска HTTPS-сертификатов;
- порт `443` — обычный защищённый HTTPS-доступ к панели и сайтам.

PostgreSQL, Redis, API, интерфейс панели на техническом порту и Caddy Admin API не должны быть открыты в интернет. В production Compose они находятся во внутренней Docker-сети.

Первый выпуск рассчитан на **одного оператора**. Внутренний `tenant_id` существует только ради совместимости текущей схемы данных; это не multi-tenant SaaS и не панель для нескольких независимых аккаунтов.

---

## 2. Словарь простых терминов

|Термин|Простое объяснение|
|---|---|
|VPS|Удалённый компьютер в дата-центре. Вы арендуете его, а подключаетесь через интернет.|
|Linux|Операционная система на VPS. Инструкция поддерживает Debian 12 и Ubuntu 22.04/24.04.|
|SSH|Безопасное подключение к серверу через терминал.|
|Домен|Имя в интернете, например `example.ru`.|
|DNS|Справочник интернета: говорит, на какой IP-адрес ведёт домен.|
|HTTPS|Защищённое соединение с замком в браузере.|
|Caddy|Программа, которая принимает HTTP/HTTPS-запросы и раздаёт панель/сайты.|
|Docker|Способ запускать компоненты приложения в изолированных контейнерах.|
|Docker Compose|Файл с описанием всех контейнеров, сетей и хранилищ приложения.|
|Container|Изолированный запущенный процесс приложения.|
|Volume|Постоянное хранилище Docker. Данные в volume остаются после перезапуска контейнера.|
|`.env`|Локальный файл настроек и секретов. Его нельзя публиковать или отправлять третьим лицам.|
|Secret|Пароль, ключ шифрования, токен, API key или другой приватный доступ.|
|TOTP / MFA|Код из приложения-аутентификатора на телефоне. Это второй фактор входа.|
|Webhook|Подписанное HTTP-уведомление о новом лиде в вашу CRM или другой сервис.|
|Backup|Зашифрованная резервная копия данных.|
|Restic|Программа, которая создаёт и хранит зашифрованные off-host backups.|
|Off-host|Хранение не на самом VPS, а в отдельном объектном хранилище.|
|Immutable release|Неизменяемая папка с точной версией кода. Обновления создают новую папку, а не меняют старую.|
|Rollback|Возврат к предыдущей рабочей версии кода.|
|Staging|Отдельная тестовая среда, похожая на production, но без реальных клиентов.|

---

## 3. Что делает installer, а что остаётся за вами

Главный сценарий установки запускается этой программой:

```text
scripts/install-production-vps.sh
```

### Что installer делает автоматически

Installer может:

1. проверить, что VPS работает на поддерживаемой Linux-системе;
2. установить Docker Engine, Docker Compose v2, restic и нужные системные программы;
3. создать отдельного системного пользователя `sitepanel-deploy`;
4. создать защищённую структуру папок `/opt/site-panel`;
5. создать или сохранить production `.env` с сильными секретами;
6. проверить согласованность доменов, HTTPS-адресов и CORS;
7. создать Docker volumes и проверить, что API и Caddy смогут записывать нужные данные;
8. подготовить зашифрованный backup через restic;
9. при наличии двух public SSH keys подготовить ограниченный GitHub deploy/recovery gateway;
10. собрать первый release из точного Git commit;
11. применить миграции базы данных;
12. поднять **только production Compose**;
13. создать первый encrypted backup;
14. проверить локальное здоровье сервисов, DNS и HTTPS;
15. интерактивно создать единственного оператора;
16. записать non-secret состояние установки, чтобы продолжить после временной ошибки.

### Что installer не может сделать за вас

Есть решения, которые нельзя безопасно угадать. Их нужно подготовить или подтвердить вручную:

|Что нужно от вас|Почему это нельзя безопасно придумать автоматически|
|---|---|
|Домены панели и API|Только вы решаете, какие домены принадлежат вам и как будут называться.|
|DNS-записи|Только у вас есть доступ к регистратору домена или DNS-провайдеру.|
|Email для Caddy/ACME|Это контакт для сертификатов HTTPS.|
|Ваш SSH public key и допустимый IP/CIDR|Без этого автоматическое ужесточение SSH может отрезать вам доступ к серверу.|
|Off-host backup repository|Это ваше отдельное хранилище, бюджет, регион и политика хранения.|
|Ключи доступа к S3-совместимому storage|Такие ключи принадлежат вашей учётной записи облачного хранилища.|
|Пароль restic|Он нужен для расшифровки backup. Его нужно хранить отдельно от VPS.|
|Пароль первого оператора|Он вводится скрыто в терминале и не передаётся через аргументы командной строки.|
|Подтверждение TOTP|Код должен быть только в приложении-аутентификаторе на вашем телефоне.|
|GitHub private keys и Environment policy|Private keys нельзя размещать на VPS; approvals и known_hosts — внешний trust boundary.|
|Webhook receiver и secret|Адрес и секрет определяются вашей CRM или другой внешней системой.|

### Что installer намеренно не делает

Installer не:

- создаёт demo-пользователя;
- включает публичную регистрацию;
- добавляет второго оператора;
- отправляет ваши secrets в Git;
- передаёт пароль оператора через URL или HTTP API;
- кладёт GitHub private keys на VPS;
- запускает `git pull` в работающем release;
- удаляет Docker volumes;
- запускает `docker compose down -v`;
- автоматически восстанавливает PostgreSQL из backup;
- автоматически меняет ключи шифрования;
- включает firewall или отключает парольный SSH-вход без отдельного явного шага.

---

## 4. Требования к серверу

### Поддерживаемая ОС

Выберите один из вариантов при покупке VPS:

- **Debian 12**;
- **Ubuntu 22.04 LTS**;
- **Ubuntu 24.04 LTS**.

Не используйте Windows Server, shared hosting, обычный PHP-хостинг, старые Ubuntu/Debian или неподтверждённые дистрибутивы для этого сценария.

### Минимальная конфигурация

|Для чего|CPU|RAM|Диск|Комментарий|
|---|---:|---:|---:|---|
|Тестовый staging|2 ядра|4 GB|40 GB SSD|Только для проверки сценария.|
|Небольшой production после release gate|4 ядра|8 GB|80–100 GB SSD|Запас для сайтов, образов и backup cache.|
|Несколько десятков сайтов|4+ ядер|16 GB|100+ GB SSD|Нужны мониторинг диска и отдельный план масштабирования.|

Нужен публичный IPv4. IPv6 — полезное дополнение, но не замена IPv4, если ваши пользователи и DNS ещё не готовы к IPv6.

### Что должно быть доступно на VPS

- root-доступ или пользователь с `sudo`;
- исходящий интернет для `apt`, Docker registry, ACME и backup storage;
- свободные порты `80` и `443`;
- возможность входа через SSH;
- желательно — web console у VPS-провайдера: она поможет, если вы неверно настроите SSH/firewall;
- достаточное место на диске для Docker images, базы, собранных сайтов и временных backup-данных.

### Что выбрать у VPS-провайдера

Если вы видите в панели провайдера эти опции, включите или подготовьте их:

- snapshot диска — полезен перед крупными изменениями;
- serial/web console — аварийный доступ, если SSH перестал работать;
- reverse DNS — обычно не обязателен для этой панели;
- отдельный firewall провайдера — можно дополнительно ограничить входящие порты до `22`, `80`, `443`;
- мониторинг CPU/RAM/disk — полезен, но не заменяет backups.

---

## 5. Что подготовить до входа на VPS

Поставьте галочки до начала установки:

- [ ] VPS на Debian 12 или Ubuntu 22.04/24.04.
- [ ] IP-адрес VPS.
- [ ] Домен, которым вы управляете.
- [ ] Два свободных поддомена: один для панели, один для API.
- [ ] Доступ к DNS-настройкам домена.
- [ ] SSH public key для вашего доступа к VPS.
- [ ] Приложение-аутентификатор на телефоне: Aegis, 1Password, Google Authenticator, Microsoft Authenticator или аналог.
- [ ] Отдельное off-host хранилище для backup: S3-compatible storage или другое поддерживаемое restic-хранилище.
- [ ] Пароль для restic, сохранённый в password manager или другом безопасном месте вне VPS.
- [ ] Email, который вы будете использовать для учётной записи оператора.
- [ ] Сильный уникальный пароль оператора длиной не менее 10 символов.
- [ ] URL вашего Git repository, если код ещё не находится на VPS.

Если планируете GitHub-managed updates, дополнительно нужны:

- [ ] две отдельные SSH key pairs: deploy и recovery;
- [ ] GitHub Environments `production-deploy` и `production-recovery`;
- [ ] возможность добавить protected Environment variables/secrets;
- [ ] заранее проверенный SSH host fingerprint/known_hosts VPS.

Подробности GitHub-контура: [docs/runbooks/github-deploy-recovery.md](./docs/runbooks/github-deploy-recovery.md).

---

## 6. Шаг 1. Подготовить домены и DNS

### Зачем нужны два домена

Рекомендуемый вариант:

```text
panel.example.ru  — интерфейс Site Panel
api.example.ru    — прямой API-адрес для диагностических задач
```

Панель работает с API через тот же адрес `panel.example.ru/api/...`, поэтому браузерный интерфейс не обязан обращаться напрямую к `api.example.ru`. Второй адрес нужен для явной API-точки и диагностики.

### Какие DNS-записи создать

У регистратора домена или DNS-провайдера добавьте две записи типа **A**:

|Имя|Тип|Значение|
|---|---|---|
|`panel`|A|Публичный IPv4 вашего VPS|
|`api`|A|Публичный IPv4 вашего VPS|

Если у VPS есть корректно настроенный IPv6 и вы понимаете, как проверять его доступность, добавьте также AAAA-записи. Если не уверены — начните только с A-записей.

### Пример

Если:

```text
Ваш домен: example.ru
IP VPS: 203.0.113.10
```

то должны появиться:

```text
panel.example.ru → 203.0.113.10
api.example.ru   → 203.0.113.10
```

`203.0.113.10` в примере — учебный адрес. Подставьте фактический IP из панели вашего VPS-провайдера.

### Как проверить DNS

С вашего компьютера или с VPS:

```bash
nslookup panel.example.ru
nslookup api.example.ru
```

или:

```bash
dig +short panel.example.ru
dig +short api.example.ru
```

В ответе должен быть IP вашего VPS.

DNS может распространяться от нескольких минут до нескольких часов. Если installer сообщает `pending dns_not_ready`, это обычно не поломка установки: дождитесь обновления DNS и повторите сценарий из раздела [Продолжение после ошибки или DNS-задержки](#14-продолжение-после-ошибки-или-dns-задержки).

### Не включайте проксирование CDN без понимания режима

Если используете Cloudflare или другой CDN/WAF, сначала решите, должен ли DNS вести **напрямую** на VPS или через proxy. ACME/HTTPS-проверки и source IP policy при проксировании могут отличаться. Для первого staging drill проще направить A-записи напрямую на VPS, а CDN подключать после отдельной проверки.

---

## 7. Шаг 2. Подключиться к серверу

### Windows: PowerShell или Windows Terminal

Откройте PowerShell и выполните:

```powershell
ssh root@IP_ВАШЕГО_VPS
```

Например:

```powershell
ssh root@203.0.113.10
```

Если провайдер выдал не root, а пользователя с sudo:

```powershell
ssh имя-пользователя@IP_ВАШЕГО_VPS
```

После входа проверьте, кто вы:

```bash
whoami
```

Для root ожидается:

```text
root
```

Для sudo-пользователя проверьте:

```bash
sudo -v
```

### macOS и Linux

Откройте Terminal и используйте ту же команду:

```bash
ssh root@IP_ВАШЕГО_VPS
```

### Первый вход по fingerprint

При первом подключении SSH может спросить, доверяете ли вы fingerprint сервера. Сверьте fingerprint с тем, который показывает VPS-провайдер в панели или web console. Не подтверждайте его вслепую, если IP-адрес или fingerprint неожиданно отличаются.

---

## 8. Шаг 3. Скачать код

Installer требует **Git checkout** без незакоммиченных tracked изменений. Это важно: первый immutable release собирается из точного commit SHA, а не из случайных файлов на диске.

### Вариант A: код находится в Git repository

На VPS создайте рабочую папку и клонируйте repository:

```bash
cd /opt
sudo git clone <URL_ВАШЕГО_REPOSITORY> site-panel-bootstrap
cd /opt/site-panel-bootstrap
```

Проверьте состояние:

```bash
git status --short
git rev-parse HEAD
```

Первая команда не должна печатать ничего. Вторая напечатает длинный идентификатор текущей версии кода.

Если repository private, настройте доступ к нему безопасно: deploy key, GitHub CLI или другой approved способ. Не вставляйте personal access token в публичные команды, README, shell history или `.env` Site Panel.

### Вариант B: вы уже скопировали код на VPS

Перейдите в папку кода и убедитесь, что это Git checkout:

```bash
cd /ПУТЬ/К/ПРОЕКТУ
git status --short
git rev-parse HEAD
```

Если команда `git rev-parse HEAD` сообщает ошибку, installer не сможет создать проверяемый initial release. Сначала получите repository нормальным способом.

### Почему нельзя просто скопировать папку и запустить

В рабочей папке могут случайно быть:

- `.env` с секретами;
- старые Docker volumes или data;
- временные файлы;
- локальные правки, которые не прошли тесты;
- служебные файлы IDE/агентов.

`git archive <SHA>` включает только файлы конкретного commit. Поэтому initial release можно воспроизвести и проверить позже.

---

## 9. Шаг 4. Создать безопасный public-config

Installer должен знать публичные имена панели/API и email для HTTPS-сертификата. Эти данные не являются паролями, но их всё равно удобно хранить в отдельном файле с ограниченным доступом.

На VPS выполните:

```bash
sudo install -m 600 /dev/null /root/site-panel-public.env
sudo nano /root/site-panel-public.env
```

В редакторе вставьте и замените значения:

```dotenv
PANEL_DOMAIN=panel.example.ru
API_DOMAIN=api.example.ru
CADDY_EMAIL=you@example.ru
SSH_PORT=22
```

Если вы уверены, что SSH должен быть доступен только с конкретной сети, можно добавить:

```dotenv
TRUSTED_SSH_CIDR=203.0.113.0/24
```

Пока не уверены в CIDR — не добавляйте эту строку. Firewall по умолчанию всё равно не включается.

### Что означает каждая строка

|Переменная|Что написать|
|---|---|
|`PANEL_DOMAIN`|Полный домен панели, например `panel.example.ru`.|
|`API_DOMAIN`|Полный домен API, например `api.example.ru`. Он должен отличаться от домена панели.|
|`CADDY_EMAIL`|Ваш реальный email для технических сообщений о сертификатах.|
|`SSH_PORT`|Порт SSH VPS. Обычно `22`. Не меняйте, если не знаете, что изменяли SSH у провайдера.|
|`TRUSTED_SSH_CIDR`|Необязательная разрешённая сеть для SSH, например офисный статический IP. Не указывайте динамический домашний IP как постоянное правило.|

### Что нельзя писать в этот файл

Installer отклонит ключи, похожие на секреты. Не добавляйте:

```dotenv
POSTGRES_PASSWORD=...
APP_SECRET_KEY=...
RESTIC_PASSWORD=...
AWS_SECRET_ACCESS_KEY=...
TOKEN=...
PRIVATE_KEY=...
```

Пароли и ключи вводятся только в подходящей защищённой фазе либо генерируются на VPS. Public-config — не замена `.env` и не backup credential file.

### Как сохранить файл в nano

1. Нажмите `Ctrl+O`.
2. Нажмите `Enter`, чтобы подтвердить имя файла.
3. Нажмите `Ctrl+X`, чтобы выйти.

Проверьте права:

```bash
sudo stat -c '%a %n' /root/site-panel-public.env
```

Ожидаемый результат начинается с:

```text
600 /root/site-panel-public.env
```

---

## 10. Шаг 5. Запустить автоматическую установку

Перейдите в папку Git checkout:

```bash
cd /opt/site-panel-bootstrap
```

Запустите основной сценарий:

```bash
sudo bash scripts/install-production-vps.sh \
  --source-dir "$PWD" \
  --config-file /root/site-panel-public.env
```

### Что происходит во время запуска

Installer выполняет фазы по порядку:

|Фаза|Что происходит|Можно ли запускать повторно?|
|---|---|---|
|`preflight`|Проверка ОС, systemd, root/sudo, портов 80/443, public-config и блокировки параллельных запусков.|Да.|
|`packages`|Установка системных пакетов, Docker, Compose, restic, fail2ban и unattended security updates.|Да.|
|`layout`|Создание `sitepanel-deploy` и защищённых каталогов `/opt/site-panel`.|Да.|
|`env`|Создание/проверка `/opt/site-panel/shared/.env` с production settings.|Да, secrets сохраняются.|
|`backup`|Настройка restic repository и backup credentials.|Да, но не заменяйте credentials без плана восстановления.|
|`volumes`|Создание Docker volumes и проверка прав записи для Caddy/API.|Да.|
|`github`|Установка ограниченного GitHub deploy/recovery gateway, если переданы обе public keys.|Да.|
|`release`|Создание archive из текущего commit, миграции и запуск production stack.|Да, после исправления причины ошибки.|
|`verify`|Проверка локального health, DNS и public HTTPS/same-origin API.|Да.|
|`operator`|Создание единственного оператора и ручное подтверждение TOTP.|Да, если эта фаза осталась pending.|

Не закрывайте SSH-окно во время работы. Первая сборка Docker images может занимать от нескольких минут до получаса и больше — зависит от CPU VPS и скорости сети.

### Важно: installer не выполняет полный `apt upgrade`

Installer ставит необходимые пакеты, но не делает неконтролируемое обновление всей ОС. Это безопаснее: обновление ядра или системных библиотек может требовать осознанного окна обслуживания и перезагрузки VPS.

### Что не нужно делать во время установки

Не запускайте параллельно:

```bash
docker compose down
docker system prune -a
git pull
reboot
```

Не открывайте вторую установку в другом SSH-окне. Installer использует lock, но лучше не создавать конкурирующие изменения.

---

## 11. Что будет спрашивать installer

### 11.1. Restic repository

Installer спросит:

```text
Restic repository:
```

Это место, где будут храниться **зашифрованные** резервные копии. Оно должно быть вне VPS. Не используйте папку на том же сервере как единственный backup: при потере VPS вы потеряете и приложение, и копии.

Пример формата S3-compatible repository:

```text
s3:https://s3.example-storage.net/site-panel-production
```

Конкретный формат зависит от вашего провайдера. Сверяйтесь с документацией вашего storage и restic.

### 11.2. Пароль restic

Installer спросит скрыто:

```text
New/existing restic password:
```

Символы не будут отображаться — это нормально. Введите длинный уникальный пароль, минимум 16 символов.

**Сохраните этот пароль отдельно от VPS.** Например, в password manager. Если потерять и VPS, и restic password, восстановить backup будет невозможно даже при наличии access key.

### 11.3. S3 access key и secret

Если repository начинается с `s3:`, installer спросит:

```text
S3 access key ID:
S3 secret access key:
```

Создайте для Site Panel отдельный access key с минимальными правами только к выделенному bucket/prefix backup. Не используйте master/admin key от всего облачного аккаунта.

Эти данные сохраняются только на VPS в:

```text
/opt/site-panel/shared/backup.env
```

и не попадают в Git release archive.

### 11.4. Данные первого оператора

После успешной проверки HTTPS installer запускает безопасный bootstrap внутри production контейнера. Он спросит:

```text
Operator email:
Operator name [Site Panel Operator]:
Operator password:
```

- Email — ваш email для входа в панель.
- Name — отображаемое имя; можно оставить стандартное, просто нажав `Enter`.
- Password — скрытый ввод; требуется не менее 10 символов.

Не добавляйте пароль в команду вида:

```bash
# НЕЛЬЗЯ
... --password MyPassword123
```

и не сохраняйте его в текстовом файле рядом с проектом.

### 11.5. Подтверждение TOTP

После входа в браузере настройте TOTP, затем installer попросит:

```text
TOTP confirmed in an authenticator? [YES/no]
```

Введите ровно:

```text
YES
```

только после того, как:

1. открыли панель по HTTPS;
2. вошли под созданным оператором;
3. открыли раздел безопасности/настроек;
4. отсканировали QR-код приложением-аутентификатором;
5. подтвердили одноразовый код из приложения;
6. убедились, что следующий вход требует TOTP.

Installer сохраняет только факт вашего ручного подтверждения, а не QR-код, TOTP secret или одноразовые коды. Это **attestation**, а не техническое доказательство работы MFA.

---

## 12. Шаг 6. Открыть панель и включить двухфакторную защиту

После того как DNS и HTTPS прошли, откройте в браузере:

```text
https://panel.example.ru
```

Подставьте ваш реальный `PANEL_DOMAIN`.

### Что должно быть видно

- адрес начинается с `https://`;
- браузер показывает значок замка без предупреждения о сертификате;
- открывается форма входа Site Panel;
- нет заранее заполненных demo credentials;
- после ввода email/password вы попадаете в панель;
- после настройки TOTP следующий вход просит код из authenticator.

### Если браузер показывает ошибку сертификата

Не игнорируйте предупреждение и не нажимайте «всё равно продолжить» для production. Проверьте:

1. DNS A-запись `panel` ведёт на IP VPS;
2. порты 80 и 443 доступны извне;
3. Caddy container работает;
4. email и домен в public-config указаны без ошибок;
5. DNS успел распространиться;
6. домен не занят старым proxy/CDN configuration.

Смотрите раздел [HTTPS не открывается](#https-не-открывается-или-браузер-ругается-на-сертификат).

---

## 13. Как понять итоговый статус

Installer хранит non-secret checkpoints здесь:

```text
/opt/site-panel/shared/installer-state.json
```

Посмотреть файл можно так:

```bash
sudo cat /opt/site-panel/shared/installer-state.json
```

В нём не должно быть паролей, ключей, email оператора, TOTP secret или содержимого `.env`.

### Основные статусы

|Статус|Что это означает|Что делать|
|---|---|---|
|`completed`|Конкретная фаза завершилась и сохранила ожидаемое локальное evidence.|Переходить к следующей фазе или проверке.|
|`pending_restore_drill`|Restic repository и initial backup готовы, но восстановление на другом VPS ещё не доказано.|Запланировать disposable staging restore drill.|
|`pending_external_environment`|GitHub public keys не переданы или GitHub Environment ещё не настроен.|Настроить GitHub secrets/known_hosts/review policy отдельно.|
|`pending dns_not_ready`|Домен ещё не резолвится на VPS из точки проверки.|Проверить A/AAAA и подождать DNS propagation.|
|`pending public_tls_or_proxy_not_ready`|DNS есть, но HTTPS или same-origin API не прошли.|Проверить Caddy, firewall, порты 80/443 и ACME.|
|`pending interactive_operator_required`|Для создания оператора нужен живой терминал.|Запустить `--resume` из SSH с интерактивным TTY.|
|`pending totp_attestation_required`|Оператор создан, но вы не подтвердили TOTP.|Настроить MFA в браузере и повторить фазу operator.|
|`pending awaiting_second_key_session`|SSH hardening включён временно, нужно подтвердить второй key-based вход.|Открыть вторую SSH session, проверить вход и отменить rollback timer.|

### Где смотреть технический статус release

```bash
export SITE_PANEL_ROOT=/opt/site-panel
sudo -u sitepanel-deploy "$SITE_PANEL_ROOT/bin/release-manager.sh" status
```

Пример смыслов:

```text
current_release=<SHA>     # какая версия кода активна
previous_release=<SHA>    # предыдущая рабочая версия
health=ok                 # внутренний API ответил успешно
last_backup_snapshot=<ID> # идентификатор последнего backup
```

`health=ok` — полезный локальный признак, но он не заменяет проверку браузером, HTTPS, лид-формы или restore drill.

---

## 14. Продолжение после ошибки или DNS-задержки

Installer сделан идемпотентным: его можно запускать повторно, не удаляя данные и не меняя существующие сильные secrets.

### Обычное продолжение всех незавершённых шагов

```bash
cd /opt/site-panel-bootstrap
sudo bash scripts/install-production-vps.sh \
  --source-dir "$PWD" \
  --config-file /root/site-panel-public.env \
  --resume
```

### Повторить только проверку DNS/HTTPS

```bash
sudo bash scripts/install-production-vps.sh \
  --source-dir "$PWD" \
  --config-file /root/site-panel-public.env \
  --phase verify
```

### Повторить только создание оператора/TOTP-attestation

```bash
sudo bash scripts/install-production-vps.sh \
  --source-dir "$PWD" \
  --config-file /root/site-panel-public.env \
  --phase operator
```

### Повторить только настройку backup

```bash
sudo bash scripts/install-production-vps.sh \
  --source-dir "$PWD" \
  --config-file /root/site-panel-public.env \
  --phase backup
```

### Важно перед повтором

Повторный запуск безопасен только при обычной ошибке или ожидании внешнего условия. Не используйте повторный installer как способ:

- поменять домен активного production без плана миграции;
- заменить пароль PostgreSQL вручную;
- заменить `FIELD_ENCRYPTION_KEY`, `APP_PEPPER` или `BLIND_INDEX_PEPPER`;
- удалить и создать базу заново;
- обойти GitHub CI;
- «починить» Docker через удаление volumes.

Если не понимаете причину ошибки, сначала сохраните текст ошибки и посмотрите логи, а не удаляйте данные.

---

## 15. Firewall и SSH: включать только после проверки доступа

Эти шаги полезны, но опасны: неправильное правило способно закрыть вам доступ к VPS. Поэтому installer не выполняет их по умолчанию.

### 15.1. Firewall (UFW)

Перед включением убедитесь:

- у вас есть рабочая SSH session;
- есть web/serial console у VPS-провайдера на случай ошибки;
- вы знаете настоящий SSH port;
- у вас есть проверенный SSH key-based вход;
- вы не используете случайный динамический IP как постоянный `TRUSTED_SSH_CIDR`.

Запустите только firewall phase:

```bash
cd /opt/site-panel-bootstrap
sudo bash scripts/install-production-vps.sh \
  --source-dir "$PWD" \
  --config-file /root/site-panel-public.env \
  --phase firewall \
  --enable-firewall
```

Installer делает операции в безопасном порядке:

1. разрешает текущий/указанный SSH port;
2. при наличии `TRUSTED_SSH_CIDR` добавляет ограниченное правило;
3. разрешает `80/tcp` и `443/tcp`;
4. устанавливает default deny для входящих подключений;
5. устанавливает default allow для исходящих;
6. включает UFW;
7. показывает итоговые правила.

Проверьте их:

```bash
sudo ufw status verbose
```

Ожидаемо должны быть разрешены SSH, `80/tcp` и `443/tcp`.

### 15.2. SSH hardening

SSH hardening отключает вход root по SSH и парольный вход. Делайте это **только после того, как key-based доступ проверен из второй SSH session**.

Запуск:

```bash
cd /opt/site-panel-bootstrap
sudo bash scripts/install-production-vps.sh \
  --source-dir "$PWD" \
  --config-file /root/site-panel-public.env \
  --phase ssh-hardening \
  --harden-ssh
```

Installer:

- создаёт `/etc/ssh/sshd_config.d/90-site-panel-hardening.conf`;
- проверяет синтаксис через `sshd -t`;
- применяет настройку;
- создаёт automatic rollback через 10 минут.

Сразу откройте **новый** терминал и попробуйте войти по ключу. Только если новый вход работает, отмените автоматический rollback командой, которую напечатает installer:

```bash
sudo systemctl cancel site-panel-ssh-hardening-rollback
```

Если новый вход не работает — не отменяйте rollback. Через 10 минут настройки SSH должны вернуться к прежнему состоянию. Используйте provider console для диагностики.

### Почему deploy account добавлен в Docker group

`sitepanel-deploy` нужен для immutable release manager, Docker Compose и scheduled backup. Docker group фактически даёт высокий уровень контроля над хостом, близкий к root. Поэтому:

- не разрешайте интерактивный обычный SSH-вход этому аккаунту;
- используйте для GitHub только две ограниченные forced-command keys;
- не выдавайте этот аккаунт посторонним людям;
- не храните в его home случайные скрипты или credentials.

---

## 16. GitHub-обновления и rollback

### Почему не надо выполнять `git pull` на production

Если обновлять работающий сервер через `git pull`, легко получить смесь старого кода, новых миграций, случайных файлов и неясного состояния. Невозможно надёжно сказать, что именно работало до изменения и как безопасно вернуться назад.

Production path использует immutable releases:

```text
Проверенный commit SHA
        ↓
GitHub CI проходит успешно
        ↓
GitHub упаковывает точный release archive
        ↓
VPS получает archive через ограниченный SSH gateway
        ↓
Release manager делает pre-deploy backup
        ↓
Новый code release запускается и проверяется
        ↓
Успех: current → новый SHA, previous → старый SHA
Ошибка health: code rollback к previous
```

Rollback возвращает **код**, но не автоматически базу данных. Поэтому каждая migration должна быть совместима с предыдущим release в пределах окна rollback.

### Подготовка двух GitHub keys

На доверенной администраторской машине, не на VPS и не в GitHub runner, создайте две разные key pairs:

```bash
ssh-keygen -t ed25519 -a 100 -f site-panel-deploy -C "github-site-panel-deploy"
ssh-keygen -t ed25519 -a 100 -f site-panel-recovery -C "github-site-panel-recovery"
```

Назначение:

|Ключ|Где хранится private half|Что делает public half на VPS|
|---|---|---|
|Deploy|GitHub Environment `production-deploy`|Разрешает загрузить archive и активировать проверенный release.|
|Recovery|GitHub Environment `production-recovery`|Разрешает status/restart/rollback/controlled recovery/explicit restore.|

На VPS передаются только `.pub` файлы. Private keys должны оказаться только в защищённых GitHub Environment secrets.

### Запуск installer с public keys

```bash
sudo bash scripts/install-production-vps.sh \
  --source-dir "$PWD" \
  --config-file /root/site-panel-public.env \
  --deploy-public-key-file /root/site-panel-deploy.pub \
  --recovery-public-key-file /root/site-panel-recovery.pub
```

Ограниченный gateway не даёт этим GitHub keys:

- интерактивную shell;
- PTY;
- port forwarding;
- agent forwarding;
- X11 forwarding;
- произвольные команды;
- произвольную запись файлов через SCP.

### Управление обновлениями из панели

После настройки GitHub deploy/recovery контур можно дополнительно включить экраном **«Обновления»** в панели. Он не даёт панели shell-доступ к VPS: панель создаёт только строго ограниченный `workflow_dispatch` запрос в GitHub Actions, а дальше продолжают работать protected Environments, pinned SSH host key, forced-command gateway и `release-manager`.

#### 1. Создайте отдельный GitHub fine-grained token

Создайте token только для repository Site Panel. Минимально необходимы:

- **Actions: Read and write** — получить успешные CI releases и запустить fixed deploy/recovery workflows;
- **Contents: Read** — GitHub API/repository metadata, нужные workflow provenance check.

Не используйте personal token с доступом ко всем repositories, `admin:org`, `workflow` для всех проектов или SSH private key вместо token. Token не должен попадать в browser, GitHub Actions artifact, Git repository, screenshot или support chat.

#### 2. Запишите token только в VPS-local production env

Откройте файл на VPS:

```bash
sudo nano /opt/site-panel/shared/.env
```

Добавьте реальные значения:

```dotenv
GITHUB_REPOSITORY=owner/repository
GITHUB_CONTROL_TOKEN=<fine-grained-token>
GITHUB_API_URL=https://api.github.com
```

Замените `owner/repository` на имя GitHub repository. Сохраните файл, затем сохраните его restrictive permissions и перезапустите только API:

```bash
sudo chown sitepanel-deploy:sitepanel-deploy /opt/site-panel/shared/.env
sudo chmod 600 /opt/site-panel/shared/.env

RELEASE="$(readlink -f /opt/site-panel/current)"
sudo -u sitepanel-deploy docker compose \
  --project-name site-panel \
  --env-file "$RELEASE/.env" \
  -f "$RELEASE/infra/docker/docker-compose.production.yml" \
  up -d --force-recreate api
```

Никогда не добавляйте фактический `GITHUB_CONTROL_TOKEN` в `.env.production.example`, commit, release archive или frontend variables. Если token отсутствует, ручной путь через GitHub Actions остаётся единственным и безопасным способом обновления.

#### 3. Используйте экран «Обновления»

После входа под единственным оператором с подтверждённым TOTP:

1. откройте **Система → Обновления**;
2. убедитесь, что GitHub control показывает `настроен`;
3. выберите SHA только из списка successful CI commits в `main`;
4. введите `DEPLOY` и отправьте запрос;
5. при необходимости подтвердите protected Environment review в GitHub;
6. наблюдайте queued/in-progress/success/failure status и GitHub run link в панели.

Панель не может запросить branch name, arbitrary archive, arbitrary workflow или SHA без successful CI. Update запускает existing `Deploy production` workflow: он делает pre-deploy encrypted backup, build, health check и code rollback на `previous` при неудаче. Database restore при update невозможен.

#### Recovery из панели

В том же экране доступны строго allowlisted действия:

|Действие|Что делает|
|---|---|
|`STATUS`|Запрашивает current/previous SHA, health и последний backup snapshot.|
|`RESTART`|Контролируемо перезапускает API, worker, panel и Caddy.|
|`ROLLBACK`|Меняет только code release `current ↔ previous` с health probe.|
|`RECOVER`|Ограниченная последовательность restart → code rollback.|
|`RESTORE`|Заменяет данные строго выбранным restic snapshot после ввода snapshot ID и точного `RESTORE` confirmation.|

`RESTORE` — destructive операция. Перед ней workflow обязан создать новый encrypted pre-restore backup; если это не получается, restore не начинается. Ни GitHub schedule, ни panel button не могут автоматически выполнить PostgreSQL restore, schema downgrade, volume deletion или `docker compose down -v`.

Если панель или GitHub control недоступны, используйте ручные **Deploy production** / **Recover production** workflows в GitHub Actions или доверенную console/sudo procedure из runbook.

Installer не может и не должен без явного отдельного доверия создавать GitHub secrets. Настройте вручную:

- Environments `production-deploy` и `production-recovery`;
- required reviewers;
- branch policy для `main`;
- `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_PORT`, `DEPLOY_ROOT`;
- `DEPLOY_SSH_KEY` и проверенный `DEPLOY_KNOWN_HOSTS`;
- `RECOVERY_HOST`, `RECOVERY_USER`, `RECOVERY_PORT`;
- `RECOVERY_SSH_KEY` и проверенный `RECOVERY_KNOWN_HOSTS`;
- repository variable `PUBLIC_HEALTH_URL`.

Полная таблица и порядок: [docs/runbooks/github-deploy-recovery.md](./docs/runbooks/github-deploy-recovery.md).

---

## 17. Резервные копии и восстановление

### Что попадает в backup

Daily restic backup содержит:

- PostgreSQL custom dump;
- `sites_data` — опубликованные статические сайты;
- `uploads_data` — загруженные медиа/файлы;
- `caddy_data` — в том числе данные сертификатов Caddy;
- `caddy_config` — сохранённая конфигурация Caddy и dynamic routes;
- `/opt/site-panel/shared/.env`;
- release state и audit metadata;
- manifest с версией backup policy и перечнем volume-данных.

### Что намеренно не попадает в backup

`dsar_data` не архивируется. Это короткоживущие sensitive exports, а долговременный источник данных находится в PostgreSQL. При restore этот volume очищается, чтобы не переносить устаревшие выгрузки ПДн.

### Почему backup должен быть off-host

Backup на том же VPS не спасает, если:

- VPS удалён или сломан у провайдера;
- диск повреждён;
- сервер скомпрометирован;
- вы случайно удалили Docker volumes;
- дата-центр/аккаунт VPS недоступен.

Off-host storage отделяет копию данных от самого VPS.

### Где находятся backup settings

```text
/opt/site-panel/shared/backup.env
/opt/site-panel/shared/restic-password
```

Оба файла должны иметь mode `0600` и не должны попадать в Git, issue comments, screenshots, GitHub artifacts или мессенджеры.

`backup.env` — это не shell script. Он принимает только разрешённые literal `KEY=value` строки. Не добавляйте `export`, `$(...)`, команды, непонятные ключи или кавычки в надежде на shell interpolation: они не будут исполнены как код и могут быть отклонены.

### Проверить backup timer

```bash
sudo systemctl status site-panel-backup.timer
sudo systemctl list-timers | grep site-panel-backup
```

Посмотреть последний snapshot в состоянии release manager:

```bash
export SITE_PANEL_ROOT=/opt/site-panel
sudo -u sitepanel-deploy "$SITE_PANEL_ROOT/bin/release-manager.sh" status
```

### Восстановление — только осознанно

Restore заменяет данные. Он требует:

- конкретный snapshot ID;
- literal confirmation `RESTORE` в GitHub workflow;
- успешный pre-restore backup;
- участия оператора.

Никогда не используйте restore как первый способ исправить обычную ошибку приложения. Сначала проверьте status, restart и code rollback.

Новый безопасный порядок:

1. Зафиксировать incident time и current/previous release SHA.
2. Проверить, что проблема не решается restart или rollback.
3. Найти конкретный restic snapshot.
4. Убедиться, что похожий restore проходил на disposable staging VPS.
5. Запустить manual `Recover production → restore`.
6. Указать snapshot и `confirmation=RESTORE`.
7. После restore вручную проверить вход/MFA, панель, сайты, uploads, Caddy routes, lead form и webhook.

Подробная процедура: [docs/runbooks/disaster-recovery.md](./docs/runbooks/disaster-recovery.md).

### Важное ограничение

Успешный `restic init`, `restic check` или создание backup **не доказывают восстановление**. Только реальный restore на отдельном disposable VPS показывает, что пароль, repository, volumes, база и release действительно восстанавливаются вместе.

---

## 18. Ежедневные команды оператора

### Статус release и сервисов

```bash
export SITE_PANEL_ROOT=/opt/site-panel
sudo -u sitepanel-deploy "$SITE_PANEL_ROOT/bin/release-manager.sh" status
```

### Логи приложения

```bash
export SITE_PANEL_ROOT=/opt/site-panel
RELEASE="$(readlink -f "$SITE_PANEL_ROOT/current")"
sudo -u sitepanel-deploy docker compose \
  --project-name site-panel \
  --env-file "$RELEASE/.env" \
  -f "$RELEASE/infra/docker/docker-compose.production.yml" \
  logs -f --tail=200 api worker caddy panel
```

Остановить просмотр логов: `Ctrl+C`. Это не останавливает контейнеры.

### Состояние контейнеров

```bash
export SITE_PANEL_ROOT=/opt/site-panel
RELEASE="$(readlink -f "$SITE_PANEL_ROOT/current")"
sudo -u sitepanel-deploy docker compose \
  --project-name site-panel \
  --env-file "$RELEASE/.env" \
  -f "$RELEASE/infra/docker/docker-compose.production.yml" \
  ps
```

### Контролируемый restart

```bash
export SITE_PANEL_ROOT=/opt/site-panel
sudo -u sitepanel-deploy "$SITE_PANEL_ROOT/bin/release-manager.sh" restart
```

Эта команда перезапускает API, worker, panel и Caddy, затем ждёт health API.

### Контролируемый code rollback

```bash
export SITE_PANEL_ROOT=/opt/site-panel
sudo -u sitepanel-deploy "$SITE_PANEL_ROOT/bin/release-manager.sh" rollback
```

Используйте только когда есть `previous_release` и вы понимаете, почему текущая версия плохая. Rollback не выполняет schema downgrade и не восстанавливает данные.

### Последние операционные события

```bash
sudo tail -n 100 /opt/site-panel/shared/release-state/audit.log
```

В audit log должны быть только технические события: timestamp, SHA, snapshot ID, action/result. Не добавляйте туда PII, пароли, webhook body или credentials.

### Место на диске

```bash
df -h
docker system df
```

Если диск почти заполнен, не запускайте сразу агрессивную очистку Docker. Сначала выясните, что занимает место: Docker images, logs, sites volume, uploads или backup staging. Не удаляйте volumes без подтверждённого restore path.

---

## 19. Проверка перед реальной эксплуатацией

До любых реальных клиентов выполните staging drill на disposable VPS с реальным DNS.

### Минимальный сценарий проверки

- [ ] Installer прошёл на чистом supported VPS.
- [ ] Панель открывается по `https://PANEL_DOMAIN` без ошибки сертификата.
- [ ] API health доступен через panel same-origin path `/api/v1/health`.
- [ ] Создан ровно один оператор.
- [ ] TOTP реально включена и проверена повторным входом.
- [ ] Снаружи не открыты PostgreSQL, Redis, API, Panel technical port и Caddy Admin.
- [ ] Создан Project, подтверждены facts, выбраны семантика/география.
- [ ] Создан PagePlan, candidate build, authenticated preview.
- [ ] Explicit publish и selected rollback работают на тестовом домене.
- [ ] Клиентский домен ведёт на VPS и Caddy routes работают.
- [ ] Тестовая lead form доходит до inbox.
- [ ] Подписанный webhook уходит в тестовый receiver, retry/DLQ проверены.
- [ ] Создан restic backup.
- [ ] Выполнен `restic check`.
- [ ] Snapshot восстановлен на **другой disposable VPS**.
- [ ] После restore проверены DB, login/MFA, sites, uploads, Caddy and lead path.
- [ ] Измерены и записаны RTO/RPO, SHA, snapshot ID, дата и результат.

Зафиксируйте результаты в [docs/ХОД-РАБОТ.md](./docs/ХОД-РАБОТ.md). До прохождения этого списка не объявляйте систему production-ready.

---

## 20. Разбор частых проблем

### Installer говорит: `This installer supports Linux VPS hosts only`

Вы пытаетесь запустить production installer на Windows или macOS. Это ожидаемо: production path рассчитан на Linux VPS.

Что делать:

1. Подключитесь по SSH к Debian/Ubuntu VPS.
2. Запустите команду там.
3. На Windows используйте [`scripts/install.ps1`](./scripts/install.ps1) только для local development, не для public VPS.

### Installer говорит: `Only Debian 12 is supported` или `Ubuntu 22.04 or 24.04 is required`

ОС VPS не входит в список проверенных систем.

Что делать:

- создайте новый VPS с Debian 12, Ubuntu 22.04 LTS или Ubuntu 24.04 LTS;
- не обходите проверку редактированием скрипта;
- перенесите данные только по проверенной recovery procedure.

### Installer говорит: `Host port 80 or 443 is already in use`

На сервере уже работает веб-сервер, proxy или другой контейнер, использующий порт 80/443.

Диагностика:

```bash
sudo ss -ltnp | grep -E ':(80|443)'
```

Возможные причины:

- установлен Nginx или Apache;
- старый Caddy;
- другая панель на этом VPS;
- Docker container от другого проекта.

Не останавливайте неизвестный сервис вслепую. Сначала выясните, кому принадлежит сервер и нужен ли этот сервис. Лучше использовать отдельный чистый VPS для первого staging drill.

### Installer говорит, что source checkout dirty

Первый release должен соответствовать точному commit, поэтому installer останавливается при tracked/staged изменениях.

Проверьте:

```bash
cd /opt/site-panel-bootstrap
git status --short
```

Что делать:

- если изменения ваши и должны попасть в release — закоммитьте их, дождитесь CI и используйте этот commit;
- если это временные изменения — сохраните их отдельно или удалите только после понимания происхождения;
- не копируйте `.env` в Git checkout.

### `docker info` не работает

Проверьте:

```bash
sudo systemctl status docker
sudo docker info
```

Если Docker service не запущен:

```bash
sudo systemctl enable --now docker
```

Если installer не смог поставить Docker, сохраните текст ошибки `apt`/Docker repository и не подменяйте production Compose development Compose файлом.

### Ошибка `pending dns_not_ready`

Это означает: с VPS домен ещё не резолвится в IPv4.

Проверьте:

```bash
getent ahostsv4 panel.example.ru
getent ahostsv4 api.example.ru
```

Проверьте у DNS-провайдера:

- есть ли A-запись;
- совпадает ли IP с IP VPS;
- не ошиблись ли в имени поддомена;
- не оставили ли старую A/AAAA-запись;
- не ждёте ли propagation слишком рано.

После исправления:

```bash
sudo bash scripts/install-production-vps.sh \
  --source-dir "$PWD" \
  --config-file /root/site-panel-public.env \
  --resume
```

### HTTPS не открывается или браузер ругается на сертификат

Проверьте DNS, затем порты 80/443 и Caddy:

```bash
sudo ufw status verbose
export SITE_PANEL_ROOT=/opt/site-panel
RELEASE="$(readlink -f "$SITE_PANEL_ROOT/current")"
sudo -u sitepanel-deploy docker compose \
  --project-name site-panel \
  --env-file "$RELEASE/.env" \
  -f "$RELEASE/infra/docker/docker-compose.production.yml" \
  logs --tail=200 caddy
```

Причины обычно такие:

- DNS ещё не указывает на VPS;
- порт 80/443 блокирует firewall провайдера или UFW;
- другой сервис занял порт;
- домен проходит через CDN/proxy с неподходящими настройками;
- email/домен в config содержит опечатку;
- Caddy не может записать данные сертификата в свой volume: в актуальном production release volumes `caddy_data` и `caddy_config` инициализируются installer-ом для UID `10001`, а Compose использует `nocopy`, чтобы Docker не перезаписал эти права при первом старте. Обновите code release и повторите installer phase `volumes`; не удаляйте volumes.

Не отключайте TLS-проверку браузера как «решение». Сначала устраните причину.

### `pending public_tls_or_proxy_not_ready`

Локальные контейнеры могут работать, но external HTTPS/same-origin API ещё не прошли.

Проверьте с VPS:

```bash
curl -I https://panel.example.ru/
curl -fsS https://panel.example.ru/api/v1/health
```

Замените домен на ваш. Затем повторите `--phase verify`.

### Не удаётся создать оператора

Bootstrap сознательно отказывается, если в базе уже есть несколько пользователей или owner scopes. Это защита single-operator модели.

Возможные сообщения:

|Сообщение|Смысл|
|---|---|
|`Multiple user accounts exist`|В базе больше одного аккаунта. Не создавайте ещё один.|
|`Multiple owner scopes exist`|В базе больше одного internal owner scope. Нужен разбор данных.|
|`An operator account already exists`|Оператор уже создан. Используйте существующий email или осознанный reset password.|
|`Password must contain at least 10 characters`|Введите более длинный пароль.|

Не решайте проблему прямым удалением строк из PostgreSQL без backup и понимания модели данных.

### TOTP не настроена или потерян телефон

Пока есть доступная сессия, настройте второй authenticator или сохраните recovery process в вашем password manager согласно политике компании. Не храните TOTP secret в обычном текстовом файле на VPS.

Если TOTP уже включена и доступ полностью потерян, не обнуляйте базу. Сначала изучите безопасную процедуру recovery для single operator и сделайте backup. Отдельная runtime-проверенная админ-процедура должна быть подготовлена до реальной эксплуатации.

### Ошибка restic repository или credentials

Проверьте:

- repository URL/тип;
- доступность storage из VPS;
- access key и secret;
- существование и права `restic-password`;
- что credentials имеют доступ только к нужному bucket/prefix.

Не вставляйте содержимое `backup.env` в issue, чат или логи. Учитывайте, что `backup.env` не является shell script: expressions не будут исполнены.

### Backup есть, но restore ещё не проверен

Это нормальный статус `pending_restore_drill`. Наличие backup недостаточно.

Что делать:

1. Создать второй disposable VPS.
2. Установить ту же supported ОС.
3. Поднять known-good release через installer/release workflow.
4. Подключить прежний restic repository и пароль.
5. Восстановить конкретный snapshot только с explicit confirmation.
6. Проверить login/MFA, сайты, uploads, Caddy, lead/webhook.

### Контейнеры перезапускаются или API unhealthy

Посмотрите логи:

```bash
export SITE_PANEL_ROOT=/opt/site-panel
RELEASE="$(readlink -f "$SITE_PANEL_ROOT/current")"
sudo -u sitepanel-deploy docker compose \
  --project-name site-panel \
  --env-file "$RELEASE/.env" \
  -f "$RELEASE/infra/docker/docker-compose.production.yml" \
  logs --tail=200 api worker postgres caddy panel
```

Не исправляйте проблему командами `down -v`, удалением database volume или заменой `.env` случайным шаблоном.

### Закончилось место на диске

Сначала посмотрите:

```bash
df -h
docker system df
sudo du -sh /opt/site-panel/* 2>/dev/null
```

Возможные причины:

- накопились Docker images после release;
- слишком много uploads;
- растёт sites volume;
- логи занимают место;
- backup staging временно занимает диск.

Перед очисткой подтвердите, что понимаете последствия и что restore path проверен. Не удаляйте `site-panel_pgdata`, `site-panel_sites_data`, `site-panel_uploads_data`, `site-panel_caddy_data` или `site-panel_caddy_config`.

---

## 21. Вопросы и ответы

### Это SaaS, куда могут зарегистрироваться клиенты?

Нет. Первый release — self-hosted panel для одного оператора. Public registration намеренно отсутствует.

### Нужен ли отдельный сервер под PostgreSQL?

Для первого staging/малого production после всех release gates — нет. PostgreSQL живёт на том же VPS во внутренней Docker network. При росте нагрузки отдельная БД становится отдельным архитектурным решением.

### Можно ли поставить на обычный shared hosting?

Нет. Нужен VPS/VDS с root/sudo и поддерживаемой Linux ОС. Обычный PHP-hosting не даёт нужный доступ к Docker, volumes, firewall и systemd.

### Можно ли поставить на Windows VPS?

Нет для production path. Используйте Debian 12 или Ubuntu 22.04/24.04. Windows пригодна для local development, но не для этого production installer.

### Можно ли использовать один домен для панели и API?

Installer требует разные `PANEL_DOMAIN` и `API_DOMAIN`. Панель при этом всё равно ходит к API через same-origin `/api/...`, что уменьшает CORS surface.

### Почему нужно два поддомена, если панель использует `/api`?

`panel.example.ru/api/...` — основной путь для браузерной панели. `api.example.ru` сохраняется как отдельный явный API origin для диагностических/операционных случаев и production config contract.

### Обязательно ли иметь домен?

Для intended public production path — да. HTTPS и cookie security должны работать на реальном hostname. Запуск по IP не является заменой release gate.

### Можно ли использовать Cloudflare?

Можно после понимания DNS/proxy/TLS режима. Для первой staging-проверки проще направить DNS напрямую на VPS. При включённом proxy нужно отдельно проверять ACME, original IP policy, firewall и health checks.

### Какие порты должны быть открыты?

Снаружи: SSH (обычно `22`), `80`, `443`. PostgreSQL `5432`, Redis `6379`, API `8000`, development panel `5173` и Caddy Admin `2019` не должны быть открыты наружу.

### Почему UFW не включается сразу?

Чтобы не заблокировать SSH. Firewall безопасно включать только когда вы проверили ключевой доступ и имеете provider console как запасной путь.

### Почему SSH hardening не включается сразу?

По той же причине: ошибка в ключах или конфигурации может отрезать администратора. Installer создаёт timed rollback, но всё равно нужен второй проверенный вход.

### Можно ли передать все секреты флагами команды для полностью unattended install?

Нет. Командная строка часто попадает в shell history, process list, CI logs или screenshots. Installer не принимает secrets, пароль оператора и private keys в аргументах.

### Где лежат application secrets?

В `/opt/site-panel/shared/.env`, mode `0600`. Не меняйте `FIELD_ENCRYPTION_KEY`, `BLIND_INDEX_PEPPER` и `APP_PEPPER` без отдельного recovery/rotation plan: старые зашифрованные данные и blind indexes могут перестать читаться.

### Где лежат backup credentials?

В `/opt/site-panel/shared/backup.env` и `/opt/site-panel/shared/restic-password`, mode `0600`. Эти файлы должны быть защищены как production secrets.

### Можно ли хранить backup на том же VPS?

Только как дополнительную временную копию, но не как единственный backup. Основной backup должен быть off-host.

### Почему DSAR exports не сохраняются?

Это короткоживущие sensitive exports. При restore они очищаются, а исходные данные восстанавливаются из PostgreSQL согласно policy. Это уменьшает лишнее хранение выгрузок персональных данных.

### Что делать, если утёк `.env`?

Считайте скомпрометированными application/database/provider credentials. Изолируйте доступ, зафиксируйте incident, не публикуйте содержимое файла, выполните плановую ротацию и оценку уже зашифрованных данных. Не ограничивайтесь обычным restart.

### Можно ли удалить `.env` и сгенерировать новый, если что-то не работает?

Нет. Это может сделать недоступными зашифрованные лиды и другие данные. Сначала backup, диагностика и recovery plan.

### Можно ли выполнить `docker compose down -v`, если хочется начать сначала?

На production — нет. Эта команда удаляет volumes и может уничтожить PostgreSQL/sites/uploads/Caddy state. На disposable staging — только если вы осознанно хотите удалить всё и уже имеете нужные backup.

### Почему нельзя обновлять `git pull`?

Это нарушает immutable release model и усложняет rollback. Обновления должны идти через verified GitHub CI/release workflow.

### Что произойдёт, если новый release не запустился?

Если есть `previous`, release manager пытается вернуть code на прошлый healthy release. Он не откатывает schema и не восстанавливает DB автоматически. Если это самый первый release, `previous` ещё нет: installer сохранит диагностику и потребуется исправить причину, затем безопасно повторить phase.

### Можно ли восстановить базу автоматически при failed deploy?

Нет. Automatic DB restore способен превратить временную проблему в необратимую потерю. Он всегда требует выбранного snapshot и явного подтверждения оператора.

### Нужны ли ключи LLM/AI?

Нет для базового первого operator workflow. Controlled generation использует подтверждённые facts и deterministic rules; не включайте неподтверждённые AI endpoints в production только потому, что ключ доступен.

Если оператор отдельно включит AI-модуль, параметры preflight задаются в защищённом production `.env`: `AI_DISABLED=true` полностью блокирует новые AI-запросы (чтобы разрешить их, установите `false`); `AI_DAILY_BUDGET_USD` по умолчанию `10`, а `AI_MONTHLY_BUDGET_USD` по умолчанию `100` для скользящих суток/30 дней. Проверка суммирует уже записанные расходы `ai_runs` перед вызовом, но не резервирует бюджет атомарно между параллельными запросами. Это не жёсткая гарантия выставленного счёта: настройте отдельный spending cap на стороне каждого провайдера. Произвольные внешние AI endpoint domains требуют явного `AI_ENDPOINT_ALLOWLIST`; не добавляйте туда private/loopback hosts без отдельной архитектурной проверки.

### Где смотреть, дошёл ли лид?

В разделе лидов Site Panel, в логах worker и по статусу webhook delivery. Перед production обязательно проверьте весь маршрут от реального клиентского hostname до тестового webhook receiver.

### Может ли исход лида автоматически менять сайт или SEO?

Нет. Исход лида — материал для следующей ручной итерации оператора. Он не должен автоматически переписывать content, routing, SEO или publication state.

### Можно ли использовать один VPS для нескольких агентств?

Первый release для этого не предназначен. Multi-tenant agency hosting требует отдельного post-release redesign, а не флага установки.

### Зачем нужен отдельный `api.example.ru`, если API не открыт на порту 8000?

Это HTTPS reverse proxy на том же Caddy, а не прямой доступ к техническому API port. Порт `8000` остаётся внутри Docker network.

### Installer сказал `completed`. Можно приглашать клиентов?

Не автоматически. Выполните список из раздела [Проверка перед реальной эксплуатацией](#19-проверка-перед-реальной-эксплуатацией), включая реальный browser/Caddy/lead/backup-restore staging drill.

---

## 22. Что нельзя делать

Следующие действия могут привести к утрате данных, утечке секретов или недоступности сервиса:

```bash
# Не удаляйте production volumes.
docker compose down -v

# Не запускайте development stack на public VPS.
docker compose -f infra/docker/docker-compose.yml up

# Не обновляйте активный production release напрямую.
git pull

# Не передавайте пароль оператора в history/process list.
python scripts/bootstrap_operator.py --email you@example.ru --password MyPassword

# Не публикуйте секреты.
cat /opt/site-panel/shared/.env
cat /opt/site-panel/shared/backup.env
```

Также не отправляйте в GitHub Actions artifacts, issue comments, support chat, screenshots или мессенджеры:

- `.env`;
- `backup.env`;
- restic password;
- object-storage access key/secret;
- GitHub private SSH key;
- database dump;
- browser cookies;
- TOTP secret или QR-код;
- raw PII лидов;
- webhook body/signature secret.

---

## Полезные ссылки внутри repository

|Документ|Когда открывать|
|---|---|
|[README.md](./README.md)|Краткий обзор продукта и локальные команды.|
|[README-LOCAL.md](./README-LOCAL.md)|Установка для разработки на своём компьютере.|
|[docs/ХОД-РАБОТ.md](./docs/ХОД-РАБОТ.md)|Фактический статус, P0/P1 blockers и release gate.|
|[docs/runbooks/github-deploy-recovery.md](./docs/runbooks/github-deploy-recovery.md)|Настройка GitHub immutable releases, recovery и Environment secrets.|
|[docs/runbooks/disaster-recovery.md](./docs/runbooks/disaster-recovery.md)|Инцидент, data restore и полный перенос на новый VPS.|
|[.env.production.example](./.env.production.example)|Список production env полей без реальных secrets.|
|[scripts/install-production-vps.sh](./scripts/install-production-vps.sh)|Автоматический production installer.|
|[scripts/validate_production_env.py](./scripts/validate_production_env.py)|Проверка production `.env` без вывода его значений.|

---

## Финальный короткий чеклист

Перед тем как считать VPS подготовленным, убедитесь:

- [ ] У вас есть второй путь входа: проверенный SSH key и/или provider console.
- [ ] DNS panel/API ведёт на нужный VPS.
- [ ] Панель открывается по HTTPS без certificate warning.
- [ ] Оператор создан один, demo account отсутствует.
- [ ] TOTP включена и проверена повторным входом.
- [ ] Снаружи открыты только SSH, 80 и 443.
- [ ] `.env`, `backup.env`, restic password имеют mode `0600`.
- [ ] Off-host restic backup создаётся.
- [ ] GitHub deployment/recovery keys ограничены forced-command gateway, если GitHub контур включён.
- [ ] Вы понимаете, где хранятся private keys и restic password.
- [ ] Выполнен отдельный restore drill на disposable VPS.
- [ ] Проверен путь: панель → сайт → домен → лид → webhook.
- [ ] В [docs/ХОД-РАБОТ.md](./docs/ХОД-РАБОТ.md) отмечены реальные результаты, а не планы.

Если хотя бы один пункт, связанный с HTTPS, TOTP, backup restore или lead flow, не подтверждён — не приглашайте реальных клиентов и не называйте установку production-ready.
