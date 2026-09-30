import { expect, test, type Page } from "@playwright/test";

const provider = {
  id: "11111111-1111-4111-8111-111111111111",
  provider_id: "mock_gateway",
  label: "Mock gateway",
  kind: "openai_compatible",
  base_url: "https://mock.provider.invalid/v1",
  credential_last4: "6789",
  enabled: true,
};

const models = [
  {
    provider_id: "mock_gateway",
    model_id: "mock-small",
    display_name: "Mock Small",
    input_price_usd_per_million: 0.2,
    output_price_usd_per_million: 0.8,
    is_free: false,
    metadata_source: "mock fixture",
  },
  {
    provider_id: "mock_gateway",
    model_id: "mock-structured",
    display_name: "Mock Structured",
    input_price_usd_per_million: 1,
    output_price_usd_per_million: 4,
    is_free: false,
    metadata_source: "mock fixture",
  },
];

async function mockAuth(page: Page) {
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
}

function mockProviderList(page: Page) {
  return page.route("**/api/v1/ai/providers", (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([provider]) });
    }
    return route.fulfill({ status: 404, contentType: "application/json", body: '{"detail":"not used by proof"}' });
  });
}

test("оператор обновляет ограниченный список моделей без egress", async ({ page }) => {
  await mockAuth(page);
  await mockProviderList(page);
  await page.route("**/api/v1/ai/providers/*/models", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(models) }),
  );

  await page.getByRole("link", { name: "Провайдеры" }).click();
  await expect(page.getByRole("heading", { name: "AI-провайдеры" })).toBeVisible();
  await expect(page.getByText("••••6789")).toBeVisible();

  await page.getByRole("button", { name: "Обновить модели" }).click();
  await expect(page.getByText("Доступные модели (2)")).toBeVisible();
  await expect(page.getByText("mock-small", { exact: true })).toBeVisible();
  await expect(page.getByText("mock-structured", { exact: true })).toBeVisible();
  await expect(page.getByText("https://mock.provider.invalid/v1")).toBeVisible();
  await expect(page.locator('input[type="password"]')).toHaveValue("");
});

test("ошибка discovery не раскрывает секрет и остаётся в интерфейсе", async ({ page }) => {
  await mockAuth(page);
  await mockProviderList(page);
  await page.route("**/api/v1/ai/providers/*/models", (route) =>
    route.fulfill({
      status: 502,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Model discovery unavailable" }),
    }),
  );

  await page.getByRole("link", { name: "Провайдеры" }).click();
  await page.getByRole("button", { name: "Обновить модели" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Model discovery unavailable" })).toBeVisible();
  await expect(page.getByText("sk-live-never-render-this-secret", { exact: true })).not.toBeVisible();
});

test("semantic coverage остаётся read-only advisory обзором", async ({ page }) => {
  await mockAuth(page);
  const projectId = "33333333-3333-4333-8333-333333333333";
  await page.route(`**/api/v1/projects/${projectId}`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ name: "Coverage proof" }) }),
  );
  await page.route(`**/api/v1/projects/${projectId}/semantic-signals`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        totals: { members: 2, bindings: 2, covered: 1, planned: 1, uncovered: 0, unbound: 0 },
        coverage: [
          { collection_keyword_id: "member-covered", project_keyword_id: "keyword-1", keyword_id: "keyword-1", geo_binding_id: "geo-1", status: "covered", plans: [{ plan_id: "plan-1", slug: "/approved", state: "approved" }] },
          { collection_keyword_id: "member-planned", project_keyword_id: "keyword-2", keyword_id: "keyword-2", geo_binding_id: "geo-2", status: "planned", plans: [{ plan_id: "plan-2", slug: "/draft", state: "draft" }] },
        ],
        collisions: [],
        cannibalization: [],
        unmapped_plans: [{ plan_id: "plan-3", slug: "/legacy", state: "draft" }],
        policy: { mode: "advisory", read_only: true, blocks_candidate: false, basis: "persisted snapshots" },
      }),
    }),
  );

  await page.evaluate((path) => {
    window.history.pushState({}, "", path);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, `/projects/${projectId}/semantic-coverage`);
  await expect(page.getByRole("heading", { name: "Coverage proof: semantic coverage" })).toBeVisible();
  await expect(page.getByText("planned (draft/review)")).toBeVisible();
  await expect(page.getByText("Unmapped plans: /legacy (draft). They do not count as semantic coverage.")).toBeVisible();
  await expect(page.getByText("не создаёт drafts, не применяет изменения, не запускает build и не публикует сайт")).toBeVisible();
  await expect(page.getByRole("button", { name: /Создать|Применить|Собрать|Опубликовать/ })).toHaveCount(0);
});

