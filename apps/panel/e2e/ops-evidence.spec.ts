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

function observability() {
  return {
    observed_at: "2026-10-01T12:00:00Z",
    builds: { failed: 0, latest_success: { build_hash: "a".repeat(64), created_at: "2026-10-01T12:00:00Z" } },
    ai: { status_counts: {}, failed_error_codes: {}, reserved_estimated_usd: 0, recorded_cost_usd: 0, unresolved_dead_letter_jobs: 0 },
    media: { assets: 0, missing_files: 0, provenance_gaps: 0, expired_licenses: 0, references: { status: "manifest_snapshot", source: "controlled", assets: 0, pages: 0, invalid_entries: 0 } },
    content_gaps: { thin_pages: 0, noindex_pages: 0 },
    system: { backups: { status: "not_observed", reason: "No persisted backup completion or restore-drill result" } },
    worker: { status: "not_observed", last_heartbeat_at: null, age_seconds: null, stale_after_seconds: 90, reason: "No persisted worker heartbeat has been recorded" },
  };
}

function summary() {
  return { sites: 0, pages_estimate: 0, leads: 0, active_leads: 0, delivery_pending: 0, delivery_dead_letter: 0, domains_pending_tls: 0, domains_tls_error: 0 };
}

function verification() {
  return {
    scope: "bounded recorded verification evidence; not a production-readiness certification",
    checks: [
      { check_key: "controlled_ci_candidate_flow", label: "Controlled candidate workflow", mode: "ci", state: "passed", observed_at: "2026-10-01T11:00:00Z", coverage: "Bounded candidate and preview workflow", limitation: "Does not verify an external VPS origin." },
      { check_key: "vps_external_origin", label: "External VPS origin", mode: "vps", state: "not_observed", observed_at: null, coverage: "No bounded external-origin result has been recorded.", limitation: "The panel does not probe or certify VPS, DNS, TLS, browser, or external delivery." },
    ],
  };
}

test("Ops shows bounded verification evidence without mutation", async ({ page }) => {
  const mutationRequests: string[] = [];
  await mockAuth(page);
  await page.route("**/api/v1/panel/**", (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() !== "GET") mutationRequests.push(`${request.method()} ${url.pathname}`);
    if (url.pathname.endsWith("/reports/summary")) return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(summary()) });
    if (url.pathname.endsWith("/reports/observability")) return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(observability()) });
    if (url.pathname.endsWith("/reports/verification")) return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(verification()) });
    if (url.pathname.endsWith("/incidents") || url.pathname.endsWith("/events")) return route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    return route.fulfill({ status: 404, contentType: "application/json", body: '{"detail":"not used by proof"}' });
  });

  await page.goto("/ops");
  await expect(page.getByRole("heading", { name: "Статус системы" })).toBeVisible();
  await expect(page.getByText("Границы доказательств")).toBeVisible();
  await expect(page.getByText("Последняя готовая candidate (не публикация): aaaaaaaaaaaa")).toBeVisible();
  await expect(page.getByText("passed", { exact: true })).toBeVisible();
  await expect(page.getByText("not_observed", { exact: true })).toBeVisible();
  await expect(page.getByText("не доказывают staging или VPS production", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: /Записать|Добавить|Подтвердить evidence/ })).toHaveCount(0);
  expect(mutationRequests).toEqual([]);
});

test("Ops snoozes one persisted incident without external delivery", async ({ page }) => {
  const incident = {
    id: "11111111-1111-4111-8111-111111111111",
    signal_code: "qa-block",
    severity: "warning",
    status: "open",
    occurrence_count: 2,
    opened_at: "2026-10-01T12:00:00Z",
    acknowledged_at: null,
    snoozed_until: null,
    resolved_at: null,
  };
  const mutations: { path: string; body: unknown }[] = [];
  let snoozedUntil: string | null = null;
  await mockAuth(page);
  await page.route("**/api/v1/panel/**", (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname.endsWith("/reports/summary")) return route.fulfill({ status: 200, body: JSON.stringify(summary()) });
    if (url.pathname.endsWith("/reports/observability")) return route.fulfill({ status: 200, body: JSON.stringify(observability()) });
    if (url.pathname.endsWith("/reports/verification")) return route.fulfill({ status: 200, body: JSON.stringify(verification()) });
    if (url.pathname.endsWith("/events")) return route.fulfill({ status: 200, body: "[]" });
    if (url.pathname.endsWith("/incidents") && request.method() === "GET") return route.fulfill({ status: 200, body: JSON.stringify([{ ...incident, snoozed_until: snoozedUntil }]) });
    if (url.pathname.endsWith(`/incidents/${incident.id}`) && request.method() === "PATCH") {
      mutations.push({ path: url.pathname, body: request.postDataJSON() });
      snoozedUntil = "2026-10-09T13:00:00Z";
      return route.fulfill({ status: 200, body: JSON.stringify({ ...incident, snoozed_until: snoozedUntil }) });
    }
    return route.fulfill({ status: 404, body: '{"detail":"not used by proof"}' });
  });

  await page.goto("/ops");
  await page.getByRole("button", { name: "Отложить 1 ч" }).click();
  await expect.poll(() => mutations).toEqual([{ path: `/api/v1/panel/incidents/${incident.id}`, body: { action: "snooze", snooze_minutes: 60 } }]);
  await expect(page.getByText("Отложен до", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: "Закрыть" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Отложить 1 ч" })).toBeVisible();
});
