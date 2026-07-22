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
// --zoomAmp：环绕全程叠加的正弦 dolly 幅度(wheel deltaY 峰值)，0=不缩放，
// 只做水平旋转/俯仰摆动；>0 时旋转的同时来回推拉镜头(拉远→推近→拉远…)。
const zoomAmp = Number(flag('--zoomAmp', 0));
// --zoomCycles：zoomAmp 的正弦波在整段 orbit 时长里走几个完整周期
const zoomCycles = Number(flag('--zoomCycles', 1));
// --demo：脚本化演示手势(见下方 demo 分支)，会替代 zoomAmp 的正弦缩放
const demo = argv.includes('--demo');
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

console.log(`orbit-shoot ${html} -> ${out} (wait ${waitS}s, orbit ${orbitS}s, ${fps}fps, wheel ${wheelTotal}, polar ${polarAmp}, zoomAmp ${zoomAmp}x${zoomCycles})`);
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
  // wheel 缩放监听各家绑定目标不一致(有的在 canvas/renderer.domElement 上，不在 window 上，
  // dispatch 到 window 不会冒泡进子孙元素)，所以 canvas+window 双派发，兼容两种绑定方式。
  window.__wheel = (deltaY) => {
    const mk = () => new WheelEvent('wheel', { deltaY, bubbles: true, cancelable: true, view: window });
    canvas.dispatchEvent(mk()); window.dispatchEvent(mk());
  };
  // 缩放按钮兜底：有的家(如 gpt-5.6-sol)只接按钮点击、完全没接 wheel 监听，
  // 纯 wheel 派发对它们是彻底的空操作，必须补一条按钮点击路径。
  window.__clickZoom = (dir) => {
    const sel = dir < 0
      ? '#zoomIn, #zoom-in, #zin, [id*="zoom-in" i], [id*="zoomin" i]'
      : '#zoomOut, #zoom-out, #zout, [id*="zoom-out" i], [id*="zoomout" i]';
    document.querySelector(sel)?.click();
  };
  window.__paint = () => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
}, { vpW, vpH });

// dolly 到目标尺寸（分帧注入，让模型缓动）
if (wheelTotal !== 0) {
  const steps = 24;
  for (let k = 1; k <= steps; k++) {
    await page.evaluate((dy) => window.__wheel(dy), wheelTotal / steps);
    await page.evaluate(() => window.__paint());
  }
}

await page.evaluate(() => { window.__fire('pointerdown', window.__cx, window.__cy); window.__zoomSent = 0; });

const nFrames = Math.round(fps * orbitS);
const dxTotal = (2 * Math.PI) / 0.005; // 360° 方位角

