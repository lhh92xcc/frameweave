import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import path from "node:path";
import type { Project } from "../src/api";

// Offline synthetic media and API responses; these tests never generate media.
const png = readFileSync(path.resolve("../tests/fixtures/reference.png"));

async function offlineStudio(page: Page) {
  const project: Project = {
    id: "performance", title: "离线性能验证", mode: "drama", brief: "", source: "离线资料",
    style: "", language: "简体中文", duration: 10, aspect_ratio: "16:9", episodes_count: 1,
    chapters: [], version: 1, created_at: 1, updated_at: 1,
    contract: { revision: 1, markdown: "离线规范", hash: "offline", approved: true },
    plan: { assets: [{ id: "person", name: "离线角色", kind: "character", description: "多图预览" }],
      episodes: [{ id: "ep1", title: "离线第一集", shots: [] }] },
    plan_revision: 1, plan_approved: true, plan_contract_hash: "offline",
    automation: false, paused: false, cancelled: false, jobs: [],
    progress: [
      { id: "requirements", name: "制作规范", done: 1, total: 1 },
      { id: "plan", name: "分集分镜", done: 1, total: 1 },
      { id: "reference", name: "素材参考", done: 0, total: 1 },
    ],
    artifacts: [2, 1].map((index) => ({
      id: `image_${index}`, project_id: "performance", entity_id: "person", kind: "reference",
      url: `/api/artifacts/image_${index}/content`, thumbnail_url: `/api/artifacts/image_${index}/thumbnail`,
      mime: "image/png", review: "pending", stale: false, archived: false,
      contract_revision: 1, plan_revision: 1, created_at: index, job_id: "offline",
      route: { provider: "user-upload", model: "original", paid: false }, metadata: { reference_role: "front" },
    })),
  };
  const fixture = {
    project,
    counts: {} as Record<string, number>,
    scripts: [] as string[],
    mutations: [] as string[],
    delay: undefined as ((pathname: string) => Promise<void>) | undefined,
  };
  page.on("request", (request) => {
    if (request.resourceType() === "script") fixture.scripts.push(new URL(request.url()).pathname);
  });
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    fixture.counts[pathname] = (fixture.counts[pathname] || 0) + 1;
    if (request.method() !== "GET") fixture.mutations.push(pathname);
    await fixture.delay?.(pathname);
    if (/\/artifacts\/[^/]+\/(content|thumbnail)$/.test(pathname))
      return route.fulfill({ contentType: "image/png", body: png });
    const json = pathname === "/api/projects" ? [project]
      : pathname === "/api/projects/performance" ? project
      : pathname === "/api/health" ? { status: "ok", engine: "ready", gateway: "ready", worker: true }
      : pathname === "/api/audio/voice-library" ? { voices: [] } : [];
    return route.fulfill({ json });
  });
  return fixture;
}

async function pausedClock(page: Page) {
  const now = new Date("2026-10-29T12:00:00Z");
  await page.clock.install({ time: now });
  await page.clock.pauseAt(new Date(now.getTime() + 1000));
}

