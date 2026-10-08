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
  await expect(page).toHaveURL(/\/$/);
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

test("замена ключа не объявляется неуспешной при ошибке обновления списка", async ({ page }) => {
  await mockAuth(page);
  let refreshUnavailable = false;
  const mutations: string[] = [];
  await page.route("**/api/v1/ai/providers", (route) => route.fulfill(refreshUnavailable
    ? { status: 503, contentType: "application/json", body: '{"detail":"provider list unavailable"}' }
    : { status: 200, contentType: "application/json", body: JSON.stringify([provider]) }));
  await page.route(`**/api/v1/ai/providers/${provider.id}`, (route) => {
    if (route.request().method() === "PATCH") {
      mutations.push(route.request().postData() || "");
      refreshUnavailable = true;
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ...provider, credential_last4: "4321" }) });
    }
    return route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
  });

  await page.getByRole("link", { name: "Провайдеры" }).click();
  await page.getByRole("button", { name: "Заменить ключ" }).click();
  await page.getByLabel("Новый API key").fill("controlled-replacement-key");
  await page.getByRole("button", { name: "Сохранить новый ключ" }).click();
  await expect(page.getByRole("status")).toContainText("API key для Mock gateway заменён");
  await expect(page.getByRole("alert")).toContainText("Действие выполнено, но список подключений не обновился");
  await expect(page.getByText("controlled-replacement-key")).toHaveCount(0);
  expect(mutations).toEqual([JSON.stringify({ api_key: "controlled-replacement-key" })]);
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

test("facts deep link shows redacted revisions without mutation", async ({ page }) => {
  await mockAuth(page);
  const projectId = "88888888-8888-4888-8888-888888888888";
  const mutationRequests: string[] = [];
  await page.route("**/api/v1/**", (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === "/api/v1/security/me" || url.pathname === "/api/v1/auth/refresh") {
      return route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
    }
    if (request.method() !== "GET") mutationRequests.push(`${request.method()} ${url.pathname}`);
    if (url.pathname === `/api/v1/projects/${projectId}`) {
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ id: projectId, name: "Facts proof" }) });
    }
    if (url.pathname === `/api/v1/projects/${projectId}/facts`) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([{
          id: "99999999-9999-4999-8999-999999999999",
          version: 3,
          state: "confirmed",
          facts: {
            organization: "Proof Organization",
            contacts: { phone: "+7 900 000-00-00", work_hours: "09:00–18:00" },
            allowed_claims: ["Письменная гарантия"],
          },
          has_private_lead_email: true,
          source_notes: "Contact private-recipient@example.test only through encrypted delivery.",
          facts_hash: "a".repeat(64),
          confirmed_at: "2026-10-01T12:00:00Z",
          created_at: "2026-10-01T11:00:00Z",
        }]),
      });
    }
    return route.fulfill({ status: 404, contentType: "application/json", body: '{"detail":"not used by proof"}' });
  });

  await page.evaluate((path) => {
    window.history.pushState({}, "", path);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, `/projects/${projectId}/facts`);

  await expect(page.getByRole("heading", { name: "Facts · Facts proof" })).toBeVisible();
  await expect(page.getByText("Proof Organization", { exact: true })).toBeVisible();
  await expect(page.getByText("configured", { exact: true })).toBeVisible();
  await expect(page.getByText("private-recipient@example.test", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Сохранить|Подтвердить/ })).toHaveCount(0);
  expect(mutationRequests).toEqual([]);
});

