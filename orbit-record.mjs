#!/usr/bin/env node
// Re-record an EXISTING artifact while driving its interactive camera:
// after the intro cinematic lands, inject slow pointer drags to orbit the
// scene (azimuth ~360° total, polar oscillating) so lensing is shown from
// many angles. Complements record.mjs (which never injects input).
// Usage: node orbit-record.mjs --html <path> --out <webm-path> [--wait 14] [--orbit 40] [--width 1920] [--height 1080]
import { chromium } from 'playwright';
import { readdirSync, mkdirSync, copyFileSync, rmSync } from 'fs';
import { dirname, join, resolve } from 'path';

const argv = process.argv.slice(2);
const flag = (name, dflt) => {
  const i = argv.indexOf(name);
  return i >= 0 ? argv[i + 1] : dflt;
};
const html = resolve(flag('--html'));
const out = resolve(flag('--out'));
const waitS = Number(flag('--wait', 14));
const orbitS = Number(flag('--orbit', 40));
// --wheel：开拖前注入的滚轮总量（正=拉远，负=推近）。用于把不同产物的
// 黑洞视觉尺寸调到相近占比（两家 dolly 灵敏度同为 exp(deltaY*0.0012)）
const wheelTotal = Number(flag('--wheel', 0));
const vpW = Number(flag('--width', 1920));
const vpH = Number(flag('--height', 1080));

const tmpDir = join(dirname(out), `tmp_orbit_${Date.now() % 1e6}`);
mkdirSync(tmpDir, { recursive: true });

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

console.log(`orbit-record ${html} -> ${out} (wait ${waitS}s, orbit ${orbitS}s)`);
await page.goto('file://' + html);
await page.waitForTimeout(waitS * 1000);

// 相机驱动全部在页面内完成（rAF + 合成 PointerEvent/WheelEvent）：
// 走 Playwright mouse API 的话，重 shader 页面上每次协议往返高达数百 ms，
// 拖拽会被拉慢近 10 倍（ep10 教训）。合成事件坐标不受视口边界限制，
// 可一次连续拖完 360°。两家产物拖拽灵敏度均为 0.005 rad/px。
await page.evaluate(({ wheelTotal, orbitMs, vpW, vpH }) => {
  const cx = vpW / 2, cy = vpH / 2;
  const canvas = document.querySelector('canvas') || document.body;
  // 同时派发到 canvas 和 window：有的产物把 pointermove/up 绑在 canvas(renderer.domElement)、
  // 有的绑在 window。只发 window 会让「绑 canvas」的那家(如 kimi/gpt)完全不转（踩过）。
  const fire = (type, x, y) => {
    const mk = () => new PointerEvent(type, {
      clientX: x, clientY: y, pointerId: 1, isPrimary: true,
      bubbles: true, cancelable: true, view: window,
    });
    canvas.dispatchEvent(mk());
    window.dispatchEvent(mk());
  };
  const start = performance.now();
  const wheelMs = wheelTotal !== 0 ? 1200 : 0;
  let wheelSent = 0;
  let dragStarted = false;
  const dxTotal = (2 * Math.PI) / 0.005;   // 360° azimuth
  const dyAmp = 130;                        // ±0.65 rad polar oscillation
  const smooth = (x) => x * x * (3 - 2 * x);
  function tick(now) {
    const t = now - start;
    if (t < wheelMs) {
      // 先分帧注入 dolly（正=拉远负=推近），把黑洞占比调到目标大小
      const target = wheelTotal * Math.min(1, t / wheelMs);
      const d = target - wheelSent;
      if (Math.abs(d) > 1) {
        window.dispatchEvent(new WheelEvent('wheel', {
          deltaY: d, bubbles: true, cancelable: true, view: window,
        }));
        wheelSent = target;
      }
    } else {
      const ot = Math.min(1, (t - wheelMs) / orbitMs);
      if (!dragStarted) { fire('pointerdown', cx, cy); dragStarted = true; }
      // 方位角单向匀速转满一圈；极角双周期正弦往复（平视↔倾角）
      const x = cx + dxTotal * ot;
      const y = cy + dyAmp * Math.sin(ot * Math.PI * 4);
      fire('pointermove', x, y);
      if (ot >= 1) { fire('pointerup', x, y); return; }
    }
    requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
}, { wheelTotal, orbitMs: orbitS * 1000, vpW, vpH });

await page.waitForTimeout((wheelTotal !== 0 ? 1.2 : 0) * 1000 + orbitS * 1000 + 2000);
await ctx.close();
await browser.close();

const webm = readdirSync(tmpDir).find((f) => f.endsWith('.webm'));
copyFileSync(join(tmpDir, webm), out);
rmSync(tmpDir, { recursive: true, force: true });
console.log(`done. consoleErrors=${errors.length}${errors.length ? ' first: ' + errors[0].slice(0, 120) : ''}`);
