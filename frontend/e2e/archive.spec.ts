import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import path from "node:path";

test("archive library filters, paginates and preserves historical review", async ({
  page,
}) => {
  const png = readFileSync(path.resolve("../tests/fixtures/reference.png"));
  const artifacts = Array.from({ length: 35 }, (_, n) => ({
    id: `archive-${n}`,
    project_id: "archive-demo",
    project_title: "历史档案测试",
    entity_id: `legacy-${n}`,
    title: `EP01-S${String(n + 1).padStart(2, "0")}.png`,
    kind: "reference",
    mime: "image/png",
    url: "/archive-fixture.png",
    thumbnail_url: "/archive-fixture.png",
    review: "pending",
    archived: true,
    stale: false,
    created_at: 0,
    contract_revision: 0,
    plan_revision: 1,
    route: { provider: "legacy-import", model: "原始素材", paid: false },
    metadata: {
      imported: {
        group: n < 33 ? "早期试片" : "制作候选",
        source_path: `original/EP01-S${n + 1}.png`,
        quality_status: "FAIL",
        production_approval: "WITHDRAWN",
        sha256: "test-fixture",
      },
    },
  }));
  await page.route("**/api/artifacts", (route) =>
    route.fulfill({ json: artifacts }),
  );
  await page.route("**/archive-fixture.png", (route) =>
    route.fulfill({ contentType: "image/png", body: png }),
  );
  await page.goto("/#assets/archive-demo");
  await expect(page.locator(".media-card")).toHaveCount(32);
  await expect(page.locator(".archive-filters")).toContainText("35 份素材");
  await page.getByRole("button", { name: "下一页", exact: true }).click();
  await expect(page.locator(".media-card")).toHaveCount(3);
  await page.getByLabel("按来源分组筛选").selectOption("制作候选");
  await expect(page.locator(".media-card")).toHaveCount(2);
  await page.getByLabel("搜索历史素材").fill("S35");
  await expect(page.locator(".media-card")).toHaveCount(1);
  await page.getByRole("button", { name: "预览 EP01-S35.png" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("FAIL");
  await expect(dialog).toContainText("WITHDRAWN");
  await expect(dialog.getByRole("button", { name: "批准素材" })).toHaveCount(0);
  await page.getByRole("button", { name: "查看导入记录" }).click();
  await expect(page.locator(".prompt-code")).toContainText(
    "original/EP01-S35.png",
  );
  await page.getByRole("button", { name: "关闭预览" }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(() => document.body.scrollWidth),
  ).toBeLessThanOrEqual(390);
});