test("pages deep link shows PagePlan and QA lineage without mutation", async ({ page }) => {
  await mockAuth(page);
  const projectId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
  const planId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
  const mutationRequests: string[] = [];
  await page.route("**/api/v1/**", (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === "/api/v1/security/me" || url.pathname === "/api/v1/auth/refresh") {
      return route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
    }
    if (request.method() !== "GET") mutationRequests.push(`${request.method()} ${url.pathname}`);
    if (url.pathname === `/api/v1/projects/${projectId}`) {
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ id: projectId, name: "Pages proof" }) });
    }
    if (url.pathname === `/api/v1/projects/${projectId}/page-plans`) {
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([{
        id: planId, slug: "/repair", objective: "Explain repair service", intent: "commercial", kit_key: "service-local-v1", state: "approved", version: 2, decision_reason: null, reviewed_at: "2026-10-01T12:00:00Z",
      }]) });
    }
    if (url.pathname === `/api/v1/projects/${projectId}/page-drafts`) {
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([{
        id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc", page_plan_id: planId, revision: 3, state: "review", content_hash: "b".repeat(64), last_qa_verdict: "pass", qa_runs: [{ verdict: "pass" }], failure_message: null, updated_at: "2026-10-01T12:30:00Z",
      }]) });
    }
    return route.fulfill({ status: 404, contentType: "application/json", body: '{"detail":"not used by proof"}' });
  });

  await page.evaluate((path) => {
    window.history.pushState({}, "", path);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, `/projects/${projectId}/pages`);

  await expect(page.getByRole("heading", { name: "Pages · Pages proof" })).toBeVisible();
  await expect(page.getByText("/repair", { exact: true })).toHaveCount(2);
  await expect(page.getByText("pass", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /Создать|Проверить|Применить|Одобрить|Опубликовать/ })).toHaveCount(0);
  expect(mutationRequests).toEqual([]);
});

test("project overview points to the next manual step without leaking facts", async ({ page }) => {
  await mockAuth(page);
  const projectId = "33333333-3333-4333-8333-333333333333";
  const mutationRequests: string[] = [];
  await page.route("**/api/v1/**", (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/v1/security/me" || path === "/api/v1/auth/refresh") {
      return route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
    }
    if (request.method() !== "GET") mutationRequests.push(`${request.method()} ${path}`);
    if (path === `/api/v1/projects/${projectId}`) {
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
        id: projectId, name: "Overview proof", domain: "city.example.test", niche: "ремонт", site_id: null,
        domain_check_meta: { dns_status: "ok" },
      }) });
    }
    if (path === `/api/v1/projects/${projectId}/workflow-summary`) {
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
        facts_confirmed: true, keyword_count: 3, geo_count: 1, approved_collection_count: 1,
        approved_structure_count: 0, approved_plan_count: 0, applied_draft_count: 0,
        ready_candidate_count: 0, running_candidate_count: 0, published: false,
      }) });
    }
    return route.fulfill({ status: 404, contentType: "application/json", body: '{"detail":"not used by proof"}' });
  });

  await page.goto(`/projects/${projectId}/overview`);
  await expect(page.getByRole("heading", { name: "Overview proof" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Следующее действие" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Следующее действие" }).locator("..").getByText("Одобрить единую структуру сайта")).toBeVisible();
  await expect(page.getByRole("link", { name: "Перейти к этапу" })).toHaveAttribute("href", `/projects/${projectId}/site-structure`);
  await expect(page.getByText("private-recipient@example.test")).toHaveCount(0);
  expect(mutationRequests).toEqual([]);
});

