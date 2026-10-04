import { expect, test, type Page } from "@playwright/test";

const projectId = "d1000000-0000-4000-8000-000000000001";
const revisionId = "d1000000-0000-4000-8000-000000000002";

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

test("design profile проходит draft и review без изменения сайта", async ({ page }) => {
  const mutations: { path: string; body: string }[] = [];
  let revisions: unknown[] = [];
  let generationRuns: unknown[] = [];
  await page.route("**/api/v1/**", (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const json = (body: unknown) => route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
    if (request.method() !== "GET") mutations.push({ path, body: request.postData() || "" });
    if (path === `/api/v1/projects/${projectId}/design-profiles`) {
      if (request.method() === "POST") {
        const body = JSON.parse(request.postData() || "{}");
        revisions = [{
          id: revisionId,
          project_id: projectId,
          scope: body.scope,
          name: body.name,
          version: 1,
          state: "draft",
          profile: body.profile,
          profile_hash: "a".repeat(64),
          supersedes_id: null,
          submitted_at: null,
          reviewed_at: null,
          decision_reason: null,
          created_at: "2026-10-04T12:00:00Z",
        }];
        return json(revisions[0]);
      }
      return json(revisions);
    }
    if (path === `/api/v1/projects/${projectId}/design-profile`) return json({
      project_id: projectId,
      inherited_from_project_id: null,
      family_profile: null,
      project_profile: null,
      effective_profile: null,
      effective_profile_hash: null,
    });
    if (path === `/api/v1/projects/${projectId}/intent-generation-runs`) return json(generationRuns);
    if (path === `/api/v1/projects/${projectId}/page-plans`) return json([{ id: "d1000000-0000-4000-8000-000000000003", slug: "/repair", objective: "Ремонт окон", state: "approved" }]);
    if (path === "/api/v1/ai/providers") return json([{ id: "d1000000-0000-4000-8000-000000000004", label: "Mock provider", provider_id: "mock", enabled: true }]);
    if (path.endsWith("/intent-generation/quote")) return json({ provider_id: "mock", model_id: "mock-model", estimated_cost_usd: 0.01, max_cost_usd: 0.1, input_snapshot_hash: "b".repeat(64), pricing_source: "fixture", pricing_observed_at: "2026-10-04T12:00:00Z" });
    if (path.endsWith("/intent-generation") && request.method() === "POST") {
      generationRuns = [{ id: "d1000000-0000-4000-8000-000000000005", status: "reserved", created_at: "2026-10-04T12:00:00Z", output: {}, error_code: null }];
      return json(generationRuns[0]);
    }
    if (path === "/api/v1/blocks/kits") return json([{ key: "service-local-v1", name: "Local services", blocks: ["hero", "process_steps", "faq", "lead_form"] }]);
    if (path === `/api/v1/projects/${projectId}/design-profiles/${revisionId}/submit-review`) return json({ ...revisions[0], state: "review" });
    return json([]);
  });
  await mockAuth(page);
  await page.goto(`/projects/${projectId}/design`);

  await expect(page.getByRole("heading", { name: "Генерация и дизайн" })).toBeVisible();
  await expect(page.getByText("Профиль ещё не назначен")).toBeVisible();
  await page.getByRole("button", { name: "Создать draft profile" }).click();
  await expect(page.getByText("Создан draft design profile.")).toBeVisible();
  await page.getByRole("button", { name: "На review" }).click();
  await page.getByRole("button", { name: "На review" }).last().click();
  await page.getByLabel("Approved PagePlan").selectOption("d1000000-0000-4000-8000-000000000003");
  await page.getByLabel("Активный AI provider").selectOption("d1000000-0000-4000-8000-000000000004");
  await page.getByLabel("Model ID").fill("mock-model");
  await page.getByRole("button", { name: "Рассчитать intent proposal" }).click();
  await expect(page.getByText("Предварительная оценка: $0.010000")).toBeVisible();
  await page.getByText("Подтверждаю quote, передачу frozen public context выбранному provider и его spending limit.").click();
  await page.getByRole("button", { name: "Поставить intent proposal в очередь" }).click();
  await expect(page.getByText("reserved", { exact: true })).toBeVisible();

  const create = mutations.find((item) => item.path.endsWith("/design-profiles"));
  expect(create?.body).toContain("local-service");
  expect(create?.body).not.toMatch(/<style|<script|https?:\/\/|cloaking/i);
  const intentQueue = mutations.find((item) => item.path.endsWith("/intent-generation"));
  expect(intentQueue?.body).toContain("mock-model");
  expect(intentQueue?.body).not.toMatch(/page_draft|publish|indexnow/i);
  expect(mutations.filter((item) => /builds|publish|indexnow|page-drafts/i.test(item.path))).toEqual([]);
});
