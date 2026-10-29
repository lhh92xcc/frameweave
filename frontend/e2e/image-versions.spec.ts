import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import path from "node:path";
import type { Artifact, Project } from "../src/api";

// Local offline fixtures exercise browsing and review wiring, not model output.
const png = readFileSync(path.resolve("../tests/fixtures/reference.png"));

function offlineFixture() {
  const artifact = (id: string, changes: Partial<Artifact> = {}): Artifact => ({
    id, project_id: "project_versions", entity_id: "lin", kind: "reference",
    url: `/api/artifacts/${id}/content`, mime: "image/png", job_id: "offline_job",
    contract_revision: 2, plan_revision: 1, created_at: 10,
    review: "pending", stale: false, archived: false,
    route: { provider: "user-upload", model: "original", paid: false },
    metadata: { reference_role: "front" }, ...changes,
  });
  const project: Project = {
    id: "project_versions", title: "离线多图浏览验证", mode: "drama", brief: "",
    source: "离线示例", style: "", language: "简体中文", duration: 10,
    aspect_ratio: "16:9", episodes_count: 1, chapters: [], version: 1,
    contract: { revision: 2, markdown: "离线规范", hash: "offline_contract", approved: true },
    plan: { assets: [{ id: "lin", name: "离线修理师", kind: "character", description: "多视角图片浏览" }],
      episodes: [{ id: "ep1", title: "第一集", shots: [{ id: "s1", title: "读信", duration: 10, asset_ids: ["lin"] }] }] },
    plan_revision: 1, plan_approved: true, plan_contract_hash: "offline_contract",
    automation: false, paused: false, cancelled: false, jobs: [],
    artifacts: [
      artifact("side_latest", { created_at: 40, metadata: { reference_role: "side" },
        route: { provider: "seedream", model: "offline-recorded-model", paid: true } }),
      artifact("front_approved", { created_at: 30, review: "approved" }),
      artifact("back_rejected", { created_at: 20, review: "rejected", metadata: { reference_role: "back" } }),
      artifact("old_approved", { created_at: 5, contract_revision: 1, review: "approved", stale: true }),
      artifact("archived_hidden", { created_at: 1, archived: true }),
    ],
    progress: [
      { id: "requirements", name: "制作规范", done: 1, total: 1 },
      { id: "plan", name: "分集分镜", done: 1, total: 1 },
      { id: "reference", name: "素材参考", done: 0, total: 1 },
      { id: "keyframe", name: "镜头画面", done: 0, total: 1 },
    ], created_at: 1, updated_at: 1,
  };
  return { project, artifact, mutations: [] as { path: string; body: any }[] };
}

async function openStudio(page: Page, fixture: ReturnType<typeof offlineFixture>) {
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const respond = (json: unknown) => route.fulfill({ json });
    if (request.method() !== "GET") fixture.mutations.push({ path: url.pathname, body: request.postDataJSON() });
    if (url.pathname === "/api/templates") return respond([]);
    if (url.pathname === "/api/health") return respond({ status: "ok", worker: true, engine: "ready", gateway: "ready" });
    if (url.pathname === "/api/projects") return respond([fixture.project]);
    if (url.pathname === `/api/projects/${fixture.project.id}`) return respond(fixture.project);
    if (url.pathname === "/api/jobs") return respond([]);
    if (/\/artifacts\/[^/]+\/content$/.test(url.pathname))
      return route.fulfill({ contentType: "image/png", body: png });
    const review = url.pathname.match(/^\/api\/artifacts\/([^/]+)\/review$/);
    if (review) {
      const item = fixture.project.artifacts.find((candidate) => candidate.id === review[1])!;
      item.review = request.postDataJSON().approved ? "approved" : "rejected";
      return respond(item);
    }
    return route.fulfill({ status: 404, json: { error: { message: "未提供的离线接口" } } });
  });
  await page.goto("/#project/" + fixture.project.id);
  await page.locator(".pipeline-step").filter({ hasText: "素材参考" }).click();
  return page.locator(".media-card").filter({ has: page.getByRole("heading", { name: "离线修理师", exact: true }) });
}