test("operator batches only approved PageDraft snapshots without apply or publish", async ({ page }) => {
  await mockAuth(page);
  const projectId = "33333333-3333-4333-8333-333333333333";
  const planIds = ["44444444-4444-4444-8444-444444444441", "44444444-4444-4444-8444-444444444442"];
  const mutations: { path: string; body: unknown }[] = [];
  await page.route("**/api/v1/**", (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const json = (body: unknown) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
    if (path === "/api/v1/security/me" || path === "/api/v1/auth/refresh") return json({});
    if (request.method() !== "GET") mutations.push({ path, body: request.postDataJSON() });
    if (path === `/api/v1/projects/${projectId}`) return json({
      id: projectId, name: "Batch proof", domain: null, site_id: null, current_fact_revision_id: null, domain_check_meta: {},
    });
    if (path === `/api/v1/projects/${projectId}/page-plans/drafts/batch`) return json({ drafts: [], drafts_only: true });
    if (path === `/api/v1/projects/${projectId}/page-plans`) return json(planIds.map((id, index) => ({
      id, slug: index ? "/second/" : "/first/", objective: "Проверка безопасных черновиков", state: "approved",
      kit_key: "service-local-v1", version: 1, block_selection: {}, intent: null,
    })));
    if (path === `/api/v1/projects/${projectId}/page-drafts`) return json([{
      id: "55555555-5555-4555-8555-555555555555", page_plan_id: planIds[0], revision: 2,
      state: "draft", page_manifest: {}, qa_runs: [], last_qa_verdict: null, content_hash: "a".repeat(64),
      failure_message: null, author_profile_revision_id: null,
    }]);
    if (path === "/api/v1/keywords") return json({ items: [] });
    if (path === `/api/v1/projects/${projectId}/coverage`) return json({ selected: 0, covered: 0, uncovered: [], plans: 2 });
    if (path === `/api/v1/projects/${projectId}/semantic-signals`) return json({
      totals: { members: 0, bindings: 0, covered: 0, planned: 0, uncovered: 0, unbound: 0 },
      cannibalization: [], unmapped_plans: [],
    });
    if (path === `/api/v1/projects/${projectId}/semantic-sources/bukvarix/status`) return json({
      enabled: true, status: "https_public_free", message: "HTTPS public free-mode",
      personal_credentials_supported: false, max_seed_keywords: 10, max_results_per_run: 1000, non_publish_policy: true,
    });
    if (path.startsWith(`/api/v1/projects/${projectId}/`) || path === "/api/v1/geo" || path === "/api/v1/media" || path === "/api/v1/ai/providers") return json([]);
    return route.fulfill({ status: 404, contentType: "application/json", body: '{"detail":"not used by proof"}' });
  });

  await page.goto(`/projects/${projectId}`);
  await expect(page.getByRole("heading", { name: "Batch proof" })).toBeVisible();
  await page.getByLabel("В пакет черновиков: /first/ · v1").check();
  await page.getByLabel("В пакет черновиков: /second/ · v1").check();
  await page.getByText("Подтверждаю создание только PageDraft для выбранных планов.").click();
  await page.getByRole("button", { name: "Создать выбранные черновики (2)" }).click();
  await expect(page.getByText("Созданы только выбранные noindex PageDraft. QA, review, apply, candidate и публикация выполняются отдельно.")).toBeVisible();
  expect(mutations).toEqual([{ path: `/api/v1/projects/${projectId}/page-plans/drafts/batch`, body: {
    items: [
      { plan_id: planIds[0], expected_latest_revision: 2 },
      { plan_id: planIds[1], expected_latest_revision: 0 },
    ],
    confirm_drafts_only: true,
  } }]);
});

