import { expect, test, type Page } from "@playwright/test";
import { readFileSync, mkdirSync } from "node:fs";
import path from "node:path";

// Explicit offline UI fixtures. No external generation or real credentials.
function fixture() {
  const projects: any[] = [
    "working",
    "attention",
    "ready",
    "paused",
    "draft",
  ].map((state, n) => ({
    id: "offline_" + state,
    title:
      "离线示例 · " +
      {
        working: "猫咪早餐",
        attention: "灯塔来信",
        ready: "春日散步",
        paused: "海边日记",
        draft: "新故事",
      }[state],
    version: 1,
    mode: "drama",
    duration: 30,
    aspect_ratio: "9:16",
    episodes_count: 1,
    created_at: 10 - n,
    updated_at: 10 - n,
    production_mode: "full",
    automation: state !== "draft",
    automation_status: {
      state: {
        working: "retrying",
        attention: "attention",
        ready: "completed",
        paused: "paused",
        draft: "disabled",
      }[state],
      job_ids: state === "working" ? ["offline_auto_retry"] : [],
    },
    paused: state === "paused",
    cancelled: false,
    source: "离线示例",
    brief: "",
    style: "",
    language: "简体中文",
    chapters: [],
    contract: {
      revision: 1,
      markdown: "# 离线制作要求",
      hash: "offline",
      approved: true,
    },
    plan_approved: true,
    plan_revision: 1,
    plan: {
      assets: [{ id: "cat", name: "橘猫", kind: "character" }],
      episodes: [
        {
          id: "ep1",
          title: "第一集",
          shots: [
            {
              id: "s1",
              title: "准备早餐",
              duration: 30,
              asset_ids: ["cat"],
              dialogue: "",
              narration: "",
            },
          ],
        },
      ],
    },
    progress: [
      { id: "requirements", name: "制作要求", done: 1, total: 1 },
      { id: "plan", name: "分镜", done: 1, total: 1 },
      { id: "reference", name: "参考", done: 1, total: 3 },
      { id: "video", name: "视频", done: state === "ready" ? 2 : 0, total: 2 },
      { id: "render", name: "成片", done: state === "ready" ? 1 : 0, total: 1 },
    ],
    jobs: [],
    artifacts: [],
  }));
  const media = ["render", "reference", "audio"].map((kind, n) => ({
    id: "offline_media_" + kind,
    project_id: projects[2].id,
    project_title: projects[2].title,
    entity_id: kind === "render" ? "ep1" : "cat",
    title: kind === "render" ? "春日散步 · 第一集" : "橘猫参考",
    kind,
    mime:
      kind === "render"
        ? "video/mp4"
        : kind === "audio"
          ? "audio/mpeg"
          : "image/png",
    url: "/api/artifacts/offline_media_" + kind + "/content",
    thumbnail_url: "/api/artifacts/offline_media_" + kind + "/thumbnail",
    review: "approved",
    stale: false,
    archived: false,
    contract_revision: 1,
    plan_revision: 1,
    created_at: n,
    job_id: "offline_media_job",
    route: { provider: "offline-fixture", model: "offline-only", paid: false },
    metadata: {},
  }));
  projects[2].artifacts = media;
  const jobs: any[] = [
    {
      id: "offline_auto_retry",
      project_id: projects[0].id,
      stage: "video",
      entity_id: "s1",
      status: "blocked",
      error: { code: "VIDEO_QC_FAILED", message: "离线示例：画面检查未通过" },
    },
    {
      id: "offline_unknown",
      project_id: projects[1].id,
      stage: "video",
      entity_id: "s1",
      status: "blocked",
      error: { code: "SUBMISSION_UNKNOWN", message: "离线示例：请求结果不明" },
      verification_required: true,
    },
    {
      id: "offline_old",
      project_id: projects[2].id,
      stage: "render",
      entity_id: "ep1",
      status: "failed",
      error: { code: "NO_ROUTE", message: "离线旧失败" },
    },
    {
      id: "offline_complete",
      project_id: projects[2].id,
      stage: "render",
      entity_id: "ep1",
      status: "succeeded",
      retry_of: "offline_old",
    },
  ].map((j, n) => ({
    ...j,
    created_at: n,
    attempt: 1,
    events: [],
    runtime: {},
  }));
  projects.forEach((p) => (p.jobs = jobs.filter((j) => j.project_id === p.id)));
  const settings: any = {
    text: [
      {
        transport: "cloud",
        provider: "compatible",
        model: "offline-writer",
        configured: true,
        base_url: "https://offline.example/v1",
      },
    ],
    image: [],
    vision: [],
    video: [],
    audio_mode: "edge",
    audio: {},
    voice: "zh-CN-XiaoxiaoNeural",
    speech: { speed: 1, vol: 1, pitch: 0 },
    allow_paid: false,
    subtitles: true,
    image_qc_attempts: 3,
    image_qc_budget_cny: 0,
  };
  return {
    projects,
    jobs,
    media,
    settings,
    accounts: [] as any[],
    mutations: [] as string[],
  };
}
async function mock(page: Page, data: ReturnType<typeof fixture>) {
  const png = readFileSync(path.resolve("../tests/fixtures/reference.png"));
  await page.route("**/api/**", async (route) => {
    const r = route.request(),
      url = new URL(r.url()),
      key = url.pathname;
    if (r.method() !== "GET") data.mutations.push(key);
    if (key.endsWith("/thumbnail"))
      return route.fulfill({ contentType: "image/png", body: png });
    if (key.endsWith("/content")) return route.fulfill({ status: 204 });
    let json: any = {};
    if (key === "/api/projects") json = data.projects;
    else if (key.startsWith("/api/projects/"))
      json = data.projects.find((p) => key.endsWith(p.id));
    else if (key === "/api/templates") json = [];
    else if (key === "/api/health")
      json = { status: "ok", gateway: "ready", engine: "ready", worker: true };
    else if (key === "/api/artifacts") json = data.media;
    else if (key === "/api/jobs") json = data.jobs;
    else if (key.startsWith("/api/jobs/"))
      json = data.jobs.find((j) => key.endsWith(j.id));
    else if (key === "/api/settings") json = data.settings;
    else if (key === "/api/audio/voice-library") json = { voices: [] };
    else if (key === "/api/gateway/catalog")
      json = { accounts: data.accounts, image: [], video: [] };
    else if (key === "/api/gateway/seedream") json = { configured: false };
    return route.fulfill({ json });
  });
}

