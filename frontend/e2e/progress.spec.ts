import { expect, test, type Page } from "@playwright/test";

function offlineFixture() {
  const timestamp = Math.floor(Date.now() / 1000);
  const job = (id: string, entity: string, status: string) => ({
    id,
    project_id: "project_progress",
    stage: "reference",
    entity_id: entity,
    status,
    created_at: timestamp,
    attempt: 1,
    runtime: {},
  });
  const assets = ["人物", "场景", "道具", "宠物"].map((name, index) => ({
    id: `asset${index + 1}`,
    name,
    kind: "character",
    description: `离线测试素材${name}`,
  }));
  const jobs: any[] = [
    {
      ...job("job_running", "asset1", "running"),
      latest_event: { created: timestamp, message: "离线测试：等待网页网关" },
    },
    {
      ...job("job_failed", "asset2", "failed"),
      error: { code: "NO_ROUTE", message: "没有可用模型渠道" },
    },
    job("job_queued", "asset3", "queued"),
    job("job_success", "asset4", "succeeded"),
  ];
  const project: any = {
    id: "project_progress",
    title: "离线进度测试",
    mode: "drama",
    brief: "",
    source: "离线测试文本",
    style: "",
    language: "简体中文",
    duration: 10,
    aspect_ratio: "9:16",
    episodes_count: 1,
    chapters: [],
    contract: {
      revision: 1,
      markdown: "测试规范",
      hash: "contract",
      approved: true,
    },
    plan: { assets, episodes: [{ id: "ep1", title: "第一集", shots: [] }] },
    plan_revision: 1,
    plan_approved: true,
    automation: true,
    automation_status: { state: "running" },
    paused: false,
    cancelled: false,
    artifacts: [],
    jobs,
    progress: [
      { id: "requirements", name: "制作规范", done: 1, total: 1 },
      { id: "plan", name: "分集分镜", done: 1, total: 1 },
      { id: "reference", name: "素材参考", done: 0, total: 4 },
      { id: "keyframe", name: "镜头画面", done: 0, total: 0 },
      { id: "video", name: "动态视频", done: 0, total: 0 },
      { id: "render", name: "成片交付", done: 0, total: 1 },
    ],
    created_at: timestamp,
    updated_at: timestamp,
  };
  return {
    project,
    worker: true,
    retryCalls: 0,
    scheduleCalls: 0,
    generationCalls: 0,
    timestamp,
  };
}

async function mockStudio(
  page: Page,
  fixture: ReturnType<typeof offlineFixture>,
) {
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const respond = (data: unknown, status = 200) =>
      route.fulfill({ status, json: data });
    if (path === "/api/templates") return respond([]);
    if (path === "/api/health")
      return respond({
        status: "ok",
        worker: fixture.worker,
        gateway: "ready",
        engine: "ready",
      });
    if (path === "/api/projects") return respond([fixture.project]);
    if (path === "/api/projects/project_progress")
      return respond(fixture.project);
    if (
      path === "/api/projects/project_progress/jobs" &&
      request.method() === "POST"
    ) {
      fixture.generationCalls++;
      return respond({}, 202);
    }
    if (
      path === "/api/projects/project_progress/automation" &&
      request.method() === "POST"
    ) {
      fixture.scheduleCalls++;
      fixture.project.automation_status = {
        state: "scheduling",
        stage: "reference",
      };
      return respond(fixture.project);
    }
    if (path === "/api/jobs/job_failed/retry" && request.method() === "POST") {
      fixture.retryCalls++;
      const failed = fixture.project.jobs.find(
        (job: any) => job.id === "job_failed",
      );
      const retried = {
        ...failed,
        id: "job_retry",
        status: "queued",
        error: null,
        attempt: 2,
        created_at: fixture.timestamp + 1,
      };
      fixture.project.jobs.push(retried);
      return respond(retried, 202);
    }
    if (path === "/api/jobs")
      return respond(
        fixture.project.jobs.map(({ runtime, ...job }: any) => ({
          ...job,
          verification_required: !!runtime?.cloud_submitting,
        })),
      );
    if (path.startsWith("/api/jobs/")) {
      const id = path.split("/").pop();
      const job = fixture.project.jobs.find((item: any) => item.id === id);
      return job
        ? respond({
            ...job,
            events: [
              { created: fixture.timestamp, message: "离线测试：渠道不可用" },
            ],
          })
        : respond({ error: { message: "任务不存在" } }, 404);
    }
    if (path.startsWith("/api/artifacts/"))
      return route.fulfill({ status: 404 });
    return respond({ error: { message: "离线测试未提供此接口" } }, 404);
  });
}

