import { expect, test, type Page } from "@playwright/test";

type OfflineAccount = {
  id: string;
  name: string;
  enabled: boolean;
  state: string;
  error?: { code: string } | null;
};

function offlineGateway(initial: OfflineAccount[] = []) {
  return {
    accounts: initial.map((account) => ({ ...account })),
    cookies: new Map<string, string>(),
    calls: [] as { method: string; path: string; body: any }[],
    rejectCheck: "",
    seedreamConfigured: false,
  };
}

async function mockStudio(
  page: Page,
  fixture: ReturnType<typeof offlineGateway>,
) {
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();
    const respond = (data: unknown, status = 200) =>
      route.fulfill({ status, json: data });
    if (path === "/api/templates") return respond([]);
    if (path === "/api/projects") return respond([]);
    if (path === "/api/health")
      return respond({
        status: "ok",
        gateway: "ready",
        engine: "ready",
        worker: true,
      });
    if (path === "/api/settings")
      return respond({
        text: [],
        image: [],
        vision: [],
        video: [],
        image_qc_attempts: 3,
        image_qc_budget_cny: 0,
        audio: {},
        allow_paid: false,
        audio_mode: "edge",
        voice: "",
        subtitles: false,
      });
    if (path === "/api/upstreams") return respond({});
    if (path === "/api/gateway/seedream") {
      const body = request.postData() ? request.postDataJSON() : null;
      if (method !== "GET") fixture.calls.push({ method, path, body });
      if (method === "PUT") fixture.seedreamConfigured = true;
      if (method === "DELETE") fixture.seedreamConfigured = false;
      return respond({
        configured: fixture.seedreamConfigured,
        model: "doubao-seedream-5-0-flash-260915",
        base_url: "https://ark.cn-beijing.volces.com/api/v3",
        output_price_cny: 0.12,
      });
    }
    if (path === "/api/gateway/catalog")
      return respond({
        accounts: fixture.accounts,
        image: [{ id: "bing", name: "Bing 网页", configured: false }],
        video: [],
      });
    if (path === "/api/gateway/accounts" && method === "POST") {
      const body = request.postDataJSON();
      fixture.calls.push({ method, path, body });
      if ([...fixture.cookies.values()].includes(body.secure_1psid))
        return respond(
          { error: { message: "这份 Cookie 已用于另一个账号。" } },
          409,
        );
      const account = {
        id: `offline_${fixture.accounts.length + 1}`,
        name: body.name,
        enabled: true,
        state: "configured",
        error: null,
      };
      fixture.accounts.push(account);
      fixture.cookies.set(account.id, body.secure_1psid);
      return respond(account, 201);
    }
    const match = path.match(
      /^\/api\/gateway\/accounts\/([^/]+)(?:\/(connect|retry))?$/,
    );
    if (match) {
      const account = fixture.accounts.find((item) => item.id === match[1]);
      if (!account) return respond({}, 404);
      const body = request.postData() ? request.postDataJSON() : null;
      fixture.calls.push({ method, path, body });
      if (method === "PUT" && !match[2]) {
        account.name = body.name;
        account.enabled = body.enabled;
        account.state = body.enabled ? "configured" : "disabled";
        account.error = null;
        if (body.secure_1psid)
          fixture.cookies.set(account.id, body.secure_1psid);
        return respond(account);
      }
      if (method === "POST" && match[2] === "connect") {
        if (fixture.rejectCheck === account.id) {
          account.state = "login_required";
          account.error = { code: "authentication_failed" };
          return respond({ error: { message: "离线测试：登录已失效" } }, 401);
        }
        account.state = "ready";
        return respond({ connected: true, models: [] });
      }
      if (method === "POST" && match[2] === "retry") {
        account.error = null;
        return respond(account);
      }
    }
    return respond({ error: { message: "离线测试未提供此接口" } }, 404);
  });
}

async function openConnections(page: Page) {
  await page.goto("/#settings");
  await page.getByRole("button", { name: "账号与连接", exact: true }).click();
  return page.locator(".connection-card").filter({
    has: page.getByRole("heading", { name: "Gemini 网页", exact: true }),
  });
}