test("offline everyday projects: truthful status filters, counts, progress and next actions", async ({
  page,
}) => {
  const data = fixture();
  await mock(page, data);
  await page.goto("/#projects");
  await expect(page.locator(".project-card")).toHaveCount(5);
  await page.getByRole("button", { name: "已有成片 1", exact: true }).click();
  await expect(page.locator(".project-card")).toHaveCount(1);
  await expect(page.locator(".project-card")).toContainText("成片已完成");
  await expect(page.getByRole("link", { name: "下载作品包" })).toHaveAttribute(
    "href",
    "/api/projects/offline_ready/export.zip",
  );
  const progress = page.getByRole("progressbar");
  expect(
    Number(await progress.getAttribute("aria-valuenow")),
  ).toBeLessThanOrEqual(100);
  await page.getByRole("button", { name: "需要处理 1", exact: true }).click();
  await expect(page.locator(".project-card")).toContainText("查看并解决");
  await page.getByRole("button", { name: "正在制作 1", exact: true }).click();
  await expect(page.locator(".project-card")).toContainText("自动重试中");
  await page.getByLabel("搜索项目").fill("不存在的作品");
  await expect(page.getByRole("button", { name: "清除筛选" })).toBeVisible();
  await page.getByRole("button", { name: "清除筛选" }).click();
  await expect(page.locator(".project-card")).toHaveCount(5);
  expect(data.mutations).toEqual([]);
});

test("offline everyday library: completed downloads, media explanation and a separate saved-voice area", async ({
  page,
}) => {
  const data = fixture();
  await mock(page, data);
  await page.goto("/#assets");
  await expect(
    page.getByRole("region", { name: "固定音色资源库" }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: /下载最终成片/ }).click();
  await expect(page.locator(".media-card")).toHaveCount(1);
  await expect(
    page.getByRole("link", { name: "下载 MP4", exact: true }),
  ).toHaveAttribute("href", /offline_media_render\/content\?download=true$/);
  await expect(page.locator(".media-help")).toContainText(
    "已经拼接好的整集视频",
  );
  await page
    .getByRole("button", { name: "已保存的角色声音", exact: true })
    .click();
  await expect(
    page.getByRole("region", { name: "固定音色资源库" }),
  ).toBeVisible();
  await expect(page.locator(".media-grid")).toHaveCount(0);
  expect(data.mutations).toEqual([]);
});

