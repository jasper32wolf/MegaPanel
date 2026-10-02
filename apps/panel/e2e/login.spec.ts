import { expect, test, type Page } from "@playwright/test";

const email = process.env.E2E_OPERATOR_EMAIL || "e2e@example.com";
const password = process.env.E2E_OPERATOR_PASSWORD || "e2e-ci-password";

async function login(page: Page) {
  await page.goto("/");

  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByLabel("Email")).toBeVisible();
  await expect(page.getByLabel("Пароль")).toBeVisible();
  await expect(page.getByRole("button", { name: "Войти" })).toBeVisible();

  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Пароль").fill(password);
  await page.getByRole("button", { name: "Войти" }).click();

  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("heading", { name: "Обзор" })).toBeVisible();
  await expect(page.getByText("API: ok")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Вход" })).not.toBeVisible();
}

test("оператор входит и видит обзор", async ({ page }) => {
  await login(page);
});

test("навигация не превращает выход в плавающий блок", async ({ page }) => {
  await login(page);

  const footer = page.locator(".nav-footer");
  const logout = footer.getByRole("button", { name: "Выйти" });
  await footer.scrollIntoViewIfNeeded();
  await expect(logout).toBeVisible();

  const layout = await page.evaluate(() => {
    const navElement = document.querySelector<HTMLElement>(".nav");
    const footerElement = document.querySelector<HTMLElement>(".nav-footer");
    const mainElement = document.querySelector<HTMLElement>(".main");
    if (!navElement || !footerElement || !mainElement) throw new Error("panel shell is incomplete");
    const navRect = navElement.getBoundingClientRect();
    const footerRect = footerElement.getBoundingClientRect();
    const mainRect = mainElement.getBoundingClientRect();
    return {
      navOverflow: getComputedStyle(navElement).overflowY,
      footerInsideNav: footerRect.left >= navRect.left && footerRect.right <= navRect.right,
      footerInsideViewport: footerRect.top >= 0 && footerRect.bottom <= window.innerHeight,
      footerBeforeMain: footerRect.right <= mainRect.left,
    };
  });

  expect(layout.navOverflow).toBe("auto");
  expect(layout.footerInsideNav).toBe(true);
  expect(layout.footerInsideViewport).toBe(true);
  expect(layout.footerBeforeMain).toBe(true);

  await page.setViewportSize({ width: 760, height: 700 });
  await page.reload();
  await expect(page.getByRole("heading", { name: "Обзор" })).toBeVisible();
  await expect(page.locator(".nav-footer").getByRole("button", { name: "Выйти" })).toBeVisible();
  await expect(page.locator(".nav")).toHaveCSS("overflow-y", "visible");
});

test("оператор видит и отзывает другую сессию", async ({ page, browser }) => {
  await login(page);

  const otherContext = await browser.newContext({ baseURL: "http://127.0.0.1:5173" });
  try {
    await login(await otherContext.newPage());
    await page.getByRole("link", { name: "Настройки", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "Настройки", exact: true }),
    ).toBeVisible();

    const revokeButtons = page.getByRole("button", { name: "Отозвать" });
    await expect.poll(() => revokeButtons.count()).toBeGreaterThan(0);
    const before = await revokeButtons.count();
    await revokeButtons.first().click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await page.getByRole("dialog").getByRole("button", { name: "Отозвать" }).click();
    await expect(revokeButtons).toHaveCount(before - 1);
    await page.getByRole("link", { name: "Обзор" }).click();
    await expect(page.getByRole("heading", { name: "Обзор" })).toBeVisible();
    await expect(page.getByText("API: ok")).toBeVisible();
  } finally {
    await otherContext.close();
  }
});