test("releases center shows queued candidate details without mutation", async ({ page }) => {
  await mockAuth(page);
  const projectId = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
  const mutationRequests: string[] = [];
  await page.route("**/api/v1/**", (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === "/api/v1/security/me" || url.pathname === "/api/v1/auth/refresh") {
      return route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
    }
    if (request.method() !== "GET") mutationRequests.push(`${request.method()} ${url.pathname}`);
    if (url.pathname === `/api/v1/projects/${projectId}`) {
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
        id: projectId, name: "Releases proof", domain: "example.test", site_id: "site-proof", domain_check_meta: { dns_status: "ok" },
      }) });
    }
    if (url.pathname === `/api/v1/projects/${projectId}/builds`) {
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([{
        id: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee", status: "queued", build_hash: null, previous_build_hash: null, pages_built: 0, duration_ms: 0,
        attempt_count: 0, failure_code: null, input_snapshot_hash: "c".repeat(64), snapshot_version: 1, created_at: "2026-10-01T12:00:00Z", started_at: null,
        completed_at: null, activated_at: null, first_published_at: null, is_active: false, is_historical_published: false, retryable: false, rollback_eligible: false,
        events: [{ sequence: 1, attempt: 0, type: "queued", code: null, details: {}, created_at: "2026-10-01T12:00:00Z" }],
        release_gate: null,
        legal_review: { status: "block", blockers: ["Approve the legal review for this candidate build"], review: { state: "pending", evidence_ref: null, reason: null, replacement_guidance: null, reviewed_at: null } },
        index_promotion_provenance: [],
      }]) });
    }
    if (url.pathname === `/api/v1/projects/${projectId}/scheduled-work`) {
      return route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    }
    return route.fulfill({ status: 404, contentType: "application/json", body: '{"detail":"not used by proof"}' });
  });

  await page.evaluate((path) => {
    window.history.pushState({}, "", path);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, `/projects/${projectId}/releases`);

  await expect(page.getByRole("heading", { name: "Candidate-сборки · Releases proof" })).toBeVisible();
  await expect(page.getByText("queued", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Безопасная история выполнения · 1")).toBeVisible();
  await expect(page.getByRole("button", { name: "Создать candidate-сборку" })).toBeVisible();
  expect(mutationRequests).toEqual([]);
});

test("releases keep candidate history visible when scheduler is unavailable", async ({ page }) => {
  await mockAuth(page);
  const projectId = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
  let schedulerAvailable = false;
  const mutationRequests: string[] = [];
  await page.route("**/api/v1/**", (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/v1/security/me" || path === "/api/v1/auth/refresh") {
      return route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
    }
    if (request.method() !== "GET") mutationRequests.push(`${request.method()} ${path}`);
    if (path === `/api/v1/projects/${projectId}`) {
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
        id: projectId, name: "Scheduler outage proof", domain: "example.test", site_id: "site-proof", domain_check_meta: {},
      }) });
    }
    if (path === `/api/v1/projects/${projectId}/builds`) {
      return route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    }
    if (path === `/api/v1/projects/${projectId}/scheduled-work`) {
      return route.fulfill(schedulerAvailable ? {
        status: 200, contentType: "application/json", body: JSON.stringify([{
          id: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee", work_type: "site_build", source_id: "candidate",
          state: "queued", priority: 50, not_before: null, attempt_count: 0, lease_expires_at: null, failure_code: null,
        }]),
      } : { status: 503, contentType: "application/json", body: '{"detail":"queue unavailable"}' });
    }
    return route.fulfill({ status: 404, contentType: "application/json", body: '{"detail":"not used by proof"}' });
  });

  await page.goto(`/projects/${projectId}/releases`);
  await expect(page.getByRole("heading", { name: "Candidate-сборки · Scheduler outage proof" })).toBeVisible();
  await expect(page.getByText("Сборок пока нет")).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("Её состояние неизвестно");
  await expect(page.getByText("В очереди нет сборок")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Пауза" })).toHaveCount(0);
  schedulerAvailable = true;
  await page.getByRole("button", { name: "Обновить" }).click();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Пауза" })).toBeVisible();
  expect(mutationRequests).toEqual([]);
});

