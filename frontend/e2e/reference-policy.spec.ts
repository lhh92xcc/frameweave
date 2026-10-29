import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import path from "node:path";
import type { Artifact, Project } from "../src/api";

test("offline strict references: retain story, explicit reapproval and missing-view checklist", async ({ page, request }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const created = await request.post("/api/projects", { data: {
    title: "离线强制参考检查", source: "第一章 灯塔\n修理师在灯塔读信。", episodes: 1, duration: 10,
  } });
  expect(created.status()).toBe(201);
  const original = await created.json();
  const base = `/api/projects/${original.id}`;
  expect((await request.post(base + "/contract/approve", { data: { hash: original.contract.hash } })).ok()).toBeTruthy();
  const plan = {
    assets: [{ id: "lin", name: "离线修理师", kind: "character", description: "穿深蓝工装的修理师", prompt: "修理师标准参考" }],
    episodes: [{ id: "ep1", title: "灯塔", synopsis: "修理师读信", source_chapters: [1], shots: [{
      id: "s01", title: "读信", description: "修理师在灯塔读信", image_prompt: "灯塔室内读信", video_prompt: "修理师打开信",
      duration: 10, dialogue: "信到了。", narration: "", asset_ids: ["lin"],
    }] }],
  };
  expect((await request.put(base + "/plan", { data: { plan, expected_revision: 0 } })).ok()).toBeTruthy();
  expect((await request.post(base + "/plan/approve", { data: { revision: 1 } })).ok()).toBeTruthy();
  const policy = { version: 1, assets: { lin: { required_roles: ["front", "side", "full_body"], shot_roles: ["front", "full_body"] } } };
  const markdown = "# 离线规范\n```frameweave-reference-policy\n" + JSON.stringify(policy) + "\n```";
  const edit = await request.put(base + "/contract", { data: { markdown, expected_revision: original.contract.revision } });
  expect(edit.ok()).toBeTruthy();
  await page.goto("/#project/" + original.id);
  await page.getByRole("button", { name: "批准规范", exact: true }).click();
  await page.getByRole("button", { name: "进入分集分镜" }).click();
  await page.getByRole("button", { name: "按新规范复核并批准现有蓝图" }).click();
  await page.getByRole("button", { name: "建立素材参考" }).click();
  const checklist = page.getByRole("region", { name: "强制参考检查" });
  await expect(checklist).toBeVisible();
  await expect(checklist.getByRole("button", { name: /缺少/ })).toHaveCount(3);
  await expect(page.getByRole("button", { name: "生成当前素材库" })).toBeDisabled();
  const role = page.getByText("生成 / 上传参考视角").locator("select");
  await expect(role.locator("option")).toHaveCount(4);
  await role.selectOption("side");
  // Exercise the real server gate without a successful model submission.
  const denied = await request.post(base + "/jobs", { data: { stage: "reference", entity_id: "lin", reference_role: "side" } });
  expect(denied.status()).toBe(422);
  const after = await (await request.get(base)).json();
  expect(after.plan).toEqual(plan);
  expect(after.jobs).toHaveLength(0);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(checklist).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  expect(errors).toEqual([]);
});

