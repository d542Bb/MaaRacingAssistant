#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
摇杆-光标速度模型训练器。

目的：
    拟合「摇杆幅度 → 光标速度(px/s)」的映射曲线，供后续导航引擎精确控制光标。
    原导航用分段经验公式（navigation._move_cursor_to_target），本脚本用实测数据
    训练出准确的「幅度-速度」曲线，覆盖游戏原生摇杆全范围 0~32767。

采集协议：
    1. 自建虚拟手柄（vg.VX360Gamepad）→ 游戏光标回到左上角（已知行为）。
    2. 归中：把光标推回屏幕中央安全区（距边 >= SAFE_MARGIN，防边缘遮挡污染数据）。
    3. 对每个「幅度档位 × 重复次数」：
         a. recenter 确保起点在安全区中央；
         b. 记录起点 P0（多帧中位，抗抖动）；
         c. 推摇杆幅度 magnitude（沿指定方向）保持 T 秒；
         d. 摇杆归零，记录终点 P1（多帧中位）；
         e. 校验 P0、P1 都在安全区 → 有效样本，speed = |P1-P0| / T；否则丢弃。
    4. 低幅度档位用于死区探测（速度 ≈ 0 的起始幅度）。
    5. 主方向 = 右(+x)，另采少量下(+y)/右下(45°) 样本校验轴对称性。

模型拟合：
    speed_px_s = poly(magnitude)，一阶/二阶取残差更小者；死区取首个速度>阈值幅度。
    输出 JSON（导航可直接读用）+ 拟合图 PNG。

用法：
    python cursor_refactor/train_stick_speed.py
    可选：--magnitudes "5000,8000,..." --repeats 5 --show（显示识别叠加窗口）
退出：Ctrl+C 安全停止（摇杆归零、捕获关闭、部分结果落盘）。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cursor_monitor import detect_cursor, select_cursor

try:
    from maaracing_assistant.core.vgamepad_lazy import vg
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from maaracing_assistant.core.vgamepad_lazy import vg
from maaracing_assistant.core.wgcap import WgcCapture
from maaracing_assistant.core.window_utils import find_game_hwnd

# ======================================================================
#  训练参数
# ======================================================================

SAFE_MARGIN = 120        # 安全区边距(px)：光标距屏幕边缘至少此距离
CENTER_TOL = 60          # 归中判定：光标距屏幕中央小于此距离才算归中完成
MAX_AXIS = 32767         # vgamepad 摇杆原生最大值
DEADZONE_STEP = 4260     # 已知游戏摇杆死区 13%（主文档 §10.3），低档位从死区下测起
SPEED_EPS = 15.0         # 速度低于此(px/s)视为「不动」，用于死区判定
POS_MED_FRAMES = 3       # 起点/终点取几帧位置的中位数
T_BY_MAG = [             # (幅度上界, 脉冲时长秒)：小幅度慢、大幅度快，防出安全区
    (8000, 0.35),
    (16000, 0.25),
    (24000, 0.20),
    (MAX_AXIS + 1, 0.15),
]
V_EST_K = 0.02           # 无模型时的保守速度估计系数(px/s per axis-unit)
BUDGET_RATIO = 0.5       # 预测位移不超过安全预算的比例

# 幅度档位：死区下 1 档 + 死区上均匀覆盖 0~32767
DEFAULT_MAGNITUDES = [3000, 5000, 9000, 14000, 20000, 26000, 32000]
REPEATS = 5              # 每档位主方向重复次数
SYM_DIRS = {             # 对称性校验：方向 → 采样的档位子集（幅度绝对值）
    "down": (14000, 26000),
    "diag": (14000, 26000),
}
MAIN_DIR = (1.0, 0.0)    # 主方向：向右 +x

OUT_DIR = Path(__file__).resolve().parent / "models"


# ======================================================================
#  工具
# ======================================================================

