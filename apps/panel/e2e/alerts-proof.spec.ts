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

test("operator alert inbox shows channel status, read action and explicit test delivery", async ({ page }) => {
  await mockAuth(page);
  let testCreated = false;
  let read = false;
  await page.route("**/api/v1/**", (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === "/api/v1/security/me" || url.pathname === "/api/v1/auth/refresh") return route.fulfill({ status: 200, body: "{}" });
    if (url.pathname === "/api/v1/panel/alert-settings") return route.fulfill({ status: 200, body: JSON.stringify({ channels: { enabled: true, email: true, telegram: true }, recipient: { configured: true, masked_email: "a***@example.test" } }) });
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
        deliveries: { email: "delivered", telegram: "retrying" },
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
  await expect(page.getByRole("heading", { name: "Оповещения" })).toBeVisible();
  await expect(page.getByText("Email: готов", { exact: true })).toBeVisible();
  await expect(page.getByText("Telegram: готов", { exact: true })).toBeVisible();
  await expect(page.getByText("Выполнен вход в панель", { exact: true })).toBeVisible();
  await expect(page.getByText("Email: delivered", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Прочитано" }).click();
  await expect(page.getByText("Входящие (0 непрочитанных)", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Отправить проверочное оповещение" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("button", { name: "Отправить", exact: true }).click();
  await expect(page.getByText("Проверка оповещений", { exact: true })).toBeVisible();
});
