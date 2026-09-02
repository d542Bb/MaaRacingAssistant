#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
光标识别监控工具 —— 只做「全屏扫描识别」+ 实时画面预览，绝不发摇杆/按键。

用途：
    用户在真实游戏里手动操作，本工具用 WGC 实时抓取游戏窗口画面，
    全屏扫描找出所有「疑似光标圆形候选」，逐一对候选做径向剖面签名比对，
    判定为 常态 / 可交互 / 按下 三种状态之一（或剔除），并在独立窗口实时叠加显示，
    供用户目视评估识别准确度。控制操作完全由用户手工完成。

识别核心 = 签名剖面法（区别于原 navigation._find_cursor_by_shape 的轮廓+圆度法）：
    光标是纯色同心圆（内盘 + 外环），三态签名如下（1280x720 实测色值）：
        - 常态     内盘 RGB(255) 半径 ~9-11px + 中灰环 RGB(133) 厚 4-5px
        - 可交互   内盘 RGB(192) 半径 ~9px    + 亮白环 RGB(250) + 深灰描边 RGB(130)
        - 按下     内盘 RGB(100) 半径 ~15px 无环（按住 A 稳定形态）
    判断流程：
        1. 中性灰掩码（R≈G≈B）粗定位出所有候选连通域（面积/圆度初筛）
        2. 对每个候选质心做 4 方向径向剖面，量化出「内盘色 / 环色 / 尺寸」
        3. 与三态签名表做归一化打分，取最高分态（分数不足则判为「非光标」剔除）

用法：
    python cursor_refactor/cursor_monitor.py
退出：按 Esc / 关闭窗口，或 Ctrl+C。

会话录制（每次运行自动生成）：
    cursor_refactor/captures/run_YYYYmmdd_HHMMSS/
        frame_000001.jpg    # 30fps 全量原始帧（无叠加，JPEG 快速编码）
        cursor_log.jsonl    # 逐帧识别日志（JSON Lines，与帧号一一对应）
    离线分析/抽帧：python cursor_refactor/export_debug_frames.py --anomalies