test("offline fixture: shows live task progress, failures, retry and review separately", async ({
  page,
}) => {
  const fixture = offlineFixture();
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await mockStudio(page, fixture);
  await page.goto("/#project/project_progress");
  const panel = page.getByRole("region", { name: "项目生产进度" });
  await expect(panel).toContainText("自动推进进行中");
  await expect(panel).toContainText("生成中 1");
  await expect(panel).toContainText("排队 1");
  await expect(panel).toContainText("成功 1");
  await expect(panel).toContainText("失败 1");
  await expect(panel).toContainText("离线测试：等待网页网关");
  const failed = panel.locator(".activity-entry").filter({ hasText: "场景" });
  await expect(failed).toContainText("没有可用模型渠道");
  await failed.getByRole("button", { name: /任务详情/ }).click();
  await expect(failed).toContainText("NO_ROUTE");
  await expect(failed).toContainText("离线测试：渠道不可用");

  fixture.project.jobs[0].latest_event.message = "离线测试：收到远端进度";
  await expect(panel).toContainText("离线测试：收到远端进度", {
    timeout: 10000,
  });
  await failed.getByRole("button", { name: "重试" }).click();
  await expect.poll(() => fixture.retryCalls).toBe(1);
  await expect(panel).toContainText("失败 0");
  await expect(panel).toContainText("排队 2");

  fixture.project.jobs = fixture.project.jobs
    .filter((job: any) => job.id !== "job_failed")
    .map((job: any) => ({ ...job, status: "succeeded" }));
  fixture.project.automation_status = { state: "review", stage: "reference" };
  fixture.project.artifacts = [
    {
      id: "fixture_pending",
      kind: "reference",
      entity_id: "asset1",
      stale: false,
      review: "pending",
      url: "/api/artifacts/fixture_pending/content",
    },
    {
      id: "fixture_approved",
      kind: "reference",
      entity_id: "asset2",
      stale: false,
      review: "approved",
      url: "/api/artifacts/fixture_approved/content",
    },
  ];
  await expect(panel).toContainText("等待人工审核", { timeout: 10000 });
  await panel.getByRole("button", { name: "前往审核" }).click();
  await expect(page.locator(".media-filters")).toContainText("已有结果 2 / 4");
  await expect(page.locator(".media-filters")).toContainText("待审核 1");
  await expect(page.locator(".media-filters")).toContainText("已批准 1");
  fixture.project.automation_status = { state: "rework", stage: "reference" };
  await expect(panel).toContainText("等待返工", { timeout: 10000 });
  await expect(panel.getByRole("button", { name: "前往处理" })).toBeVisible();
  expect(pageErrors).toEqual([]);
});