test("offline reference overview: thumbnails open the matching image, missing slots and invalid sources stay clear", async ({ page }, testInfo) => {
  // Synthetic local images and mocked metadata exercise UI only; no external generation.
  const png = readFileSync(path.resolve("../tests/fixtures/reference.png"));
  const roles = ["front", "left", "right", "back", "full_body", "detail"];
  const artifacts: Artifact[] = [];
  const requirements = (id: string, views: string[], approved: number, invalid = false) => ({
    required_roles: views, shot_roles: [views[0]],
    views: views.map((role, index) => {
      const available = id !== "neighbor";
      const artifactId = `${id}_${role}`;
      if (available) artifacts.push({
        id: artifactId, project_id: "overview", entity_id: id, kind: "reference",
        url: `/api/artifacts/${artifactId}/content`, thumbnail_url: `/api/artifacts/${artifactId}/thumbnail`,
        review: index < approved ? "approved" : "pending", stale: invalid && index === views.length - 1,
        mime: "image/png", contract_revision: 1, plan_revision: 1, created_at: 1,
        route: { provider: "user-upload", model: "original", paid: false },
        metadata: { reference_role: role }, job_id: "offline",
      });
      return { role, artifact_id: available ? artifactId : undefined, approved: index < approved };
    }),
  });
  const readiness = { max_references: 8, assets: {
    cat: requirements("cat", roles, 6),
    visitor: requirements("visitor", ["front", "side", "full_body"], 1, true),
    neighbor: requirements("neighbor", ["front", "side", "full_body"], 0),
  } };
  const project: Project = {
    id: "overview", title: "离线参考图预览", version: 1, mode: "drama", brief: "", source: "离线素材",
    style: "", language: "简体中文", duration: 10, aspect_ratio: "16:9", episodes_count: 1, chapters: [],
    contract: { revision: 1, markdown: "离线规范", hash: "offline", approved: true },
    plan: { assets: [
      { id: "cat", name: "离线猫咪", kind: "character", description: "合成参考图片" },
      { id: "visitor", name: "离线青年顾客", kind: "character", description: "合成参考图片" },
      { id: "neighbor", name: "离线邻摊顾客", kind: "character", description: "合成参考图片" },
    ], episodes: [{ id: "ep1", title: "离线第一集", shots: [] }] },
    plan_revision: 1, plan_approved: true, plan_contract_hash: "offline", automation: false, paused: false,
    cancelled: false, jobs: [], artifacts, reference_readiness: readiness,
    progress: [{ id: "reference", name: "素材参考", done: 1, total: 3 }], created_at: 1, updated_at: 1,
  };
  const mutations: string[] = [];
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (request.method() !== "GET") mutations.push(pathname);
    if (/\/artifacts\/[^/]+\/(thumbnail|content)$/.test(pathname))
      return route.fulfill({ contentType: "image/png", body: png });
    const json = pathname === "/api/projects" ? [project]
      : pathname === "/api/projects/overview" ? project
      : pathname === "/api/health" ? { status: "ok", worker: true } : [];
    return route.fulfill({ json });
  });
  await page.goto("/#project/overview");
  await page.locator(".pipeline-step").filter({ hasText: "素材参考" }).click();
  const overview = page.getByRole("region", { name: "强制参考检查" });
  await expect(overview).toBeVisible();
  await expect(overview.locator(".reference-overview-title")).toContainText("7 / 12 已批准");
  await expect(overview.locator("img")).toHaveCount(9);
  await expect(overview.locator("details")).not.toHaveAttribute("open");
  const side = overview.getByRole("button", { name: "离线青年顾客 · 人物侧面 · 待审核", exact: true });
  await expect(side.locator("img")).toHaveAttribute("src", /visitor_side\/thumbnail$/);
  await side.click();
  const dialog = page.getByRole("dialog", { name: "素材预览与审核" });
  await expect(dialog.locator(".media-detail-preview img")).toHaveAttribute("src", /visitor_side\/content$/);
  await expect(dialog.getByRole("button", { name: "批准素材" })).toBeEnabled();
  await dialog.getByRole("button", { name: "关闭预览" }).click();
  await overview.getByRole("button", { name: "离线青年顾客 · 人物全身 · 来源失效", exact: true }).click();
  await expect(dialog.getByRole("button", { name: "批准素材" })).toBeDisabled();
  await dialog.getByRole("button", { name: "关闭预览" }).click();
  await expect(overview.getByRole("button", { name: /离线邻摊顾客.*缺少/ })).toHaveCount(3);
  for (const missing of await overview.getByRole("button", { name: /缺少/ }).all()) await expect(missing).toBeDisabled();
  await overview.getByText("查看参考规则", { exact: true }).click();
  await expect(overview.locator("details")).toHaveAttribute("open", "");
  await overview.getByText("查看参考规则", { exact: true }).click();
  await overview.screenshot({ path: testInfo.outputPath("reference-overview-desktop.png"), animations: "disabled" });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await expect(overview.locator(".reference-overview-grid").first()).toHaveCSS("grid-template-columns", /[\d.]+px [\d.]+px [\d.]+px/);
  await overview.screenshot({ path: testInfo.outputPath("reference-overview-mobile.png"), animations: "disabled" });
  expect(mutations).toEqual([]);
});

test("offline default consistency: lock the first master and explicitly select a new approved original", async ({ page, request }) => {
  const created = await request.post('/api/projects', { data: {
    title: '离线默认一致性验证', source: '第一章 读信\n修理师打开信。', episodes: 1, duration: 10,
  } });
  const p = await created.json();
  const base = `/api/projects/${p.id}`;
  await request.post(base + '/contract/approve', { data: { hash: p.contract.hash } });
  const plan = { assets: [{ id: 'lin', name: '离线修理师', kind: 'character', description: '蓝工装修理师', prompt: '穿蓝工装的修理师' }],
    episodes: [{ id: 'ep1', title: '读信', synopsis: '修理师在室内读信', source_chapters: [1], shots: [{
      id: 's1', title: '读信', description: '修理师读信', image_prompt: '修理师在室内读信', video_prompt: '修理师缓缓展开信',
      duration: 10, dialogue: '', narration: '', asset_ids: ['lin'],
    }] }] };
  expect((await request.put(base + '/plan', { data: { plan, expected_revision: 0 } })).ok()).toBeTruthy();
  await request.post(base + '/plan/approve', { data: { revision: 1 } });
  const png = readFileSync(path.resolve('../tests/fixtures/reference.png'));
  const ids: string[] = [];
  for (let index = 0; index < 2; index++) {
    const uploaded = await request.post(base + '/upload', { multipart: { stage: 'reference', entity_id: 'lin',
      file: { name: 'offline.png', mimeType: 'image/png', buffer: png } } });
    expect(uploaded.ok()).toBeTruthy();
    const artifact = await uploaded.json();
    ids.push(artifact.id);
    await request.post(`/api/artifacts/${artifact.id}/review`, { data: { approved: true } });
  }
  await page.goto('/#project/' + p.id);
  await page.locator('.pipeline-step').filter({ hasText: '素材参考' }).click();
  const overview = page.getByRole('region', { name: '强制参考检查' });
  await expect(overview).toContainText('项目资产母版');
  await expect(overview.locator('img')).toHaveAttribute('src', new RegExp(ids[0]));
  await expect(page.getByRole('button', { name: '生成当前素材库' })).toBeEnabled();
  await page.getByRole('button', { name: '设为后续生成母版' }).click();
  await expect(overview.locator('img')).toHaveAttribute('src', new RegExp(ids[1]));
  const updated = await (await request.get(base)).json();
  expect(updated.reference_masters.lin).toBe(ids[1]);
  expect(updated.jobs.every((job: any) => job.status === 'succeeded')).toBeTruthy();
});