test("candidate action reports success even when refresh fails", async ({ page }) => {
  await mockAuth(page);
  const projectId = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
  let created = false;
  const mutations: string[] = [];
  await page.route("**/api/v1/**", (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/v1/security/me" || path === "/api/v1/auth/refresh") {
      return route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
    }
    if (request.method() !== "GET") mutations.push(`${request.method()} ${path}`);
    if (path === `/api/v1/projects/${projectId}`) {
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
        id: projectId, name: "Refresh proof", domain: "example.test", site_id: "site-proof", domain_check_meta: {},
      }) });
    }
    if (path === `/api/v1/projects/${projectId}/builds`) {
      if (request.method() === "POST") {
        created = true;
        return route.fulfill({ status: 202, contentType: "application/json", body: "{}" });
      }
      return route.fulfill(created
        ? { status: 503, contentType: "application/json", body: '{"detail":"history unavailable"}' }
        : { status: 200, contentType: "application/json", body: "[]" });
    }
    if (path === `/api/v1/projects/${projectId}/scheduled-work`) {
      return route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    }
    return route.fulfill({ status: 404, contentType: "application/json", body: '{"detail":"not used by proof"}' });
  });

  await page.goto(`/projects/${projectId}/releases`);
  await page.getByRole("button", { name: "Создать candidate-сборку" }).click();
  await expect(page.getByText("Снимок зафиксирован. Очередь начнёт сборку в указанное время и не публикует сайт автоматически.")).toBeVisible();
  await expect(page.getByRole("alert").first()).toContainText("Действие выполнено, но история сборок не обновилась");
  await expect(page.getByRole("button", { name: "Пауза" })).toHaveCount(0);
  expect(mutations).toEqual([`POST /api/v1/projects/${projectId}/builds`]);
});

test("rollback requires exact hash phrase and never publishes", async ({ page }) => {
  await mockAuth(page);
  const projectId = "abababab-abab-4bab-8bab-abababababab";
  const buildHash = "d".repeat(64);
  const mutationRequests: { path: string; body: unknown }[] = [];
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === "/api/v1/security/me" || url.pathname === "/api/v1/auth/refresh") {
      return route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
    }
    if (request.method() === "POST") mutationRequests.push({ path: url.pathname, body: request.postDataJSON() });
    if (url.pathname === `/api/v1/projects/${projectId}`) {
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
        id: projectId, name: "Rollback proof", domain: "example.test", site_id: "site-proof", domain_check_meta: { dns_status: "ok" },
      }) });
    }
    if (url.pathname === `/api/v1/projects/${projectId}/builds`) {
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([{
        id: "bcbcbcbc-bcbc-4cbc-8cbc-bcbcbcbcbcbc", status: "ready", build_hash: buildHash, previous_build_hash: "e".repeat(64), pages_built: 1, duration_ms: 12,
        attempt_count: 1, failure_code: null, input_snapshot_hash: "c".repeat(64), snapshot_version: 1, created_at: "2026-10-01T12:00:00Z", started_at: "2026-10-01T12:00:01Z",
        completed_at: "2026-10-01T12:00:02Z", activated_at: "2026-10-01T12:00:02Z", first_published_at: "2026-10-01T12:00:02Z", is_active: false, is_historical_published: true, retryable: false, rollback_eligible: true,
        events: [], release_gate: { status: "pass", blockers: [], warnings: [] },
        legal_review: { status: "pass", blockers: [], review: { state: "approved", evidence_ref: "LEGAL-1", reason: null, replacement_guidance: null, reviewed_at: "2026-10-01T12:00:02Z" } },
        index_promotion_provenance: [],
      }]) });
    }
    if (url.pathname === `/api/v1/projects/${projectId}/rollbacks`) {
      return route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
    }
    if (url.pathname === `/api/v1/projects/${projectId}/scheduled-work`) {
      return route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    }
    return route.fulfill({ status: 404, contentType: "application/json", body: '{"detail":"not used by proof"}' });
  });

  await page.goto(`/projects/${projectId}/releases`);
  const rollbackTrigger = page.getByRole("button", { name: "Откатить на выбранный hash" });
  await rollbackTrigger.click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toHaveAttribute("aria-labelledby");
  await expect(dialog).toHaveAttribute("aria-describedby");
  const descriptionId = await dialog.getAttribute("aria-describedby");
  await expect(page.locator(`#${descriptionId}`)).toContainText("Текущий release");
  const confirm = dialog.getByRole("button", { name: "Выполнить откат" });
  await dialog.getByLabel(`Введите: ROLLBACK ${buildHash}`).fill("ROLLBACK wrong");
  await expect(confirm).toBeDisabled();
  expect(mutationRequests).toEqual([]);
  await page.keyboard.press("Escape");
  await expect(dialog).not.toBeVisible();
  await expect(rollbackTrigger).toBeFocused();
  expect(mutationRequests).toEqual([]);
  await page.getByRole("button", { name: "Откатить на выбранный hash" }).click();
  await expect(dialog).toBeVisible();
  await dialog.getByLabel(`Введите: ROLLBACK ${buildHash}`).fill(`ROLLBACK ${buildHash}`);
  await expect(confirm).toBeEnabled();
  await confirm.click();
  await expect.poll(() => mutationRequests.length).toBe(1);
  expect(mutationRequests).toEqual([{
    path: `/api/v1/projects/${projectId}/rollbacks`,
    body: { build_hash: buildHash, confirmation_text: `ROLLBACK ${buildHash}` },
  }]);
  expect(mutationRequests.some((request) => request.path.endsWith("/publish"))).toBe(false);
});