test("offline: add multiple Gemini accounts without exposing saved credentials", async ({
  page,
}) => {
  const fixture = offlineGateway();
  await mockStudio(page, fixture);
  const card = await openConnections(page);
  await expect(card).toContainText("可以分别添加多个 Gemini 网页账号");

  for (const [index, secret] of [
    "offline-cookie-one",
    "offline-cookie-two",
  ].entries()) {
    await card.getByRole("button", { name: "添加账号" }).click();
    const dialog = page.getByRole("dialog", { name: "配置模型连接" });
    await expect(dialog.getByLabel("账号名称")).toHaveValue(
      `Gemini 账号 ${index + 1}`,
    );
    await dialog.getByLabel("__Secure-1PSID Cookie Value").fill(secret);
    await dialog.getByRole("button", { name: "保存连接" }).click();
    await expect(dialog).toHaveCount(0);
  }

  await expect(card).toContainText("2 个账号");
  await expect(card).toContainText("2 个已启用");
  await expect(card.getByText("已保存，待检查")).toHaveCount(2);
  await expect(card.getByText("Gemini 账号 1")).toBeVisible();
  await expect(card.getByText("Gemini 账号 2")).toBeVisible();
  expect(fixture.calls.filter((call) => call.path.includes("accounts")).map((call) => [call.method, call.path])).toEqual([
    ["POST", "/api/gateway/accounts"],
    ["POST", "/api/gateway/accounts"],
  ]);
  await expect(page.locator("body")).not.toContainText("offline-cookie-one");
  await expect(page.locator("body")).not.toContainText("offline-cookie-two");
});

test("offline: synchronize Seedream settings without exposing saved key", async ({ page }) => {
  const fixture = offlineGateway();
  await mockStudio(page, fixture);
  await openConnections(page);
  const card = page.getByRole("region", { name: "Seedream 付费兜底" });
  await expect(card).toContainText("未配置");
  await expect(card).toContainText("所有启用账号（至少两个）均登录失效时");
  await expect(card).toContainText("状态不明或其他账号不可用不会触发");
  const key = card.getByLabel("火山方舟 API Key");
  await expect(key).toHaveAttribute("type", "password");
  await key.fill("offline-seedream-key");
  await card.getByRole("button", { name: "保存 Seedream 密钥" }).click();
  await expect(card).toContainText("已配置");
  await expect(key).toHaveValue("");
  await expect(page.locator("body")).not.toContainText("offline-seedream-key");
  await page.getByRole("button", { name: "生成策略", exact: true }).click();
  await expect(page.getByText(/Seedream.*付费/)).toBeVisible();
  await page.getByRole("button", { name: "账号与连接", exact: true }).click();
  page.once("dialog", (dialog) => dialog.accept());
  await card.getByRole("button", { name: "清除 Seedream 密钥" }).click();
  await expect(card).toContainText("未配置");
  expect(fixture.calls.filter((call) => call.path === "/api/gateway/seedream").map((call) => call.method)).toEqual(["PUT", "DELETE"]);
});

test("offline: manage each account independently and show individual failures", async ({
  page,
}) => {
  const fixture = offlineGateway([
    { id: "offline_1", name: "离线账号甲", enabled: true, state: "ready" },
    {
      id: "offline_2",
      name: "离线账号乙",
      enabled: true,
      state: "login_required",
      error: { code: "authentication_failed" },
    },
  ]);
  await mockStudio(page, fixture);
  const card = await openConnections(page);
  const first = card.locator(".account-row").filter({ hasText: "离线账号甲" });
  const second = card.locator(".account-row").filter({ hasText: "离线账号乙" });
  await expect(first).toContainText("已连接");
  await expect(second).toContainText("登录失效");
  await expect(second).toContainText("登录已失效，请更新此账号的 Cookie");

  await second.getByRole("button", { name: "编辑 离线账号乙" }).click();
  const dialog = page.getByRole("dialog", { name: "配置模型连接" });
  await expect(dialog.getByLabel("账号名称")).toHaveValue("离线账号乙");
  await expect(dialog.getByLabel("__Secure-1PSID Cookie Value")).toHaveValue(
    "",
  );
  await dialog.getByLabel("账号名称").fill("离线账号乙（备用）");
  await dialog.getByRole("button", { name: "保存连接" }).click();
  await expect(dialog).toHaveCount(0);
  const updated = card
    .locator(".account-row")
    .filter({ hasText: "离线账号乙（备用）" });
  await expect(updated).toContainText("已保存，待检查");
  expect(fixture.calls.at(-1)?.body).toMatchObject({
    name: "离线账号乙（备用）",
    enabled: true,
    secure_1psid: "",
    secure_1psidts: "",
  });
  expect(fixture.calls.at(-1)?.path).toBe("/api/gateway/accounts/offline_2");

  await updated
    .getByRole("button", { name: "停用 离线账号乙（备用）" })
    .click();
  await expect(updated).toContainText("已停用");
  await expect(
    updated.getByRole("button", { name: "检查 离线账号乙（备用） 连接" }),
  ).toBeDisabled();
  await updated
    .getByRole("button", { name: "启用 离线账号乙（备用）" })
    .click();
  await updated
    .getByRole("button", { name: "检查 离线账号乙（备用） 连接" })
    .click();
  await expect(updated).toContainText("已连接");
  expect(fixture.calls.at(-1)?.path).toBe(
    "/api/gateway/accounts/offline_2/connect",
  );

  fixture.rejectCheck = "offline_1";
  await first.getByRole("button", { name: "检查 离线账号甲 连接" }).click();
  await expect(page.getByRole("alert")).toContainText(
    "离线账号甲」连接检查失败",
  );
  await expect(first).toContainText("登录失效");
  await expect(updated).toContainText("已连接");
});

