#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
趋近验证工具 —— 连续 P 控制 + 滑停微调。

目的：
    验证「摇杆-光标速度模型 + 连续闭环趋近」在真实游戏里的效果：
    平滑度、停靠精度、是否震荡。对比原导航「一推一停一截图」的分段盲试。

控制律（基于已验证结论）：
    - 速度模型 v(mag) = k·mag（mag≥deadzone=5000），位移 = speed×T 全范围成立
    - 连续 P 控制：每帧 v_des = KP·dist → mag = clamp(v_des/k, deadzone, 32767)
    - 停靠提前量：dist ≤ 当前速度×反馈延迟 + 余量 → 滑停（光标无惯性，归零立即停）
    - 末段微调：5000×1帧 ≈ 2.2px/步（最小微调步长），收敛到 TOL=5px

用法：
    python cursor_refactor/approach_validate.py          # 5 个随机安全区目标
    python cursor_refactor/approach_validate.py --show   # 显示识别叠加+轨迹窗口
    --target x,y   指定单一目标  --kp 2.0  --count 5 等参数可调
输出：
    每次趋近：P阶段帧数 / 微调步数 / 总时长 / 最终误差 / 是否震荡
    轨迹记录 models/approach_trace.json
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import train_stick_speed as ts
from cursor_monitor import _put

OUT_DIR = Path(__file__).resolve().parent / "models"
MODEL_PATH = OUT_DIR / "stick_speed_model.json"

# ---- 控制参数 ----
KP = 2.0             # P 增益(1/s)：v_des = KP·dist；dist=375px 时满速
LAG_S = 0.07         # 视觉反馈延迟估计(2帧≈67ms)，停靠提前量
STOP_MARGIN = 6.0    # 停靠额外余量 px
MICRO_MAG = 5000     # 微调幅度=最小有效幅度（硬开关死区值）
MICRO_T = 1 / 60.0   # 微调脉冲 1 帧 ≈16.7ms → ≈2.2px
TOL = 5.0            # 最终误差容差 px（>微调步长2.2，留余量）
MAX_P_STEPS = 200    # P 阶段最大帧数
MAX_MICRO_STEPS = 25 # 微调最大步数
MIN_TARGET_DIST = 60 # 随机目标与光标最小距离 px
SAFE_TARGET = ts.SAFE_MARGIN + 30  # 目标点距边最小距离


