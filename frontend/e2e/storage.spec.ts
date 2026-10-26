import { test, expect } from "@playwright/test";

test("COS settings keep saved credentials hidden and report retained local copies", async ({
  page,
}) => {
  const publicConfig = {
    enabled: true,
    auto_upload: false,
    bucket: "offline-fixture-1234567890",
    region: "ap-guangzhou",
    prefix: "frameweave",
    configured: true,
    usage: { originals: 4, thumbnails: 3, bytes: 4096, local_copies: 4 },
  };
  let saved: any;
  await page.route("**/api/storage", async (route) => {
    if (route.request().method() === "PUT")
      saved = route.request().postDataJSON();
    await route.fulfill({ json: publicConfig });
  });
  await page.route("**/api/storage/test", (route) =>
    route.fulfill({
      json: {
        status: "ok",
        upload: true,
        download_verified: true,
        lifecycle: {
          status: "unknown",
          message:
            "无法读取生命周期规则；请先确认长期前缀不会过期，本地副本保留。",
        },
      },
    }),
  );
  await page.goto("/#settings");
  await page.getByRole("button", { name: "云端存储", exact: true }).click();
  await expect(page.getByLabel("COS SecretKey", { exact: true })).toHaveValue(
    "",
  );
  await expect(page.locator(".settings-content")).toContainText(
    "4 份原始素材仍有本地副本",
  );
  await page
    .getByLabel("COS SecretKey", { exact: true })
    .fill("offline-fixture-secret");
  await page
    .getByRole("button", { name: "保存并检查连接", exact: true })
    .click();
  await expect(page.locator(".settings-content")).toContainText("本地副本保留");
  expect(saved.secret_key).toBe("offline-fixture-secret");
  expect(saved.auto_upload).toBe(false);
  expect(saved.usage).toBeUndefined();
  await expect(page.getByLabel("COS SecretKey", { exact: true })).toHaveValue(
    "",
  );
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(() => document.body.scrollWidth),
  ).toBeLessThanOrEqual(390);
});