test("legal rejection сохраняет reason и manual remediation без публикации", async ({ page }) => {
  await mockAuth(page);
  const projectId = "55555555-5555-4555-8555-555555555555";
  const buildId = "66666666-6666-4666-8666-666666666666";
  let legalReviewBody: Record<string, unknown> | null = null;
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    if (route.request().method() === "POST" && url.pathname.endsWith(`/builds/${buildId}/legal-review`)) {
      legalReviewBody = route.request().postDataJSON() as Record<string, unknown>;
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "block" }) });
    }
    if (url.pathname === `/api/v1/projects/${projectId}`) {
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ id: projectId, name: "Legal proof", domain: "proof.test", niche: null, site_id: "site-proof", current_fact_revision_id: null, domain_check_meta: {} }) });
    }
    if (url.pathname.endsWith("/coverage")) return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ selected: 0, covered: 0, uncovered: [], plans: 0 }) });
    if (url.pathname.endsWith("/semantic-signals")) return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ totals: { members: 0, bindings: 0, covered: 0, planned: 0, uncovered: 0, unbound: 0 }, cannibalization: [], unmapped_plans: [] }) });
    if (url.pathname.endsWith("/lead-routing")) return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [] }) });
    if (url.pathname.endsWith("/builds")) return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([{ id: buildId, status: "ready", build_hash: "b".repeat(64), previous_build_hash: null, pages_built: 1, created_at: "2026-10-01T12:00:00Z", activated_at: null, release_gate: { status: "pass", blockers: [], warnings: [] }, legal_review: { status: "block", blockers: ["Approve the legal review for this candidate build"], review: { state: "pending", evidence_ref: null, reason: null, replacement_guidance: null, reviewed_at: null }, history: [] } }]) });
    if (url.pathname === "/api/v1/keywords") return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [] }) });
    return route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });

  await page.evaluate((path) => {
    window.history.pushState({}, "", path);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, `/projects/${projectId}`);
  await expect(page.getByText("Legal proof")).toBeVisible();
  await page.getByLabel("Причина отклонения").fill("Юридический адрес требует подтверждения.");
  await page.getByLabel("Рекомендация по исправлению").fill("Обновите подтверждённые facts и создайте новый candidate.");
  await page.getByRole("button", { name: "Отклонить legal review" }).click();
  await page.getByLabel("Ссылка или внутренний идентификатор evidence").fill("LEGAL-PROOF-1");
  await page.getByRole("button", { name: "Подтвердить отклонение" }).click();
  await expect.poll(() => legalReviewBody).not.toBeNull();
  expect(legalReviewBody).toMatchObject({
    decision: "rejected",
    evidence_ref: "LEGAL-PROOF-1",
    reason: "Юридический адрес требует подтверждения.",
    replacement_guidance: "Обновите подтверждённые facts и создайте новый candidate.",
  });
});

