import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { readFileSync, mkdtempSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

const png = readFileSync(path.resolve("../tests/fixtures/reference.png"));

test("real studio: source, contract, storyboard, imported references, video, review, final render", async ({
  page,
  request,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "每个想法，都值得被看见。" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "开启新创作" }).click();
  await page.getByRole("button", { name: "填入示例小说" }).click();
  const title = "浏览器流程验证 " + Date.now();
  await page.getByLabel("项目名称").fill(title);
  await page.getByLabel("计划集数").fill("1");
  await page.getByLabel("每集时长").selectOption("15");
  await page.getByRole("button", { name: "创建项目", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: title, exact: true }),
  ).toBeVisible();
  const id = page.url().split("project/")[1];
  const detail = await (await request.get("/api/projects/" + id)).json();
  expect(detail.chapters.length).toBe(3);
  await page.getByRole("button", { name: "批准规范", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "进入分集分镜" }),
  ).toBeVisible();
  const plan = {
    assets: [
      {
        id: "lin",
        name: "林夏",
        kind: "character",
        description: "深蓝色工装的年轻灯塔修理师",
        prompt: "深蓝色工装修理师，标准全身人物设定",
      },
    ],
    episodes: [
      {
        id: "ep01",
        title: "云海来信",
        synopsis: "林夏发现未来来信，修好机械鸟，看到云海中的浮岛。",
        source_chapters: [1, 2, 3],
        shots: [1, 2, 3].map((n) => ({
          id: `s0${n}`,
          title: `镜头 ${n}`,
          description: "林夏在灯塔里打开来信，柔和晨光从窗外照进房间。",
          image_prompt: "林夏在灯塔打开来信，深蓝色工装，中景自然光",
          video_prompt: "林夏打开来信再缓缓抬头，镜头平稳推进，保持身份一致",
          duration: 5,
          narration: "",
          dialogue: "",
          asset_ids: ["lin"],
        })),
      },
    ],
  };
  const edited = await request.put("/api/projects/" + id + "/plan", {
    data: { plan, expected_revision: 0 },
  });
  expect(edited.ok()).toBeTruthy();
  await page.getByRole("button", { name: "进入分集分镜" }).click();
  await expect(page.locator(".shot-row")).toHaveCount(3);
  await page.getByRole("button", { name: "编辑分镜 镜头 1" }).click();
  await page
    .getByLabel("画面内容")
    .fill("林夏在灯塔里仔细展开来信，柔和晨光照亮她的深蓝色工装。");
  // A slow refresh must finish before the next approval can use its revision.
  await page.route("**/api/projects/" + id, async (route) => {
    const response = await route.fetch();
    await new Promise((resolve) => setTimeout(resolve, 300));
    await route.fulfill({ response });
  });
  await page.getByRole("button", { name: "保存分镜新版本" }).click();
  await expect(page.getByRole("dialog", { name: "编辑分镜" })).toHaveCount(0);
  await page.getByRole("button", { name: "批准这份蓝图" }).click();
  await page.getByRole("button", { name: "建立素材参考" }).click();
  const chooser = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "上传已有素材" }).click();
  await (
    await chooser
  ).setFiles({ name: "fixture.png", mimeType: "image/png", buffer: png });
  await expect(
    page.getByRole("button", { name: "审核", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "审核", exact: true }).click();
  await expect(
    page.getByRole("dialog", { name: "素材预览与审核" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "批准素材", exact: true }).click();
  await expect(page.locator(".media-card .badge")).toContainText("已批准");
  // Imported fixtures test the real validation/render path without invoking paid models.
  for (const n of [1, 2, 3]) {
    const res = await request.post("/api/projects/" + id + "/upload", {
      multipart: {
        stage: "keyframe",
        entity_id: `s0${n}`,
        file: { name: "fixture.png", mimeType: "image/png", buffer: png },
      },
    });
    expect(res.ok(), await res.text()).toBeTruthy();
    await request.post("/api/artifacts/" + (await res.json()).id + "/review", {
      data: { approved: true },
    });
  }
  const tmp = mkdtempSync(path.join(tmpdir(), "frameweave-e2e-")),
    video = path.join(tmp, "sample.mp4");
  execFileSync(
    ["/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg", "/usr/local/opt/ffmpeg-full/bin/ffmpeg"].find(existsSync) || "ffmpeg",
    [
      "-y",
      "-v",
      "error",
      "-f",
      "lavfi",
      "-i",
      "testsrc2=size=160x288:rate=24",
      "-t",
      "5",
      "-c:v",
      "libx264",
      "-pix_fmt",
      "yuv420p",
      video,
    ],
  );
  for (const n of [1, 2, 3]) {
    const res = await request.post("/api/projects/" + id + "/upload", {
      multipart: {
        stage: "video",
        entity_id: `s0${n}`,
        file: {
          name: "fixture.mp4",
          mimeType: "video/mp4",
          buffer: readFileSync(video),
        },
      },
    });
    expect(res.ok(), await res.text()).toBeTruthy();
    await request.post("/api/artifacts/" + (await res.json()).id + "/review", {
      data: { approved: true },
    });
  }
  await page.locator(".pipeline-step").filter({ hasText: "成片交付" }).click();
  await page.getByRole("button", { name: "生成当前集" }).click();
  await expect(page.locator(".media-card .badge")).toContainText("待审核", {
    timeout: 30000,
  });
  await page.getByRole("button", { name: "审核", exact: true }).click();
  await expect(page.locator(".media-detail-preview video")).toBeVisible();
  await page.getByRole("button", { name: "批准素材", exact: true }).click();
  const result = await request.get("/api/projects/" + id + "/export.zip");
  expect(result.headers()["content-type"]).toContain("application/zip");
  expect((await result.body()).length).toBeGreaterThan(1000);
  expect(errors).toEqual([]);
});

test("settings preserve write-only keys and expose current gateway catalog", async ({
  page,
}) => {
  await page.goto("/#settings");
  await expect(
    page.getByRole("heading", { name: "连接模型，释放创作。" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "账号与连接", exact: true }).click();
  await expect(page.locator(".connection-card")).not.toHaveCount(0);
  await page.getByRole("button", { name: "生成策略", exact: true }).click();
  await page.getByRole("button", { name: "保存生成策略" }).click();
  await expect(page.getByRole("status")).toContainText("模型策略已保存");
});

test("mobile navigation and workspace fit a 390px screen", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "每个想法，都值得被看见。" }),
  ).toBeVisible();
  expect(
    await page.evaluate(() => document.body.scrollWidth),
  ).toBeLessThanOrEqual(390);
  await page.getByRole("button", { name: "打开导航" }).click();
  await page.getByRole("button", { name: "生成队列", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "创作在进行，一切有迹可循。" }),
  ).toBeVisible();
  expect(
    await page.evaluate(() => document.body.scrollWidth),
  ).toBeLessThanOrEqual(390);
});
