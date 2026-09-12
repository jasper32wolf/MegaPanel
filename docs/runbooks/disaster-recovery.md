# Disaster Recovery runbook (TZ 13)

## Goal
Rebuild the full site grid from PostgreSQL JSON manifests on a clean VPS.

## Steps
1. Restore PostgreSQL from encrypted backup (PITR if needed).
2. `alembic upgrade head`
3. Recreate Redis (queues are ephemeral; rebuild drip from `site_pages.index_state`).
4. For each site: `POST /api/v1/sites/{id}/build`
5. Re-apply Caddy vhosts via Admin API / domains registry.
6. Verify SSL, robots, sitemap, IndexNow key files.
7. Smoke-test lead form + webhook.

## RTO / RPO
Target depends on site count and CPU. Document measured values after load test (k6).
