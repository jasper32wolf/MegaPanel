import { expect, test } from "@playwright/test";

const asset = {
  id: "11111111-1111-4111-8111-111111111111",
  path: "/api/v1/media/11111111-1111-4111-8111-111111111111/file",
  content_type: "image/webp",
  source: "licensed registry",
  license: "CC BY 4.0",
  author: "Operator",
  phash: "aaaaaaaaaaaaaaaa",
  normalized: false,
  tags: [],
  provenance: { rights_basis: "cc", rights_confirmed: true },
  hashes: { stored_sha256: "a".repeat(64) },
  availability: "eligible",
};

async function mockMediaAuth(page: import("@playwright/test").Page) {
  await page.route("**/api/v1/security/me", (route) =>
    route.fulfill({ status: 401, contentType: "application/json", body: '{"detail":"not authenticated"}' }),
  );
  await page.route("**/api/v1/auth/login", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: '{"ok":true}' }),
  );
  await page.route("**/api/v1/auth/refresh", (route) =>
    route.fulfill({ status: 401, contentType: "application/json", body: '{"detail":"not authenticated"}' }),
  );
}

async function loginToMedia(page: import("@playwright/test").Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill("manager@example.test");
  await page.getByLabel("Пароль").fill("controlled-e2e-password");
  await page.getByRole("button", { name: "Войти" }).click();
  const skip = page.getByRole("link", { name: "Перейти к основному содержимому" });
  await skip.focus();
  await expect(skip).toBeFocused();
  await skip.press("Enter");
  await expect(page.locator("#main-content")).toBeFocused();
  await page.getByRole("link", { name: "Медиатека" }).click();
  await expect(page.locator("#main-content")).toBeFocused();
}

test("manager records a route-mocked rejected media review without replacement", async ({ page }) => {
  let current: Record<string, unknown> | null = null;
  await mockMediaAuth(page);
  await page.route("**/api/v1/media", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([asset]) }),
  );
  await page.route("**/api/v1/media/*/review-decisions", async (route) => {
    if (route.request().method() === "POST") {
      current = route.request().postDataJSON() as Record<string, unknown>;
      return route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          id: 1,
          ...current,
          stored_sha256: asset.hashes.stored_sha256,
          actor_id: "22222222-2222-4222-8222-222222222222",
          created_at: "2026-10-01T12:00:00Z",
          record_hash: "b".repeat(64),
        }),
      });
    }
    const item = current
      ? {
          id: 1,
          ...current,
          stored_sha256: asset.hashes.stored_sha256,
          actor_id: "22222222-2222-4222-8222-222222222222",
          created_at: "2026-10-01T12:00:00Z",
          record_hash: "b".repeat(64),
        }
      : null;
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        asset_id: asset.id,
        stored_sha256: asset.hashes.stored_sha256,
        current: item,
        items: item ? [item] : [],
      }),
    });
  });

  await loginToMedia(page);
  const trigger = page.getByRole("button", { name: "Записать решение проверки" });
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "Решение проверки медиа" });
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText("Медиа 11111111 · image/webp");
  await expect(dialog).toContainText("SHA-256 aaaaaaaaaaaaaaaa");
  await expect(dialog).toHaveAttribute("aria-describedby");
  await dialog.getByLabel("Решение").selectOption("rejected");
  await dialog.getByLabel(/Причина/).fill("License evidence is incomplete");
  await dialog.getByLabel(/Инструкция по ручной замене/).fill("Upload a licensed replacement manually");
  await dialog.getByLabel(/Доказательство/).fill("Review ticket MR-1");
  await dialog.getByRole("button", { name: "Сохранить решение" }).click();

  await expect.poll(() => current).toMatchObject({
    decision: "rejected",
    reason: "License evidence is incomplete",
    manual_replacement_guidance: "Upload a licensed replacement manually",
    evidence: "Review ticket MR-1",
  });
  await expect(dialog).toBeHidden();
  await expect(trigger).toBeFocused();
  await expect(page.getByRole("status")).toContainText("Решение проверки сохранено.");
  await expect(page.getByText("проверка отклонена")).toBeVisible();
  await expect(page.getByText("Ручная замена: Upload a licensed replacement manually", { exact: true })).toBeVisible();
  const historySummary = page.locator("summary", { hasText: "История проверок" });
  await trigger.press("Tab");
  await expect(historySummary).toBeFocused();
  await expect(historySummary).toHaveCSS("outline-style", "solid");
  await expect(historySummary).toHaveCSS("outline-width", "3px");
});

test("media review keeps validation feedback in the open dialog", async ({ page }) => {
  await mockMediaAuth(page);
  await page.route("**/api/v1/media", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([asset]) }),
  );
  await page.route("**/api/v1/media/*/review-decisions", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ asset_id: asset.id, stored_sha256: asset.hashes.stored_sha256, current: null, items: [] }),
    }),
  );

  await loginToMedia(page);
  await page.getByRole("button", { name: "Записать решение проверки" }).click();
  const dialog = page.getByRole("dialog", { name: "Решение проверки медиа" });
  await dialog.getByLabel("Решение").selectOption("rejected");
  await dialog.getByLabel(/Причина/).fill("Причина без инструкции");
  await dialog.getByLabel(/Инструкция по ручной замене/).fill(" ");
  await dialog.getByRole("button", { name: "Сохранить решение" }).click();
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("alert")).toContainText("Для отклонения укажите причину");
});
