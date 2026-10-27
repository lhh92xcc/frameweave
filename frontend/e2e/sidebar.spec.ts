import { expect, test, type Page } from "@playwright/test";

const projects = Array.from({ length: 5 }, (_, index) => ({
  id: `sidebar_project_${index + 1}`,
  title: `离线侧边栏项目 ${index + 1}`,
  mode: "drama",
  aspect_ratio: "9:16",
  episodes_count: 1,
  contract: { approved: false },
  progress: [],
  jobs: [],
  artifacts: [],
}));

async function mockStudio(page: Page) {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/templates") return route.fulfill({ json: [] });
    if (path === "/api/projects") return route.fulfill({ json: projects });
    if (path === "/api/health")
      return route.fulfill({
        json: { status: "ok", gateway: "ready", engine: "ready", worker: true },
      });
    if (path === "/api/settings")
      return route.fulfill({
        json: {
          text: [],
          image: [],
          video: [],
          audio: {},
          allow_paid: false,
          audio_mode: "edge",
          voice: "",
          subtitles: false,
        },
      });
    if (path === "/api/gateway/catalog")
      return route.fulfill({ json: { accounts: [], image: [], video: [] } });
    if (path === "/api/upstreams") return route.fulfill({ json: {} });
    return route.fulfill({ status: 404, json: {} });
  });
}

async function scrollSidebar(page: Page) {
  const sidebar = page.getByRole("complementary", { name: "工作台导航" });
  const bounds = await sidebar.boundingBox();
  if (!bounds) throw new Error("侧边栏不可见");
  await page.mouse.move(
    bounds.x + bounds.width / 2,
    bounds.y + bounds.height / 2,
  );
  await page.mouse.wheel(0, 2000);
  await expect
    .poll(() => sidebar.evaluate((element) => element.scrollTop))
    .toBeGreaterThan(0);
  return sidebar;
}

test("desktop sidebar scrolls independently without compressing recent projects", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1470, height: 728 });
  await mockStudio(page);
  await page.goto("/");

  const sidebar = page.getByRole("complementary", { name: "工作台导航" });
  const recent = sidebar.locator(".recent-link");
  await expect(recent).toHaveCount(4);
  expect((await recent.first().boundingBox())?.height).toBeGreaterThan(25);
  await expect(
    sidebar.getByRole("button", { name: "模型与连接" }),
  ).not.toBeInViewport();

  await scrollSidebar(page);
  expect(await page.evaluate(() => window.scrollY)).toBe(0);
  await expect(
    sidebar.getByRole("button", { name: "模型与连接" }),
  ).toBeInViewport({
    ratio: 1,
  });
  await expect(
    sidebar.getByRole("button", { name: "使用指南" }),
  ).toBeInViewport({
    ratio: 1,
  });
  await expect(sidebar.locator(".user-card")).toBeInViewport({ ratio: 1 });
  await sidebar.getByRole("button", { name: "模型与连接" }).click();
  await expect(page).toHaveURL(/#settings$/);
  await expect(
    page.getByRole("heading", { name: "连接模型，释放创作。" }),
  ).toBeVisible();
});

test("keyboard can scroll the sidebar in either direction", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 600 });
  await mockStudio(page);
  await page.goto("/");

  const sidebar = page.getByRole("complementary", { name: "工作台导航" });
  await sidebar.focus();
  await page.keyboard.press("End");
  await expect
    .poll(() => sidebar.evaluate((element) => element.scrollTop))
    .toBeGreaterThan(0);
  await expect(
    sidebar.getByRole("button", { name: "使用指南" }),
  ).toBeInViewport({
    ratio: 1,
  });
  await page.keyboard.press("Home");
  await expect
    .poll(() => sidebar.evaluate((element) => element.scrollTop))
    .toBe(0);
  await expect(
    sidebar.getByRole("button", { name: "开启新创作" }),
  ).toBeInViewport({
    ratio: 1,
  });
});

test.describe("mobile navigation", () => {
  test.use({
    viewport: { width: 390, height: 600 },
    isMobile: true,
    hasTouch: true,
  });

  test("swipes to settings and closes after navigation", async ({ page }) => {
    await mockStudio(page);
    await page.goto("/");
    await page.getByRole("button", { name: "打开导航" }).click();

    const sidebar = page.getByRole("complementary", { name: "工作台导航" });
    await expect
      .poll(async () => Math.round((await sidebar.boundingBox())?.x ?? -1))
      .toBe(0);
    const bounds = await sidebar.boundingBox();
    if (!bounds) throw new Error("侧边栏不可见");
    const touch = await page.context().newCDPSession(page);
    const x = bounds.x + bounds.width / 2;
    const startY = bounds.y + bounds.height * 0.8;
    await touch.send("Input.dispatchTouchEvent", {
      type: "touchStart",
      touchPoints: [{ x, y: startY }],
    });
    for (let step = 1; step <= 8; step++) {
      await touch.send("Input.dispatchTouchEvent", {
        type: "touchMove",
        touchPoints: [{ x, y: startY - step * 55 }],
      });
    }
    await touch.send("Input.dispatchTouchEvent", {
      type: "touchEnd",
      touchPoints: [],
    });
    await expect
      .poll(() => sidebar.evaluate((element) => element.scrollTop))
      .toBeGreaterThan(0);
    await expect(
      sidebar.getByRole("button", { name: "模型与连接" }),
    ).toBeInViewport({
      ratio: 1,
    });
    await sidebar.getByRole("button", { name: "模型与连接" }).click();
    await expect(page).toHaveURL(/#settings$/);
    await expect(sidebar).not.toHaveClass(/shown/);
    expect(
      await page.evaluate(() => document.body.scrollWidth),
    ).toBeLessThanOrEqual(390);
  });
});

test("a tall sidebar shows all navigation without unnecessary scrolling", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1470, height: 1400 });
  await mockStudio(page);
  await page.goto("/");

  const sidebar = page.getByRole("complementary", { name: "工作台导航" });
  await expect(sidebar.locator(".recent-link")).toHaveCount(4);
  expect(
    await sidebar.evaluate(
      (element) => element.scrollHeight - element.clientHeight,
    ),
  ).toBe(0);
  await expect(
    sidebar.getByRole("button", { name: "使用指南" }),
  ).toBeInViewport({
    ratio: 1,
  });
  await sidebar.getByRole("button", { name: "我的项目" }).click();
  await expect(
    page.getByRole("heading", { name: "离线侧边栏项目 5" }),
  ).toBeVisible();
});
