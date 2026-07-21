#!/usr/bin/env node
// 逐帧截图版 orbit：驱动交互相机做 360° 环绕，每帧 page.screenshot() 存 PNG，
// 再用 ffmpeg 近无损(H264 CRF16)封装。为什么不用 record.mjs/orbit-record.mjs 的
// recordVideo：Playwright 的 recordVideo 是低码率 VP8，会把黑洞的丝缕/星点/光子环
// 压成马赛克(实测截图锐利、webm 糊)。截图无压缩，封装码率我们控，成片才清晰。
// Usage: node orbit-shoot.mjs --html <path> --out <mp4> [--wait 12] [--orbit 24]
//        [--wheel 0] [--width 1080] [--height 1920] [--fps 30]
import { chromium } from 'playwright';
import { mkdirSync, rmSync } from 'fs';
import { dirname, join, resolve } from 'path';
import { spawnSync } from 'child_process';

const argv = process.argv.slice(2);
const flag = (n, d) => { const i = argv.indexOf(n); return i >= 0 ? argv[i + 1] : d; };
const html = resolve(flag('--html'));
const out = resolve(flag('--out'));
const waitS = Number(flag('--wait', 12));
const orbitS = Number(flag('--orbit', 24));
const wheelTotal = Number(flag('--wheel', 0));
// --tilt：环绕全程叠加的垂直拖拽偏移(px)，用来把各家倾角调到一致(如对齐 fable 的经典倾角)。
// 正/负决定俯仰方向；0=保持入场倾角。旋转仍只走水平方位角。
const tilt = Number(flag('--tilt', 0));
// --polar：极角俯仰摆动幅度(px)。0=保持恒定倾角只水平转(看不到开合变化)；
// >0=环绕时同时上下俯仰(edge-on↔face-on 来回扫)，让黑洞各个角度都露出来。
const polarAmp = Number(flag('--polar', 0));
const vpW = Number(flag('--width', 1080));
const vpH = Number(flag('--height', 1920));
const fps = Number(flag('--fps', 30));
const SCRIPT_DIR = dirname(new URL(import.meta.url).pathname);
const FFMPEG = join(SCRIPT_DIR, 'video/node_modules/.bin/remotion');

const tmp = join(dirname(out), `shoot_${Date.now() % 1e6}`);
mkdirSync(tmp, { recursive: true });

const browser = await chromium.launch({
  args: ['--use-angle=metal', '--enable-gpu', '--ignore-gpu-blocklist'],
});
const page = await browser.newPage({ viewport: { width: vpW, height: vpH }, deviceScaleFactor: 1 });
const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });

console.log(`orbit-shoot ${html} -> ${out} (wait ${waitS}s, orbit ${orbitS}s, ${fps}fps, wheel ${wheelTotal})`);
await page.goto('file://' + html);
await page.waitForTimeout(waitS * 1000);

// 装好相机驱动 + 起手 pointerdown（canvas+window 双派发，兼容两种监听目标）
await page.evaluate(({ vpW, vpH }) => {
  const canvas = document.querySelector('canvas') || document.body;
  window.__cx = vpW / 2; window.__cy = vpH / 2;
  window.__fire = (type, x, y) => {
    const mk = () => new PointerEvent(type, {
      clientX: x, clientY: y, pointerId: 1, isPrimary: true,
      bubbles: true, cancelable: true, view: window,
    });
    canvas.dispatchEvent(mk()); window.dispatchEvent(mk());
  };
  window.__paint = () => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
}, { vpW, vpH });

// dolly 到目标尺寸（分帧注入，让模型缓动）
if (wheelTotal !== 0) {
  const steps = 24;
  for (let k = 1; k <= steps; k++) {
    await page.evaluate((dy) => window.dispatchEvent(new WheelEvent('wheel', {
      deltaY: dy, bubbles: true, cancelable: true, view: window,
    })), wheelTotal / steps);
    await page.evaluate(() => window.__paint());
  }
}

await page.evaluate(() => window.__fire('pointerdown', window.__cx, window.__cy));

const nFrames = Math.round(fps * orbitS);
const dxTotal = (2 * Math.PI) / 0.005; // 360° 方位角
const dyAmp = 0;                        // 极角摆动=0：只做水平 360° 方位角旋转，保持入场的倾角不变（不上下俯仰）
for (let i = 0; i < nFrames; i++) {
  const ot = i / (nFrames - 1);
  await page.evaluate(({ ot, dxTotal, tilt, polarAmp }) => {
    const x = window.__cx + dxTotal * ot;
    // 水平方位角旋转 + 可选极角俯仰摆动(polarAmp>0 时看到 edge-on↔face-on 各角度)
    const y = window.__cy + tilt + polarAmp * Math.sin(ot * Math.PI * 4);
    window.__fire('pointermove', x, y);
    return window.__paint();
  }, { ot, dxTotal, tilt, polarAmp });
  await page.screenshot({ path: join(tmp, `f_${String(i).padStart(5, '0')}.png`) });
  if (i % 60 === 0) console.log(`  frame ${i}/${nFrames}`);
}
await page.evaluate(() => window.__fire('pointerup', window.__cx, window.__cy));
await browser.close();

// 近无损封装：H264 CRF16（截图 PNG → mp4），清晰度不再被压
const r = spawnSync(FFMPEG, ['ffmpeg', '-y', '-loglevel', 'error',
  '-framerate', String(fps), '-i', join(tmp, 'f_%05d.png'),
  '-c:v', 'libx264', '-crf', '16', '-preset', 'medium', '-pix_fmt', 'yuv420p', out],
  { stdio: 'inherit' });
rmSync(tmp, { recursive: true, force: true });
console.log(`done. frames=${nFrames} consoleErrors=${errors.length}${errors.length ? ' first:' + errors[0].slice(0, 100) : ''} ffmpeg=${r.status}`);
