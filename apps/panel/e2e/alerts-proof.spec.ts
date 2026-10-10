import { expect, test, type Page } from "@playwright/test";

async function mockAuth(page: Page) {
  let authenticated = false;
  await page.route("**/api/v1/security/me", (route) => route.fulfill({ status: authenticated ? 200 : 401, body: authenticated ? "{}" : '{"detail":"not authenticated"}' }));
  await page.route("**/api/v1/auth/login", (route) => {
    authenticated = true;
    return route.fulfill({ status: 200, body: '{"ok":true}' });
  });
  await page.route("**/api/v1/auth/refresh", (route) => route.fulfill({ status: authenticated ? 200 : 401, body: authenticated ? "{}" : '{"detail":"not authenticated"}' }));
  await page.goto("/login");
  await page.getByLabel("Email").fill("operator@example.test");
  await page.getByLabel("Пароль").fill("controlled-e2e-password");
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page).toHaveURL(/\/$/);
}

test("login page shows the Mega Panel identity", async ({ page }) => {
  await page.goto("/login");

  await expect(page.getByRole("heading", { name: "Mega Panel" })).toBeVisible();
  await expect(page.getByText("Многофункциональная SEO панель.", { exact: true })).toBeVisible();
  await expect(page.locator('img[src="/Iconka.svg"]')).toBeVisible();
});

