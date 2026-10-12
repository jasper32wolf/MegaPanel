import { expect, test, type Page } from "@playwright/test";

async function mockAuth(page: Page) {
  let authenticated = false;
  await page.route("**/api/v1/security/me", (route) =>
    route.fulfill({
      status: authenticated ? 200 : 401,
      body: authenticated ? "{}" : '{"detail":"not authenticated"}',
    }),
  );
  await page.route("**/api/v1/auth/login", (route) => {
    authenticated = true;
    return route.fulfill({ status: 200, body: '{"ok":true}' });
  });
  await page.route("**/api/v1/auth/refresh", (route) =>
    route.fulfill({ status: authenticated ? 200 : 401, body: authenticated ? "{}" : "{}" }),
  );
  await page.goto("/login");
  await page.getByLabel("Email").fill("operator@example.test");
  await page.getByLabel("Пароль").fill("controlled-e2e-password");
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page).toHaveURL(/\/$/);
}

test("storage screen previews and queues only allowlisted expired data", async ({ page }) => {
  await mockAuth(page);
  let queued = false;
  const preview = {
    id: "10000000-0000-4000-8000-000000000001",
    status: "preview",
    policy_hash: "a".repeat(64),
    preview: {
      policy: {
        auth_session_retention_days: 180,
        notification_retention_days: 90,
        operational_event_retention_days: 90,
        delivery_attempt_retention_days: 90,
        dsar_export_ttl_hours: 24,
      },
      sources: {
        auth_sessions: 3,
        read_terminal_notifications: 2,
        operational_events: 1,
        delivered_webhook_attempts: 4,
      },
      protected: { audit_log: "immutable" },
    },
    result: {},
    error_code: null,
    expires_at: "2026-10-11T12:15:00Z",
    created_at: "2026-10-11T12:00:00Z",
    started_at: null,
    completed_at: null,
  };

  await page.route("**/api/v1/**", (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === "/api/v1/security/me" || url.pathname === "/api/v1/auth/refresh") {
      return route.fulfill({ status: 200, body: "{}" });
    }
    if (url.pathname === "/api/v1/panel/alerts") {
      return route.fulfill({ status: 200, body: '{"items":[],"total":0,"unread":0,"channels":{}}' });
    }
    if (url.pathname === "/api/v1/system/storage" && request.method() === "GET") {
      return route.fulfill({
        status: 200,
        body: JSON.stringify({
          policy: preview.preview.policy,
          protected_sources: ["audit_log", "release_evidence", "open_incidents", "active_deliveries"],
          runs: queued ? [{ ...preview, status: "queued" }] : [],
        }),
      });
    }
    if (url.pathname === "/api/v1/system/storage/previews" && request.method() === "POST") {
      return route.fulfill({ status: 200, body: JSON.stringify(preview) });
    }
    if (
      url.pathname === "/api/v1/system/storage/previews/10000000-0000-4000-8000-000000000001/run" &&
      request.method() === "POST"
    ) {
      if (request.postDataJSON().confirmation !== "PURGE EXPIRED DATA") {
        return route.fulfill({ status: 409, body: '{"detail":"Retention confirmation does not match"}' });
      }
      queued = true;
      return route.fulfill({ status: 200, body: JSON.stringify({ ...preview, status: "queued" }) });
    }
    return route.fulfill({ status: 404, body: '{"detail":"not used by proof"}' });
  });

  await page.goto("/system/storage");
  await expect(page.getByRole("heading", { name: "Хранение и журналы" })).toBeVisible();
  await expect(page.getByText("Docker stdout/stderr: 10 MiB × 3 файла на сервис.")).toBeVisible();
  await expect(page.getByText("audit_log", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Рассчитать предпросмотр" }).click();
  await expect(page.getByText("Сессии", { exact: true })).toBeVisible();
  await expect(page.getByText("Попытки webhook с успешной доставкой", { exact: true })).toBeVisible();
  const queueButton = page.getByRole("button", { name: "Поставить очистку в очередь" });
  await expect(queueButton).toBeDisabled();
  await page.getByLabel("Для подтверждения введите PURGE EXPIRED DATA").fill("PURGE EXPIRED DATA");
  await expect(queueButton).toBeEnabled();
  await queueButton.click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("button", { name: "Поставить в очередь" }).click();
  await expect(page.getByText("Очистка поставлена в очередь обработчика.")).toBeVisible();
  await expect(page.getByText("в очереди", { exact: true })).toBeVisible();
});