test("offline fixture: a draft reference can be uploaded and previewed without generation or approval", async ({
  page,
}) => {
  const fixture = offlineFixture();
  fixture.project.contract.approved = false;
  fixture.project.plan_approved = false;
  fixture.project.plan_contract_hash = fixture.project.contract.hash;
  fixture.project.automation = false;
  fixture.project.automation_status = { state: "disabled" };
  fixture.project.jobs = [];
  let uploadCalls = 0;
  await mockStudio(page, fixture);
  await page.route("**/api/projects/project_progress/upload", async (route) => {
    uploadCalls++;
    const body = route.request().postData() || "";
    expect(body).toContain("asset1");
    expect(body).toContain("reference");
    fixture.project.artifacts.unshift({
      id: "fixture_draft_reference",
      kind: "reference",
      entity_id: "asset1",
      stale: false,
      archived: false,
      review: "pending",
      url: "/api/artifacts/fixture_draft_reference/content",
      mime: "image/png",
      route: { provider: "user-upload", model: "original", paid: false },
      metadata: {},
      created_at: fixture.timestamp,
    });
    return route.fulfill({ status: 201, json: fixture.project.artifacts[0] });
  });
  await page.goto("/#project/project_progress");
  await page
    .locator(".pipeline-steps")
    .getByRole("button", { name: /素材参考/ })
    .click();
  await expect(page.getByText(/可以先导入角色/)).toBeVisible();
  await expect(page.getByRole("region", { name: "批量审核" })).toHaveCount(0);
  const first = page.locator(".media-card").filter({ has: page.getByRole("heading", { name: "人物", exact: true }) });
  await expect(
    first.getByRole("button", { name: "生成", exact: true }),
  ).toBeDisabled();
  await first.getByRole("button", { name: "上传已有素材" }).click();
  await page.locator('input[type="file"]').setInputFiles({
    name: "offline-reference.png",
    mimeType: "image/png",
    buffer: Buffer.from("offline fixture, not model output"),
  });
  await expect.poll(() => uploadCalls).toBe(1);
  await expect(first).toContainText("待审核");
  await first.getByRole("button", { name: "预览" }).click();
  const dialog = page.getByRole("dialog", { name: "素材预览与审核" });
  await expect(dialog).toContainText("请先批准当前制作规范与分镜");
  await expect(dialog.getByRole("button", { name: "批准素材" })).toBeDisabled();
  await expect(dialog.getByRole("button", { name: "需要返工" })).toBeDisabled();
  expect(fixture.generationCalls).toBe(0);
  expect(fixture.scheduleCalls).toBe(0);
});

test("offline fixture: batch review confirms, reports partial failures, and preserves approved items", async ({ page }) => {
  const fixture = offlineFixture();
  fixture.project.jobs = [];
  fixture.project.automation_status = { state: "review", stage: "reference" };
  fixture.project.artifacts = ["pending", "pending", "approved", "pending"].map((review, index) => ({
    id: `offline_review_${index + 1}`,
    project_id: fixture.project.id,
    kind: "reference",
    entity_id: `asset${index + 1}`,
    stale: false,
    archived: index === 3,
    review,
    url: `/api/artifacts/offline_review_${index + 1}/content`,
    mime: "image/png",
    route: {}, metadata: {}, contract_revision: 1, plan_revision: 1,
    created_at: fixture.timestamp,
  }));
  const calls: { id: string; body: any }[] = [];
  let failSecond = true;
  await mockStudio(page, fixture);
  await page.route("**/api/artifacts/*/review", async (route) => {
    const id = new URL(route.request().url()).pathname.split("/")[3];
    const body = route.request().postDataJSON();
    calls.push({ id, body });
    if (id === "offline_review_2" && failSecond)
      return route.fulfill({ status: 409, json: { error: { message: "素材审核状态已变化" } } });
    const item = fixture.project.artifacts.find((artifact: any) => artifact.id === id);
    item.review = body.approved ? "approved" : "rejected";
    return route.fulfill({ status: 200, json: item });
  });
  await page.goto("/#project/project_progress");
  await page.locator(".pipeline-step").filter({ hasText: "素材参考" }).click();
  const batch = page.getByRole("region", { name: "批量审核" });
  await expect(batch).toContainText("已选择 0 / 2 项待审核");
  await expect(page.getByRole("checkbox", { name: "选择审核 道具" })).toHaveCount(0);
  await expect(page.getByRole("checkbox", { name: "选择审核 宠物" })).toHaveCount(0);
  await batch.getByRole("button", { name: "全选待审核" }).click();
  await batch.getByLabel("审核备注（可选）").fill("离线审核备注");
  page.once("dialog", async (dialog) => {
    expect(dialog.message()).toContain("可能继续生成并产生费用");
    await dialog.dismiss();
  });
  await batch.getByRole("button", { name: "批量批准" }).click();
  expect(calls).toHaveLength(0);
  page.once("dialog", (dialog) => dialog.accept());
  await batch.getByRole("button", { name: "批量批准" }).click();
  await expect(batch).toContainText("成功 1 项，失败 1 项");
  await expect(batch.getByRole("alert")).toContainText("场景：素材审核状态已变化");
  await expect(batch).toContainText("已选择 1 / 1 项待审核");
  expect(calls.map((call) => call.body)).toEqual([
    { approved: true, note: "离线审核备注", expected_review: "pending" },
    { approved: true, note: "离线审核备注", expected_review: "pending" },
  ]);
  failSecond = false;
  page.once("dialog", (dialog) => dialog.accept());
  await batch.getByRole("button", { name: "批量标记返工" }).click();
  await expect(batch).toContainText("成功 1 项，失败 0 项");
  expect(calls[2].body).toEqual({ approved: false, note: "离线审核备注", expected_review: "pending" });
  expect(fixture.project.artifacts.map((artifact: any) => artifact.review)).toEqual([
    "approved", "rejected", "approved", "pending",
  ]);
  expect(fixture.generationCalls).toBe(0);
});

