import { expect, test, type Page } from "@playwright/test";

async function mockAuth(page: Page) {
  let authenticated = false;
  await page.route("**/api/v1/security/me", (route) => route.fulfill({
    status: authenticated ? 200 : 401,
    contentType: "application/json",
    body: authenticated ? "{}" : '{"detail":"not authenticated"}',
  }));
  await page.route("**/api/v1/auth/login", (route) => {
    authenticated = true;
    return route.fulfill({ status: 200, contentType: "application/json", body: '{"ok":true}' });
  });
  await page.route("**/api/v1/auth/refresh", (route) => route.fulfill({
    status: authenticated ? 200 : 401,
    contentType: "application/json",
    body: authenticated ? "{}" : '{"detail":"not authenticated"}',
  }));
  await page.goto("/login");
  await page.getByLabel("Email").fill("operator@example.test");
  await page.getByLabel("Пароль").fill("controlled-e2e-password");
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page).toHaveURL(/\/$/);
}

function session(id: string, index: number) {
  return {
    id,
    device_label: "Chrome on Windows",
    browser_name: "Chrome",
    language: "ru-RU",
    ip_address: `8.8.8.${index + 1}`,
    country: "Russia",
    city: "Kazan",
    current: index === 0,
    can_revoke: index > 0,
    status: index === 0 ? "current" : "active",
    created_at: `2026-10-08T12:${String(59 - index).padStart(2, "0")}:00Z`,
    expires_at: "2026-10-22T12:00:00Z",
    revoked_at: null,
  };
}

test("settings shows only ten session snapshots and opens older-session log", async ({ page }) => {
  await mockAuth(page);
  const visible = Array.from({ length: 10 }, (_, index) => session(`10000000-0000-4000-8000-0000000000${String(index).padStart(2, "0")}`, index));
  const older = [session("20000000-0000-4000-8000-000000000001", 11), session("20000000-0000-4000-8000-000000000002", 12)];
  const historyRequests: string[] = [];
  await page.route("**/api/v1/**", (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === "/api/v1/security/me" || url.pathname === "/api/v1/auth/refresh") return route.fulfill({ status: 200, body: "{}" });
    if (url.pathname === "/api/v1/health/live") return route.fulfill({ status: 200, body: JSON.stringify({ status: "ok", version: "test", env: "test" }) });
    if (url.pathname === "/api/v1/health/ready") return route.fulfill({ status: 200, body: JSON.stringify({ status: "ok" }) });
    if (url.pathname === "/api/v1/security/sessions") return route.fulfill({ status: 200, body: JSON.stringify({ items: visible, total: 12, older_total: 2 }) });
    if (url.pathname === "/api/v1/panel/alerts") return route.fulfill({ status: 200, body: JSON.stringify({ items: [], total: 0, unread: 0, channels: { enabled: true, email: true, telegram: true } }) });
    if (url.pathname === "/api/v1/security/sessions/history") {
      historyRequests.push(url.search);
      return route.fulfill({ status: 200, body: JSON.stringify({ items: older, total: 2, offset: 0, limit: 25 }) });
    }
    return route.fulfill({ status: 404, body: '{"detail":"not used by proof"}' });
  });

  await page.goto("/settings");
  await expect(page.getByRole("heading", { name: "Сессии" })).toBeVisible();
  await expect(page.getByText("Chrome", { exact: true })).toHaveCount(10);
  await expect(page.getByText("8.8.8.1", { exact: true })).toBeVisible();
  await expect(page.getByText("Russia · Kazan", { exact: true })).toHaveCount(10);
  expect(historyRequests).toEqual([]);
  await page.getByRole("link", { name: "Открыть журнал прошлых сессий (2)" }).click();
  await expect(page.getByRole("heading", { name: "Журнал прошлых сессий" })).toBeVisible();
  await expect(page.getByText("8.8.8.12", { exact: true })).toBeVisible();
  expect(historyRequests.length).toBeGreaterThan(0);
  expect(historyRequests.every((query) => query === "?limit=25&offset=0")).toBe(true);
});
