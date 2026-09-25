import { expect, test, type Page } from "@playwright/test";

const email = process.env.E2E_OPERATOR_EMAIL || "e2e@example.test";
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

test("оператор создаёт и готовит candidate без публикации", async ({ page }) => {
  const suffix = `${Date.now()}-${test.info().retry}`;
  const keyword = `E2E услуга ${suffix}`;
  const city = `E2E город ${suffix}`;
  const projectName = `E2E проект ${suffix}`;
  const projectSlug = `e2e-project-${Date.now()}-${test.info().retry}`;
  const domain = `e2e-${Date.now()}-${test.info().retry}.example.test`;

  await login(page);

  await page.getByRole("link", { name: "Семантика" }).click();
  await expect(page.getByRole("heading", { name: "Семантика" })).toBeVisible();
  await page.getByLabel("CSV-файл (UTF-8, до 10 МБ)").setInputFiles({
    name: "e2e-keywords.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(
      `phrase,frequency,group,intent,city,priority\n${keyword},1,E2E,commercial,${city},high\n`,
      "utf-8",
    ),
  });
  await page.getByRole("button", { name: "Импортировать" }).click();
  await expect(page.getByText("Добавлено: 1")).toBeVisible();
  await expect(page.getByText(keyword)).toBeVisible();

  await page.getByRole("link", { name: "География" }).click();
  await expect(page.getByRole("heading", { name: "География" })).toBeVisible();
  await page.getByLabel("Название").fill(city);
  await page.getByRole("button", { name: "Добавить", exact: true }).click();
  await expect(page.getByText(city)).toBeVisible();

  await page.getByRole("link", { name: "Проекты" }).click();
  await expect(page.getByRole("heading", { name: "Проекты" })).toBeVisible();
  await page.getByLabel("Название проекта").fill(projectName);
  await page.getByLabel("Идентификатор проекта").fill(projectSlug);
  await page.getByLabel("Домен").fill(domain);
  await page.getByLabel("Ниша").fill("E2E услуги");
  await page.getByRole("button", { name: "Создать проект" }).click();
  await expect(page.getByText(projectName, { exact: true })).toBeVisible();
  await page.getByRole("link", { name: "Открыть проект" }).click();
  await expect(page).toHaveURL(/\/projects\/[0-9a-f-]+$/);
  await expect(page.getByRole("heading", { name: projectName })).toBeVisible();

  await page.getByLabel("Организация").fill(projectName);
  await page.getByLabel("Основная услуга").fill("E2E услуга");
  await page.getByLabel("Телефон").fill("+79990000000");
  await page.getByLabel("Источник фактов").fill("Проверяемые CI данные");
  await page.getByRole("button", { name: "Сохранить новую версию фактов" }).click();
  await page.getByRole("button", { name: "Подтвердить факты" }).click();
  await expect(page.getByText(/версия 1: confirmed/)).toBeVisible();

  await page.getByLabel(`Выбрать ${keyword}`).check();
  await page.getByRole("button", { name: "Сохранить выбранные ключи" }).click();
  await page.getByLabel(`Добавить ${city}`).check();
  await page.getByLabel(`Основное место ${city}`).check();
  await page.getByRole("button", { name: "Сохранить географию" }).click();

  await page.getByLabel("Путь страницы").fill("/");
  await page.getByLabel("Цель страницы").fill("Проверка основного operator workflow");
  await page.getByLabel("Намерение").fill("заказать услугу");
  await page.getByRole("button", { name: "Создать черновик плана" }).click();
  await page.getByRole("button", { name: "На проверку" }).click();
  await page.getByRole("button", { name: "Одобрить" }).first().click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("dialog").getByRole("button", { name: "Одобрить" }).click();
  await page.getByRole("button", { name: "Создать черновик" }).click();

  await page.getByRole("button", { name: "Проверить" }).click();
  await expect(page.getByText(/warn|pass/)).toBeVisible();
  await page.getByRole("button", { name: "На ручную проверку" }).click();
  await page.getByRole("button", { name: "Применить" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  const reason = page.getByLabel("Причина применения с предупреждениями");
  if (await reason.isVisible()) await reason.fill("CI подтверждает тестовый warning override");
  await page.getByRole("dialog").getByRole("button", { name: "Применить" }).click();
  await expect(page.getByText("Сначала примените черновик страницы.")).not.toBeVisible();

  await page.getByRole("button", { name: "Создать candidate-сборку" }).click();
  const preview = page.getByRole("link", { name: "Preview" });
  await expect(preview).toBeVisible();
  const [previewPage] = await Promise.all([page.waitForEvent("popup"), preview.click()]);
  await expect(previewPage).toHaveURL(/\/preview\/$/);
  await expect(previewPage.getByText("E2E услуга")).toBeVisible();
  await expect(page.getByRole("button", { name: "Опубликовать" })).not.toBeVisible();
});