test("offline: duplicate Gemini credentials do not overwrite an existing account", async ({
  page,
}) => {
  const fixture = offlineGateway();
  await mockStudio(page, fixture);
  const card = await openConnections(page);
  await card.getByRole("button", { name: "添加账号" }).click();
  let dialog = page.getByRole("dialog", { name: "配置模型连接" });
  await dialog
    .getByLabel("__Secure-1PSID Cookie Value")
    .fill("offline-duplicate");
  await dialog.getByRole("button", { name: "保存连接" }).click();
  await expect(dialog).toHaveCount(0);
  await card.getByRole("button", { name: "添加账号" }).click();
  dialog = page.getByRole("dialog", { name: "配置模型连接" });
  await dialog
    .getByLabel("__Secure-1PSID Cookie Value")
    .fill("offline-duplicate");
  await dialog.getByRole("button", { name: "保存连接" }).click();
  await expect(dialog).toContainText("这份 Cookie 已用于另一个账号");
  await expect(card).toContainText("1 个账号");
  expect(fixture.accounts).toHaveLength(1);
});

test("offline: a successful recheck clears a prior login failure", async ({
  page,
}) => {
  const fixture = offlineGateway([
    {
      id: "offline_1",
      name: "离线恢复账号",
      enabled: true,
      state: "login_required",
      error: { code: "authentication_failed" },
    },
  ]);
  await mockStudio(page, fixture);
  const card = await openConnections(page);
  const account = card.locator(".account-row");
  await expect(account).toContainText("登录失效");
  await account.getByRole("button", { name: "检查 离线恢复账号 连接" }).click();
  await expect(account).toContainText("已连接");
  await expect(account).not.toContainText("登录已失效");
  expect(fixture.calls.map((call) => call.path)).toEqual([
    "/api/gateway/accounts/offline_1/connect",
    "/api/gateway/accounts/offline_1/retry",
  ]);
});

test("offline: Gemini account controls fit a narrow mobile viewport", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 700 });
  const fixture = offlineGateway([
    {
      id: "offline_1",
      name: "离线移动账号甲",
      enabled: true,
      state: "configured",
    },
    {
      id: "offline_2",
      name: "离线移动账号乙",
      enabled: false,
      state: "disabled",
    },
  ]);
  await mockStudio(page, fixture);
  const card = await openConnections(page);
  await expect(card.getByRole("button", { name: "添加账号" })).toBeVisible();
  await expect(
    card.getByRole("button", { name: "启用 离线移动账号乙" }),
  ).toBeVisible();
  expect(
    await page.evaluate(() => document.body.scrollWidth),
  ).toBeLessThanOrEqual(390);
  await card.getByRole("button", { name: "添加账号" }).click();
  await expect(
    page.getByRole("dialog", { name: "配置模型连接" }),
  ).toBeVisible();
  expect(
    await page.evaluate(() => document.body.scrollWidth),
  ).toBeLessThanOrEqual(390);
});