def _median_pos(hist):
    """从位置历史中取中位数（抗单帧抖动）；偶数长度取中间两点平均。"""
    if not hist:
        return None
    xs = sorted(p[0] for p in hist)
    ys = sorted(p[1] for p in hist)
    n = len(xs)
    if n % 2 == 1:
        return (xs[n // 2], ys[n // 2])
    return ((xs[n // 2 - 1] + xs[n // 2]) / 2, (ys[n // 2 - 1] + ys[n // 2]) / 2)


def _in_safe(pos, w, h):
    x, y = pos
    return (SAFE_MARGIN <= x <= w - SAFE_MARGIN and
            SAFE_MARGIN <= y <= h - SAFE_MARGIN)


class SpeedTrainer:
    """采集 + 拟合 摇杆-光标速度模型。"""

    def __init__(self, show: bool = False):
        self.show = show
        self.hwnd = find_game_hwnd()
        if not self.hwnd:
            raise RuntimeError("未找到游戏窗口，请先启动游戏")
        self.cap = WgcCapture(self.hwnd)
        self.cap.start()
        # 取一帧确认分辨率
        frame, _, _, _ = self.cap.get_latest()
        while frame is None:
            time.sleep(0.05)
            frame, _, _, _ = self.cap.get_latest()
        self.h, self.w = frame.shape[:2]
        self.center = (self.w // 2, self.h // 2)
        print(f"画面 {self.w}x{self.h}，安全区边距 {SAFE_MARGIN}px，中央 {self.center}")

        self.pad = vg.VX360Gamepad()
        self.last_pos = None
        self.miss_streak = 0
        # 模型状态：coeffs 随采集实时更新，用于后续样本的位移预算
        self.coeffs: list[float] | None = None
        self.deadzone = 0.0

    # ---------- 光标定位 ----------

    def get_pos(self) -> tuple | None:
        frame, _, _, _ = self.cap.get_latest()
        if frame is None:
            return None
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGRA2RGB)
        targets, _ = detect_cursor(rgb)
        sel = select_cursor(targets, self.last_pos, self.miss_streak)
        if sel is not None:
            self.last_pos = sel.pos
            self.miss_streak = 0
        else:
            self.miss_streak += 1
        if self.show and frame is not None:
            overlay = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            from cursor_monitor import draw_overlay
            draw_overlay(overlay, targets, sel)
            cv2.imshow("Train", overlay)
            cv2.waitKey(1)
        return sel.pos if sel is not None else None

    def read_pos(self, n: int = POS_MED_FRAMES, timeout: float = 1.0) -> tuple | None:
        """连续取 n 帧有效位置，返回中位数；超时返回 None。"""
        hist = []
        t0 = time.perf_counter()
        while len(hist) < n and time.perf_counter() - t0 < timeout:
            p = self.get_pos()
            if p is not None:
                hist.append(p)
            else:
                time.sleep(0.02)
        return _median_pos(hist)

    # ---------- 摇杆控制 ----------

    def push(self, mag: int, dir_xy: tuple, T: float):
        """沿屏幕方向 dir_xy 推摇杆 mag 保持 T 秒，然后归零。

        dir_xy 为屏幕坐标单位向量（y 向下）；摇杆 y 轴取反（屏幕Y向下 vs 摇杆Y向上）。
        """
        dx, dy = dir_xy
        lx = int(round(dx * mag))
        ly = int(round(-dy * mag))
        self.pad.left_joystick(x_value=lx, y_value=ly)
        self.pad.update()
        time.sleep(T)
        self.pad.left_joystick(x_value=0, y_value=0)
        self.pad.update()

    def blind_pull(self, dx: float, dy: float, mag: int, T: float, steps: int):
        """不依赖识别，直接朝 (dx,dy) 屏幕方向连续推几段（起始归中用）。"""
        for _ in range(steps):
            self.push(mag, (dx, dy), T)
            time.sleep(0.08)

    # ---------- 归位 ----------

    def recenter(self, max_iters: int = 30, anchor: tuple = None) -> bool:
        """把光标收敛到目标锚点（默认屏幕中央，±CENTER_TOL）。识别不到先盲推。

        anchor 允许归位到任意安全点（如左边缘起航点）。
        """
        anchor = tuple(anchor) if anchor else self.center
        none_streak = 0
        for _ in range(max_iters):
            p = self.read_pos(2)
            if p is None:
                none_streak += 1
                if none_streak >= 2:
                    # 光标可能还在左上角出屏区，朝中央盲推
                    self.blind_pull(1.0, 1.0, 14000, 0.12, 3)
                    time.sleep(0.1)
                continue
            none_streak = 0
            dx = anchor[0] - p[0]
            dy = anchor[1] - p[1]
            dist = math.hypot(dx, dy)
            if dist <= CENTER_TOL:
                return True
            ux, uy = dx / dist, dy / dist
            mag = max(6000, min(20000, int(dist * 40)))
            # 只推预测距离的一半（保守防过头震荡），多次迭代逼近
            v_pred = max(50.0, self.predict_speed(mag))
            T = min(0.25, 0.5 * dist / v_pred)
            self.push(mag, (ux, uy), T)
            time.sleep(0.1)
        print(f"[警告] 归中失败：光标未能回到锚点 {anchor}")
        return False

    # ---------- 单样本采集 ----------

    def predict_speed(self, mag: float) -> float:
        """用当前模型预测速度；无模型时用保守线性估计。"""
        if self.coeffs is None:
            return mag * V_EST_K
        return float(np.polyval(self.coeffs, mag))

    def pulse_T(self, mag: int) -> float:
        for upper, t in T_BY_MAG:
            if mag < upper:
                return t
        return 0.15

    def measure(self, mag: int, dir_xy: tuple) -> tuple | None:
        """采一个脉冲样本，返回 (speed_px_s, p0, p1)；无效(出安全区/未识别)返回 None。"""
        if not self.recenter(max_iters=10):
            return None
        p0 = self.read_pos()
        if p0 is None or not _in_safe(p0, self.w, self.h):
            return None
        T = self.pulse_T(mag)
        # 位移预算：朝 dir 方向到安全区边缘的最大可用距离
        ux, uy = dir_xy
        if ux > 0:
            budget_x = (self.w - SAFE_MARGIN) - p0[0]
        else:
            budget_x = p0[0] - SAFE_MARGIN
        if uy > 0:
            budget_y = (self.h - SAFE_MARGIN) - p0[1]
        else:
            budget_y = p0[1] - SAFE_MARGIN
        budget = min(budget_x if ux != 0 else 1e9, budget_y if uy != 0 else 1e9)
        est_dist = self.predict_speed(mag) * T
        if est_dist > budget * BUDGET_RATIO:
            T = max(0.05, budget * BUDGET_RATIO / max(1e-6, self.predict_speed(mag)))
            T = min(T, self.pulse_T(mag))

        self.push(mag, dir_xy, T)
        time.sleep(0.05)  # 等光标停稳
        p1 = self.read_pos()
        if p1 is None or not _in_safe(p1, self.w, self.h):
            return None
        dist = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
        if T < 1e-6:
            return None
        if dist < 2.0:
            # 光标不动 → 死区样本（speed=0 有效，正是死区探测数据）
            return 0.0, p0, p1
        return dist / T, p0, p1

    # ---------- 主采集 ----------

    def collect(self, magnitudes: list[int], repeats: int) -> list[tuple]:
        """采集全部样本。返回 [(mag, speed), ...]。"""
        print("开始采集（先归中，随后按档位脉冲推杆）...")
        if not self.recenter():
            raise RuntimeError("初始归中失败，无法开始采集")
        time.sleep(0.3)

        samples: list[tuple] = []
        # 主方向：全部档位 × repeats（每档位有重试上限，防死循环）
        for mag in magnitudes:
            ok, tries = 0, 0
            max_tries = repeats * 4
            while ok < repeats and tries < max_tries:
                tries += 1
                res = self.measure(mag, MAIN_DIR)
                if res is None:
                    time.sleep(0.2)
                    continue
                speed, p0, p1 = res
                samples.append((mag, speed))
                ok += 1
                print(f"  mag={mag:>6}  #{ok}/{repeats}  speed={speed:>7.1f}px/s  "
                      f"p0={p0} p1={p1}")
                self._refit(samples)
                time.sleep(0.15)
            if ok < repeats:
                print(f"[警告] mag={mag} 仅采到 {ok}/{repeats} 个有效样本"
                      f"（尝试 {tries} 次），继续下一档位")
        # 对称性校验：下/右下 45° 各采少量代表档位
        for name, dir_xy in (("down", (0.0, 1.0)), ("diag", (math.sqrt(0.5), math.sqrt(0.5)))):
            for mag in SYM_DIRS.get(name, ()):
                res = self.measure(mag, dir_xy)
                if res is not None:
                    speed, p0, p1 = res
                    samples.append((mag, speed))
                    print(f"  [{name}] mag={mag:>6}  speed={speed:>7.1f}px/s  p0={p0} p1={p1}")
                    self._refit(samples)
                time.sleep(0.15)
        return samples

    def _refit(self, samples: list[tuple]):
        """用已有样本即时重拟合并更新模型状态（供后续样本位移预算）。"""
        if len(samples) < 3:
            return
        xs = np.array([s[0] for s in samples], dtype=float)
        ys = np.array([s[1] for s in samples], dtype=float)
        best = None
        for deg in (1, 2):
            c = np.polyfit(xs, ys, deg)
            rmse = float(np.sqrt(np.mean((np.polyval(c, xs) - ys) ** 2)))
            if best is None or rmse < best[1]:
                best = (c, rmse)
        self.coeffs = best[0].tolist()
        # 死区：首个实测速度 > SPEED_EPS 的幅度
        moving = [m for m, v in samples if v > SPEED_EPS]
        self.deadzone = float(min(moving)) if moving else 0.0

    def fit(self, samples: list[tuple]) -> dict:
        """最终拟合，返回模型 dict。"""
        xs = np.array([s[0] for s in samples], dtype=float)
        ys = np.array([s[1] for s in samples], dtype=float)
        results = []
        for deg in (1, 2):
            c = np.polyfit(xs, ys, deg)
            pred = np.polyval(c, xs)
            rmse = float(np.sqrt(np.mean((pred - ys) ** 2)))
            r2 = 1.0 - float(np.sum((ys - pred) ** 2) / max(1e-9, np.sum((ys - ys.mean()) ** 2)))
            results.append((deg, c.tolist(), rmse, r2))
        results.sort(key=lambda r: r[2])
        deg, coeffs, rmse, r2 = results[0]
        moving = [m for m, v in samples if v > SPEED_EPS]
        return {
            "model": "speed_px_s = poly(magnitude)",
            "degree": deg,
            "coeffs": coeffs,
            "rmse_px_s": rmse,
            "r2": r2,
            "deadzone": float(min(moving)) if moving else 0.0,
            "max_axis": MAX_AXIS,
            "safe_margin": SAFE_MARGIN,
            "resolution": [self.w, self.h],
            "samples": {"mag": [s[0] for s in samples], "speed": [s[1] for s in samples]},
            "captured_at": datetime.now().isoformat(timespec="seconds"),
        }

    def save_plot(self, model: dict, path: Path):
        """用 cv2 画散点+拟合曲线并保存 PNG（避免引入 matplotlib 依赖）。"""
        xs = model["samples"]["mag"]
        ys = model["samples"]["speed"]
        W, H = 900, 520
        pad_l, pad_r, pad_t, pad_b = 70, 30, 40, 60
        plot_w, plot_h = W - pad_l - pad_r, H - pad_t - pad_b
        img = np.full((H, W, 3), 245, dtype=np.uint8)
        mx, my = float(max(xs)) * 1.05, float(max(ys)) * 1.1
        def to_px(x, y):
            return int(pad_l + x / mx * plot_w), int(pad_t + plot_h - y / my * plot_h)
        # 散点
        for x, y in zip(xs, ys):
            px, py = to_px(x, y)
            cv2.circle(img, (px, py), 5, (20, 90, 220), -1, cv2.LINE_AA)
        # 拟合曲线
        c = model["coeffs"]
        for i in range(200):
            x0 = mx * i / 200
            x1 = mx * (i + 1) / 200
            y0 = float(np.polyval(c, x0))
            y1 = float(np.polyval(c, x1))
            cv2.line(img, to_px(x0, y0), to_px(x1, y1), (30, 160, 60), 2, cv2.LINE_AA)
        # 死区线
        dz = model["deadzone"]
        if dz > 0:
            px, _ = to_px(dz, 0)
            cv2.line(img, (px, pad_t), (px, pad_t + plot_h), (0, 0, 255), 1)
            cv2.putText(img, f"deadzone~{dz:.0f}", (px - 20, pad_t + 16),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1, cv2.LINE_AA)
        for xt in range(0, int(mx), 5000):
            cv2.line(img, to_px(xt, 0), to_px(xt, my * 0.02), (0, 0, 0), 1)
        cv2.putText(img, "magnitude (axis)", (pad_l, H - 18),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 1, cv2.LINE_AA)
        cv2.putText(img, "speed px/s", (16, pad_t + 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 1, cv2.LINE_AA)
        cv2.putText(img, f"fit: deg={model['degree']} rmse={model['rmse_px_s']:.1f} r2={model['r2']:.3f}",
                    (pad_l, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (20, 60, 20), 1, cv2.LINE_AA)
        cv2.imwrite(str(path), img)

    def close(self):
        try:
            self.pad.left_joystick(x_value=0, y_value=0)
            self.pad.update()
        except Exception:
            pass
        try:
            self.cap.stop()
        except Exception:
            pass
        if self.show:
            cv2.destroyAllWindows()


def main():
    ap = argparse.ArgumentParser(description="训练摇杆-光标速度模型")
    ap.add_argument("--magnitudes", help='逗号分隔的幅度档位，如 "5000,8000,12000"')
    ap.add_argument("--repeats", type=int, default=REPEATS, help=f"主方向每档重复次数（默认 {REPEATS}）")
    ap.add_argument("--show", action="store_true", help="显示识别叠加窗口")
    args = ap.parse_args()

    if args.magnitudes:
        magnitudes = [int(m.strip()) for m in args.magnitudes.split(",") if m.strip()]
    else:
        magnitudes = DEFAULT_MAGNITUDES

    OUT_DIR.mkdir(exist_ok=True)
    trainer = SpeedTrainer(show=args.show)
    print(f"已连接游戏窗口 hwnd={trainer.hwnd}，自建虚拟手柄 → 光标应回到左上角，随后自动归中。")
    print(f"幅度档位 {magnitudes}，每档 {args.repeats} 次。Ctrl+C 可安全停止。")
    try:
        samples = trainer.collect(magnitudes, args.repeats)
        if len(samples) < 3:
            print("[错误] 有效样本不足，无法拟合。请检查背景是否太复杂/光标是否可见。")
            return 1
        model = trainer.fit(samples)
        json_path = OUT_DIR / "stick_speed_model.json"
        png_path = OUT_DIR / "stick_speed_fit.png"
        json_path.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
        trainer.save_plot(model, png_path)
        print("\n=== 拟合结果 ===")
        print(f"  degree={model['degree']}  coeffs={model['coeffs']}")
        print(f"  rmse={model['rmse_px_s']:.1f}px/s  r2={model['r2']:.3f}")
        print(f"  deadzone≈{model['deadzone']:.0f}（首个速度>{SPEED_EPS:.0f}px/s 的幅度）")
        print(f"  样本数 {len(samples)}（含对称性校验）")
        print(f"  模型已保存: {json_path}")
        print(f"  拟合图:     {png_path}")
        return 0
    except KeyboardInterrupt:
        print("\n已手动停止，部分结果未落盘。")
        return 130
    finally:
        trainer.close()


if __name__ == "__main__":
    sys.exit(main())
