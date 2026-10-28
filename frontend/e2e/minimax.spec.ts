import { expect, test, type Page } from "@playwright/test";

// All API responses below are offline fixtures, with no external synthesis or real keys.
function offlineFixture() {
  const route = { transport: "cloud", provider: "compatible", model: "tts-1", base_url: "", configured: false };
  const settings: any = { audio_mode: "native", audio: route, voice: "zh-CN-XiaoxiaoNeural",
    speech: { speed: 1, vol: 1, pitch: 0, emotion: null }, allow_paid: true, subtitles: true,
    text: [], image: [], vision: [], video: [], image_qc_attempts: 3, image_qc_budget_cny: 0 };
  const project: any = {
    id: "project_minimax_offline", version: 1, title: "离线配音界面验证", mode: "drama",
    brief: "", source: "离线测试原文", style: "", language: "简体中文", duration: 10,
    aspect_ratio: "16:9", episodes_count: 1, chapters: [],
    contract: { revision: 1, hash: "offline-contract", markdown: "离线制作规范，保留原台词", approved: true },
    plan_revision: 1, plan_approved: true, automation: false, paused: false, cancelled: false,
    created_at: 1, updated_at: 1, artifacts: [], jobs: [],
    plan: { assets: [{ id: "cat", kind: "character", name: "栗栗", description: "离线猫素材" },
      { id: "customer", kind: "character", name: "顾客", description: "离线顾客素材" }],
      episodes: [{ id: "ep01", title: "第一集", synopsis: "离线故事", source_chapters: [1], shots: [
        { id: "s01", title: "开摊", description: "离线开摊动作", duration: 10, dialogue: "今日上新，全店三本。", narration: "", asset_ids: ["cat"] },
      ] }] },
    audio_cast: { voices: { cat: { voice_id: "cute_boy", speed: 1, vol: 1, pitch: 0 },
      customer: { voice_id: "male-qn-daxuesheng", speed: 1, vol: 1, pitch: 0 } }, speakers: { s01: "cat" } },
    progress: [{ id: "requirements", name: "制作规范", done: 1, total: 1 },
      { id: "plan", name: "分集分镜", done: 1, total: 1 },
      { id: "video", name: "动态视频", done: 0, total: 1 }],
  };
  return { settings, project, library: [] as any[], saves: [] as any[], generationCalls: [] as any[], catalogueCalls: 0 };
}

async function mockStudio(page: Page, fixture: ReturnType<typeof offlineFixture>) {
  await page.route("**/api/**", async (route) => {
    const request = route.request(), path = new URL(request.url()).pathname;
    const respond = (json: unknown, status = 200) => route.fulfill({ json, status });
    if (path === "/api/templates") return respond([]);
    if (path === "/api/projects") return respond([fixture.project]);
    if (path === "/api/health") return respond({ status: "ok", gateway: "ready", engine: "ready", worker: true });
    if (path === "/api/settings") {
      if (request.method() === "PUT") {
        const body = request.postDataJSON(); fixture.saves.push(body);
        fixture.settings = { ...body, audio: { ...body.audio, configured: true } };
        delete fixture.settings.audio.api_key;
      }
      return respond(fixture.settings);
    }
    if (path === "/api/upstreams") return respond({});
    if (path === "/api/artifacts") return respond([]);
    if (path === "/api/audio/voice-library") return respond({ voices: fixture.library });
    if (path === "/api/gateway/catalog") return respond({ accounts: [], image: [], video: [] });
    if (path === "/api/gateway/seedream") return respond({ configured: false });
    if (path === "/api/audio/minimax/voices") {
      fixture.catalogueCalls++;
      return respond({ voices: [{ id: "cute_boy", name: "可爱男童", kind: "system_voice" }] });
    }
    if (path === `/api/projects/${fixture.project.id}`) return respond(fixture.project);
    if (path.endsWith("/audio-cast")) {
      const body = request.postDataJSON();
      expect(body.expected_version).toBe(fixture.project.version);
      fixture.project.audio_cast = { voices: body.voices, speakers: body.speakers };
      fixture.project.version++;
      return respond(fixture.project);
    }
    if (path.endsWith("/jobs") && request.method() === "POST") {
      fixture.generationCalls.push(request.postDataJSON());
      return respond({ id: "offline-audition", status: "queued" }, 202);
    }
    return respond({ error: { message: "离线测试未提供此接口" } }, 404);
  });
}

test("offline MiniMax setup presets, key save and non-generating voice check", async ({ page }) => {
  const fixture = offlineFixture();
  const errors: string[] = []; page.on("pageerror", (error) => errors.push(error.message));
  await mockStudio(page, fixture);
  await page.goto("/#settings");
  await page.getByLabel("音频方式").selectOption("cloud");
  await page.getByLabel("配音服务").selectOption("minimax");
  await expect(page.getByLabel("配音 API 地址")).toHaveValue("https://api.minimaxi.com/v1");
  await expect(page.getByLabel("配音模型")).toHaveValue("speech-2.8-turbo");
  await expect(page.getByLabel("声线 ID", { exact: true })).toHaveValue("cute_boy");
  await page.getByLabel("API Key", { exact: true }).fill("offline-ui-key");
  await page.getByRole("button", { name: "保存生成策略" }).click();
  await expect(page.getByLabel("API Key", { exact: true })).toHaveValue("");
  await page.getByRole("button", { name: "读取可用音色 / 检查密钥" }).click();
  await expect.poll(() => fixture.catalogueCalls).toBe(1);
  expect(fixture.saves[0].audio.model).toBe("speech-2.8-turbo");
  expect(fixture.generationCalls).toHaveLength(0);
  expect(errors).toEqual([]);
});

