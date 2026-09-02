#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""手柄光标导航 + 确认点击（后台点击执行单元）。

由 cursor_refactor/ 的探针实现（cursor_monitor 三态识别 + approach_validate 连续 P 趋近 +
train_stick_speed 速度模型）沉淀而来，供 core.clicker 的「后台(手柄)」点击方式复用，
与「前台(鼠标)」SendInput 点击同层。**运行时不再依赖 cursor_refactor 脚本目录。**

职责：
  - 识别：签名剖面法，识别游戏内白色圆盘光标（normal / interactive 两态）。
  - 导航：以「摇杆-光标速度模型 + 闭环趋近」把光标从当前位收敛到目标像素坐标。
  - 确认：导航到位后按 A 键触发点击；（意图模式）只导航不确认，由用户自己按。

底座与手柄均依赖注入：截图帧源（WgcCapture 或回环帧）与虚拟手柄由调用方提供
（复用 controller 的 _gpad / treasures 的 capture），本模块不自建，避免手柄/截图冲突。
"""
from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Callable

import numpy as np
import cv2

# core 自带的摇杆-光标速度模型（cursor_refactor 标定产物，k/deadzone/resolution）。
# GamepadClicker(model_path=None) 时默认加载；标定工具归档于 archive/cursor_refactor/。
_DEFAULT_MODEL_PATH = Path(__file__).resolve().parent / "resources" / "stick_speed_model.json"


# ======================================================================
#  光标三态签名（实测色值，容差见 *_TOL）——从 cursor_monitor 迁移
# ======================================================================
GRAY_DIFF = 18            # 三通道两两最大差 ⇐ 此值 → 中性灰像素（掩码层，宽松）
SAT_THRES = 40            # 饱和度过高的彩色排除
PURE_GRAY_TOL = 10        # 射线解析层纯灰门槛

# 三态签名（BGR 顺序读取；剖面在 RGB 空间比对则转换）。pressed 态不识别
# （按下由程序自身触发，程序知道按下时机）。
STATE_SIGNATURES = {
    "normal":     {"center": (255, 255, 255), "radius": (6, 13),   "ring": (133, 133, 133), "ring_thick": (2, 7)},
    "interactive": {"center": (192, 192, 192), "radius": (6, 12),  "ring": (250, 250, 250), "ring_thick": (2, 6)},
}
CENTER_TOL = 30      # 内盘色容差（每通道）
RING_TOL = 30        # 环色容差（每通道）
THRESH_SCORE = 0.60  # 归一化匹配分下限，低于此视为非光标

# 种子定位参数
SEED_CORE_TOL = 25
SEED_RING_TOL = 20
CORE_AREA = (80, 900)
RING_AREA = (120, 900)
CORE_MIN_CIRC = 0.45
RING_MIN_CIRC = 0.25
SEED_MERGE_DIST = 16

# 时间连续性先验
JUMP_DIST = 80.0
JUMP_MIN_SCORE = 0.85
NEAR_SCORE_REL = 0.15
MISS_STREAK_RESET = 15

# 趋近控制参数（从 approach_validate 迁移）
KP = 2.0             # P 增益(1/s)：v_des = KP·dist；dist=375px 时满速
LAG_S = 0.07         # 视觉反馈延迟估计(2帧≈67ms)，停靠提前量
STOP_MARGIN = 6.0    # 停靠额外余量 px
MICRO_MAG = 5000     # 微调幅度=最小有效幅度（硬开关死区值）
MICRO_T = 1 / 60.0   # 微调脉冲 1 帧 ≈16.7ms
TOL = 5.0            # 最终误差容差 px
MAX_P_STEPS = 200
MAX_MICRO_STEPS = 25

# 归位/安全区
SAFE_MARGIN = 120
CENTER_TOL_POS = 60
MAX_AXIS = 32767
POS_MED_FRAMES = 3


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _is_gray_px(b, g, r):
    return (abs(b - g) <= GRAY_DIFF and abs(g - r) <= GRAY_DIFF
            and abs(b - r) <= GRAY_DIFF)


def _is_pure_gray(b, g, r):
    return (abs(b - g) <= PURE_GRAY_TOL and abs(g - r) <= PURE_GRAY_TOL
            and abs(b - r) <= PURE_GRAY_TOL)


def _color_dist(c1, c2):
    return math.sqrt((c1[0] - c2[0]) ** 2 + (c1[1] - c2[1]) ** 2 + (c1[2] - c2[2]) ** 2)


def radial_profile(frame, cx, cy, n_rays=4, max_r=32):
    """沿多个方向从质心逐像素采样色值序列（遇到越界立即截断）。"""
    h, w = frame.shape[:2]
    dirs = [(-1, -1), (1, -1), (-1, 1), (1, 1)]
    prof = []
    for dx, dy in dirs:
        seq = []
        for k in range(0, max_r + 1):
            x = int(round(cx + dx * k))
            y = int(round(cy + dy * k))
            if not (0 <= x < w and 0 <= y < h):
                break
            b, g, r = int(frame[y, x, 0]), int(frame[y, x, 1]), int(frame[y, x, 2])
            seq.append((b, g, r))
        if len(seq) >= 4:
            prof.append(seq)
    return prof


def _ray_segments(seq, max_tol=40):
    if not seq:
        return []
    segments = []
    cur_start = 0
    for i in range(1, len(seq)):
        if _color_dist(seq[i], seq[i - 1]) > max_tol:
            segments.append((cur_start, i))
            cur_start = i
    segments.append((cur_start, len(seq)))
    out = []
    for s, e in segments:
        if e - s >= 2:
            seg = seq[s:e]
            avg = tuple(int(round(sum(c[k] for c in seg) / len(seg))) for k in range(3))
            out.append((avg, s, e))
    return out


def _ray_parse(seq):
    segs = _ray_segments(seq)
    if not segs:
        return None, None, None, 0
    center_color, s0, e0 = segs[0]
    if not _is_pure_gray(*center_color):
        return center_color, e0, None, 0
    center_r = e0
    for avg, s, e in segs[1:]:
        if _is_pure_gray(*avg) and _color_dist(avg, center_color) > CENTER_TOL * 0.8:
            return center_color, center_r, avg, e - s
    return center_color, center_r, None, 0


def _color_mask(frame, ref, tol):
    ref = np.array(ref, dtype=np.int16)
    diff = frame.astype(np.int16) - ref
    dist2 = (diff * diff).sum(axis=2)
    return (dist2 <= tol * tol).astype(np.uint8) * 255


def _seeds_from_mask(mask, area_lo, area_hi, min_circ):
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    seeds = []
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < area_lo or area > area_hi:
            continue
        perimeter = cv2.arcLength(cnt, True)
        if perimeter < 1e-6:
            continue
        circularity = 4 * math.pi * area / (perimeter * perimeter)
        if circularity < min_circ:
            continue
        x, y, cw, ch = cv2.boundingRect(cnt)
        seeds.append((x + cw // 2, y + ch // 2, area, circularity))
    return seeds


def _dedup_seeds(all_seeds):
    all_seeds = sorted(all_seeds, key=lambda s: s[2], reverse=True)
    kept = []
    for s in all_seeds:
        if all((s[0] - k[0]) ** 2 + (s[1] - k[1]) ** 2 > SEED_MERGE_DIST ** 2
               for k in kept):
            kept.append(s)
    return kept


def build_seeds(frame_rgb):
    R, G, B = (frame_rgb[:, :, i].astype(np.int16) for i in range(3))
    rgb = np.stack([R, G, B], axis=2)
    seeds = []
    seeds += _seeds_from_mask(_color_mask(rgb, STATE_SIGNATURES["interactive"]["center"],
                                          SEED_CORE_TOL),
                              *CORE_AREA, CORE_MIN_CIRC)
    seeds += _seeds_from_mask(_color_mask(rgb, STATE_SIGNATURES["normal"]["ring"],
                                          SEED_RING_TOL),
                              *RING_AREA, RING_MIN_CIRC)
    max_diff = np.maximum(np.maximum(np.abs(R - G), np.abs(G - B)), np.abs(B - R))
    gray_mask = (max_diff <= GRAY_DIFF).astype(np.uint8) * 255
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask = cv2.morphologyEx(gray_mask, cv2.MORPH_OPEN, kernel, iterations=1)
    seeds += _seeds_from_mask(mask, 120, 2600, 0.55)
    return _dedup_seeds(seeds)


class CursorCandidate:
    __slots__ = ("pos", "area", "radius_est", "circularity", "aspect",
                 "score", "state", "center_color", "ring_color", "ring_thick")

    def __init__(self):
        self.pos = (0, 0)
        self.area = 0.0
        self.radius_est = 0.0
        self.circularity = 0.0
        self.aspect = 1.0
        self.score = 0.0
        self.state = "reject"
        self.center_color = None
        self.ring_color = None
        self.ring_thick = 0


def _score_against_signature(cand, sig):
    if cand.center_color is None:
        return 0.0
    center = cand.center_color
    s_center = sig["center"]
    center_err = _color_dist(center, s_center)
    center_score = max(0.0, 1.0 - center_err / (CENTER_TOL * 3.0))
    r_est = cand.radius_est
    r_lo, r_hi = sig["radius"]
    if r_lo <= r_est <= r_hi:
        r_score = 1.0
    else:
        r_score = max(0.0, 1.0 - abs(r_est - (r_lo + r_hi) / 2) / max(1, (r_hi - r_lo) or 1))
    if sig["ring"] is None:
        if cand.ring_color is None or cand.ring_thick < 1:
            ring_score = 1.0
        else:
            ring_score = max(0.0, 1.0 - cand.ring_thick / 6.0)
    else:
        if cand.ring_color is None:
            ring_score = 0.2
        else:
            ring_err = _color_dist(cand.ring_color, sig["ring"])
            ring_score = max(0.0, 1.0 - ring_err / (RING_TOL * 3.0))
            if sig["ring_thick"]:
                thick_lo, thick_hi = sig["ring_thick"]
                if not (thick_lo <= cand.ring_thick <= thick_hi):
                    ring_score *= max(0.3, 1.0 - abs(cand.ring_thick - (thick_lo + thick_hi) / 2) / 6.0)
    shape_score = min(1.0, cand.circularity / 0.9)
    score = center_score * 0.45 + r_score * 0.15 + ring_score * 0.30 + shape_score * 0.10
    return float(max(0.0, min(1.0, score)))


def detect_cursor(frame_rgb):
    """识别光标，返回 (列表[CursorCandidate]，选中或None)。"""
    frame = frame_rgb  # RGB 空间
    seeds = build_seeds(frame)
    if not seeds:
        return [], None
    targets = []
    for sx, sy, s_area, s_circ in seeds:
        c = CursorCandidate()
        c.pos = (sx, sy)
        c.area = float(s_area)
        c.radius_est = math.sqrt(s_area / math.pi)
        c.circularity = s_circ
        prof = radial_profile(frame, sx, sy)
        parsed = [_ray_parse(s) for s in prof]
        valid = [p for p in parsed if p[0] is not None and _is_pure_gray(*p[0])]
        if not valid:
            c.center_color = None
            c.score = 0.0
            c.state = "reject"
            targets.append(c)
            continue
        centers = [p[0] for p in valid]
        c.center_color = tuple(int(round(sum(x[k] for x in centers) / len(centers))) for k in range(3))
        radii = sorted(p[1] for p in valid)
        c.radius_est = radii[len(radii) // 2] if len(radii) else 0
        ring_rays = [p for p in valid if p[2] is not None]
        if ring_rays:
            ring_colors = [p[2] for p in ring_rays]
            c.ring_color = tuple(int(round(sum(x[k] for x in ring_colors) / len(ring_colors))) for k in range(3))
            c.ring_thick = sorted(p[3] for p in ring_rays)[len(ring_rays) // 2]
        else:
            c.ring_color, c.ring_thick = None, 0
        best_state = "reject"
        best_score = 0.0
        for name, sig in STATE_SIGNATURES.items():
            sc = _score_against_signature(c, sig)
            if sc > best_score:
                best_state = name
                best_score = sc
        c.state = "interactive" if best_state == "interactive" else best_state
        c.score = best_score
        targets.append(c)
    targets.sort(key=lambda c: c.score, reverse=True)
    sel = select_cursor(targets, None, 0)
    return targets, sel


def select_cursor(targets, last_pos, miss_streak):
    if not targets:
        return None
    top = targets[0]
    if top.score < THRESH_SCORE:
        return None
    if (last_pos is None or miss_streak >= MISS_STREAK_RESET):
        return top
    dist = lambda c: math.hypot(c.pos[0] - last_pos[0], c.pos[1] - last_pos[1])
    near = [c for c in targets if c.score >= THRESH_SCORE and dist(c) <= JUMP_DIST]
    if near:
        best_near = max(near, key=lambda c: c.score)
        if top.score - best_near.score <= NEAR_SCORE_REL:
            return best_near
        return top
    return top if top.score >= JUMP_MIN_SCORE else None


# ======================================================================
#  手柄导航 + 确认点击执行器
# ======================================================================

class GamepadClicker:
    """手柄光标导航 + 确认点击。

    注入：
      - capture: 返回 RGB ndarray 帧的 callable，或提供 .get_latest() 的帧源。
      - gpad: 提供 .left_joystick(x,y)/.press_button/.release_button/.update 的手柄对象。
      - model_path: stick_speed_model.json 路径（速度模型 k / deadzone / resolution）。
    意图模式：intent 开关置位时只导航到位、不按 A 确认（由用户手动按下）。
    """

    def __init__(self, capture, gpad, model_path: Path | None = None,
                 resolution: tuple[int, int] | None = None):
        self._capture = capture
        self._gpad = gpad
        self._model = self._load_model(model_path)
        self.res = tuple(self._model.get("resolution", [1282, 759])) if self._model else \
            (resolution or (1280, 720))
        self.k = self._model["k"] if self._model else 0.02
        self.deadzone = self._model["deadzone"] if self._model else 4260.0
        self.last_pos: tuple | None = None
        self.miss_streak = 0

    @staticmethod
    def _load_model(model_path: Path | None):
        if model_path is None:
            model_path = _DEFAULT_MODEL_PATH  # 未指定 → 尝试 core 自带标定模型
        if not model_path.is_file():
            return None
        import json
        try:
            return json.loads(model_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 —— 模型损坏回退线性估计
            return None

    # ---------- 帧/光标 ----------

    def _frame(self) -> np.ndarray | None:
        cap = self._capture
        if callable(cap):
            return cap()
        getter = getattr(cap, "get_latest", None)
        if getter is not None:
            frame, *_ = getter()
            return frame
        return None

    def read_pos(self, n: int = POS_MED_FRAMES, timeout: float = 1.0) -> tuple | None:
        hist = []
        t0 = time.perf_counter()
        while len(hist) < n and time.perf_counter() - t0 < timeout:
            frame = self._frame()
            if frame is not None:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGRA2RGB) if frame.shape[2] == 4 else frame
                _, sel = detect_cursor(rgb)
                if sel is not None:
                    hist.append(sel.pos)
                    continue
            time.sleep(0.02)
        if not hist:
            return None
        hist.sort()
        return hist[len(hist) // 2]

    # ---------- 摇杆 ----------

    def _speed(self, mag: int) -> float:
        return self.k * mag if mag >= self.deadzone else 0.0

    def set_stick(self, mag: int, dir_xy: tuple):
        dx, dy = dir_xy
        self._gpad.left_joystick(x_value=int(round(dx * mag)),
                                 y_value=int(round(-dy * mag)))
        gpad_update(self._gpad)

    def stick_zero(self):
        self._gpad.left_joystick(x_value=0, y_value=0)
        gpad_update(self._gpad)

    @property
    def confirm_button(self):
        """当前确认按钮对象；未注入时的默认值。"""
        return self._confirm_btn

    def set_confirm_button(self, button):
        """注入确认按钮对象（如 vg.XUSB_BUTTON.XUSB_GAMEPAD_A）。"""
        self._confirm_btn = button

    def press_confirm(self, button=None, duration: float = 0.15):
        """按确认按钮触发点击（后台点击的「确认」动作）。

        button 为手柄按钮对象（如 vg.XUSB_BUTTON.XUSB_GAMEPAD_A）；为 None 时默认用 A。
        """
        try:
            from maaracing_assistant.core.vgamepad_lazy import vg
            default_btn = vg.XUSB_BUTTON.XUSB_GAMEPAD_A
        except Exception:  # noqa: BLE001 —— 无手柄驱动时确认按钮不可用
            default_btn = None
        btn = button if button is not None else (self._confirm_btn or default_btn)
        if btn is None:
            return False
        self._gpad.press_button(btn)
        gpad_update(self._gpad)
        time.sleep(duration)
        self._gpad.release_button(btn)
        gpad_update(self._gpad)
        return True

    # ---------- 归位 ----------

    def recenter(self, max_iters: int = 30, anchor: tuple | None = None) -> bool:
        anchor = tuple(anchor) if anchor else (self.res[0] // 2, self.res[1] // 2)
        for _ in range(max_iters):
            p = self.read_pos(2)
            if p is None:
                self.blind_pull(1.0, 1.0, 14000, 0.12, 3)
                time.sleep(0.1)
                continue
            dx = anchor[0] - p[0]
            dy = anchor[1] - p[1]
            dist = math.hypot(dx, dy)
            if dist <= CENTER_TOL_POS:
                return True
            ux, uy = dx / dist, dy / dist
            mag = max(6000, min(20000, int(dist * 40)))
            v_pred = max(50.0, self._speed(mag) or mag * 0.02)
            T = min(0.25, 0.5 * dist / v_pred)
            self._push(mag, (ux, uy), T)
            time.sleep(0.1)
        return False

    def blind_pull(self, dx: float, dy: float, mag: int, T: float, steps: int):
        for _ in range(steps):
            self._push(mag, (dx, dy), T)
            time.sleep(0.08)

    def _push(self, mag: int, dir_xy: tuple, T: float):
        dx, dy = dir_xy
        lx = int(round(dx * mag))
        ly = int(round(-dy * mag))
        self._gpad.left_joystick(x_value=lx, y_value=ly)
        gpad_update(self._gpad)
        time.sleep(T)
        self._gpad.left_joystick(x_value=0, y_value=0)
        gpad_update(self._gpad)

    # ---------- 趋近 ----------

    def _phase_p(self, target) -> dict:
        n_frames = 0
        mag = 0
        last_pos = None
        overshoot_flip = 0
        while n_frames < MAX_P_STEPS:
            pos = self.read_pos(1, timeout=0.2)
            if pos is None:
                time.sleep(0.02)
                continue
            n_frames += 1
            dx, dy = target[0] - pos[0], target[1] - pos[1]
            dist = math.hypot(dx, dy)
            cur_speed = self._speed(mag)
            stop_dist = cur_speed * LAG_S + STOP_MARGIN
            if dist <= stop_dist:
                break
            if last_pos is not None:
                d_before = math.hypot(last_pos[0] - target[0], last_pos[1] - target[1])
                if d_before < dist:
                    overshoot_flip += 1
            last_pos = pos
            v_des = KP * dist
            mag = int(max(self.deadzone, min(MAX_AXIS, v_des / self.k))) \
                if self.k else 0
            if dist < 1e-6:
                break
            self.set_stick(mag, (dx / dist, dy / dist))
            time.sleep(0.033)
        self.stick_zero()
        return {"p_frames": n_frames, "osc_flip": overshoot_flip}

    def _phase_micro(self, target) -> dict:
        time.sleep(0.1)
        micro_steps = 0
        while micro_steps < MAX_MICRO_STEPS:
            pos = self.read_pos(3)
            if pos is None:
                time.sleep(0.02)
                continue
            dx, dy = target[0] - pos[0], target[1] - pos[1]
            dist = math.hypot(dx, dy)
            if dist <= TOL:
                return {"micro_steps": micro_steps, "err": dist, "ok": True}
            if abs(dx) < 2.0:
                ux, uy = 0.0, (1.0 if dy > 0 else -1.0)
            elif abs(dy) < 2.0:
                ux, uy = (1.0 if dx > 0 else -1.0), 0.0
            else:
                ux, uy = dx / dist, dy / dist
            self.set_stick(MICRO_MAG, (ux, uy))
            time.sleep(MICRO_T)
            self.stick_zero()
            time.sleep(0.06)
            micro_steps += 1
        pos = self.read_pos(3)
        err = math.hypot(target[0] - pos[0], target[1] - pos[1]) if pos else float("nan")
        return {"micro_steps": micro_steps, "err": err, "ok": False}

    def approach(self, target: tuple, anchor: tuple | None = None,
                 intent: bool = False) -> dict:
        """导航到目标像素坐标。intent=True → 只导航不按 A 确认。

        target 为像素坐标（x, y）。anchor 为趋近前归位的锚点（默认屏幕中央）。
        """
        if not self.recenter(max_iters=12, anchor=anchor):
            return {"ok": False, "reason": "归中失败"}
        t0 = time.perf_counter()
        p = self._phase_p(target)
        m = self._phase_micro(target)
        dt = time.perf_counter() - t0
        ok = m.get("ok", False)
        if ok and not intent:
            self.press_confirm()
        return {"target": list(target), **p, **m, "total_s": round(dt, 2), "ok": ok}


def gpad_update(gpad):
    """手柄 update 兼容包装（XInput 物理手柄无 update 方法时静默跳过）。"""
    upd = getattr(gpad, "update", None)
    if upd is not None:
        upd()