import { expect, test, type Page } from "@playwright/test";

const projectId = "b1000000-0000-4000-8000-000000000001";
const logoId = "b1000000-0000-4000-8000-000000000002";
const faviconId = "b1000000-0000-4000-8000-000000000003";

async function mockAuth(page: Page) {
  let authenticated = false;
  await page.route("**/api/v1/security/me", (route) => route.fulfill({
    status: authenticated ? 200 : 401,
    body: authenticated ? "{}" : '{"detail":"not authenticated"}',
  }));
  await page.route("**/api/v1/auth/login", (route) => {
    authenticated = true;
    return route.fulfill({ status: 200, body: "{}" });
  });
  await page.route("**/api/v1/auth/refresh", (route) => route.fulfill({
    status: authenticated ? 200 : 401,
    body: authenticated ? "{}" : "{}",
  }));
  await page.goto("/login");
  await page.getByLabel("Email").fill("operator@example.test");
  await page.getByLabel("Пароль").fill("controlled-e2e-password");
  await page.getByRole("button", { name: "Войти" }).click();
}

test("project branding saves pinned assets without building or publishing", async ({ page }) => {
  const mutations: { path: string; body: string }[] = [];
  let branding = { version: 4 as number, logo: undefined as unknown, favicon: undefined as unknown };
  const asset = (id: string) => ({
    id,
    path: `/api/v1/media/${id}/file`,
    content_type: "image/webp",
    author: null,
    license: "own",
    availability: "eligible",
    hashes: { stored_sha256: id === logoId ? "a".repeat(64) : "b".repeat(64) },
  });

  await page.route("**/api/v1/**", (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const json = (body: unknown) => route.fulfill({ status: 200, body: JSON.stringify(body) });
    if (request.method() !== "GET") mutations.push({ path, body: request.postData() || "" });
    if (path === "/api/v1/security/me" || path === "/api/v1/auth/refresh") return json({});
    if (path === `/api/v1/projects/${projectId}/branding`) {
      if (request.method() === "PATCH") {
        const body = request.postDataJSON();
        branding = { version: body.version + 1, logo: body.logo, favicon: body.favicon };
      }
      return json(branding);
    }
    if (path === "/api/v1/media") return json([asset(logoId), asset(faviconId)]);
    if (path === `/api/v1/projects/${projectId}/design-profiles`) return json([]);
    if (path === `/api/v1/projects/${projectId}/design-profile`) return json({
      project_id: projectId,
      inherited_from_project_id: null,
      family_profile: null,
      project_profile: null,
      effective_profile: null,
      effective_profile_hash: null,
    });
    if (path === "/api/v1/blocks/kits") return json([]);
    if (path === `/api/v1/projects/${projectId}/intent-generation-runs`) return json([]);
    if (path === `/api/v1/projects/${projectId}/page-plans`) return json([]);
    if (path === "/api/v1/ai/providers") return json([]);
    if (path === "/api/v1/panel/alerts") return json({ items: [], unread: 0 });
    return json({});
  });

  await mockAuth(page);
  await page.goto(`/projects/${projectId}/design`);
  await expect(page.getByRole("heading", { name: "Логотип и значок сайта" })).toBeVisible();
  await page.getByLabel("Выбор логотипа").selectOption(logoId);
  await page.getByLabel("Описание логотипа", { exact: true }).fill("Логотип мастерской");
  await page.getByLabel("Выбор значка сайта").selectOption(faviconId);
  await page.getByRole("button", { name: "Сохранить оформление" }).click();
  await expect(page.getByText("Логотип и значок сайта сохранены для будущих пробных сборок.")).toBeVisible();

  const save = mutations.find((item) => item.path.endsWith("/branding"));
  expect(save?.body).toContain(logoId);
  expect(save?.body).toContain(faviconId);
  expect(save?.body).toContain("Логотип мастерской");
  expect(mutations.filter((item) => /builds|publish|page-drafts|indexnow/i.test(item.path))).toEqual([]);
});
