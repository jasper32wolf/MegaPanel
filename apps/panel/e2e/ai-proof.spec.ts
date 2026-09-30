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
  await page.route("**/api/v1/security/me", (route) =>
    route.fulfill({ status: 401, contentType: "application/json", body: '{"detail":"not authenticated"}' }),
  );
  await page.route("**/api/v1/auth/login", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: '{"ok":true}' }),
  );
  await page.route("**/api/v1/auth/refresh", (route) =>
    route.fulfill({ status: 401, contentType: "application/json", body: '{"detail":"not authenticated"}' }),
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