test("offline everyday queue: automatic retry differs from required action and unknown submission stays blocked", async ({
  page,
}) => {
  const data = fixture();
  await mock(page, data);
  await page.goto("/#jobs");
  await expect(page.locator(".job-table tbody tr")).toHaveCount(3);
  await page.getByRole("button", { name: "需要处理 1", exact: true }).click();
  const row = page.locator(".job-table tbody tr");
  await expect(row).toHaveCount(1);
  await expect(row).toContainText("灯塔来信");
  await expect(row).toContainText("核对供应商");
  await expect(
    row.getByRole("button", { name: "重试", exact: true }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "进行中 1", exact: true }).click();
  await expect(row).toContainText("等待自动重试");
  await expect(
    row.getByRole("button", { name: "重试", exact: true }),
  ).toBeDisabled();
  await page.getByRole("checkbox", { name: "显示以前的重试记录" }).check();
  await page.getByRole("button", { name: "全部任务 4", exact: true }).click();
  await expect(row).toHaveCount(4);
  expect(data.mutations).toEqual([]);
});

test("offline everyday settings: guided configuration, optional details and retained unsaved edits", async ({
  page,
}) => {
  const data = fixture();
  data.accounts.push({
    id: "offline_account",
    name: "离线测试账号",
    enabled: true,
    state: "ready",
  });
  await mock(page, data);
  await page.goto("/#settings");
  await expect(
    page.getByRole("region", { name: "首次配置清单" }),
  ).toContainText("1 / 4 项能力已配置");
  await expect(page.getByLabel("音频方式")).toHaveCount(0);
  await page
    .getByRole("button", { name: /检查画面是否一致.*查看与设置/ })
    .click();
  await expect(page.locator("#ability-vision")).toHaveAttribute("open", "");
  await expect(page.locator("#ability-text")).not.toHaveAttribute("open", "");
  await page
    .locator("#ability-vision")
    .getByRole("button", { name: "选择一个模型" })
    .click();
  await page
    .locator("#ability-vision")
    .getByLabel("模型名称 / ID")
    .fill("offline-vision");
  await expect(page.getByRole("status")).toContainText("有修改还没保存");
  await page.getByRole("button", { name: "从这里开始", exact: true }).click();
  await page.getByRole("button", { name: "账号与连接", exact: true }).click();
  await page
    .getByRole("button", { name: "检查 离线测试账号 连接", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "检查 离线测试账号 连接", exact: true }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "生成策略", exact: true }).click();
  await expect(
    page.locator("#ability-vision").getByLabel("模型名称 / ID"),
  ).toHaveValue("offline-vision");
  expect(data.mutations).toEqual([
    "/api/gateway/accounts/offline_account/connect",
  ]);
});

test("offline everyday five pages: desktop and mobile layouts are readable and fit the viewport", async ({
  page,
}) => {
  const data = fixture();
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await mock(page, data);
  const directory = path.resolve("../.local/qa-everyday-pages");
  mkdirSync(directory, { recursive: true });
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    for (const section of ["home", "projects", "assets", "jobs", "settings"]) {
      await page.goto("/#" + section);
      await expect(
        page.locator(
          section === "home" || section === "projects"
            ? ".home-content"
            : section === "assets"
              ? ".gallery-content"
              : section === "jobs"
                ? ".queue-content"
                : ".settings-content",
        ),
      ).toBeVisible();
      if (section === "settings")
        await expect(
          page.getByRole("region", { name: "首次配置清单" }),
        ).toBeVisible();
      if (section === "projects")
        await expect(page.locator(".project-card")).toHaveCount(5);
      if (section === "jobs")
        await expect(page.locator(".job-table tbody tr")).toHaveCount(3);
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth),
      ).toBeLessThanOrEqual(width);
      if (width === 390) {
        await expect
          .poll(() =>
            page
              .getByRole("complementary", { name: "工作台导航" })
              .evaluate((el) => el.getBoundingClientRect().right),
          )
          .toBeLessThanOrEqual(0);
      }
      await page.screenshot({
        path: path.join(directory, section + "-" + width + ".png"),
        fullPage: true,
        animations: "disabled",
      });
    }
  }
  expect(errors).toEqual([]);
  expect(data.mutations).toEqual([]);
});
