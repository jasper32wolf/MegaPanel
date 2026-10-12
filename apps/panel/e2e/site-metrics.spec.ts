import { expect, test } from "@playwright/test";

async function mockAuth(page: import("@playwright/test").Page) {
  let authenticated = false;
  await page.route("**/api/v1/security/me", (route) => route.fulfill({ status: authenticated ? 200 : 401, body: authenticated ? "{}" : '{"detail":"not authenticated"}' }));
  await page.route("**/api/v1/auth/login", (route) => { authenticated = true; return route.fulfill({ status: 200, body: '{"ok":true}' }); });
  await page.route("**/api/v1/auth/refresh", (route) => route.fulfill({ status: authenticated ? 200 : 401, body: authenticated ? "{}" : '{"detail":"not authenticated"}' }));
  await page.goto("/login");
  await page.getByLabel("Email").fill("operator@example.test");
  await page.getByLabel("Пароль").fill("controlled-e2e-password");
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page).toHaveURL(/\/$/);
}

function report(days: number, selected = false) {
  const traffic = Array.from({ length: days }, (_, index) => ({ date: `2026-10-${String(index + 1).padStart(2, "0")}`, page_views: index + 5, consented_session_days: index + 5, low_sample: false }));
  return {
    observed_at: "2026-10-30T12:00:00Z",
    days,
    privacy: { consent_required: true, ip_stored: false, low_sample_threshold: 5, session_metric: "sum_of_daily_distinct_sessions", raw_retention_days: 30, aggregate_retention_days: 365 },
    totals: { page_views: 99, leads: 7, outcomes: 2, sites_with_errors: 1, delivery_attention: 1 },
    traffic,
    leads: traffic.map((point) => ({ date: point.date, count: 1 })),
    outcomes: traffic.map((point) => ({ date: point.date, count: 0 })),
    monitor_errors: traffic.map((point) => ({ date: point.date, count: 0 })),
    sites: [{ site_id: "11111111-1111-4111-8111-111111111111", project_id: "22222222-2222-4222-8222-222222222222", domain: "example.test", publish_state: "published", page_views: 99, leads: 7, delivery_attention: 1, health: { state: "error", dns_status: "ok", ssl_status: "error", last_observed_at: null, open_incidents: 1, worst_severity: "critical" }, seo: { urls: 18, indexed_urls: 12, noindex_urls: 6, thin_urls: 2 } }],
    selected_site: selected ? { site_id: "11111111-1111-4111-8111-111111111111", domain: "example.test", traffic, leads: traffic.map((point) => ({ date: point.date, count: 1 })), monitor_errors: traffic.map((point) => ({ date: point.date, count: 0 })), top_pages: [{ path: "/service/", page_views: 22, consented_session_days: 12, low_sample: false }], seo: { urls: 18, indexed_urls: 12, noindex_urls: 6, thin_urls: 2 } } : null,
    search_data: { google_search_console: "not_connected", yandex_webmaster: "not_connected" },
  };
}

test("site analytics shows only read-only first-party metrics and site drilldown", async ({ page }) => {
  const mutations: string[] = [];
  await mockAuth(page);
  await page.route("**/api/v1/panel/**", (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() !== "GET") mutations.push(`${request.method()} ${url.pathname}`);
    if (url.pathname.endsWith("/alerts")) return route.fulfill({ status: 200, body: '{"unread":0}' });
    if (url.pathname.endsWith("/reports/site-metrics")) return route.fulfill({ status: 200, body: JSON.stringify(report(Number(url.searchParams.get("days") || 30), Boolean(url.searchParams.get("site_id")))) });
    return route.fulfill({ status: 404, body: '{"detail":"not used by proof"}' });
  });

  await page.goto("/analytics?days=7");
  await expect(page.getByRole("heading", { name: "Аналитика сайтов" })).toBeVisible();
  await expect(page.getByText("Посещаемость собирается только при согласии пользователя на аналитику.", { exact: false })).toBeVisible();
  await expect(page.getByText("Google Search Console и Яндекс Вебмастер пока не подключены.", { exact: false })).toBeVisible();
  await expect(page.getByText("есть ошибки", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Подробнее" }).click();
  await expect(page).toHaveURL(/siteId=11111111-1111-4111-8111-111111111111/);
  await expect(page.getByRole("heading", { name: "Подробно: example.test" })).toBeVisible();
  await expect(page.getByText("/service/")).toBeVisible();
  expect(mutations).toEqual([]);
});