test("offline fixture: batch selection stays within the active kind and episode", async ({ page }) => {
  const fixture = offlineFixture();
  fixture.project.jobs = [];
  fixture.project.plan.episodes = [
    { id: "ep1", title: "第一集", shots: [{ id: "shot1", title: "第一镜" }] },
    { id: "ep2", title: "第二集", shots: [{ id: "shot2", title: "第二镜" }] },
  ];
  fixture.project.artifacts = [
    { id: "ref1", kind: "reference", entity_id: "asset1" },
    { id: "frame1", kind: "keyframe", entity_id: "shot1" },
    { id: "frame2", kind: "keyframe", entity_id: "shot2" },
  ].map((item) => ({
    ...item, project_id: fixture.project.id, stale: false, review: "pending",
    url: `/api/artifacts/${item.id}/content`, mime: "image/png", route: {}, metadata: {},
    contract_revision: 1, plan_revision: 1, created_at: fixture.timestamp,
  }));
  await mockStudio(page, fixture);
  await page.goto("/#project/project_progress");
  await page.locator(".pipeline-step").filter({ hasText: "素材参考" }).click();
  const batch = page.getByRole("region", { name: "批量审核" });
  await batch.getByRole("button", { name: "全选待审核" }).click();
  await expect(batch).toContainText("已选择 1 / 1");
  await page.getByRole("button", { name: "逐镜关键帧" }).click();
  await expect(batch).toContainText("已选择 0 / 1");
  await batch.getByRole("button", { name: "全选待审核" }).click();
  await page.getByRole("combobox", { name: "选择集" }).selectOption({ label: "第二集" });
  await expect(batch).toContainText("已选择 0 / 1");
  await expect(page.getByRole("checkbox", { name: "选择审核 第二镜" })).toBeVisible();
});