test("offline images: browse every reference, source and review state, with read-only expired images", async ({ page }) => {
  const fixture = offlineFixture();
  // Archive imports must not become a card's selected production image.
  fixture.project.artifacts.unshift(fixture.project.artifacts.pop()!);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const card = await openStudio(page, fixture);
  const versions = card.getByRole("group", { name: "图片版本" });
  await expect(versions).toContainText("共 4 张 · 正在查看 1 / 4");
  await expect(versions.getByRole("button", { name: /^查看第/ })).toHaveCount(4);
  await expect(card.locator(".media-card-preview img")).toHaveAttribute("src", /side_latest\/content$/);
  await expect(card.locator(".artifact-source")).toContainText("offline-recorded-model");
  await expect(versions.getByRole("button", { name: "上一张图片" })).toBeDisabled();

  await versions.getByRole("button", { name: /查看第 2 张/ }).click();
  await expect(card.locator(".media-card-preview img")).toHaveAttribute("src", /front_approved\/content$/);
  await expect(card.locator(".media-review")).toHaveText("已批准");
  await expect(card.locator(".artifact-source")).toContainText("用户上传");
  await expect(card.getByRole("checkbox")).toHaveCount(0);
  await card.locator(".media-card-preview").click();
  const dialog = page.getByRole("dialog", { name: "素材预览与审核" });
  await expect(dialog.locator(".media-detail-preview img")).toHaveAttribute("src", /front_approved\/content$/);
  await expect(dialog.getByRole("button", { name: "批准素材" })).toBeEnabled();
  await dialog.getByRole("button", { name: "关闭预览" }).click();

  await versions.getByRole("button", { name: "下一张图片" }).click();
  await expect(card.locator(".media-card-preview img")).toHaveAttribute("src", /back_rejected\/content$/);
  await expect(card.locator(".media-review")).toHaveText("需返工");
  await versions.getByRole("button", { name: "下一张图片" }).click();
  await expect(versions.getByRole("button", { name: "下一张图片" })).toBeDisabled();
  await expect(card.locator(".media-review")).toHaveText("已过期 · 已批准");
  await card.getByRole("button", { name: "预览", exact: true }).click();
  await expect(dialog.getByRole("button", { name: "批准素材" })).toBeDisabled();
  await expect(dialog.getByRole("button", { name: "需要返工" })).toBeDisabled();
  await expect(dialog.getByTitle("下载原始素材")).toHaveAttribute("href", /old_approved\/content\?download=true$/);
  await dialog.getByRole("button", { name: "关闭预览" }).click();

  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  expect(await versions.locator(".versions-strip").evaluate((element) => element.scrollWidth > element.clientWidth)).toBeTruthy();
  expect(fixture.mutations).toEqual([]);
  expect(errors).toEqual([]);
});

test("offline images: retain the viewed image during polling and review the selected version", async ({ page }) => {
  const fixture = offlineFixture();
  fixture.project.artifacts.find((item) => item.id === "front_approved")!.review = "pending";
  const card = await openStudio(page, fixture);
  const batch = page.getByRole("region", { name: "批量审核" });
  await expect(page.locator(".media-filters")).toContainText("待审核 2");
  await expect(batch).toContainText("已选择 0 / 2 项待审核");
  await card.getByRole("button", { name: /查看第 2 张/ }).click();
  fixture.project.artifacts.unshift(fixture.artifact("new_result", { created_at: 50 }));
  await expect(card.getByRole("group", { name: "图片版本" })).toContainText("共 5 张 · 正在查看 3 / 5");
  await expect(card.locator(".media-card-preview img")).toHaveAttribute("src", /front_approved\/content$/);
  await card.getByRole("checkbox").check();
  await expect(batch).toContainText("已选择 1 / 3 项待审核");
  page.once("dialog", (dialog) => dialog.accept());
  await batch.getByRole("button", { name: "批量批准" }).click();
  await expect(card.locator(".media-review")).toHaveText("已批准");
  expect(fixture.mutations.map((call) => call.path)).toEqual(["/api/artifacts/front_approved/review"]);
  expect(fixture.project.artifacts.find((item) => item.id === "side_latest")!.review).toBe("pending");
});

