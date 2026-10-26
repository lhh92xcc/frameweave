import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import path from "node:path";

test("curated library separates references, pending candidates, repairs and old footage", async ({
  page,
}) => {
  const png = readFileSync(path.resolve("../tests/fixtures/reference.png"));
  const states = [
    "reference",
    "reference",
    "candidate",
    "repair",
    "test",
    "history",
  ];
  const rows = states.map((state, n) => ({
    id: `curated-${n}`,
    project_id: "curated-demo",
    project_title: "当前制作线",
    entity_id: `legacy-${n}`,
    title: `original-${n}.png`,
    kind: "keyframe",
    mime: "image/png",
    url: "/curation-fixture.png",
    review: "pending",
    archived: true,
    stale: false,
    created_at: 0,
    metadata: {
      imported: { source_path: `old/${n}.png`, group: "历史制作" },
      curation: {
        state,
        label: state,
        display_title: `selected-${n}`,
        reason: "独立用途，原状态不提升",
        episode: ["EP01", "", "EP06", "EP10", "", ""][n],
        order: n,
      },
    },
  }));
  await page.route("**/api/artifacts", (route) =>
    route.fulfill({ json: rows }),
  );
  await page.route("**/api/projects", (route) =>
    route.fulfill({
      json: [
        {
          id: "curated-demo",
          title: "当前制作线",
          artifacts: rows,
          jobs: [],
          library_curation: {
            counts: {
              reference: 2,
              candidate: 1,
              repair: 1,
              test: 1,
              history: 1,
            },
            note: "当前30集版本",
          },
          reference_catalog: ["EP01", "EP06", "EP10", "EP15"].map((id) => ({
            id,
            title: id,
            generated: id === "EP15" ? 0 : 1,
            missing_slots: id === "EP15" ? ["S01", "S02"] : [],
          })),
        },
      ],
    }),
  );
  await page.route("**/curation-fixture.png", (route) =>
    route.fulfill({ contentType: "image/png", body: png }),
  );
  await page.goto("/#assets/curated-demo");
  await expect(page.locator(".media-card")).toHaveCount(2);
  await expect(page.locator(".media-card").first()).toContainText("selected-0");
  await page.getByRole("button", { name: /^待复核候选/ }).click();
  await expect(page.locator(".media-card")).toHaveCount(1);
  await expect(page.locator(".media-card")).toContainText("selected-2");
  await page.getByLabel("按集数筛选素材").selectOption("EP10");
  await expect(page.locator(".media-card")).toHaveCount(0);
  await page.getByRole("button", { name: /^明确返修/ }).click();
  await expect(page.locator(".media-card")).toHaveCount(1);
  await expect(page.locator(".media-card")).toContainText("selected-3");
  await page.getByLabel("按集数筛选素材").selectOption("EP15");
  await expect(page.locator(".media-card")).toHaveCount(0);
  await expect(page.locator(".gallery-content")).toContainText("生成过 0 / 10");
  await page.getByLabel("按集数筛选素材").selectOption("");
  await page.getByRole("button", { name: /^历史排除/ }).click();
  await expect(page.locator(".media-card")).toHaveCount(1);
  await expect(page.locator(".media-card")).toContainText("selected-5");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(() => document.body.scrollWidth),
  ).toBeLessThanOrEqual(390);
});
