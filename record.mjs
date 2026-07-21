#!/usr/bin/env node
// Record each model's generated index.html running in a browser.
// Usage: node record.mjs <episode_dir> [--seconds 26] [--model <name>]
// All models are recorded IN PARALLEL (one headless browser each).
// --model restricts to a single model (used by run_showdown.py to record
// each contestant as soon as its generation finishes).
import { chromium } from 'playwright';
import { existsSync, readdirSync, mkdirSync, copyFileSync, writeFileSync, rmSync } from 'fs';
import { join, resolve } from 'path';

const args = process.argv.slice(2);
const epDir = resolve(args[0] ?? 'episodes/ep01');
const flag = (name, dflt) => {
  const i = args.indexOf(name);
  return i >= 0 ? args[i + 1] : dflt;
};
const seconds = Number(flag('--seconds', 26));
const only = flag('--model', null);
const vpW = Number(flag('--width', 720));
const vpH = Number(flag('--height', 960));
// --autostart：加载后按一次 Enter/Space 顶掉「按任意键开始」类菜单。仅对需要按键才开演的
// 作品用；靠"无输入"触发的 auto-demo 别加(按键会切手动模式卡菜单)。不按住键，避免误操控。
const autostart = args.includes('--autostart');

const FAIL_HTML = (name) => `<!DOCTYPE html><html><body style="margin:0;background:#111;
display:flex;align-items:center;justify-content:center;height:100vh;font-family:monospace">
<div style="color:#ff5555;font-size:42px;text-align:center">⚠️<br>FAILED TO RUN<br>
<span style="font-size:20px;color:#888">${name} did not produce working code</span></div></body></html>`;

const recDir = join(epDir, 'recordings');
mkdirSync(recDir, { recursive: true });

async function recordOne(model) {
  let page_url;
  const htmlPath = join(epDir, `work_${model}`, 'index.html');
  if (existsSync(htmlPath)) {
    page_url = 'file://' + htmlPath;
  } else {
    const failPath = join(recDir, `fail_${model}.html`);
    writeFileSync(failPath, FAIL_HTML(model));
    page_url = 'file://' + failPath;
  }

  const tmpDir = join(recDir, `tmp_${model}`);
  // GPU rendering (ANGLE Metal)：不开 GPU 时重 shader 走 SwiftShader 软渲染，
  // 掉帧导致录屏卡顿（ep10 教训）。软渲染回退仍可用，但画质/流畅度差。
  const browser = await chromium.launch({
    args: ['--use-angle=metal', '--enable-gpu', '--ignore-gpu-blocklist'],
  });
  const ctx = await browser.newContext({
    viewport: { width: vpW, height: vpH },
    recordVideo: { dir: tmpDir, size: { width: vpW, height: vpH } },
  });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });

  console.log(`[${model}] recording ${seconds}s from ${page_url}`);
  await page.goto(page_url);
  // 默认不注入输入：auto-demo 类靠「N 秒无输入」自动开演，注入会切手动模式卡标题屏。
  // 仅当作品是「按任意键才开始」的菜单式(如 qwen 的 PRESS ANY KEY TO FLY)才用 --autostart，
  // 按一次 Enter/Space 顶掉菜单(不按住键，避免误操控)。首帧非黑优先靠出题解决，见 SKILL.md。
  if (autostart) {
    await page.waitForTimeout(800); // 等 canvas/菜单挂载
    await page.keyboard.press('Enter').catch(() => {});
    await page.keyboard.press('Space').catch(() => {});
  }
  // screenshots at 5s and 15s to detect a frozen screen + provide a poster image
  await page.waitForTimeout(5000);
  const shot1 = await page.screenshot();
  await page.waitForTimeout(10000);
  const shot2 = await page.screenshot({ path: join(recDir, `poster_${model}.png`) });
  await page.waitForTimeout(Math.max(0, (seconds - 15) * 1000));
  await ctx.close();
  await browser.close();

  const webm = readdirSync(tmpDir).find((f) => f.endsWith('.webm'));
  copyFileSync(join(tmpDir, webm), join(recDir, `${model}.webm`));
  rmSync(tmpDir, { recursive: true, force: true });

  const frozen = shot1.equals(shot2);
  console.log(`[${model}] done. consoleErrors=${errors.length} frozenScreen=${frozen}`);
  if (errors.length) console.log(`[${model}] first errors: ${errors.slice(0, 3).join(' | ')}`);
  writeFileSync(join(recDir, `report_${model}.json`), JSON.stringify({
    model, url: page_url, seconds, consoleErrors: errors.slice(0, 20), frozen,
  }, null, 1));
}

let models = readdirSync(epDir)
  .filter((d) => d.startsWith('work_'))
  .map((d) => d.replace('work_', ''));
if (only) models = models.filter((m) => m === only);
if (!models.length) {
  console.error(only ? `no work_${only} dir in ${epDir}` : `no work_* dirs in ${epDir}`);
  process.exit(1);
}

const t0 = Date.now();
await Promise.all(models.map(recordOne));
console.log(`all recordings written to ${recDir} in ${((Date.now() - t0) / 1000).toFixed(0)}s`);