class Approacher:
    """基于速度模型 + 识别反馈的闭环趋近器。"""

    def __init__(self, show: bool = False):
        model = json.loads(MODEL_PATH.read_text(encoding="utf-8"))
        self.k = model["k"]
        self.deadzone = model["deadzone"]
        self.res = tuple(model.get("resolution", [1282, 759]))
        self.show = show
        self.tr = ts.SpeedTrainer(show=False)  # 底座：手柄/捕获/归中/读位置
        self.trace = []  # 轨迹：(t, x, y, mag, dist, phase)

    def speed(self, mag: int) -> float:
        return self.k * mag if mag >= self.deadzone else 0.0

    def set_stick(self, mag: int, dir_xy: tuple):
        dx, dy = dir_xy
        self.tr.pad.left_joystick(x_value=int(round(dx * mag)),
                                  y_value=int(round(-dy * mag)))
        self.tr.pad.update()

    def stick_zero(self):
        self.tr.pad.left_joystick(x_value=0, y_value=0)
        self.tr.pad.update()

    def _draw(self, pos, target, phase_txt):
        """只画静态叠加（目标/轨迹/HUD），不重复识别（避免与主循环竞态）。"""
        if not self.show:
            return
        frame, _, _, _ = self.tr.cap.get_latest()
        if frame is None:
            return
        overlay = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
        tx, ty = target
        cv2.drawMarker(overlay, (tx, ty), (0, 0, 255), cv2.MARKER_CROSS, 24, 2)
        cv2.circle(overlay, (tx, ty), 10, (0, 0, 255), 1, cv2.LINE_AA)
        if pos is not None:
            cv2.circle(overlay, (int(pos[0]), int(pos[1])), 12, (0, 255, 0), 1, cv2.LINE_AA)
        if len(self.trace) >= 2:
            pts = np.array([(p[1], p[2]) for p in self.trace], dtype=np.int32)
            cv2.polylines(overlay, [pts], False, (0, 255, 255), 1, cv2.LINE_AA)
        _put(overlay, phase_txt, (10, 46), (0, 220, 255), 0.5, 1)
        cv2.imshow("Approach", overlay)
        cv2.waitKey(1)

    # ---------- P 阶段 ----------

    def _phase_p(self, target) -> dict:
        """连续 P 控制：推杆保持，每帧更新幅度/方向，到停靠窗口滑停。"""
        n_frames = 0
        mag = 0
        last_pos = None
        overshoot_flip = 0
        while n_frames < MAX_P_STEPS:
            pos = self.tr.read_pos(1, timeout=0.2)
            if pos is None:
                time.sleep(0.02)
                continue
            n_frames += 1
            dx, dy = target[0] - pos[0], target[1] - pos[1]
            dist = math.hypot(dx, dy)
            cur_speed = self.speed(mag)
            stop_dist = cur_speed * LAG_S + STOP_MARGIN
            self.trace.append((time.perf_counter(), pos[0], pos[1], mag,
                               dist, "p"))
            self._draw(pos, target, f"P 步{n_frames} dist={dist:.0f} mag={mag}")

            # 停靠窗口：到达速度×延迟+余量 → 滑停
            if dist <= stop_dist:
                break
            # 震荡检测：沿目标方向位移方向翻转
            if last_pos is not None:
                d_before = math.hypot(last_pos[0] - target[0],
                                      last_pos[1] - target[1])
                if d_before < dist:
                    overshoot_flip += 1
            last_pos = pos

            v_des = KP * dist
            mag = int(max(self.deadzone, min(32767, v_des / self.k)))
            if dist < 1e-6:
                break
            self.set_stick(mag, (dx / dist, dy / dist))
            time.sleep(0.033)  # 一个视觉反馈周期
        self.stick_zero()
        return {"p_frames": n_frames, "osc_flip": overshoot_flip}

    # ---------- 末段滑停 + 微调 ----------

    def _phase_micro(self, target) -> dict:
        """归零滑停后读误差，5000×1帧 微调收敛到 TOL。"""
        time.sleep(0.1)  # 等停稳
        # 滑停后第一眼真实误差（不截断，反映 P 阶段末态精度）
        first_pos = self.tr.read_pos(3)
        first_err = math.hypot(target[0] - first_pos[0], target[1] - first_pos[1]) \
            if first_pos else float("nan")
        micro_steps = 0
        while micro_steps < MAX_MICRO_STEPS:
            pos = self.tr.read_pos(3)
            if pos is None:
                time.sleep(0.02)
                continue
            dx, dy = target[0] - pos[0], target[1] - pos[1]
            dist = math.hypot(dx, dy)
            self.trace.append((time.perf_counter(), pos[0], pos[1], MICRO_MAG,
                               dist, "m"))
            self._draw(pos, target, f"M 步{micro_steps} err={dist:.1f}")
            if dist <= TOL:
                return {"micro_steps": micro_steps, "err": dist, "ok": True,
                        "first_err": first_err}
            # 单轴优先：误差小的一轴不推
            if abs(dx) < 2.0:
                ux, uy = 0.0, (1.0 if dy > 0 else -1.0)
            elif abs(dy) < 2.0:
                ux, uy = (1.0 if dx > 0 else -1.0), 0.0
            else:
                ux, uy = dx / dist, dy / dist
            self.set_stick(MICRO_MAG, (ux, uy))
            time.sleep(MICRO_T)
            self.stick_zero()
            time.sleep(0.06)  # 归零后等稳定再读
            micro_steps += 1
        pos = self.tr.read_pos(3)
        err = math.hypot(target[0] - pos[0], target[1] - pos[1]) if pos else float("nan")
        return {"micro_steps": micro_steps, "err": err, "ok": False,
                "first_err": first_err}

    # ---------- 单次趋近 ----------

    def approach(self, target, anchor=None) -> dict:
        self.trace = []
        if not self.tr.recenter(max_iters=12, anchor=anchor):
            return {"ok": False, "reason": "归中失败"}
        t0 = time.perf_counter()
        p = self._phase_p(target)
        m = self._phase_micro(target)
        dt = time.perf_counter() - t0
        return {"target": list(target), **p, **m, "total_s": round(dt, 2),
                "ok": m.get("ok", False)}


