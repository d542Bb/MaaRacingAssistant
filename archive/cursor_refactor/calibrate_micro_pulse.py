#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微脉冲标定器 —— 回答两个趋近控制前置问题：

    Q1 游戏最小接受时长：推杆至少保持多久，游戏才认（光标真的动）？
    Q2 微调步长：不同「幅度 × 脉冲时长」下，光标各移动多远？是否线性？

背景：
    速度模型 speed(mag) 是用 0.15~0.35s 的"长脉冲"测的（speed=dist/T）。
    但趋近末段要靠"极短脉冲"微调，短脉冲下游戏输入轮询、起停延迟都可能
    让 dist 不再 = speed×T。本工具直接实测 幅度×脉冲时长 的位移矩阵，
    标定出「最小有效脉冲」与「最小微调步长」。

实验矩阵：
    幅度(超死区微调区) × 脉冲时长(帧数) × 重复取均值
    默认幅度 [5000,6000,8000,12000,16000]，帧数 [1,2,3,5,8,12,20]（1帧≈16.7ms）

用法：
    python cursor_refactor/calibrate_micro_pulse.py
    可选：--magnitudes "5000,8000" --frames "1,2,3,5" --repeats 5 --show
输出：
    cursor_refactor/models/micro_pulse.json + 终端表格
    （含：每组合实际脉冲时长ms/位移px/是否动/模型预测对比）
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import train_stick_speed as ts

FRAME_MS = 1000.0 / 60.0      # 1 帧 ≈ 16.7ms（游戏常见轮询基准，实际以实测为准）
MOVE_EPS = 1.0                # 位移 > 此值(px) 视为「光标动了」（抗识别抖动）
STABLE_FRAMES = 3             # 归零后等几帧再读终点（光标无惯性，应很快停）

OUT_DIR = Path(__file__).resolve().parent / "models"

DEFAULT_MAGS = [5000, 6000, 8000, 12000, 16000]
DEFAULT_FRAMES = [1, 2, 3, 5, 8, 12, 20]


def measure_pulse(tr: ts.SpeedTrainer, mag: int, T: float) -> tuple | None:
    """归中→起点→推 mag 保持 T→归零→终点，返回 (实际T_ms, dist_px)。

    实际 T 用 perf_counter 记录推杆前后（含 update 开销），因为
    极短脉冲下 sleep 误差/API 开销占比很大。
    """
    if not tr.recenter(max_iters=10):
        return None
    p0 = tr.read_pos()
    if p0 is None:
        return None
    t0 = time.perf_counter()
    tr.push(mag, ts.MAIN_DIR, T)
    t1 = time.perf_counter()
    time.sleep(STABLE_FRAMES * FRAME_MS / 1000.0)  # 等停稳
    p1 = tr.read_pos()
    if p1 is None:
        return None
    dist = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
    return (t1 - t0) * 1000.0, dist


def main():
    ap = argparse.ArgumentParser(description="微脉冲标定：最小接受时长 + 微调步长")
    ap.add_argument("--magnitudes", help='逗号分隔幅度，如 "5000,8000,12000"')
    ap.add_argument("--frames", help='逗号分隔脉冲帧数，如 "1,2,3,5"')
    ap.add_argument("--repeats", type=int, default=5, help="每组合重复次数（默认5）")
    ap.add_argument("--show", action="store_true", help="显示识别叠加窗口")
    args = ap.parse_args()

    mags = [int(m) for m in args.magnitudes.split(",")] if args.magnitudes else DEFAULT_MAGS
    frames = [int(f) for f in args.frames.split(",")] if args.frames else DEFAULT_FRAMES
    reps = args.repeats

    print(f"微脉冲标定：幅度 {mags} × 帧数 {frames}（1帧≈{FRAME_MS:.1f}ms）× 重复{reps}")
    print("先确认光标可见并归中，随后自动遍历矩阵。Ctrl+C 安全停止。")

    tr = ts.SpeedTrainer(show=args.show)
    if not tr.recenter():
        print("[错误] 初始归中失败")
        return 1
    time.sleep(0.3)

    results = {"matrix": [], "frames": frames, "magnitudes": mags, "repeats": reps,
               "resolution": [tr.w, tr.h], "captured_at": datetime.now().isoformat(timespec="seconds")}
    print("\n表格：mag \\ T(帧) | 位移px (实际T ms)")
    header = "        |" + "".join(f" {f}帧".rjust(9) for f in frames)
    print(header)
    print("-" * len(header))

    try:
        for mag in mags:
            row = [f"mag={mag:>5}"]
            for f in frames:
                T = f * FRAME_MS / 1000.0
                dists = []
                for _ in range(reps):
                    res = measure_pulse(tr, mag, T)
                    if res is not None:
                        dists.append(res)  # (Tms, dist)
                    time.sleep(0.1)
                if not dists:
                    row.append(f" {'无效':>9}")
                    continue
                avg_t = sum(d[0] for d in dists) / len(dists)
                avg_d = sum(d[1] for d in dists) / len(dists)
                moved = avg_d > MOVE_EPS
                row.append(f" {avg_d:>5.1f}({avg_t:>4.0f}ms)".rjust(10)
                           if moved else f" {avg_d:>5.1f}({avg_t:>4.0f}ms,不动)".rjust(14))
                results["matrix"].append({
                    "mag": mag, "frames": f, "T_ms": round(avg_t, 1),
                    "dist_px": round(avg_d, 2), "moved": moved,
                })
            print("".join(row))

        # 结论提取
        moved_hits = [m for m in results["matrix"] if m["moved"]]
        if moved_hits:
            min_eff_T = min(m["frames"] for m in moved_hits)   # 最早出现位移的帧数
            moved_hits.sort(key=lambda m: (m["mag"], m["frames"]))
            min_step = moved_hits[0]                            # 最低幅度×最短帧
        else:
            min_eff_T = min_step = None
        results["min_effective_frames"] = min_eff_T
        results["min_step"] = min_step
        results["model_speed_px_s"] = [tr.predict_speed(m) for m in mags] if tr.coeffs else None

        print("\n=== 结论 ===")
        if min_eff_T:
            print(f"  最小有效脉冲: {min_eff_T} 帧"
                  f"（{min_eff_T * FRAME_MS:.0f}ms 后光标开始移动）")
        else:
            print("  未找到有效脉冲（所有组合光标都不动）")
        if min_step:
            print(f"  最小微调步长: mag={min_step['mag']} × {min_step['frames']}帧"
                  f" → 位移 {min_step['dist_px']}px（T={min_step['T_ms']}ms）")

        OUT_DIR.mkdir(exist_ok=True)
        out = OUT_DIR / "micro_pulse.json"
        out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n已保存: {out}")
        return 0
    except KeyboardInterrupt:
        print("\n已手动停止，部分结果未落盘。")
        return 130
    finally:
        tr.close()


if __name__ == "__main__":
    sys.exit(main())