test("оператор создаёт и готовит candidate без публикации", async ({ page }) => {
  const publishRequests: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "POST" && /\/builds\/[^/]+\/publish$/.test(new URL(request.url()).pathname)) {
      publishRequests.push(request.url());
    }
  });
  const suffix = `${Date.now()}-${test.info().retry}`;
  const keyword = `E2E услуга ${suffix}`;
  const city = `E2E город ${suffix}`;
  const projectName = `E2E проект ${suffix}`;
  const projectSlug = `e2e-project-${Date.now()}-${test.info().retry}`;
  const domain = `e2e-${Date.now()}-${test.info().retry}.example.test`;

  await login(page);

  const keywordImportRequests: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "POST" && /\/keywords\/import-csv(?:\/preview)?$/.test(new URL(request.url()).pathname)) {
      keywordImportRequests.push(new URL(request.url()).pathname);
    }
  });
  await page.getByRole("link", { name: "Семантика" }).click();
  await expect(page.getByRole("heading", { name: "Семантика" })).toBeVisible();
  await page.getByLabel("CSV-файл (UTF-8, до 10 МБ)").setInputFiles({
    name: "e2e-keywords.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(
      `phrase;frequency;group;intent;city;priority\n${keyword};1;E2E;commercial;${city};high\n`,
      "utf-8",
    ),
  });
  await page.getByLabel("Разделитель").selectOption(";");
  await expect(page.getByRole("button", { name: /Импортировать проверенный CSV/ })).toBeDisabled();
  expect(keywordImportRequests).toEqual([]);
  const previewResponsePromise = page.waitForResponse((response) =>
    response.request().method() === "POST" && new URL(response.url()).pathname === "/api/v1/keywords/import-csv/preview",
  );
  await page.getByRole("button", { name: "Проверить CSV" }).click();
  expect((await previewResponsePromise).ok()).toBe(true);
  await expect(page.getByRole("heading", { name: "Preview CSV без записи" })).toBeVisible();
  await expect(page.getByText("Будет добавлено: 1")).toBeVisible();
  expect(keywordImportRequests).toEqual(["/api/v1/keywords/import-csv/preview"]);
  await page.getByLabel("Колонка города").fill("city_name");
  await expect(page.getByRole("button", { name: /Импортировать проверенный CSV/ })).toBeDisabled();
  await page.getByLabel("Колонка города").fill("city");
  const refreshedPreviewPromise = page.waitForResponse((response) =>
    response.request().method() === "POST" && new URL(response.url()).pathname === "/api/v1/keywords/import-csv/preview",
  );
  await page.getByRole("button", { name: "Проверить CSV" }).click();
  expect((await refreshedPreviewPromise).ok()).toBe(true);
  const importResponsePromise = page.waitForResponse((response) =>
    response.request().method() === "POST" && new URL(response.url()).pathname === "/api/v1/keywords/import-csv",
  );
  await page.getByRole("button", { name: /Импортировать проверенный CSV/ }).click();
  expect((await importResponsePromise).ok()).toBe(true);
  await expect(page.getByRole("heading", { name: "Фактический результат импорта" })).toBeVisible();
  await expect(page.getByText("Добавлено: 1")).toBeVisible();
  await expect(page.getByText(keyword)).toBeVisible();
  expect(keywordImportRequests).toEqual([
    "/api/v1/keywords/import-csv/preview",
    "/api/v1/keywords/import-csv/preview",
    "/api/v1/keywords/import-csv",
  ]);

  await page.getByRole("link", { name: "География" }).click();
  await expect(page.getByRole("heading", { name: "География" })).toBeVisible();
  await page.getByLabel("Название").fill(city);
  await expect(page.getByRole("button", { name: "Добавить", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "Добавить", exact: true }).click();
  const cityRow = page.locator("tbody tr").filter({ hasText: city });
  await expect(cityRow).toHaveCount(1);
  await expect(cityRow.getByRole("button", { name: "Изменить" })).toBeVisible();

  await page.getByRole("link", { name: "Проекты" }).click();
  await expect(
    page.getByRole("heading", { name: "Проекты", exact: true }),
  ).toBeVisible();
  await page.getByLabel("Название проекта").fill(projectName);
  await page.getByLabel("Идентификатор проекта").fill(projectSlug);
  await page.getByLabel("Домен").fill(domain);
  await page.getByLabel("Ниша").fill("E2E услуги");
  await page.getByRole("button", { name: "Создать проект" }).click();
  const projectCard = page.locator("article").filter({
    has: page.getByText(projectName, { exact: true }),
  });
  await expect(projectCard).toHaveCount(1);
  await projectCard.getByRole("link", { name: "Открыть проект" }).click();
  await expect(page).toHaveURL(/\/projects\/[0-9a-f-]+$/);
  await expect(page.getByRole("heading", { name: projectName })).toBeVisible();

  await page.getByLabel("Организация").fill(projectName);
  await page.getByLabel("Основная услуга").fill("E2E услуга");
  await page.getByLabel("Телефон").fill("+79990000000");
  await page.getByLabel("Источник фактов").fill("Проверяемые CI данные");
  await page.getByRole("button", { name: "Сохранить новую версию фактов" }).click();
  await page.getByRole("button", { name: "Подтвердить факты" }).click();
  await expect(page.getByText(/версия 1: confirmed/)).toBeVisible();

  const selectedKeyword = page.getByLabel(`Выбрать ${keyword}`);
  await selectedKeyword.check();
  await page.getByRole("button", { name: "Сохранить выбранные ключи" }).click();
  await expect(page.getByText("Семантика проекта сохранена.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Сохранить выбранные ключи" })).toBeEnabled();
  await expect(selectedKeyword).toBeChecked();
  const addPlace = page.getByLabel(`Добавить ${city}`);
  await expect(addPlace).toBeEnabled();
  await addPlace.check();
  const primaryPlace = page.getByLabel(`Основное место ${city}`);
  await expect(primaryPlace).toBeEnabled();
  await primaryPlace.check();
  await page.getByRole("button", { name: "Сохранить географию" }).click();
  await expect(page.getByText("География проекта сохранена.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Сохранить географию" })).toBeEnabled();
  await expect(addPlace).toBeChecked();
  await expect(primaryPlace).toBeChecked();
  await expect(selectedKeyword).toBeChecked();

  await page.getByLabel("Путь страницы").fill("/");
  await page.getByLabel("Цель страницы").fill("Проверка основного operator workflow");
  await page.getByLabel("Намерение").fill("заказать услугу");
  await page.getByRole("button", { name: "Создать черновик плана" }).click();
  await expect(page.getByText("Черновик плана страницы создан.")).toBeVisible();
  const submitPlan = page.getByRole("button", { name: "На проверку" });
  await expect(submitPlan).toBeEnabled();
  const reviewResponsePromise = page.waitForResponse((response) =>
    response.request().method() === "POST" &&
    /\/page-plans\/[^/]+\/submit-review$/.test(new URL(response.url()).pathname),
  );
  await submitPlan.click();
  const reviewResponse = await reviewResponsePromise;
  expect(reviewResponse.ok(), `submit-review ${reviewResponse.status()}: ${await reviewResponse.text()}`).toBe(true);
  expect((await reviewResponse.json()).state).toBe("review");
  await expect(page.locator("main [aria-busy]")).toHaveAttribute("aria-busy", "false");
  expect(await page.getByRole("alert").allTextContents()).toEqual([]);
  const approvePlan = page.getByRole("button", { name: "Одобрить", exact: true });
  await expect(approvePlan).toBeVisible();
  await approvePlan.click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("dialog").getByRole("button", { name: "Одобрить" }).click();
  const createDraft = page.getByRole("button", { name: "Создать черновик", exact: true });
  await expect(createDraft).toBeEnabled();
  await createDraft.click();

  const draftSection = page.locator("section.surface").filter({
    has: page.getByRole("heading", { name: "5. Черновики и проверка качества", exact: true }),
  });
  const draftRow = draftSection.locator("tbody tr");
  await expect(draftRow).toHaveCount(1);
  const checkDraft = draftRow.getByRole("button", { name: "Проверить", exact: true });
  await expect(checkDraft).toBeEnabled();
  await checkDraft.click();
  await expect(draftRow.getByText(/^(warn|pass)$/)).toBeVisible();
  const manualReview = draftRow.getByRole("button", { name: "На ручную проверку" });
  await expect(manualReview).toBeVisible();
  await manualReview.click();
  const applyDraft = draftRow.getByRole("button", { name: "Применить", exact: true });
  await expect(applyDraft).toBeVisible();
  await applyDraft.click();
  await expect(page.getByRole("dialog")).toBeVisible();
  const reason = page.getByLabel("Причина применения с предупреждениями");
  if (await reason.isVisible()) await reason.fill("CI подтверждает тестовый warning override");
  await page.getByRole("dialog").getByRole("button", { name: "Применить" }).click();
  await expect(page.getByText("Сначала примените черновик страницы.")).not.toBeVisible();
  await expect(
    page.getByRole("heading", { name: "4.5. Использование media в snapshots", exact: true }),
  ).toBeVisible();

  const createCandidate = page.getByRole("button", { name: "Создать candidate-сборку" });
  await expect(createCandidate).toBeVisible();
  await createCandidate.click();
  const preview = page.getByRole("link", { name: "Preview" });
  await expect(preview).toBeVisible();
  const [previewPage] = await Promise.all([page.waitForEvent("popup"), preview.click()]);
  await expect(previewPage).toHaveURL(/\/preview\/$/);
  await expect(previewPage.getByRole("heading", { name: "E2E услуга", exact: true })).toBeVisible();
  const leadForm = previewPage.locator("form[data-site-panel-lead-form]");
  await expect(leadForm).toBeVisible();
  await expect(leadForm).toHaveAttribute("data-endpoint", "/api/v1/leads/public");
  await expect(leadForm.locator('input[name="form_ts"]')).not.toHaveValue("");
  await expect(leadForm).toHaveAttribute("data-idempotency-key", /.+/);
  const leadPhone = `+7999${Date.now().toString().slice(-7)}`;
  const leadName = `E2E клиент ${suffix}`;
  const leadMessage = "E2E заявка из приватного preview";
  const leadResponsePromise = previewPage.waitForResponse((response) =>
    response.request().method() === "POST" &&
    new URL(response.url()).pathname === "/api/v1/leads/public",
  );
  await leadForm.getByLabel("Имя").fill(leadName);
  await leadForm.getByLabel("Телефон").fill(leadPhone);
  await leadForm.getByLabel("Комментарий").fill(leadMessage);
  await leadForm.getByLabel("Согласие на обработку ПДн").check();
  const submitLead = leadForm.getByRole("button", { name: "Отправить", exact: true });
  await expect(submitLead).toBeEnabled();
  await submitLead.click();
  const leadResponse = await leadResponsePromise;
  const leadPayload = leadResponse.request().postDataJSON() as { message: string | null };
  expect(leadPayload.message).toBe(leadMessage);
  const leadResponseBody = await leadResponse.text();
  expect(leadResponse.status(), `lead submit ${leadResponse.status()}: ${leadResponseBody}`).toBe(201);
  const submittedLead = JSON.parse(leadResponseBody) as { id: string };
  await expect(leadForm.getByText("Заявка отправлена. Мы скоро свяжемся с вами.")).toBeVisible();

  const candidateRow = page.getByRole("row").filter({ has: preview });
  await expect(candidateRow.getByRole("cell", { name: "ready", exact: true })).toBeVisible();
  const publish = candidateRow.getByRole("button", { name: "Опубликовать" });
  await expect(publish).toBeVisible();
  await expect(publish).toBeDisabled();
  expect(publishRequests).toEqual([]);
  await previewPage.close();

  await page.getByRole("link", { name: "Лиды" }).click();
  await expect(page.getByRole("heading", { name: "Лиды", exact: true })).toBeVisible();
  await page.getByLabel("Поиск").fill(domain);
  await page.getByRole("button", { name: "Найти", exact: true }).click();
  const leadRow = page.getByRole("row").filter({ has: page.getByText(domain, { exact: true }) });
  await expect(leadRow).toHaveCount(1);
  await leadRow.getByRole("button", { name: "Открыть" }).click();
  await expect(page.getByRole("heading", { name: "Карточка лида", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Раскрыть контакты и сообщение" }).click();
  await expect(page.getByText(leadPhone, { exact: true })).toBeVisible();
  await expect(page.getByText(leadName, { exact: true })).toBeVisible();
  await expect(page.getByText(leadMessage, { exact: true })).toBeVisible();
  expect(submittedLead.id).toMatch(/^[0-9a-f-]{36}$/);
  expect(publishRequests).toEqual([]);
});