test("offline fixture: image and video cards identify recorded source and fallback", async ({ page }) => {
  const fixture = offlineFixture();
  fixture.project.jobs = [];
  fixture.project.plan.episodes[0].shots = [{ id: "shot1", title: "第一镜" }];
  fixture.project.artifacts = [
    { id: "seedream", kind: "reference", entity_id: "asset1", mime: "image/png", route: { provider: "seedream", model: "doubao-seedream-5-0-flash-260915", paid: true } },
    { id: "gemini", kind: "reference", entity_id: "asset2", mime: "image/png", route: { provider: "gemini", model: "gemini-pro", paid: false } },
    { id: "upload", kind: "reference", entity_id: "asset3", mime: "image/png", route: { provider: "user-upload", model: "original", paid: false } },
    { id: "unknown", kind: "reference", entity_id: "asset4", mime: "image/png", route: {} },
    { id: "video", kind: "video", entity_id: "shot1", mime: "video/mp4", route: { provider: "kling", model: "kling-v2", paid: true } },
  ].map((item) => ({
    ...item, project_id: fixture.project.id, stale: false, review: "pending", metadata: {},
    url: `/api/artifacts/${item.id}/content`, contract_revision: 1, plan_revision: 1, created_at: fixture.timestamp,
  }));
  await mockStudio(page, fixture);
  await page.goto("/#project/project_progress");
  await page.locator(".pipeline-step").filter({ hasText: "素材参考" }).click();
  const card = (name: string) => page.locator(".media-card").filter({ has: page.getByRole("heading", { name, exact: true }) });
  await expect(card("人物")).toContainText("Seedream（火山方舟） · 付费");
  await expect(card("人物")).toContainText("doubao-seedream-5-0-flash-260915");
  await expect(card("场景")).toContainText("Gemini 网页");
  await expect(card("道具")).toContainText("用户上传 · 非模型生成");
  await expect(card("宠物")).toContainText("生成渠道未知");
  await card("人物").getByRole("button", { name: "审核", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("Seedream（火山方舟） · 付费");
  await page.getByRole("button", { name: "关闭预览" }).click();
  await page.locator(".pipeline-step").filter({ hasText: "动态视频" }).click();
  await expect(card("第一镜")).toContainText("kling · 付费");
  await expect(card("第一镜")).toContainText("kling-v2");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.body.scrollWidth)).toBeLessThanOrEqual(390);
});

test("offline fixture: reference regeneration shows the uploaded source and its binding", async ({
  page,
}) => {
  const fixture = offlineFixture();
  fixture.project.jobs = [];
  fixture.project.automation = false;
  fixture.project.automation_status = { state: "disabled" };
  fixture.project.artifacts = [
    {
      id: "fixture_result",
      kind: "reference",
      entity_id: "asset1",
      stale: false,
      review: "pending",
      url: "/api/artifacts/fixture_result/content",
      mime: "image/png",
      route: { provider: "gemini", model: "offline", paid: false },
      metadata: {},
      contract_revision: 1,
      plan_revision: 1,
      dependencies: ["fixture_original"],
      created_at: fixture.timestamp + 1,
    },
    {
      id: "fixture_original",
      kind: "reference",
      entity_id: "asset1",
      stale: true,
      review: "approved",
      url: "/api/artifacts/fixture_original/content",
      mime: "image/png",
      route: { provider: "user-upload", model: "original", paid: false },
      metadata: {},
      contract_revision: 1,
      plan_revision: 1,
      created_at: fixture.timestamp,
    },
  ];
  await mockStudio(page, fixture);
  await page.goto("/#project/project_progress");
  await page
    .locator(".pipeline-steps")
    .getByRole("button", { name: /素材参考/ })
    .click();
  const first = page.locator(".media-card").filter({ has: page.getByRole("heading", { name: "人物", exact: true }) });
  await expect(first).toContainText("重新生成将使用已批准的上传原图");
  await first.getByRole("button", { name: "审核", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "素材预览与审核" });
  await expect(dialog.getByRole("link", { name: "查看绑定原图" })).toHaveAttribute(
    "href",
    "/api/artifacts/fixture_original/content",
  );
  await dialog.getByRole("button", { name: "关闭预览" }).click();
  await first.getByRole("button", { name: "基于原图生成" }).click();
  await expect.poll(() => fixture.generationCalls).toBe(1);
});

