#!/usr/bin/env python3
"""为每个模型录屏挑一个好的起播帧(startFrom)，并输出联系表供人工微调。

为什么需要：模型常把「开始界面」做成黑底标题屏（点击开始/倒计时），录屏首帧
就是黑的。最终视频要求「首帧非黑、内容最丰富」，所以每块视频要跳过开头那段。
本脚本自动定位「开始界面结束、进入飞行/演示」的时刻，给出 startFrom 建议；
但「两块对比最强的一帧」仍需人眼定夺，故同时拼一张带亮度/对比度标注的联系表。

用法：
  python3 pick_startfrom.py episodes/ep08 [--fps 30] [--step 0.5] [--write video/props-ep08.json]

--write 时把建议的 startFrom 回填进 props(每个 model 按 video 文件名匹配)。
依赖 Remotion 自带 ffmpeg(video/ 下) + Pillow。
"""
import argparse
import glob
import json
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FFMPEG = ["npx", "--no-install", "--prefix", os.path.join(SCRIPT_DIR, "video"),
          "remotion", "ffmpeg"]


def duration_s(webm):
    out = subprocess.run(FFMPEG + ["-i", webm], capture_output=True, text=True).stderr
    for tok in out.split():
        pass
    import re
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", out)
    if not m:
        return 0.0
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


def frame_at(webm, t, out_png, scale=None):
    cmd = FFMPEG + ["-y", "-loglevel", "error", "-ss", f"{t}", "-i", webm, "-frames:v", "1"]
    if scale:
        cmd += ["-vf", f"scale={scale}"]
    cmd += [out_png]
    subprocess.run(cmd, capture_output=True)


def luma_stats(png):
    from PIL import Image, ImageStat
    im = Image.open(png).convert("L")
    st = ImageStat.Stat(im)
    return st.mean[0], (st.stddev[0] if st.stddev else 0.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("episode_dir")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--step", type=float, default=0.5, help="采样间隔秒")
    ap.add_argument("--write", metavar="PROPS_JSON", default=None,
                    help="把建议 startFrom 回填进该 props 文件")
    # 开始界面判据：近黑(mean<floor) 或 低对比度(std<std_floor) 视为未进入场景
    ap.add_argument("--luma-floor", type=float, default=28.0)
    ap.add_argument("--std-floor", type=float, default=22.0)
    args = ap.parse_args()

    rec = os.path.join(args.episode_dir, "recordings")
    webms = sorted(glob.glob(f"{rec}/*.webm"))
    if not webms:
        sys.exit(f"no .webm in {rec}")
    fdir = os.path.join(rec, "_pick")
    os.makedirs(fdir, exist_ok=True)

    from PIL import Image, ImageDraw
    suggestions = {}
    thumbs = []  # (model, [(t, thumbpath, mean, std)])
    for webm in webms:
        model = os.path.splitext(os.path.basename(webm))[0]
        dur = duration_s(webm)
        ts = [round(i * args.step, 2) for i in range(int((dur - 1) / args.step))]
        rows = []
        settled_t = None
        for t in ts:
            p = f"{fdir}/{model}_{t}.png"
            frame_at(webm, t, p, scale="320:180")
            mean, std = luma_stats(p)
            rows.append((t, p, mean, std))
            # 第一处「进入场景」：亮度过地板 且 对比度过地板，且不是过曝纯白(mean<245)
            if settled_t is None and mean >= args.luma_floor and std >= args.std_floor \
                    and mean < 245:
                settled_t = t
        if settled_t is None:
            settled_t = 0.0
        start_frame = int(round(settled_t * args.fps))
        suggestions[model] = start_frame
        thumbs.append((model, rows, settled_t))
        print(f"[{model}] dur={dur:.1f}s  建议 startFrom={start_frame} "
              f"(≈{settled_t:.1f}s，开始界面结束处)")

    # 联系表：每个模型一行，抽稀到 ~12 格，标注 t/mean/std，建议帧加★
    per_row = 12
    W, H = 320, 180
    sheet = Image.new("RGB", (W * per_row, H * len(thumbs)), (18, 18, 18))
    d = ImageDraw.Draw(sheet)
    for r, (model, rows, settled_t) in enumerate(thumbs):
        pick = rows[:: max(1, len(rows) // per_row)][:per_row]
        for c, (t, p, mean, std) in enumerate(pick):
            im = Image.open(p).resize((W, H))
            sheet.paste(im, (c * W, r * H))
            star = "★" if abs(t - settled_t) < args.step / 2 else ""
            d.text((c * W + 4, r * H + 4),
                   f"{model[:6]} {t}s{star}\nL{mean:.0f} C{std:.0f}", fill=(0, 255, 255))
    out = os.path.join(rec, "contact.png")
    sheet.save(out)
    print(f"联系表: {out}（★=建议帧；L=亮度 C=对比度；人工挑对比最强的一帧覆盖建议值）")

    if args.write:
        d = json.load(open(args.write))
        for m in d.get("models", []):
            key = os.path.splitext(os.path.basename(m.get("video", "")))[0]
            if key in suggestions:
                m["startFrom"] = suggestions[key]
        json.dump(d, open(args.write, "w"), ensure_ascii=False, indent=1)
        print(f"已回填 startFrom 到 {args.write}")


if __name__ == "__main__":
    main()
