import { test, expect } from '@playwright/test';
import path from 'node:path';
import { mkdirSync } from 'node:fs';

// Offline UI fixtures do not call generation providers or represent model output.
function project(full = false) {
  return {
    id: 'project_offline_autopilot', title: '离线界面示例 · 猫咪的早餐', version: 1,
    production_mode: full ? 'full' : 'manual', automation: full, automation_status: { state: full ? 'running' : 'disabled' },
    source: '一只橘猫准备早餐的温暖短视频，使用猫叫录音。', brief: '', mode: 'fantasy', language: '简体中文',
    aspect_ratio: '9:16', duration: 30, episodes_count: 1, style: '自然光，温暖治愈', created_at: 1, updated_at: 1,
    paused: false, cancelled: false, jobs: [], artifacts: [], chapters: [],
    contract: { approved: true, markdown: '# 离线界面示例，仅用于操作验证', hash: 'a'.repeat(64), revision: 1 },
    plan_approved: true, plan_revision: 1,
    plan: { assets: [{ id: 'cat', name: '橘猫', kind: 'character' }], episodes: [{ id: 'ep01', title: '猫咪的早餐', shots: [{ id: 's1', title: '准备早餐', duration: 30, asset_ids: ['cat'], dialogue: '喵', narration: '' }] }] },
    reference_readiness: { mode: 'full', assets: { cat: { required_roles: ['front', 'side', 'full_body'], shot_roles: ['front', 'side'], views: [{ role: 'front', approved: false }, { role: 'side', approved: false }, { role: 'full_body', approved: false }] } }, max_references: 9 },
    progress: [
      { id: 'requirements', name: '制作规范', done: 1, total: 1 }, { id: 'plan', name: '分集分镜', done: 1, total: 1 },
      { id: 'reference', name: '素材参考', done: 0, total: 1 }, { id: 'keyframe', name: '镜头画面', done: 0, total: 1 },
      { id: 'video', name: '动态视频', done: 0, total: 1 }, { id: 'audio', name: '配音与字幕', done: 0, total: 1 }, { id: 'render', name: '成片交付', done: 0, total: 1 },
    ],
  };
}
async function mock(page: any, p: any, calls: any[]) {
  await page.route('**/api/**', async (route: any) => {
    const pathname = new URL(route.request().url()).pathname;
    let data: any = {};
    if (pathname === '/api/templates') data = [{ id: 'fantasy', name: '猫咪与创意短视频', icon: 'moon', color: 'rose', input_hint: '写下你想拍的内容' }];
    else if (pathname === '/api/health') data = { worker: true, engine: true, gateway: true };
    else if (pathname.endsWith('/automation')) {
      calls.push(route.request().postDataJSON()); p.automation = true; p.production_mode = 'full'; p.automation_status = { state: 'running' }; data = p;
    } else if (pathname === '/api/projects' && route.request().method() === 'POST') {
      const body = route.request().postDataJSON(); calls.push(body); Object.assign(p, body, { automation: body.production_mode === 'full' }); data = p;
    } else if (pathname === '/api/projects') data = [p];
    else if (pathname === '/api/projects/' + p.id) data = p;
    await route.fulfill({ status: 200, json: data });
  });
}

test('offline full production: one project action authorizes unlimited automation and collapses detailed controls', async ({ page }) => {
  const p = project(); const calls: any[] = []; const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  await mock(page, p, calls);
  await page.goto('/#project/' + p.id);
  await page.getByRole('button', { name: '开启全自动 · 不设费用上限', exact: true }).click();
  await expect.poll(() => calls.length).toBe(1);
  expect(calls[0]).toEqual({ enabled: true, mode: 'full', unlimited_budget: true });
  await expect(page.getByText('全自动制作已开启')).toBeVisible();
  await expect(page.getByRole('region', { name: '全自动制作' })).toContainText('不需要逐步批准');
  await page.reload();
  await expect(page.locator('.pipeline-steps')).toHaveCount(0);
  await page.getByRole('button', { name: '展开方案、分镜与全部素材' }).click();
  await expect(page.locator('.pipeline-step').filter({ hasText: '配音与字幕' })).toBeVisible();
  expect(errors).toEqual([]);
});

test('offline creation: explicit full-production choice is included in the creation request', async ({ page }) => {
  const p = project(); const calls: any[] = [];
  await mock(page, p, calls);
  await page.goto('/');
  await page.getByRole('button', { name: '开启新创作' }).click();
  await page.getByLabel('项目名称').fill('离线猫咪短视频');
  await page.getByLabel('小说原文或视频需求').fill('一只橘猫做早餐，30秒，温暖自然光，全程使用真实猫叫。');
  await page.getByRole('checkbox', { name: /全自动完成/ }).check();
  await page.getByRole('button', { name: '开始全自动制作', exact: true }).click();
  await expect.poll(() => calls.length).toBe(1);
  expect(calls[0].production_mode).toBe('full'); expect(calls[0].unlimited_budget).toBe(true);
});

test('offline full-production mobile overview: readable actions, no page overflow', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const p = project(true);
  await mock(page, p, []);
  await page.goto('/#project/project_offline_autopilot');
  const overview = page.getByRole('region', { name: '全自动制作' });
  await expect(overview).toContainText('已授权不设费用上限');
  await expect(page.locator('.pipeline-steps')).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  const screenshots = path.resolve('../.local/qa-autopilot'); mkdirSync(screenshots, { recursive: true });
  await page.screenshot({ path: path.join(screenshots, 'mobile.png'), fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.screenshot({ path: path.join(screenshots, 'desktop.png'), fullPage: true });
  p.paused = true; p.automation_status = { state: 'paused' };
  await page.reload();
  await expect(overview).toContainText('制作已暂停，恢复后继续');
  p.paused = false; p.automation_status = { state: 'attention' };
  await page.reload();
  await expect(overview).toContainText('有个问题需要处理');
  await expect(overview).not.toContainText('你可以离开页面，系统会继续做完');
});