test("offline fixture: warns about an offline worker and an uncertain submission on mobile", async ({
  page,
}) => {
  const fixture = offlineFixture();
  fixture.worker = false;
  fixture.project.automation_status = {
    state: "attention",
    stage: "reference",
  };
  fixture.project.jobs = [
    {
      ...fixture.project.jobs[1],
      status: "blocked",
      error: { code: "SUBMISSION_UNKNOWN", message: "云模型提交结果不明" },
      runtime: { cloud_submitting: true },
    },
  ];
  await page.setViewportSize({ width: 390, height: 844 });
  await mockStudio(page, fixture);
  await page.goto("/#project/project_progress");
  const panel = page.getByRole("region", { name: "项目生产进度" });
  await expect(panel).toContainText("自动推进等待处理");
  await expect(panel).toContainText("工作线程未运行");
  await expect(panel).toContainText("请先在供应商侧核对任务和账单");
  await expect(panel.getByRole("button", { name: "重试" })).toBeDisabled();
  await page.locator(".pipeline-step").filter({ hasText: "素材参考" }).click();
  await page.getByRole("button", { name: "生成当前素材库" }).click();
  await expect(page.locator(".workspace-error")).toContainText(
    "上次提交结果不明",
  );
  const dialogs: string[] = [];
  page.once("dialog", async (dialog) => {
    dialogs.push(dialog.message());
    await dialog.dismiss();
  });
  await page
    .locator(".media-card")
    .filter({ hasText: "场景" })
    .getByRole("button", { name: "生成", exact: true })
    .click();
  await expect.poll(() => dialogs.length).toBe(1);
  expect(dialogs[0]).toContain("确认已核对");
  expect(fixture.generationCalls).toBe(0);
  await panel.getByRole("button", { name: /查看全部生成队列/ }).click();
  const row = page.locator(".job-table tbody tr");
  await expect(row.getByRole("button", { name: "重试" })).toBeDisabled();
  await row.getByRole("button", { name: "详情" }).click();
  await expect(page.getByRole("dialog", { name: "任务详情" })).toContainText(
    "请求结果不明",
  );
  expect(
    await page.evaluate(() => document.body.scrollWidth),
  ).toBeLessThanOrEqual(390);
  expect(fixture.retryCalls).toBe(0);
});

test("offline fixture: blocks retry for an uncertain remote gateway result", async ({
  page,
}) => {
  const fixture = offlineFixture();
  fixture.project.automation_status = {
    state: "attention",
    stage: "reference",
  };
  fixture.project.jobs = [
    {
      ...fixture.project.jobs[1],
      status: "blocked",
      error: {
        code: "REMOTE_RESULT_UNCERTAIN",
        message: "离线测试：请核对网页记录",
      },
      runtime: { remote_id: "offline_remote" },
    },
  ];
  await mockStudio(page, fixture);
  await page.goto("/#project/project_progress");
  const panel = page.getByRole("region", { name: "项目生产进度" });
  await expect(panel).toContainText("请先在供应商侧核对任务和账单");
  await expect(panel.getByRole("button", { name: "重试" })).toBeDisabled();
  expect(fixture.retryCalls).toBe(0);
});

test("offline fixture: shows and retries a scheduling error", async ({
  page,
}) => {
  const fixture = offlineFixture();
  fixture.project.jobs = [];
  fixture.project.automation_status = {
    state: "error",
    error: {
      code: "SCHEDULING_FAILED",
      message: "素材参考图 asset1 尚未生成或批准",
    },
  };
  await mockStudio(page, fixture);
  await page.goto("/#project/project_progress");
  const panel = page.getByRole("region", { name: "项目生产进度" });
  await expect(panel).toContainText("素材参考图 asset1 尚未生成或批准");
  await panel.getByRole("button", { name: "重试安排" }).click();
  await expect.poll(() => fixture.scheduleCalls).toBe(1);
  await expect(panel).toContainText("正在准备下一步");
});
