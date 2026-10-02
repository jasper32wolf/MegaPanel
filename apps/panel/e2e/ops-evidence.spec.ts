import { expect, test } from "@playwright/test";

async function mockAuth(page: import("@playwright/test").Page) {
  let authenticated = false;
  await page.route("**/api/v1/security/me", (route) =>
    route.fulfill({
      status: authenticated ? 200 : 401,
      contentType: "application/json",
      body: authenticated ? "{}" : '{"detail":"not authenticated"}',
    }),
  );
  await page.route("**/api/v1/auth/login", (route) => {
    authenticated = true;
    return route.fulfill({ status: 200, contentType: "application/json", body: '{"ok":true}' });
  });
  await page.route("**/api/v1/auth/refresh", (route) =>
    route.fulfill({
      status: authenticated ? 200 : 401,
      contentType: "application/json",
      body: authenticated ? "{}" : '{"detail":"not authenticated"}',
    }),
  );
  await page.goto("/login");
  await page.getByLabel("Email").fill("operator@example.test");
  await page.getByLabel("Пароль").fill("controlled-e2e-password");
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page).toHaveURL(/\/$/);
}

test("Ops shows bounded verification evidence without mutation", async ({ page }) => {
  const mutationRequests: string[] = [];
  await mockAuth(page);
  await page.route("**/api/v1/panel/**", (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === "/api/v1/security/me" || url.pathname === "/api/v1/auth/refresh") {
      return route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
    }
    if (request.method() !== "GET") mutationRequests.push(`${request.method()} ${url.pathname}`);
    if (url.pathname.endsWith("/reports/summary")) {
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ sites: 0, pages_estimate: 0, leads: 0, active_leads: 0, delivery_pending: 0, delivery_dead_letter: 0, domains_pending_tls: 0, domains_tls_error: 0 }) });
    }
    if (url.pathname.endsWith("/reports/observability")) {
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
        observed_at: "2026-10-01T12:00:00Z",
        builds: { failed: 0, latest_success: null },
        ai: { status_counts: {}, failed_error_codes: {}, reserved_estimated_usd: 0, recorded_cost_usd: 0, unresolved_dead_letter_jobs: 0 },
        media: { assets: 0, missing_files: 0, provenance_gaps: 0, expired_licenses: 0, references: { status: "manifest_snapshot", source: "controlled", assets: 0, pages: 0, invalid_entries: 0 } },
        content_gaps: { thin_pages: 0, noindex_pages: 0 },
        system: { backups: { status: "not_observed", reason: "No persisted backup completion or restore-drill result" } },
        worker: { status: "not_observed", last_heartbeat_at: null, age_seconds: null, stale_after_seconds: 90, reason: "No persisted worker heartbeat has been recorded" },
      }) });
    }
    if (url.pathname.endsWith("/reports/verification")) {
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
        scope: "bounded recorded verification evidence; not a production-readiness certification",
        checks: [
          { check_key: "controlled_ci_candidate_flow", label: "Controlled candidate workflow", mode: "ci", state: "passed", observed_at: "2026-10-01T11:00:00Z", coverage: "Bounded candidate and preview workflow", limitation: "Does not verify an external VPS origin." },
          { check_key: "vps_external_origin", label: "External VPS origin", mode: "vps", state: "not_observed", observed_at: null, coverage: "No bounded external-origin result has been recorded.", limitation: "The panel does not probe or certify VPS, DNS, TLS, browser, or external delivery." },
        ],
      }) });
    }
    if (url.pathname.endsWith("/incidents") || url.pathname.endsWith("/events")) {
      return route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    }
    return route.fulfill({ status: 404, contentType: "application/json", body: '{"detail":"not used by proof"}' });
  });

  await page.goto("/ops");
  await expect(page.getByRole("heading", { name: "Статус системы" })).toBeVisible();
  await expect(page.getByText("Границы доказательств")).toBeVisible();
  await expect(page.getByText("passed", { exact: true })).toBeVisible();
  await expect(page.getByText("not_observed", { exact: true })).toBeVisible();
  await expect(page.getByText("не доказывают staging или VPS production", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: /Записать|Добавить|Подтвердить evidence/ })).toHaveCount(0);
  expect(mutationRequests).toEqual([]);
});