def main():
    global KP
    ap = argparse.ArgumentParser(description="趋近验证：连续P控制+滑停微调")
    ap.add_argument("--show", action="store_true", help="显示识别叠加+轨迹窗口")
    ap.add_argument("--target", help='单一目标坐标 "x,y"')
    ap.add_argument("--count", type=int, default=5, help="随机目标个数（默认5）")
    ap.add_argument("--kp", type=float, default=KP, help="P增益（默认2.0）")
    ap.add_argument("--sweep", action="store_true",
                    help="横向巡航：归位到左边缘，逐个向右边缘目标长距离移动")
    ap.add_argument("--start_y", type=int, default=None,
                    help="--sweep 起航 Y 与目标 Y（默认屏高中央）")
    args = ap.parse_args()
    if args.kp:
        KP = args.kp

    app = Approacher(show=args.show)
    w, h = app.res
    print(f"模型 k={app.k:.6f} deadzone={app.deadzone} 分辨率{app.res}")

    if args.sweep:
        abs_start_y = args.start_y if args.start_y is not None else h // 2
        start_y = max(SAFE_TARGET, min(h - SAFE_TARGET, abs_start_y))
        # 起航锚点移到左边缘（安全区边界），目标分配到右边缘（y 略浮动）
        anchor = (SAFE_TARGET, start_y)
        targets = []
        x = w - SAFE_TARGET
        for _ in range(args.count):
            y = max(SAFE_TARGET, min(h - SAFE_TARGET, start_y + random.randint(-80, 80)))
            targets.append((x, y))
        print(f"横向巡航: 起航 {anchor} → 右缘 {x}，{len(targets)} 个目标。Ctrl+C 停止。")
    else:
        if not app.tr.recenter():
            print("[错误] 初始归中失败")
            return 1
        print(f"归中完成，开始趋近验证（KP={KP}）。Ctrl+C 安全停止。")
        anchor = None
        if args.target:
            tx, ty = (int(v) for v in args.target.split(","))
            targets = [(tx, ty)]
        else:
            w, h = app.res
            targets = []
            for _ in range(args.count):
                x = random.randint(SAFE_TARGET, w - SAFE_TARGET)
                y = random.randint(SAFE_TARGET, h - SAFE_TARGET)
                if targets and math.hypot(x - targets[-1][0], y - targets[-1][1]) < MIN_TARGET_DIST:
                    continue
                targets.append((x, y))

    results = []
    try:
        print(f"\n{'#':>2} {'目标':>12} {'P帧':>5} {'微调步':>6} {'总s':>6} "
              f"{'初误差':>7} {'终误差':>7} {'震荡':>5} 结果")
        for i, t in enumerate(targets, 1):
            r = app.approach(t, anchor=anchor)
            results.append(r)
            print(f"{i:>2} {str(t):>12} {r.get('p_frames','-'):>5} "
                  f"{r.get('micro_steps','-'):>6} {r.get('total_s','-'):>6} "
                  f"{r.get('first_err','?'):>7.1f} {r.get('err','?'):>7.1f} "
                  f"{r.get('osc_flip',0):>5}  "
                  f"{'OK' if r.get('ok') else 'FAIL:' + str(r.get('reason',''))}")
            if app.show:
                cv2.waitKey(300)
    except KeyboardInterrupt:
        print("\n已手动停止。")
    finally:
        app.stick_zero()
        app.tr.close()
        if app.show:
            cv2.destroyAllWindows()

    if results:
        ok = [r for r in results if r.get("ok")]
        print(f"\n汇总: {len(ok)}/{len(results)} 成功；"
              f"平均初误差 {np.mean([r.get('first_err',99) for r in results]):.1f}px；"
              f"平均终误差 {np.mean([r.get('err',99) for r in results]):.1f}px；"
              f"平均耗时 {np.mean([r.get('total_s',0) for r in results]):.2f}s")
        OUT_DIR.mkdir(exist_ok=True)
        (OUT_DIR / "approach_trace.json").write_text(
            json.dumps({"results": results, "params": {"kp": KP, "tol": TOL,
                        "deadzone": app.deadzone, "k": app.k,
                        "mode": "sweep" if args.sweep else "default",
                        "sweep_anchor": list(anchor) if args.sweep else None},
                        "captured_at": datetime.now().isoformat(timespec="seconds")},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已保存: {OUT_DIR / 'approach_trace.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