test("routing deep link shows redacted policy metadata without mutation", async ({ page }) => {
  await mockAuth(page);
  const projectId = "ffffffff-ffff-4fff-8fff-ffffffffffff";
  const mutationRequests: string[] = [];
  await page.route("**/api/v1/**", (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === "/api/v1/security/me" || url.pathname === "/api/v1/auth/refresh") {
      return route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
    }
    if (request.method() !== "GET") mutationRequests.push(`${request.method()} ${url.pathname}`);
    if (url.pathname === `/api/v1/projects/${projectId}`) {
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ id: projectId, name: "Routing proof" }) });
    }
    if (url.pathname === `/api/v1/projects/${projectId}/lead-routing`) {
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [{
        id: "11111111-2222-4333-8444-555555555555", version: 4, state: "active", submitted_at: "2026-10-01T10:00:00Z", reviewed_at: "2026-10-01T11:00:00Z", decision_reason: null, created_at: "2026-10-01T09:00:00Z",
        destinations: [{ id: "66666666-7777-4888-8999-000000000000", target_key: "primary-email", channel: "email", required: true, configured: true }],
        unsafe_recipient: "private-recipient@example.test",
        unsafe_secret: "never-render-this-routing-secret",
      }] }) });
    }
    return route.fulfill({ status: 404, contentType: "application/json", body: '{"detail":"not used by proof"}' });
  });

  await page.evaluate((path) => {
    window.history.pushState({}, "", path);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, `/projects/${projectId}/routing`);

  await expect(page.getByRole("heading", { name: "Routing · Routing proof" })).toBeVisible();
  await expect(page.getByText("primary-email · required", { exact: true })).toBeVisible();
  await expect(page.getByText("private-recipient@example.test", { exact: true })).toHaveCount(0);
  await expect(page.getByText("never-render-this-routing-secret", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Создать|На review|Активировать|Отклонить/ })).toHaveCount(0);
  expect(mutationRequests).toEqual([]);
});