"""

from __future__ import annotations

import sys
import time
import math
import json
import queue
import threading
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

try:
    from maaracing_assistant.core.wgcap import WgcCapture
    from maaracing_assistant.core.window_utils import find_game_hwnd
except ImportError:
    # 允许脚本在项目根下直跑（cursor_refactor 与 maaracing_assistant 同仓库）
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
    from maaracing_assistant.core.wgcap import WgcCapture
    from maaracing_assistant.core.window_utils import find_game_hwnd


# ======================================================================
#  三态签名规格（实测色值，容差带见下方 *_TOL）
# ======================================================================
# 中性灰判定：三通道彼此接近即视为「灰」，用于粗定位光标纯色圆盘
GRAY_DIFF = 18            # 三通道两两最大差 < 此值 → 中性灰像素（掩码层，宽松）
SAT_THRES = 40            # 饱和度过高的彩色（车漆/霓虹）排除
PURE_GRAY_TOL = 10        # 射线解析层纯灰门槛：真光标是纯中性灰（通道差≤3），
                          # 暗紫背景物（如 99,86,97 差 13）在此被硬性剔除

# 三态签名（BGR 顺序，cv2 里读取为 BGR；剖面在 RGB 空间比对则转换）
# pressed 态不识别：按下由程序自身触发（程序知道按下时机），且深灰无环
# 签名最易被暗紫背景物碰瓷（历史假阳性根源），故从签名表移除。
STATE_SIGNATURES = {
    # 名称: (内盘色, 内盘半径区间, 环色或 None, 环厚区间)
    "normal":     {"center": (255, 255, 255), "radius": (6, 13),   "ring": (133, 133, 133), "ring_thick": (2, 7)},
    "interactive":{"center": (192, 192, 192), "radius": (6, 12),   "ring": (250, 250, 250), "ring_thick": (2, 6)},
}

CENTER_TOL = 30      # 内盘色容差（每通道）
RING_TOL = 30        # 环色容差（每通道）
THRESH_SCORE = 0.60  # 归一化匹配分下限，低于此视为非光标

# ---- 会话录制参数 ----
TARGET_FPS = 30.0            # 目标处理帧率
FRAME_INTERVAL = 1.0 / TARGET_FPS
JPEG_QUALITY = 85            # 原始帧快速编码：libjpeg-turbo，720p 单帧编码 ~3-6ms


# ======================================================================
#  会话录制：30fps 全量原始帧落盘 + 逐帧 JSONL 日志（供离线按帧号抽帧）
# ======================================================================

def _cand_dict(c: "CursorCandidate"):
    """候选 → 日志字典（颜色为 RGB 顺序，与识别空间一致）。"""
    return {
        "x": c.pos[0], "y": c.pos[1],
        "area": round(c.area, 1),
        "r": round(c.radius_est, 2),
        "circ": round(c.circularity, 3),
        "asp": round(c.aspect, 3),
        "score": round(c.score, 3),
        "st": c.state,
        "c": list(c.center_color) if c.center_color is not None else None,
        "ring": list(c.ring_color) if c.ring_color is not None else None,
        "rt": c.ring_thick,
    }


class SessionRecorder:
    """一次运行的录制会话。

    目录结构：
        captures/run_YYYYmmdd_HHMMSS/
            frame_000001.jpg   # 原始帧（无叠加），seq 从 1 编号
            cursor_log.jsonl   # 每处理帧一条 JSON 记录（seq 与帧文件对应）

    帧写盘走后台线程（队列缓冲），主循环只付 ~0.5ms 的队列开销，
    保证 30fps 节拍不被磁盘抖动拖累；日志主线程直接追加（行极短）。
    """

    def __init__(self, root: Path):
        self.dir = root / ("run_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
        self.dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.dir / "cursor_log.jsonl"
        self._log = open(self.log_path, "a", encoding="utf-8")
        self._q: "queue.Queue" = queue.Queue(maxsize=96)
        self._worker = threading.Thread(target=self._loop, daemon=True, name="frame-writer")
        self._worker.start()
        self.seq = 0
        self.dropped = 0

    def _loop(self):
        while True:
            item = self._q.get()
            if item is None:
                break
            seq, bgr = item
            cv2.imwrite(str(self.dir / f"frame_{seq:06d}.jpg"), bgr,
                        [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
            self._q.task_done()

    def write(self, record: dict, frame_bgr=None) -> int:
        """写一条逐帧日志；frame_bgr 不为 None 时同步登记待保存的原始帧。

        返回分配的帧序号 seq（frame_{seq:06d}.jpg）。队列满时丢帧但
        日志 seq 保持连续，离线导出脚本按缺失文件自动跳过并警告。
        """
        self.seq += 1
        record["seq"] = self.seq
        self._log.write(json.dumps(record, ensure_ascii=False) + "\n")
        if frame_bgr is not None:
            try:
                self._q.put_nowait((self.seq, frame_bgr))
            except queue.Full:
                self.dropped += 1
        return self.seq

    def close(self):
        self._q.put(None)
        self._worker.join(timeout=10)
        self._log.flush()
        self._log.close()


# ======================================================================
#  像素工具
# ======================================================================

def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _is_gray_px(b, g, r):
    """三通道是否接近中性灰（用于粗定位掩码，宽松）。"""
    return (abs(b - g) <= GRAY_DIFF and abs(g - r) <= GRAY_DIFF
            and abs(b - r) <= GRAY_DIFF)


def _is_pure_gray(b, g, r):
    """三通道是否纯中性灰（用于射线解析验证，严格）。

    真光标盘/环为纯色（实测通道差 ≤3，JPEG 后 ≤5）；暗紫背景物
    （如 99,86,97）通道差 13+，在此被硬性剔除，防假圆上位。
    """
    return (abs(b - g) <= PURE_GRAY_TOL and abs(g - r) <= PURE_GRAY_TOL
            and abs(b - r) <= PURE_GRAY_TOL)


def _color_dist(c1, c2):
    """BGR 色彩欧氏距离。"""
    return math.sqrt((c1[0] - c2[0]) ** 2 + (c1[1] - c2[1]) ** 2 + (c1[2] - c2[2]) ** 2)


# ======================================================================
#  径向剖面分析
# ======================================================================

def radial_profile(frame, cx, cy, n_rays=4, max_r=32):
    """沿多个方向从 (cx,cy) 逐像素采样，返回每条射线的色值序列。

    返回 list[(theta, [(B,G,R), ...])]。
    遇到越界立即截断该射线。
    """
    h, w = frame.shape[:2]
    # 各方向单位向量（兼顾对角线，覆盖四象限）
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
        if len(seq) >= 4:  # 至少需要几像素才有意义
            prof.append(seq)
    return prof


def _ray_segments(seq, max_tol=40):
    """把射线按相邻像素色彩距离切成若干段。

    段边界 = 相邻两像素色差超过 max_tol（抗锯齿/边缘过渡处会切段）。
    返回 [(seg_color, start_idx, rgb_of_first), ...]，忽略 <2px 的碎片段。
    """
    if not seq:
        return []
    segments = []
    cur_start = 0
    for i in range(1, len(seq)):
        if _color_dist(seq[i], seq[i - 1]) > max_tol:
            segments.append((cur_start, i))
            cur_start = i
    segments.append((cur_start, len(seq)))
    # 聚合：取每段平均色
    out = []
    for s, e in segments:
        if e - s >= 2:
            seg = seq[s:e]
            avg = tuple(int(round(sum(c[k] for c in seg) / len(seg))) for k in range(3))
            out.append((avg, s, e))
    return out


def _ray_parse(seq):
    """解析单条射线的光标结构。返回 (盘色, 盘边界, 环色或None, 环厚)。

    以「最内第一段」为盘色；其后紧跟的灰色段为环。盘半径=第一段长度。
    """
    segs = _ray_segments(seq)
    if not segs:
        return None, None, None, 0
    # 第一段 = 内盘（质心附近）
    center_color, s0, e0 = segs[0]
    if not _is_pure_gray(*center_color):
        # 质心第一段不是纯灰 → 这不是光标候选
        return center_color, e0, None, 0
    center_r = e0
    # 其后找到第一段纯中性灰、且色异于内盘的段 = 环
    for avg, s, e in segs[1:]:
        if _is_pure_gray(*avg) and _color_dist(avg, center_color) > CENTER_TOL * 0.8:
            return center_color, center_r, avg, e - s
    return center_color, center_r, None, 0


# ======================================================================
#  种子定位：签名色掩码（不依赖背景是否为彩色，专治灰背景吞光标）
# ======================================================================

SEED_CORE_TOL = 25       # 盘色种子掩码：RGB 欧氏距离容差
SEED_RING_TOL = 20       # 环色种子掩码：RGB 欧氏距离容差
CORE_AREA = (80, 900)    # 盘色种子连通域面积窗口（内盘~254px²/按下盘~700px²）
RING_AREA = (120, 900)   # 环色种子连通域面积窗口（环~300-400px²）
CORE_MIN_CIRC = 0.45     # 盘色种子圆度下限
RING_MIN_CIRC = 0.25     # 环形连通域圆度下限（环形本身圆度偏低）
SEED_MERGE_DIST = 16     # 种子去重距离（px）


def _color_mask(frame, ref, tol):
    """生成 |RGB - ref| 欧氏距离 <= tol 的二值掩码（uint8）。"""
    ref = np.array(ref, dtype=np.int16)
    diff = frame.astype(np.int16) - ref
    dist2 = (diff * diff).sum(axis=2)
    return (dist2 <= tol * tol).astype(np.uint8) * 255


def _seeds_from_mask(mask, area_lo, area_hi, min_circ):
    """连通域 → 质心种子点列表（内置几何初筛）。"""
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
    """按 SEED_MERGE_DIST 去重（面积大者优先保留）。"""
    all_seeds = sorted(all_seeds, key=lambda s: s[2], reverse=True)
    kept = []
    for s in all_seeds:
        if all((s[0] - k[0]) ** 2 + (s[1] - k[1]) ** 2 > SEED_MERGE_DIST ** 2
               for k in kept):
            kept.append(s)
    return kept


def build_seeds(frame_rgb):
    """生成全部候选种子：三态签名色掩码 + 原中性灰掩码（保底路径）。

    背景 - 光标连通问题（白贴图/灰地面/银金属）只影响灰掩码路径；
    盘色 100/192 与环色 133 的掩码在灰背景中依然把光标切成独立区域。
    返回 [(cx, cy, area, circularity), ...]
    """
    R, G, B = (frame_rgb[:, :, i].astype(np.int16) for i in range(3))
    rgb = np.stack([R, G, B], axis=2)
    seeds = []

    # 可交互态盘色 (192,192,192)。pressed 态不识别（程序自身触发按下）。
    seeds += _seeds_from_mask(_color_mask(rgb, STATE_SIGNATURES["interactive"]["center"],
                                          SEED_CORE_TOL),
                              *CORE_AREA, CORE_MIN_CIRC)

    # 常态环色 (133,133,133)：白贴图(255)/深灰地面(~70)都不会进此掩码
    seeds += _seeds_from_mask(_color_mask(rgb, STATE_SIGNATURES["normal"]["ring"],
                                          SEED_RING_TOL),
                              *RING_AREA, RING_MIN_CIRC)

    # 原中性灰掩码路径（背景非灰时最快、最准；灰背景时此路径退化由上面兜底）
    max_diff = np.maximum(np.maximum(np.abs(R - G), np.abs(G - B)), np.abs(B - R))
    gray_mask = (max_diff <= GRAY_DIFF).astype(np.uint8) * 255
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask = cv2.morphologyEx(gray_mask, cv2.MORPH_OPEN, kernel, iterations=1)
    seeds += _seeds_from_mask(mask, 120, 2600, 0.55)

    return _dedup_seeds(seeds)


# ======================================================================
#  候选判定
# ======================================================================

class CursorCandidate:
    """单个候选：几何特征 + 剖面签名 + 三态打分。"""
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


def _score_against_signature(cand: CursorCandidate, sig: dict):
    """对候选的剖面签名与某一三态签名做归一化打分 [0,1]。

    sig: STATE_SIGNATURES 里的一项。
    """
    if cand.center_color is None:
        return 0.0
    center = cand.center_color
    s_center = sig["center"]
    # 内盘色匹配分
    center_err = _color_dist(center, s_center)
    center_score = max(0.0, 1.0 - center_err / (CENTER_TOL * 3.0))
    # 尺寸匹配分
    r_est = cand.radius_est
    r_lo, r_hi = sig["radius"]
    if r_lo <= r_est <= r_hi:
        r_score = 1.0
    else:
        r_score = max(0.0, 1.0 - abs(r_est - (r_lo + r_hi) / 2) / max(1, (r_hi - r_lo) or 1))
    # 环匹配分
    if sig["ring"] is None:
        # 按下态：期望无环，探测到弱环略加分，探到强环明显扣分
        if cand.ring_color is None or cand.ring_thick < 1:
            ring_score = 1.0
        else:
            ring_score = max(0.0, 1.0 - cand.ring_thick / 6.0)
    else:
        if cand.ring_color is None:
            ring_score = 0.2  # 缺环严重不符合
        else:
            ring_err = _color_dist(cand.ring_color, sig["ring"])
            ring_score = max(0.0, 1.0 - ring_err / (RING_TOL * 3.0))
            # 环厚匹配
            if sig["ring_thick"]:
                thick_lo, thick_hi = sig["ring_thick"]
                if thick_lo <= cand.ring_thick <= thick_hi:
                    ring_score *= 1.0
                else:
                    ring_score *= max(0.3, 1.0 - abs(cand.ring_thick - (thick_lo + thick_hi) / 2) / 6.0)
    # 几何加成（圆度）
    shape_score = min(1.0, cand.circularity / 0.9)
    # 加权
    score = center_score * 0.45 + r_score * 0.15 + ring_score * 0.30 + shape_score * 0.10
    return float(max(0.0, min(1.0, score)))


def detect_cursor(frame_rgb, debug_prof: bool = False):
    """全屏扫描识别光标三态。返回 (列表[CursorCandidate], 选中候选或None)。

    候选已按分数降序，第一个（最高分）为当前判定光标；分数不足则无判定。
    debug_prof: 为 True 时把选中的径向剖面采样点附加到候选上（供绘制）。
    """
    frame = frame_rgb  # RGB 空间，frame[:, :, 0]=R, [:, :, 1]=G, [:, :, 2]=B

    # ---- 1. 种子粗定位：签名色掩码 + 中性灰掩码（去重合并） ----
    seeds = build_seeds(frame)
    if not seeds:
        return [], None

    # ---- 2. 每个种子做径向剖面签名 ----
    targets = []
    for sx, sy, s_area, s_circ in seeds:
        c = CursorCandidate()
        c.pos = (sx, sy)
        c.area = float(s_area)
        c.radius_est = math.sqrt(s_area / math.pi)
        c.circularity = s_circ
        prof = radial_profile(frame, sx, sy)
        parsed = [_ray_parse(s) for s in prof]
        # 每条射线: (盘色, 盘半径, 环色, 环厚)。过滤最内非纯灰（质心偏出光标）的射线
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
        # 环：仅在部分有效射线中检测到，取「检测到环的射线」的环色均值 + 厚度的中位数
        ring_rays = [p for p in valid if p[2] is not None]
        if ring_rays:
            ring_colors = [p[2] for p in ring_rays]
            c.ring_color = tuple(int(round(sum(x[k] for x in ring_colors) / len(ring_colors))) for k in range(3))
            c.ring_thick = sorted(p[3] for p in ring_rays)[len(ring_rays) // 2]
        else:
            c.ring_color, c.ring_thick = None, 0

        # 三态打分
        best_state = "reject"
        best_score = 0.0
        for name, sig in STATE_SIGNATURES.items():
            sc = _score_against_signature(c, sig)
            if sc > best_score:
                best_score = sc
                best_state = name
        c.score = best_score
        c.state = best_state if best_score >= THRESH_SCORE else "reject"

        if debug_prof:
            c._prof = prof  # 供绘制
        targets.append(c)

    # 同一光标可能被多条种子路径命中：按位置去重，保留最高分
    uniq = []
    for c in sorted(targets, key=lambda x: x.score, reverse=True):
        if all((c.pos[0] - u.pos[0]) ** 2 + (c.pos[1] - u.pos[1]) ** 2 > 16 ** 2
               for u in uniq):
            uniq.append(c)
    uniq.sort(key=lambda x: x.score, reverse=True)
    selected = uniq[0] if uniq and uniq[0].score >= THRESH_SCORE else None
    return uniq, selected


# ======================================================================
#  时间连续性选择：光标巡航连续，防背景圆形结构误判上位
# ======================================================================

JUMP_DIST = 80.0        # 相邻帧位移超过此值视为「跳变」（新目标接管）
JUMP_MIN_SCORE = 0.85   # 跳变接管的最低分数：无位置连续性背书，必须高分
                        # （真光标巡航中的低分帧走近邻续跟通道，不受此限）
NEAR_SCORE_REL = 0.15   # 近邻候选相对最高分的允许落后量
MISS_STREAK_RESET = 15  # 连续丢检帧数达到此值后，位置先验失效（0.5s@30fps）


def select_cursor(targets, last_pos, miss_streak):
    """在 detect_cursor 结果上叠加时间连续性先验，返回本帧选中。

    - 上一帧位置附近(<=JUMP_DIST)存在合格候选 → 优先选近邻中最高分
      （其分数允许比全局最高分低 NEAR_SCORE_REL，连续跟踪优先）
    - 无近邻候选（真实甩动/光标新出现）→ 全局最高分需 >= JUMP_MIN_SCORE 才接管
    - 连续丢检 MISS_STREAK_RESET 帧后位置先验失效，退回全局最高分规则
    """
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
    # 无近邻 → 跳变接管需更高分
    return top if top.score >= JUMP_MIN_SCORE else None


# ======================================================================
#  绘制（覆盖在截图上的识别标注）
# ======================================================================

_STATE_COLORS = {
    "normal": (0, 255, 0),        # 绿：常态
    "interactive": (255, 0, 0),   # 蓝：可交互
    "pressed": (0, 0, 255),       # 红：按下
    "reject": (80, 80, 80),       # 灰：剔除
}
_STATE_NAMES = {
    "normal": "常态", "interactive": "可交互", "pressed": "按下", "reject": "非光标",
}


def _put(frame, text, pos, color, scale=0.5, thick=1):
    cv2.putText(frame, text, pos, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)


def draw_overlay(overlay, targets, selected):
    """在 overlay(BGR) 上绘制所有候选 + 选中光标。"""
    for c in targets:
        cx, cy = c.pos
        col = _STATE_COLORS[c.state]
        # 半径约等于面积圆半径
        r = max(8, int(math.sqrt(c.area / math.pi)))
        # 剔除的候选淡一点
        if c.state == "reject":
            cv2.circle(overlay, (cx, cy), r, col, 1, cv2.LINE_AA)
        else:
            cv2.circle(overlay, (cx, cy), r + 4, col, 2, cv2.LINE_AA)
        # 十字
        cv2.line(overlay, (cx - 10, cy), (cx + 10, cy), col, 1)
        cv2.line(overlay, (cx, cy - 10), (cx, cy + 10), col, 1)
        # 参数文字（右侧偏移，避免盖住目标本身）
        tx, ty = cx + 14, cy - 12
        info = f"{_STATE_NAMES[c.state]} A={c.area:.0f} R={c.circularity:.2f} S={c.score:.2f}"
        _put(overlay, info, (tx, ty), col, 0.42, 1)
        if c.center_color is not None:
            cc = tuple(int(v) for v in c.center_color)
            info2 = f"C{cc}"
            _put(overlay, info2, (tx, ty + 16), (200, 200, 200), 0.36, 1)

    # 选中光标：额外外圈强调
    if selected is not None:
        cx, cy = selected.pos
        col = _STATE_COLORS[selected.state]
        r = max(12, int(math.sqrt(selected.area / math.pi)) + 6)
        cv2.circle(overlay, (cx, cy), r, (255, 255, 255), 1, cv2.LINE_AA)


# ======================================================================
#  主循环
# ======================================================================

def _pace(t0: float):
    """把本帧处理节流到 TARGET_FPS 节拍（处理耗时不足时补 sleep）。"""
    remain = t0 + FRAME_INTERVAL - time.perf_counter()
    if remain > 0:
        time.sleep(remain)


def run():
    print("=== 光标识别监控工具 （只识别，不控制） ===")
    hwnd = find_game_hwnd()
    if not hwnd:
        print("[错误] 未找到游戏窗口。请先启动游戏（巅峰极速/g112/Racing Master）。")
        return 1

    cap = WgcCapture(hwnd)
    try:
        cap.start()
    except Exception as e:
        print(f"[错误] 启动捕获失败: {e}")
        return 1
    print(f"已连接游戏窗口 hwnd={hwnd}，开始实时识别。按 Esc 或关闭窗口退出。")
    print("提示：手动操作摇杆/按键，观察识别框与三态判定是否符合实际。")

    rec = SessionRecorder(Path(__file__).resolve().parent / "captures")
    print(f"会话录制中（{TARGET_FPS:.0f}fps 全量帧 + 逐帧日志）: {rec.dir}")

    win_name = "Cursor Monitor"
    cv2.namedWindow(win_name, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(win_name, 960, 540)

    fps_smooth = 0.0
    last_fid = -1
    t_start = time.perf_counter()
    last_pos, miss_streak = None, 0
    try:
        while True:
            t0 = time.perf_counter()
            frame, fid, _, age = cap.get_latest()
            if frame is None:
                time.sleep(0.005)
                continue
            if fid == last_fid:
                # WGC 帧未更新：不重复识别/记录，仅保持窗口消息泵
                cv2.waitKey(1)
                _pace(t0)
                continue
            last_fid = fid

            # WGC 默认 BGRA，转 RGB 供识别
            img_rgb = cv2.cvtColor(frame, cv2.COLOR_BGRA2RGB)
            targets, _ = detect_cursor(img_rgb)
            selected = select_cursor(targets, last_pos, miss_streak)
            if selected is not None:
                last_pos = selected.pos
                miss_streak = 0
            else:
                miss_streak += 1

            # 原始 BGR 帧供落盘，叠加绘制用副本
            frame_bgr = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR)
            overlay = frame_bgr.copy()
            draw_overlay(overlay, targets, selected)

            dt = time.perf_counter() - t0  # 纯处理耗时（写入日志 proc_ms）
            state_txt = _STATE_NAMES[selected.state] if selected else "未找到"
            info = (f"FPS {fps_smooth:.0f}  frame#{fid}  seq#{rec.seq + 1}  "
                    f"age {age:.0f}ms  > {state_txt}")
            _put(overlay, info, (10, 24), (0, 255, 255), 0.5, 1)

            # 逐帧日志（30fps 全量；seq 与 frame_XXXXXX.jpg 一一对应）
            rec.write({
                "fid": int(fid),
                "t": round(time.perf_counter() - t_start, 4),
                "proc_ms": round(dt * 1000.0, 2),
                "n": len(targets),
                "sel": 0 if selected is not None else -1,
                "state": selected.state if selected is not None else "none",
                "cands": [_cand_dict(c) for c in targets],
            }, frame_bgr)

            cv2.imshow(win_name, overlay)
            k = cv2.waitKey(1) & 0xFF
            if k == 27:  # Esc
                break
            _pace(t0)
            # 实际输出帧率（含节流与重复帧等待），平滑显示
            fps_smooth = fps_smooth * 0.9 + (1.0 / max(1e-6, time.perf_counter() - t0)) * 0.1
    finally:
        cv2.destroyAllWindows()
        cap.stop()
        rec.close()
        print(f"会话结束：共 {rec.seq} 帧日志，丢帧 {rec.dropped}。")
        print(f"日志: {rec.log_path}")
        print(f"帧图: {rec.dir}")
        print("已退出。")
    return 0


if __name__ == "__main__":
    sys.exit(run())