test("offline images: keyframe history stays separate from references and single-image cards stay compact", async ({ page }) => {
  const fixture = offlineFixture();
  fixture.project.artifacts = [
    fixture.artifact("one_reference"),
    fixture.artifact("new_frame", { kind: "keyframe", entity_id: "s1", created_at: 20, metadata: {} }),
    fixture.artifact("old_frame", { kind: "keyframe", entity_id: "s1", created_at: 10, stale: true, metadata: {} }),
  ];
  const reference = await openStudio(page, fixture);
  await expect(reference.getByRole("group", { name: "图片版本" })).toHaveCount(0);
  await expect(reference.locator(".media-card-preview img")).toHaveAttribute("src", /one_reference\/content$/);
  await page.getByRole("button", { name: "逐镜关键帧", exact: true }).click();
  const card = page.locator(".media-card");
  await expect(card.getByRole("group", { name: "图片版本" })).toContainText("共 2 张");
  await card.getByRole("button", { name: /查看第 2 张/ }).click();
  await expect(card.locator(".media-card-preview img")).toHaveAttribute("src", /old_frame\/content$/);
  await expect(card.getByRole("button", { name: "预览", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "身份与素材", exact: true }).click();
  await expect(reference.locator(".media-card-preview img")).toHaveAttribute("src", /one_reference\/content$/);
  expect(fixture.mutations).toEqual([]);
});

test("offline images: cards navigate independently and keep the selected thumbnail visible", async ({ page }, testInfo) => {
  const fixture = offlineFixture();
  fixture.project.plan.assets.push({ id: "fox", name: "离线信使", kind: "character", description: "独立图片列表" });
  fixture.project.artifacts.push(...Array.from({ length: 8 }, (_, index) => fixture.artifact(`fox_${index}`, {
    entity_id: "fox", created_at: 100 - index,
  })));
  const first = await openStudio(page, fixture);
  const second = page.locator(".media-card").filter({ has: page.getByRole("heading", { name: "离线信使", exact: true }) });
  const versions = second.getByRole("group", { name: "图片版本" });
  await versions.getByRole("button", { name: /查看第 3 张/ }).click();
  await expect(first.locator(".media-card-preview img")).toHaveAttribute("src", /side_latest\/content$/);
  await expect(second.locator(".media-card-preview img")).toHaveAttribute("src", /fox_2\/content$/);

  await page.setViewportSize({ width: 390, height: 844 });
  for (let index = 3; index < 8; index++) await versions.getByRole("button", { name: "下一张图片" }).click();
  const firstBounds = (await first.boundingBox())!;
  const secondBounds = (await second.boundingBox())!;
  expect(secondBounds.x).toBe(firstBounds.x);
  expect(secondBounds.y).toBeGreaterThan(firstBounds.y + firstBounds.height);
  const selected = versions.getByRole("button", { name: /查看第 8 张/ });
  await expect(selected).toHaveAttribute("aria-pressed", "true");
  await expect.poll(() => versions.locator(".versions-strip").evaluate((element) => {
    const bounds = element.getBoundingClientRect();
    const selected = element.querySelector('[aria-pressed="true"]')!.getBoundingClientRect();
    return selected.left >= bounds.left - 1 && selected.right <= bounds.right + 1;
  })).toBeTruthy();
  await expect(second.locator(".media-card-preview img")).toHaveAttribute("src", /fox_7\/content$/);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await page.screenshot({ path: testInfo.outputPath("image-versions-mobile.png"), fullPage: true });
  expect(fixture.mutations).toEqual([]);
});
