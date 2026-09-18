# Disaster Recovery runbook

> Основной операционный сценарий обновлений, rollback и GitHub-managed recovery находится в [github-deploy-recovery.md](./github-deploy-recovery.md). Этот файл описывает порядок действий при потере данных или всего VPS.

## Границы автоматизации

- **Автоматически:** GitHub может сделать bounded restart, а затем code rollback на `previous` release после неудачного health check.
- **Только вручную:** PostgreSQL restore, restore sites volume, смена `.env`, восстановление на новом VPS, schema repair.
- **Никогда автоматически:** `docker compose down -v`, удаление volumes, `pg_restore`, schema downgrade и ротация ключей шифрования.

Причина: автоматический restore данных способен превратить временную проблему в необратимую потерю данных. Code rollback безопаснее только при backward-compatible migration policy.

## Цели и предпосылки

Восстановить full site grid из:

1. immutable GitHub release SHA;
2. encrypted off-host restic snapshot;
3. PostgreSQL JSON manifests;
4. Docker named volumes с SSG output;
5. `/opt/site-panel/shared/.env` и release state.

До production должны быть измерены и записаны в журнале:

- **RPO** — максимальная допустимая потеря данных между backup;
- **RTO** — время от инцидента до проверенного возврата сервиса;
- выбранный backup retention и результат последнего restore drill.

## 1. Сначала классифицируйте инцидент

| Признак | Первое действие | Restore данных? |
|---|---|---|
| Public health недоступен, internal API жив | Проверить DNS/TLS/Caddy/network | Нет |
| API/worker/panel container упал | `Recover production → recover` | Нет |
| Новый release не здоров | `Recover production → rollback` | Нет, если migration совместима назад |
| PostgreSQL corruption/lost volume | Manual `restore` с snapshot | Да, после approval |
| VPS полностью потерян | Новый VPS + deploy last-good SHA + manual restore | Да |
| Утечка `.env`/ключа | Изоляция и incident response | Не автоматически; нужна ротация и оценка шифрованных данных |

## 2. Code rollback без data restore

1. Откройте `Actions → Recover production`.
2. Запустите `status` и сохраните current/previous SHA и last backup snapshot из Job Summary.
3. Если current release unhealthy, запустите `rollback`.
4. Workflow переключит только immutable code release, пересоздаст services и проверит API health.
5. Проверьте panel, домены, worker и один клиентский site вручную.
6. Если rollback успешен, остановите дальнейшие deploy до root-cause анализа.

Не делайте `git pull`, `git reset`, `down -v` или Alembic downgrade для обычного release rollback.

## 3. Manual data restore на существующем VPS

1. Остановите автоматические deploy и зафиксируйте incident time.
2. Выполните `status`; выберите **явный** restic snapshot ID, не `latest`.
3. Убедитесь, что snapshot проходил `restic check` или заранее проверенный restore drill.
4. В GitHub запустите `Recover production` с параметрами:

   ```text
   operation: restore
   snapshot: <hexadecimal snapshot ID>
   confirmation: RESTORE
   ```

5. Workflow создаёт pre-restore backup. При его ошибке restore не стартует.
6. Restore заменяет DB content, `sites_data`, shared `.env` и release state, применяет migrations и ждёт API health.
7. Проверьте:
   - login/MFA;
   - tenant isolation;
   - sample site build и выдачу Caddy;
   - robots, sitemap, TLS;
   - тестовую lead form и webhook.
8. Запишите snapshot ID, start/end time, результат и follow-up в [ХОД-РАБОТ.md](../ХОД-РАБОТ.md).

## 4. Полное восстановление на новом VPS

1. Создайте новый VPS и закройте network exposure: 22/80/443 only.
2. Установите Docker/Compose, restic и подготовьте DNS.
3. Создайте две новые GitHub SSH key pairs, добавьте public keys через `bootstrap-github-deploy.sh`.
4. Настройте `/opt/site-panel/shared/.env`, `/opt/site-panel/shared/backup.env` и restic password file.
5. Обновите GitHub Environment variables/secrets с новым host и pinned host key.
6. Запустите manual `Deploy production` для последнего known-good green SHA.
7. После успешного release activation выполните manual `restore` с выбранным snapshot.
8. Проведите полный smoke и recovery drill checklist.

Подробные команды и GitHub secret model: [github-deploy-recovery.md](./github-deploy-recovery.md).

## 5. После инцидента

- Зафиксируйте impact, затронутые tenant, RTO/RPO и timeline.
- При PII/security incident следуйте [incident-response.md](../security/incident-response.md).
- Не удаляйте failed release и backup snapshot до завершения анализа.
- Добавьте regression test или release gate для первопричины.
- Обновите статусы и журнал в [ХОД-РАБОТ.md](../ХОД-РАБОТ.md).