test("offline performance: load pages on demand and download originals only in detail", async ({ page }, testInfo) => {
  const fixture = await offlineStudio(page);
  await page.goto("/");
  await expect(page.locator(".recent-link")).toHaveCount(1);
  expect(fixture.scripts.some((url) => /SettingsPanel-|ProjectWorkspace-|AudioCastPanel-|ArtifactGallery-/.test(url))).toBe(false);
  await page.locator(".recent-link").click();
  await expect(page.locator(".workspace-title h1")).toHaveText(fixture.project.title);
  expect(fixture.scripts.some((url) => /ProjectWorkspace-/.test(url))).toBe(true);
  expect(fixture.scripts.some((url) => /AudioCastPanel-|SettingsPanel-/.test(url))).toBe(false);
  await page.locator(".pipeline-step").filter({ hasText: "素材参考" }).click();
  const card = page.locator(".media-card");
  await expect(card.locator(".media-card-preview img")).toHaveAttribute("src", /image_2\/thumbnail$/);
  await card.getByRole("button", { name: /查看第 2 张/ }).click();
  await expect(card.locator(".media-card-preview img")).toHaveAttribute("src", /image_1\/thumbnail$/);
  expect(Object.keys(fixture.counts).some((url) => url.endsWith("/content"))).toBe(false);
  await page.screenshot({ path: testInfo.outputPath("workspace-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: testInfo.outputPath("workspace-mobile.png"), fullPage: true });
  await card.locator(".media-card-preview").click();
  const dialog = page.getByRole("dialog", { name: "素材预览与审核" });
  await expect(dialog.locator(".media-detail-preview img")).toHaveAttribute("src", /image_1\/content$/);
  expect(fixture.mutations).toEqual([]);
});

test("offline performance: refresh current project while reusing project list and health, suspend hidden tabs", async ({ page }) => {
  await pausedClock(page);
  const fixture = await offlineStudio(page);
  await page.goto("/#project/performance");
  await expect(page.locator(".workspace-title h1")).toHaveText(fixture.project.title);
  for (let tick = 1; tick <= 8; tick++) {
    fixture.project.title = `离线轮询 ${tick}`;
    await page.clock.runFor(4000);
    await expect(page.locator(".workspace-title h1")).toHaveText(fixture.project.title);
    expect(fixture.counts["/api/projects"]).toBe(tick < 8 ? 1 : 2);
    expect(fixture.counts["/api/health"]).toBe(tick < 8 ? 1 : 2);
  }
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", { configurable: true, value: true });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  const before = { ...fixture.counts };
  await page.clock.runFor(60000);
  expect(fixture.counts).toEqual(before);
  fixture.project.title = "离线返回立即刷新";
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", { configurable: true, value: false });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await expect(page.locator(".workspace-title h1")).toHaveText(fixture.project.title);
  expect(fixture.counts["/api/projects/performance"]).toBe(before["/api/projects/performance"] + 1);
  expect(fixture.mutations).toEqual([]);
});

test("offline performance: slow project polls do not overlap or queue a burst", async ({ page }) => {
  await pausedClock(page);
  const fixture = await offlineStudio(page);
  await page.goto("/#project/performance");
  await expect(page.locator(".workspace-title h1")).toBeVisible();
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  fixture.delay = (pathname) => pathname === "/api/projects/performance" ? gate : Promise.resolve();
  await page.clock.runFor(4000);
  await expect.poll(() => fixture.counts["/api/projects/performance"]).toBe(2);
  await page.clock.runFor(20000);
  expect(fixture.counts["/api/projects/performance"]).toBe(2);
  fixture.project.title = "离线慢请求完成";
  release();
  await expect(page.locator(".workspace-title h1")).toHaveText(fixture.project.title);
  await page.clock.runFor(3999);
  expect(fixture.counts["/api/projects/performance"]).toBe(2);
  fixture.project.title = "离线下一轮刷新";
  await page.clock.runFor(1);
  await expect(page.locator(".workspace-title h1")).toHaveText(fixture.project.title);
  expect(fixture.counts["/api/projects/performance"]).toBe(3);
});

test("offline performance: delayed project navigation cannot replace the asset library", async ({ page }) => {
  const fixture = await offlineStudio(page);
  await page.goto("/");
  await expect(page.locator(".recent-link")).toHaveCount(1);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  fixture.delay = (pathname) => pathname === "/api/projects/performance" ? gate : Promise.resolve();
  await page.locator(".recent-link").click();
  await expect.poll(() => fixture.counts["/api/projects/performance"]).toBe(1);
  await page.evaluate(() => { location.hash = "assets"; });
  await expect(page.locator(".gallery-content")).toBeVisible();
  const response = page.waitForResponse("**/api/projects/performance");
  release();
  await response;
  await expect(page).toHaveURL(/#assets$/);
  await expect(page.locator(".gallery-content")).toBeVisible();
  await expect(page.locator(".workspace-content")).toHaveCount(0);
});

test("offline performance: queue polling waits for completion and stops on page leave", async ({ page }) => {
  await pausedClock(page);
  const fixture = await offlineStudio(page);
  await page.goto("/#jobs");
  await expect(page.locator(".queue-content")).toBeVisible();
  await expect.poll(() => fixture.counts["/api/jobs"]).toBe(1);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  fixture.delay = (pathname) => pathname === "/api/jobs" ? gate : Promise.resolve();
  await page.clock.runFor(3000);
  await expect.poll(() => fixture.counts["/api/jobs"]).toBe(2);
  await page.clock.runFor(15000);
  expect(fixture.counts["/api/jobs"]).toBe(2);
  await page.evaluate(() => { location.hash = "home"; });
  await expect(page.locator(".queue-content")).toHaveCount(0);
  const response = page.waitForResponse("**/api/jobs");
  release();
  await response;
  await page.clock.runFor(10000);
  expect(fixture.counts["/api/jobs"]).toBe(2);
});