if (demo) {
  // --demo：脚本化演示手势，替代正弦缩放——固定时间点触发离散动作：
  // 5s 放大 → 10s 缩小 → 15s 快速旋转 → 20s 点开 2D(停 3s) → 23s 切回 3D。
  // 2D/3D 切换靠真实点击按钮(通用 id*="2d"/"3d" 选择器，四家命名虽不同但都符合)。
  console.log('  demo: zoom-in@5s zoom-out@10s fast-spin@15s open2D@20s(3s) back3D@23s');
  const T_ZOOM_IN = 5, T_ZOOM_OUT = 10, T_SPIN = 15, T_2D = 20, T_3D = 23;
  const ZOOM_DUR = 1.5, SPIN_DUR = 1.5, ZOOM_AMT = 220;
  const baseRate = dxTotal / orbitS; // px/s，全程缓慢环绕的速率(与非 demo 模式的总量一致)
  const spinRate = baseRate + (2 * dxTotal) / SPIN_DUR; // 15s 附近叠加两圈快速自转

  let x = vpW / 2, zoomSent = 0, clicked2d = false, clicked3d = false;
  let pointerDown = true; // 循环外已 pointerdown 起手
  const dt = 1 / fps;
  for (let i = 0; i < nFrames; i++) {
    const t = i / fps;
    const in2dWindow = t >= T_2D && t < T_3D;
    // 缩放坡道期间必须先松开拖拽指针：OrbitControls 的 onMouseWheel 有
    // `state !== STATE.NONE` 就直接 return 的门槛(state 在 pointerdown 后
    // 一直停在 ROTATE，从不清零)，边转边发 wheel 对这类实现是彻底空操作
    // (gemini-3.6-flash 实测验证)。松指针→发 wheel→缩放坡道结束再按回去。
    const zoomRamping = (t >= T_ZOOM_IN && t < T_ZOOM_IN + ZOOM_DUR) ||
      (t >= T_ZOOM_OUT && t < T_ZOOM_OUT + ZOOM_DUR);
    if (zoomRamping && pointerDown) {
      await page.evaluate(() => window.__fire('pointerup', window.__cx, window.__cy));
      pointerDown = false;
    } else if (!zoomRamping && !in2dWindow && !pointerDown) {
      await page.evaluate(() => window.__fire('pointerdown', window.__cx, window.__cy));
      pointerDown = true;
    }
    if (!in2dWindow && !zoomRamping) {
      const rate = (t >= T_SPIN && t < T_SPIN + SPIN_DUR) ? spinRate : baseRate;
      x += rate * dt;
      const y = vpH / 2 + tilt + polarAmp * Math.sin((t / orbitS) * Math.PI * 4);
      await page.evaluate(({ x, y }) => { window.__fire('pointermove', x, y); return window.__paint(); }, { x, y });
    } else {
      await page.evaluate(() => window.__paint());
    }
    // deltaY 正=滚轮下=拉远(缩小)，负=滚轮上=推近(放大)——三家(kimi-k3 的
    // dist*=1+deltaY*k、OrbitControls、gpt-5.6-sol 的 applyZoom)都遵循这个标准约定，
    // 所以"放大"目标要用负值，"缩小"回到 0(基准)。
    let zoomTarget = zoomSent;
    if (t >= T_ZOOM_IN && t < T_ZOOM_IN + ZOOM_DUR) {
      zoomTarget = -ZOOM_AMT * ((t - T_ZOOM_IN) / ZOOM_DUR);
    } else if (t >= T_ZOOM_IN + ZOOM_DUR && t < T_ZOOM_OUT) {
      zoomTarget = -ZOOM_AMT;
    } else if (t >= T_ZOOM_OUT && t < T_ZOOM_OUT + ZOOM_DUR) {
      zoomTarget = -ZOOM_AMT * (1 - (t - T_ZOOM_OUT) / ZOOM_DUR);
    } else if (t >= T_ZOOM_OUT + ZOOM_DUR) {
      zoomTarget = 0;
    }
    const d = zoomTarget - zoomSent;
    if (Math.abs(d) > 0.5) {
      await page.evaluate((d) => window.__wheel(d), d);
      zoomSent = zoomTarget;
    }
    // 按钮兜底：wheel 完全没接的家(gpt-5.6-sol)靠这条路径缩放，其余家的按钮
    // handler 落地值都会被各自的 clamp 收敛，跟 wheel 叠加不会产生明显跳变。
    if (zoomRamping && i % 4 === 0) {
      const dir = (t < T_ZOOM_OUT) ? -1 : 1;
      await page.evaluate((dir) => window.__clickZoom(dir), dir);
    }
    if (!clicked2d && t >= T_2D) {
      await page.evaluate(() => document.querySelector('button[id*="2d" i]')?.click());
      clicked2d = true;
      console.log(`  [t=${t.toFixed(1)}s] click 2D`);
    }
    if (!clicked3d && t >= T_3D) {
      await page.evaluate(() => document.querySelector('button[id*="3d" i]')?.click());
      clicked3d = true;
      console.log(`  [t=${t.toFixed(1)}s] click 3D`);
    }
    await page.screenshot({ path: join(tmp, `f_${String(i).padStart(5, '0')}.png`) });
    if (i % 60 === 0) console.log(`  frame ${i}/${nFrames}`);
  }
} else {
  for (let i = 0; i < nFrames; i++) {
    const ot = i / (nFrames - 1);
    await page.evaluate(({ ot, dxTotal, tilt, polarAmp, zoomAmp, zoomCycles }) => {
      const x = window.__cx + dxTotal * ot;
      // 水平方位角旋转 + 可选极角俯仰摆动(polarAmp>0 时看到 edge-on↔face-on 各角度)
      const y = window.__cy + tilt + polarAmp * Math.sin(ot * Math.PI * 4);
      window.__fire('pointermove', x, y);
      // 可选正弦 dolly：旋转的同时来回推拉镜头，而不是只做平面旋转
      if (zoomAmp !== 0) {
        const target = zoomAmp * Math.sin(ot * Math.PI * 2 * zoomCycles);
        const d = target - window.__zoomSent;
        if (Math.abs(d) > 0.5) {
          window.__wheel(d);
          window.__zoomSent = target;
        }
      }
      return window.__paint();
    }, { ot, dxTotal, tilt, polarAmp, zoomAmp, zoomCycles });
    await page.screenshot({ path: join(tmp, `f_${String(i).padStart(5, '0')}.png`) });
    if (i % 60 === 0) console.log(`  frame ${i}/${nFrames}`);
  }
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
