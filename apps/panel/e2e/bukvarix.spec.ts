import { expect, test, type Page } from "@playwright/test";

const projectId = "d0000000-0000-4000-8000-000000000001";
const seedId = "d0000000-0000-4000-8000-000000000002";
const resultId = "d0000000-0000-4000-8000-000000000003";
const runId = "d0000000-0000-4000-8000-000000000004";

async function mockAuth(page: Page) {
  let authenticated = false;
  await page.route("**/api/v1/security/me", (route) => route.fulfill({
    status: authenticated ? 200 : 401,
    contentType: "application/json",
    body: authenticated ? "{}" : '{"detail":"not authenticated"}',
  }));
  await page.route("**/api/v1/auth/login", (route) => {
    authenticated = true;
    return route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
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
}

test("Букварикс: HTTPS preview становится импортом только после явного выбора", async ({ page }) => {
  let listRequests = 0;
  const mutations: { method: string; path: string; body: string }[] = [];

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const json = (body: unknown) => route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
    if (request.method() !== "GET") mutations.push({ method: request.method(), path, body: request.postData() || "" });
    if (path === `/api/v1/projects/${projectId}`) return json({ id: projectId, name: "Bukvarix proof", site_id: null });
    if (path === `/api/v1/projects/${projectId}/keywords`) return json([{
      id: seedId, keyword_id: "d0000000-0000-4000-8000-000000000005", phrase: "ремонт окон", cluster: null, intent: null, priority: null,
    }]);
    if (path === "/api/v1/keywords") return json({ items: [] });
    if (path === `/api/v1/projects/${projectId}/semantic-sources/bukvarix/status`) return json({
      enabled: true, status: "https_public_free", message: "HTTPS public free-mode", personal_credentials_supported: false,
      max_seed_keywords: 10, max_results_per_run: 1000, non_publish_policy: true,
    });
    if (path === `/api/v1/projects/${projectId}/bukvarix-keyword-runs`) {
      if (request.method() === "POST") return json({
        id: runId, project_id: projectId, status: "queued", provider_mode: "https_public_free", query_count: 0, result_count: 0,
        failure_code: null, committed_source_run_id: null, queued_at: "2026-10-04T12:00:00Z", started_at: null, completed_at: null, created_at: "2026-10-04T12:00:00Z", results: [],
      });
      listRequests += 1;
      const completed = listRequests >= 3;
      return json([{ id: runId, project_id: projectId, status: completed ? "completed" : "queued", provider_mode: "https_public_free", query_count: completed ? 1 : 0, result_count: completed ? 1 : 0,
        failure_code: null, committed_source_run_id: null, queued_at: "2026-10-04T12:00:00Z", started_at: null, completed_at: completed ? "2026-10-04T12:01:00Z" : null, created_at: "2026-10-04T12:00:00Z",
        results: completed ? [{ id: resultId, source_project_keyword_id: seedId, phrase: "ремонт окон цены", metrics: [42, 3] }] : [],
      }]);
    }
    if (path === `/api/v1/projects/${projectId}/bukvarix-keyword-runs/${runId}/commit`) return json({
      source_run_id: "d0000000-0000-4000-8000-000000000006", created_keywords: 1, existing_keywords: 0, linked_project_keywords: 1,
    });
    if (path === `/api/v1/projects/${projectId}/coverage`) return json({ selected: 0, covered: 0, uncovered: [], plans: 0 });
    if (path === `/api/v1/projects/${projectId}/semantic-signals`) return json({ totals: { members: 0, bindings: 0, covered: 0, planned: 0, uncovered: 0, unbound: 0 }, cannibalization: [], unmapped_plans: [] });
    if (path === "/api/v1/geo") return json([]);
    if (path === "/api/v1/media") return json([]);
    if (path === "/api/v1/ai/providers") return json([]);
    if (path.includes(`/api/v1/projects/${projectId}/`)) return json([]);
    return json([]);
  });
  await mockAuth(page);

  await page.evaluate((nextPath) => {
    window.history.pushState({}, "", nextPath);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, `/projects/${projectId}`);

  const seed = page.getByLabel("Seed Букварикс: ремонт окон");
  await expect(seed).toBeVisible();
  await seed.check();
  await page.getByText("Подтверждаю запуск фиксированного HTTPS public free-mode Букварикса для выбранных seed-фраз.").click();
  await page.getByRole("button", { name: "Получить HTTPS preview" }).click();
  await expect(page.getByText("queued", { exact: true })).toBeVisible();
  await expect.poll(() => listRequests).toBeGreaterThanOrEqual(3);
  await expect(page.getByText("ремонт окон цены", { exact: true })).toBeVisible();

  await page.getByLabel("Импорт Букварикс: ремонт окон цены").check();
  await page.getByText("Подтверждаю импорт только выбранных preview-фраз с provenance HTTPS public free-mode.").click();
  await page.getByRole("button", { name: "Импортировать выбранные фразы" }).click();

  const previewRequest = mutations.find((item) => item.path.endsWith("/bukvarix-keyword-runs") && item.method === "POST");
  const commitRequest = mutations.find((item) => item.path.endsWith("/commit"));
  expect(previewRequest?.body).toContain(seedId);
  expect(previewRequest?.body).not.toMatch(/api_key|endpoint|https?:\/\//i);
  expect(commitRequest?.body).toContain(resultId);
  expect(mutations.filter((item) => /page-plans|builds|publish|indexnow/i.test(item.path))).toEqual([]);
});