test("offline character casting preserves script, submits one line and blocks uncertain repeats", async ({ page }) => {
  const fixture = offlineFixture();
  fixture.settings.audio_mode = "cloud";
  fixture.settings.audio = { transport: "cloud", provider: "minimax", model: "speech-2.8-turbo", base_url: "https://api.minimaxi.com/v1", configured: true };
  const errors: string[] = []; page.on("pageerror", (error) => errors.push(error.message));
  await mockStudio(page, fixture);
  await page.goto(`/#project/${fixture.project.id}`);
  await page.getByRole("button", { name: "角色配音", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "角色配音", exact: true });
  await expect(dialog.getByLabel("栗栗声线 ID")).toHaveValue("cute_boy");
  await dialog.getByLabel("栗栗声线 ID").fill("clever_boy");
  await dialog.getByRole("button", { name: "保存角色配音", exact: true }).click();
  await expect.poll(() => fixture.project.audio_cast.voices.cat.voice_id).toBe("clever_boy");
  expect(fixture.project.plan_revision).toBe(1);
  expect(fixture.project.contract.markdown).toBe("离线制作规范，保留原台词");
  await dialog.getByRole("button", { name: "生成栗栗试音", exact: true }).click();
  await expect.poll(() => fixture.generationCalls.length).toBe(1);
  expect(fixture.generationCalls[0]).toEqual({ stage: "audio", entity_id: "s01", force: true });
  fixture.project.jobs = [{ id: "offline-uncertain", stage: "audio", entity_id: "s01", status: "blocked", verification_required: true }];
  await dialog.getByRole("button", { name: "关闭角色配音" }).click();
  await page.reload();
  await page.getByRole("button", { name: "角色配音", exact: true }).click();
  await page.getByRole("button", { name: "生成栗栗试音", exact: true }).click();
  await expect(page.getByText("上次试音结果不明，请先在任务页核对供应商记录和账单。")).toBeVisible();
  expect(fixture.generationCalls).toHaveLength(1);
  expect(errors).toEqual([]);
});

test("offline locked voice library previews and reuses saved IDs without synthesis", async ({ page }) => {
  const fixture = offlineFixture();
  fixture.library = ["青年顾客", "邻摊顾客", "老人"].map((name, i) => ({
    id: "voice_" + String(i + 1).repeat(20), name, provider: "minimax",
    voice_id: "offline-activated-" + i, profile: { voice_id: "offline-activated-" + i, speed: 1.06, vol: 1, pitch: 0 },
    locked: true, activated: true, review: "pending", preview_url: "/offline-preview-" + i + ".mp3",
  }));
  const errors: string[] = []; page.on("pageerror", (error) => errors.push(error.message));
  await mockStudio(page, fixture);
  await page.goto("/#assets");
  const library = page.getByRole("region", { name: "固定音色资源库" });
  await expect(library.getByRole("heading", { name: "固定音色资源库 · 3" })).toBeVisible();
  await expect(library.locator("audio")).toHaveCount(3);
  await expect(library.getByRole("link", { name: "备份音色资源库" })).toHaveAttribute("href", "/api/audio/voice-library/export.zip");
  await page.goto(`/#project/${fixture.project.id}`);
  await page.getByRole("button", { name: "角色配音", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "角色配音", exact: true });
  await dialog.getByLabel("顾客复用资源库音色", { exact: true }).selectOption(fixture.library[0].id);
  await expect(dialog.getByLabel("顾客声线 ID", { exact: true })).toHaveValue("offline-activated-0");
  await expect(dialog.getByLabel("顾客声线 ID", { exact: true })).toHaveAttribute("readonly");
  await dialog.getByRole("button", { name: "保存角色配音", exact: true }).click();
  await expect.poll(() => fixture.project.audio_cast.voices.customer.library_id).toBe(fixture.library[0].id);
  await page.reload();
  await page.getByRole("button", { name: "角色配音", exact: true }).click();
  await expect(page.getByLabel("顾客复用资源库音色", { exact: true })).toHaveValue(fixture.library[0].id);
  expect(fixture.generationCalls).toHaveLength(0);
  expect(fixture.catalogueCalls).toBe(0);
  expect(errors).toEqual([]);
});

test("offline cat recording selection persists and disables paid TTS", async ({ page }) => {
  const fixture = offlineFixture();
  fixture.settings.audio_mode = "cloud";
  fixture.settings.audio = { transport: "cloud", provider: "minimax", model: "speech-2.8-turbo", base_url: "https://api.minimaxi.com/v1", configured: true };
  await mockStudio(page, fixture);
  await page.goto(`/#project/${fixture.project.id}`);
  await page.getByRole("button", { name: "角色配音", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "角色配音", exact: true });
  await dialog.getByLabel("栗栗供音方式", { exact: true }).selectOption("recording");
  await expect(dialog.getByRole("button", { name: "使用已有录音，不生成试音", exact: true })).toBeDisabled();
  await expect(dialog.getByLabel("栗栗声线 ID")).toHaveCount(0);
  await dialog.getByRole("button", { name: "保存角色配音", exact: true }).click();
  await expect.poll(() => fixture.project.audio_cast.voices.cat.source).toBe("recording");
  expect(fixture.generationCalls).toHaveLength(0);
});