test("operator alert inbox shows channel status, read action and explicit test delivery", async ({ page }) => {
  await mockAuth(page);
  let testCreated = false;
  let read = false;
  await page.route("**/api/v1/**", (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === "/api/v1/security/me" || url.pathname === "/api/v1/auth/refresh") return route.fulfill({ status: 200, body: "{}" });
    if (url.pathname === "/api/v1/panel/alert-settings") return route.fulfill({ status: 200, body: JSON.stringify({ channels: { enabled: true, email: true, telegram: true }, recipient: { configured: true, masked_email: "a***@example.test" }, transport: { selected: "smtp_bz_smtp", configured: true, smtp_configured: true, api_configured: false, sender_email: "a***@osco-servis.ru", revision: 1, updated_at: null } }) });
    if (url.pathname === "/api/v1/panel/alerts/test" && request.method() === "POST") {
      testCreated = true;
      return route.fulfill({ status: 200, body: "{}" });
    }
    if (url.pathname === "/api/v1/panel/alerts/10000000-0000-4000-8000-000000000001/read" && request.method() === "POST") {
      read = true;
      return route.fulfill({ status: 200, body: "{}" });
    }
    if (url.pathname === "/api/v1/panel/alerts") {
      const securityAlert = {
        id: "10000000-0000-4000-8000-000000000001",
        category: "security",
        signal_code: "security-login",
        subject_kind: "user",
        subject_key: "10000000-0000-4000-8000-000000000002",
        priority: "high",
        title: "Выполнен вход в панель",
        body: "Создана новая сессия оператора.",
        read,
        created_at: "2026-10-09T12:00:00Z",
        deliveries: { smtp_bz_smtp: "dead_letter", telegram: "retrying" },
        delivery_errors: { smtp_bz_smtp: "authentication_failed" },
      };
      const testAlert = {
        ...securityAlert,
        id: "10000000-0000-4000-8000-000000000003",
        category: "system",
        signal_code: "operator-alert-test",
        title: "Проверка оповещений",
        read: false,
      };
      return route.fulfill({ status: 200, body: JSON.stringify({ items: testCreated ? [testAlert, securityAlert] : [securityAlert], total: testCreated ? 2 : 1, unread: (read ? 0 : 1) + (testCreated ? 1 : 0), channels: { enabled: true, email: true, telegram: true } }) });
    }
    return route.fulfill({ status: 404, body: '{"detail":"not used by proof"}' });
  });

  await page.goto("/alerts");
  await expect(page.getByRole("heading", { name: "Mega Panel" })).toBeVisible();
  await expect(page.getByText("Многофункциональная SEO панель", { exact: true })).toBeVisible();
  await expect(page.locator('img[src="/Iconka.svg"]')).toBeVisible();
  await expect(page.getByRole("heading", { name: "Оповещения" })).toBeVisible();
  await expect(page.getByText("Email: SMTP.bz SMTP", { exact: true })).toBeVisible();
  await expect(page.getByText("Telegram: готов", { exact: true })).toBeVisible();
  await expect(page.getByText("Выполнен вход в панель", { exact: true })).toBeVisible();
  await expect(page.getByText("Email: dead_letter · SMTP.bz отклонил логин или пароль (authentication_failed)", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Прочитано" }).click();
  await expect(page.getByText("Входящие (0 непрочитанных)", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Отправить проверочное оповещение" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("button", { name: "Отправить", exact: true }).click();
  await expect(page.getByText("Проверка оповещений", { exact: true })).toBeVisible();
});

test("SMTP.bz credentials stay write-only and connection test does not send an alert", async ({ page }) => {
  await mockAuth(page);
  let recipientSaved = false;
  let smtpSaved = false;
  let tested = false;

  function alertSettings() {
    return {
      channels: { enabled: true, email: smtpSaved, telegram: false },
      recipient: {
        configured: recipientSaved,
        masked_email: recipientSaved ? "o***@example.test" : null,
      },
      transport: {
        selected: smtpSaved ? "smtp_bz_smtp" : "none",
        configured: smtpSaved,
        smtp_configured: smtpSaved,
        api_configured: false,
        sender_email: smtpSaved ? "a***@osco-servis.ru" : null,
        revision: smtpSaved ? 1 : null,
        updated_at: null,
      },
    };
  }

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === "/api/v1/security/me") {
      return route.fulfill({ status: 200, body: JSON.stringify({ email: "operator@example.test", mfa_enabled: true, mfa_pending: false }) });
    }
    if (url.pathname === "/api/v1/health/live") return route.fulfill({ status: 200, body: '{"status":"ok","version":"test","env":"test"}' });
    if (url.pathname === "/api/v1/health/ready") return route.fulfill({ status: 200, body: '{"status":"ok"}' });
    if (url.pathname === "/api/v1/security/sessions") return route.fulfill({ status: 200, body: '{"items":[],"total":0,"older_total":0}' });
    if (url.pathname === "/api/v1/panel/alert-settings" && request.method() === "GET") {
      return route.fulfill({ status: 200, body: JSON.stringify(alertSettings()) });
    }
    if (url.pathname === "/api/v1/panel/alert-settings/recipient" && request.method() === "PUT") {
      recipientSaved = true;
      return route.fulfill({ status: 200, body: '{}' });
    }
    if (url.pathname === "/api/v1/panel/alert-settings/transports/smtp-bz-smtp" && request.method() === "PUT") {
      const body = request.postDataJSON();
      if (body.port !== 465 || body.tls_mode !== "implicit_tls" || body.password !== "write-only-password") {
        return route.fulfill({ status: 422, body: '{"detail":"invalid SMTP configuration"}' });
      }
      smtpSaved = true;
      return route.fulfill({ status: 200, body: JSON.stringify(alertSettings().transport) });
    }
    if (url.pathname === "/api/v1/panel/alert-settings/test-connection" && request.method() === "POST") {
      tested = true;
      return route.fulfill({ status: 200, body: '{"result":"ok"}' });
    }
    if (url.pathname === "/api/v1/panel/alerts") {
      return route.fulfill({ status: 200, body: '{"items":[],"total":0,"unread":0,"channels":{"enabled":true,"email":false,"telegram":false}}' });
    }
    if (url.pathname === "/api/v1/auth/refresh") return route.fulfill({ status: 200, body: '{}' });
    return route.fulfill({ status: 404, body: '{"detail":"not used by proof"}' });
  });

  await page.goto("/settings");
  await page.getByLabel("Email для оповещений").fill("operator@example.test");
  await page.getByRole("button", { name: "Сохранить адрес" }).click();
  await expect(page.getByText(/Текущий: o\*\*\*@example\.test/)).toBeVisible();

  await page.getByLabel("Адрес отправителя на подтверждённом домене").first().fill("alerts@osco-servis.ru");
  await page.getByLabel("Порт").selectOption("465");
  await expect(page.getByLabel("Защита соединения")).toHaveValue("implicit_tls");
  await page.getByLabel("SMTP.bz логин").fill("write-only-login");
  await page.getByLabel("SMTP.bz пароль").fill("write-only-password");
  await page.getByRole("button", { name: "Сохранить SMTP и сделать активным" }).click();

  await expect(page.getByLabel("SMTP.bz пароль")).toHaveValue("");
  await expect(page.getByText("Email SMTP.bz: готов", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Проверить соединение" }).click();
  await expect(page.getByText("Соединение SMTP.bz и авторизация проверены. Письмо не отправлялось.", { exact: true })).toBeVisible();
  expect(tested).toBe(true);
});