test("legal rejection сохраняет reason и manual remediation без публикации", async ({ page }) => {
  await mockAuth(page);
  const projectId = "55555555-5555-4555-8555-555555555555";
  const buildId = "66666666-6666-4666-8666-666666666666";
  let legalReviewBody: Record<string, unknown> | null = null;
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/v1/security/me" || url.pathname === "/api/v1/auth/refresh") {
      return route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
    }
    if (route.request().method() === "POST" && url.pathname.endsWith(`/builds/${buildId}/legal-review`)) {
      legalReviewBody = route.request().postDataJSON() as Record<string, unknown>;
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "block" }) });
    }
    if (url.pathname === `/api/v1/projects/${projectId}`) {
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ id: projectId, name: "Legal proof", domain: "proof.test", site_id: "site-proof", domain_check_meta: {} }) });
    }
    if (url.pathname.endsWith("/builds")) return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([{
      id: buildId, status: "ready", build_hash: "b".repeat(64), previous_build_hash: null, pages_built: 1, duration_ms: 0,
      attempt_count: 1, failure_code: null, input_snapshot_hash: "c".repeat(64), snapshot_version: 1, created_at: "2026-10-01T12:00:00Z", started_at: null,
      completed_at: null, activated_at: null, first_published_at: null, is_active: false, is_historical_published: false, retryable: false, rollback_eligible: false,
      events: [], release_gate: { status: "pass", blockers: [], warnings: [] },
      legal_review: { status: "block", blockers: ["Approve the legal review for this candidate build"], review: { state: "pending", evidence_ref: null, reason: null, replacement_guidance: null, reviewed_at: null } },
      index_promotion_provenance: [],
    }]) });
    if (url.pathname === `/api/v1/projects/${projectId}/scheduled-work`) {
      return route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    }
    return route.fulfill({ status: 404, contentType: "application/json", body: '{"detail":"not used by proof"}' });
  });

  await page.goto(`/projects/${projectId}/releases`);
  await expect(page.getByRole("heading", { name: "Candidate-сборки · Legal proof" })).toBeVisible();
  await page.getByLabel("Ссылка или внутренний идентификатор evidence").fill("LEGAL-PROOF-1");
  await page.getByLabel("Причина отклонения").fill("Юридический адрес требует подтверждения.");
  await page.getByLabel("Рекомендация по исправлению").fill("Обновите подтверждённые facts и создайте новый candidate.");
  await page.getByRole("button", { name: "Отклонить legal review" }).click();
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

test("releases center renders immutable index-promotion provenance without mutation", async ({ page }) => {
  await mockAuth(page);
  const projectId = "33333333-3333-4333-8333-333333333333";
  const buildId = "44444444-4444-4444-8444-444444444444";
  const siteId = "55555555-5555-4555-8555-555555555555";
  const mutationRequests: string[] = [];
  await page.route("**/api/v1/**", (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === "/api/v1/security/me" || url.pathname === "/api/v1/auth/refresh") {
      return route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
    }
    if (request.method() !== "GET") mutationRequests.push(`${request.method()} ${url.pathname}`);
    if (url.pathname === `/api/v1/projects/${projectId}`) {
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
        id: projectId, name: "Controlled provenance project", domain: "example.test", site_id: siteId, domain_check_meta: {},
      }) });
    }
    if (url.pathname === `/api/v1/projects/${projectId}/builds`) {
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([{
        id: buildId, status: "ready", build_hash: "a".repeat(64), previous_build_hash: null, pages_built: 1, duration_ms: 0,
        attempt_count: 1, failure_code: null, input_snapshot_hash: "c".repeat(64), snapshot_version: 1, created_at: "2026-10-01T12:00:00Z", started_at: null,
        completed_at: null, activated_at: null, first_published_at: null, is_active: false, is_historical_published: false, retryable: false, rollback_eligible: false,
        events: [], release_gate: { status: "pass", blockers: [], warnings: [] },
        legal_review: { status: "pass", blockers: [], review: { state: "approved", evidence_ref: "LEGAL-1", reason: null, replacement_guidance: null, reviewed_at: "2026-10-01T12:00:00Z" } },
        index_promotion_provenance: [{ slug: "/", reason: "Подтверждено для выдачи после passing QA", decided_at: "2026-10-01T11:30:00Z" }],
      }]) });
    }
    if (url.pathname === `/api/v1/projects/${projectId}/scheduled-work`) {
      return route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    }
    return route.fulfill({ status: 404, contentType: "application/json", body: '{"detail":"not used by proof"}' });
  });

  await page.goto(`/projects/${projectId}/releases`);
  const provenance = page.locator("details").filter({ hasText: "Подтверждений индексации: 1" });
  await provenance.locator("summary").click();
  await expect(provenance.locator("p")).toContainText("Подтверждено для выдачи после passing QA");
  await expect(provenance.locator("p")).toContainText("/");
  expect(mutationRequests).toEqual([]);
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