test("prompt history показывает stale evidence и блокирует активацию", async ({ page }) => {
  await mockAuth(page);
  const promptId = "architecture/propose-site-map";
  const revisionId = "44444444-4444-4444-8444-444444444444";
  await page.route("**/api/v1/ai/prompts", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([{
        id: promptId,
        baseline_version: "1",
        baseline_hash: "current-baseline-hash",
        path: "prompts/ai/architecture/propose-site-map.md",
        revisions: [{
          id: revisionId,
          key: promptId,
          version: 2,
          instructions: "Показывайте ограничения.",
          active: false,
          state: "approved",
          stale: false,
          runtime_using_packaged_baseline: false,
          baseline_hash: "current-baseline-hash",
          activation: { eligible: false, blockers: ["fixture_changed"] },
          activation_eligible: false,
          effective_diff: null,
          created_at: "2026-09-30T12:00:00Z",
          submitted_at: null,
          reviewed_at: "2026-09-30T12:01:00Z",
          decision_reason: null,
        }],
      }]),
    }),
  );
  await page.route("**/api/v1/ai/prompts/**/evaluations", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([
        {
          id: "run-current",
          status: "passed",
          baseline_hash: "current-baseline-hash",
          effective_prompt_hash: "effective-hash",
          fixture_hash: "old-fixture-hash",
          ruleset_version: "offline-fixture-v1",
          case_count: 2,
          passed_count: 2,
          error: null,
          completed_at: "2026-09-30T12:02:00Z",
          cases: [{ name: "current-contract", status: "passed", assertion_keys: ["safe"], diagnostic: null }],
        },
        {
          id: "run-failed",
          status: "failed",
          baseline_hash: "old-baseline-hash",
          effective_prompt_hash: "old-effective-hash",
          fixture_hash: "old-fixture-hash",
          ruleset_version: "offline-fixture-v1",
          case_count: 2,
          passed_count: 0,
          error: "fixture contract failed",
          completed_at: "2026-09-29T12:02:00Z",
          cases: [{ name: "old-contract", status: "failed", assertion_keys: ["safe"], diagnostic: "contract mismatch" }],
        },
      ]),
    }),
  );

  await page.getByRole("link", { name: "Системные prompts" }).click();
  await expect(page.getByRole("heading", { name: "Системные prompts" })).toBeVisible();
  await expect(page.getByText("Сохранённая evaluation относится к предыдущей версии fixture.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Активировать" })).toBeDisabled();
  await page.getByRole("button", { name: "Показать history" }).click();
  await expect(page.getByText("Evaluation history · 2 run(s)")).toBeVisible();
  await page.getByText("Evaluation history · 2 run(s)").click();
  await expect(page.getByText("fixture contract failed")).toBeVisible();
  await page.getByText("Fixture cases").first().click();
  await page.getByText("Fixture cases").nth(1).click();
  await expect(page.getByText("current-contract")).toBeVisible();
  await expect(page.getByText("old-contract")).toBeVisible();
});

test("AI workspace показывает queued, running и pending approval через polling", async ({ page }) => {
  await mockAuth(page);
  await mockProviderList(page);
  await page.route("**/api/v1/projects", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );

  const statuses = ["queued", "queued", "running", "pending_approval"];
  let runRequests = 0;
  await page.route("**/api/v1/ai/runs**", (route) => {
    const status = statuses[Math.min(runRequests++, statuses.length - 1)];
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([
        {
          id: "22222222-2222-4222-8222-222222222222",
          action: "architecture.site-map",
          status,
          provider_id: "mock_gateway",
          model_id: "mock-small",
          prompt_id: "architecture/propose-site-map",
          prompt_version: "1",
          prompt_hash: "prompt-hash",
          input_snapshot_hash: "input-hash",
          usage: { input_tokens: 0, output_tokens: 0 },
          cost_usd: null,
          error_code: null,
          created_at: "2026-09-30T12:00:00Z",
        },
      ]),
    });
  });

  await page.getByRole("link", { name: "AI workspace" }).click();
  await expect(page.getByRole("heading", { name: "AI workspace" })).toBeVisible();
  await expect(page.getByText("queued", { exact: true })).toBeVisible();
  await expect.poll(() => runRequests, { timeout: 7000 }).toBeGreaterThanOrEqual(4);
  await expect(page.getByText("pending_approval", { exact: true })).toBeVisible();
});
