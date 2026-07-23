#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MaaRacingAssistant — 调试可视化模块
每帧截图标注：探测轮廓(黄) / 入围候选(绿) / 选中光标(红) / 按钮目标

两套渲染：
  • 存盘模式（enabled）→ 全量绘制，保存到磁盘
  • PEEP 模式（peep_enabled）→ 精简绘制，仅关键逻辑

共用辅助方法 _draw_* 统一绘制逻辑，lite=True 时跳过文字标注和边缘散点。
"""

from __future__ import annotations

import threading
import cv2
import numpy as np
from pathlib import Path
from datetime import datetime


def _put_text(frame, text, pos, scale=0.5, color=(255, 255, 255), stroke=1):
    """带黑色阴影和描边的文字绘制，确保任何背景都清晰"""
    x, y = pos
    # 1px 阴影（始终有，极低成本提升可读性）
    cv2.putText(frame, text, (x + 1, y + 1), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 1)
    if stroke > 1:
        cv2.putText(frame, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), stroke * 2 + 1)
    cv2.putText(frame, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, max(1, stroke))


def _draw_dashed_rect(frame, pt1, pt2, color, thickness=1, dash_len=8):
    """画虚线矩形"""
    x1, y1 = pt1
    x2, y2 = pt2
    for i in range(x1, x2, dash_len * 2):
        x_end = min(i + dash_len, x2)
        cv2.line(frame, (i, y1), (x_end, y1), color, thickness)
        cv2.line(frame, (i, y2), (x_end, y2), color, thickness)
    for i in range(y1, y2, dash_len * 2):
        y_end = min(i + dash_len, y2)
        cv2.line(frame, (x1, i), (x1, y_end), color, thickness)
        cv2.line(frame, (x2, i), (x2, y_end), color, thickness)


def _dedup_overlapping(dets, iou_thresh=0.5):
    """同类别重叠框去重：每个重叠区域只保留最高置信度的一个，避免虚线框堆叠"""

    if not dets:
        return []
    by_class: dict[str, list] = {}
    for d in dets:
        by_class.setdefault(d["class_name"], []).append(d)

    result = []
    for cls, cls_dets in by_class.items():
        sorted_dets = sorted(cls_dets, key=lambda d: -d["confidence"])
        kept = []
        for d in sorted_dets:
            x1, y1, x2, y2 = d["box"]
            overlap = False
            for k in kept:
                kx1, ky1, kx2, ky2 = k["box"]
                ix1, iy1 = max(x1, kx1), max(y1, ky1)
                ix2, iy2 = min(x2, kx2), min(y2, ky2)
                if ix1 < ix2 and iy1 < iy2:
                    inter = (ix2 - ix1) * (iy2 - iy1)
                    union = (x2 - x1) * (y2 - y1) + (kx2 - kx1) * (ky2 - ky1) - inter
                    if union > 0 and inter / union > iou_thresh:
                        overlap = True
                        break
            if not overlap:
                kept.append(d)
        result.extend(kept)
    return result


class NavigationDebugger:
    """导航调试：每帧保存带标注的截图，用于排查光标识别问题"""

    def __init__(self, proj_dir: Path):
        self.proj_dir = proj_dir
        self.enabled = False  # GUI 控制开关 → 存盘
        self.session_dir: Path | None = None
        self.frame_count = 0

        # PEEP 实时预览（独立于存盘）
        self.peep_enabled = False
        self._peep_window = "PEEP - Live Debug View"
        self._latest_frame: np.ndarray | None = None
        self._frame_lock = threading.Lock()

    # ---------- PEEP 实时预览 ----------

    def enable_peep(self):
        """打开 PEEP 实时预览窗口（独立线程）"""
        if self.peep_enabled:
            return
        self.peep_enabled = True
        t = threading.Thread(target=self._peep_loop, daemon=True)
        t.start()
        print(f"[PEEP] 窗口已打开: {self._peep_window}")

    def disable_peep(self):
        """关闭 PEEP 预览窗口"""
        self.peep_enabled = False

    def _peep_loop(self):
        """独立线程：OpenCV 窗口循环，~30fps 刷新最新调试帧"""
        try:
            cv2.namedWindow(self._peep_window, cv2.WINDOW_NORMAL)
            cv2.resizeWindow(self._peep_window, 960, 540)
        except Exception:
            self.peep_enabled = False
            return

        while self.peep_enabled:
            frame = None
            with self._frame_lock:
                if self._latest_frame is not None:
                    frame = self._latest_frame.copy()
            if frame is not None:
                try:
                    cv2.imshow(self._peep_window, frame)
                except Exception:
                    break
            cv2.waitKey(30)
        try:
            cv2.destroyWindow(self._peep_window)
        except Exception:
            pass

    def start_session(self, label: str):
        """开始一次导航调试会话（仅 enabled 时创建目录）"""
        self.frame_count = 0
        self.session_dir = None
        if not self.enabled:
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.session_dir = self.proj_dir / "debug" / "navigate" / f"{label}_{ts}"
        self.session_dir.mkdir(parents=True, exist_ok=True)

    # ---------- 颜色常量 ----------

    _YOLO_COLORS = {
        "coin": (0, 215, 255),      # 金色
        "car": (0, 0, 220),         # 红色
        "bonus_car": (220, 0, 220), # 紫色
    }

    _REASON_COLORS = {
        "归中": (0, 165, 255), "跳板车": (220, 0, 220), "避障": (0, 0, 220),
        "金币": (0, 215, 255), "标线": (0, 200, 0), "直行": (180, 180, 180),
        "防撞": (0, 0, 255), "冷却": (100, 100, 100), "回带": (0, 165, 255),
    }

    # ---------- 场景类型判断 ----------

    @staticmethod
    def _is_racing(label: str) -> bool:
        return label.startswith("race_") if label else False

    # ==================================================================
    #  共用绘制辅助方法
    # ==================================================================

    def _draw_yolo_dets(self, frame, detections, lite=False):
        """YOLO 正式检测框（置信度过滤后）"""
        if not detections:
            return
        for det in detections:
            x1, y1, x2, y2 = det["box"]
            cls_name = det.get("class_name", "?")
            color = self._YOLO_COLORS.get(cls_name, (255, 255, 255))
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            if not lite:
                _put_text(frame, f"{cls_name} {det['confidence']:.2f}", (x1, y1 - 5), 0.4, color)

    def _draw_raw_dets(self, frame, all_raw, detections):
        """全部原始检测框（低阈值，虚线，仅 debug 全量模式）

        去重策略：
          1. 同类别重叠框只保留最高分（_dedup_overlapping）
          2. 与实线框重叠的虚线框→隐藏（NMS 跨类压制导致高分变虚线很迷惑）
        """
        if not all_raw:
            return
        det_set = {(d["box"], d["class_name"]) for d in (detections or [])}
        deduped = _dedup_overlapping(all_raw, iou_thresh=0.5)

        # 构建实线框的边界盒列表供重叠检查
        solid_boxes = [d["box"] for d in (detections or [])]

        to_draw = []
        for det in deduped:
            if (det["box"], det["class_name"]) in det_set:
                continue
            dx1, dy1, dx2, dy2 = det["box"]
            # 检查是否与任何实线框显著重叠（IoU > 0.3，跨类也压）
            overlaps_solid = False
            for sx1, sy1, sx2, sy2 in solid_boxes:
                ix1, iy1 = max(dx1, sx1), max(dy1, sy1)
                ix2, iy2 = min(dx2, sx2), min(dy2, sy2)
                if ix1 < ix2 and iy1 < iy2:
                    inter = (ix2 - ix1) * (iy2 - iy1)
                    d_area = (dx2 - dx1) * (dy2 - dy1)
                    s_area = (sx2 - sx1) * (sy2 - sy1)
                    union = d_area + s_area - inter
                    if union > 0 and inter / union > 0.3:
                        overlaps_solid = True
                        break
            if not overlaps_solid:
                to_draw.append(det)

        for det in to_draw:
            x1, y1, x2, y2 = det["box"]
            cls_name = det.get("class_name", "?")
            conf = det.get("confidence", 0)
            color = self._YOLO_COLORS.get(cls_name, (200, 200, 200))
            _draw_dashed_rect(frame, (x1, y1), (x2, y2), color, 1)
            _put_text(frame, f"{cls_name} {conf:.2f}", (x1, y1 - 3), 0.3, color)

    def _draw_lane(self, frame, lane, lite=False):
        """标线扫描区域 + 边缘散点 + 标线位置。
        lite=True 只画扫描区域框和标线，不画边缘散点和文字。
        """
        if not lane:
            return
        h, w = frame.shape[:2]
        dbg = lane.get("_debug")
        failed = lane.get("failed") or lane.get("_failed")

        # 扫描区域（青色虚线框）
        if dbg and "zone" in dbg:
            zx1, zy1, zx2, zy2 = dbg["zone"]
            _draw_dashed_rect(frame, (zx1, zy1), (zx2, zy2), (0, 200, 200), 1)
            if not lite:
                _put_text(frame, f"scan y={zy1}-{zy2}", (zx1 + 4, zy1 + 14), 0.35, (0, 200, 200))

        # 边缘散点（左=橙色，右=蓝色）— 仅 debug 全量模式
        if not lite and dbg:
            for side, color in [("left", (0, 140, 255)), ("right", (255, 140, 0))]:
                xs, ys = dbg.get(side, ([], []))
                step = max(1, len(xs) // 300)
                for i in range(0, len(xs), step):
                    cv2.circle(frame, (int(xs[i]), int(ys[i])), 1, color, -1)

        if failed:
            if not lite:
                _put_text(frame, f"LANE FAIL: {failed}", (10, 100), 0.45, (0, 0, 255))
            return

        # 画标线（新格式单边或旧格式双边兼容）
        side = lane.get("side")
        pos = lane.get("pos")
        if dbg and "zone" in dbg:
            zy1, zy2 = dbg["zone"][1], dbg["zone"][3]
        else:
            zy1, zy2 = 0, h

        ln_w = 3 if not lite else 2
        if side and pos is not None:
            cv2.line(frame, (pos, zy1), (pos, zy2), (0, 255, 255), ln_w)
            if not lite:
                mid_y = (zy1 + zy2) // 2
                label = "L" if side == "left" else "R"
                _put_text(frame, f"{label}={pos}", (
                    pos + 4 if side == "left" else pos - 56, mid_y), 0.4, (0, 255, 255))
            # 估算中线（细绿线）
            cx = lane.get("center")
            if cx is not None:
                cv2.line(frame, (cx, zy1), (cx, zy2), (0, 200, 0), 1)
                if not lite:
                    _put_text(frame, f"C={cx}", (cx + 4, (zy1 + zy2) // 2 + 16), 0.35, (0, 200, 0))
        else:
            # 旧格式兼容（left/right/center）
            lx = lane.get("left")
            rx = lane.get("right")
            cx = lane.get("center")
            if lx is not None:
                cv2.line(frame, (lx, zy1), (lx, zy2), (0, 255, 255), ln_w)
            if rx is not None:
                cv2.line(frame, (rx, zy1), (rx, zy2), (0, 255, 255), ln_w)
            if cx is not None:
                cv2.line(frame, (cx, zy1), (cx, zy2), (0, 255, 0), 1)
            if not lite:
                mid_y = (zy1 + zy2) // 2
                if lx is not None:
                    _put_text(frame, f"L={lx}", (lx + 4, mid_y), 0.4, (0, 255, 255))
                if rx is not None:
                    _put_text(frame, f"R={rx}", (rx - 60, mid_y), 0.4, (0, 255, 255))
                if cx is not None:
                    _put_text(frame, f"C={cx}", (cx + 4, mid_y + 16), 0.35, (0, 255, 0))

    def _draw_racing_zones(self, frame, zone_lines: tuple | None, horizon_locked: bool = False):
        """地平线 + 远/中/近 + 透视车道线（未锁地平线时 30% 不透明度）"""
        if not zone_lines or len(zone_lines) < 4:
            return
        horizon, far_bot, mid_bot, roi_bot = zone_lines
        h, w = frame.shape[:2]

        alpha = 0.65 if horizon_locked else 0.30
        SKY_BLUE = (210, 180, 100)  # BGR 天蓝色
        overlay = frame.copy()

        # ── 水平距离分界线 ──
        for y, label in [
            (horizon, "地平线"), (far_bot, "远/中"), (mid_bot, "中/近"),
        ]:
            cv2.line(overlay, (0, y), (w, y), SKY_BLUE, 1, cv2.LINE_AA)
            _put_text(overlay, label, (w - 65, y - 5), 0.35, SKY_BLUE)
        _put_text(overlay, "天", (6, horizon // 2 + 4), 0.45, SKY_BLUE)
        _put_text(overlay, "远", (6, (horizon + far_bot) // 2 + 4), 0.45, SKY_BLUE)
        _put_text(overlay, "中", (6, (far_bot + mid_bot) // 2 + 4), 0.45, SKY_BLUE)
        _put_text(overlay, "近", (6, (mid_bot + roi_bot) // 2 + 4), 0.45, SKY_BLUE)

        # ── 透视车道线 ──
        center_x = w // 2
        vp_left = int(center_x - w * 0.005)
        vp_right = int(center_x + w * 0.005)

        lane_pts = [
            (0.00, 0.61, vp_left),   # 左侧路缘
            (0.00, 0.75, vp_left),   # 左1/左2
            (0.22, 1.00, vp_left),   # 左2/中
            (0.78, 1.00, vp_right),  # 中/右2
            (1.00, 0.75, vp_right),  # 右2/右1
            (1.00, 0.61, vp_right),  # 右侧路缘
        ]
        for x_frac, y_frac, vp_x in lane_pts:
            mx = int(x_frac * w)
            my = int(y_frac * h)
            if my <= horizon:
                continue
            dx = mx - vp_x
            dy = my - horizon
            x_bot = vp_x + dx * (h - horizon) // dy if dy else vp_x
            cv2.line(overlay, (vp_x, horizon), (mx, my), SKY_BLUE, 1, cv2.LINE_AA)
            cv2.line(overlay, (mx, my), (x_bot, h), SKY_BLUE, 1, cv2.LINE_AA)

        cv2.addWeighted(overlay, alpha, frame, 1 - alpha, 0, frame)

    def _draw_cursor(self, frame, cursor_pos, cursor_area=0.0, cursor_score=0.0, dist=None, lite=False):
        """选中的光标位置（红色圈 + 十字）"""
        h, w = frame.shape[:2]
        if cursor_pos:
            cx, cy = cursor_pos
            r = 8 if lite else 12
            cv2.circle(frame, (cx, cy), r, (0, 0, 220), 2)
            cv2.line(frame, (cx - 6, cy), (cx + 6, cy), (0, 0, 220), 1)
            cv2.line(frame, (cx, cy - 6), (cx, cy + 6), (0, 0, 220), 1)
            if not lite:
                info = f"CURSOR({cx},{cy}) A={cursor_area:.0f} S={cursor_score:.3f}"
                if dist is not None:
                    info += f" D={dist:.0f}"
                _put_text(frame, info, (cx + 14, cy - 6), 0.4, (0, 0, 220))
        elif not lite:
            _put_text(frame, "NO CURSOR", (w // 2 - 50, 22), 0.55, (0, 0, 220), stroke=2)

    def _draw_button(self, frame, button_pos, lite=False):
        """按钮目标（蓝色圈 + 十字）"""
        if not button_pos:
            return
        bx, by = button_pos
        r = 10 if lite else 14
        cv2.circle(frame, (bx, by), r, (235, 206, 135), 2)
        cv2.line(frame, (bx - 8, by), (bx + 8, by), (235, 206, 135), 1)
        cv2.line(frame, (bx, by - 8), (bx, by + 8), (235, 206, 135), 1)
        if not lite:
            _put_text(frame, f"btn({bx},{by})", (bx + 16, by + 4), 0.4, (235, 206, 135))

    def _draw_templates(self, frame, template_rects):
        """模板匹配矩形（青色）— 仅 debug 全量模式"""
        if not template_rects:
            return
        for tr in template_rects:
            cx, cy = tr["pos"]
            tw, th = tr["size"]
            x1 = cx - tw // 2
            y1 = cy - th // 2
            cv2.rectangle(frame, (x1, y1), (x1 + tw, y1 + th), (255, 255, 0), 2)
            _put_text(frame, f"TPL {tr.get('name','')} {tr['confidence']:.2f}", (x1, y1 - 5), 0.4, (255, 255, 0))

    def _draw_nav_candidates(self, frame, candidates, all_candidates):
        """导航光标候选（黑=过滤, 绿=入围, 紫=拉黑）— 仅 debug 全量模式"""
        # 被过滤拉黑的轮廓（黑色）
        if all_candidates:
            cand_set = {c["pos"] for c in (candidates or [])}
            for c in all_candidates:
                if c["pos"] in cand_set:
                    continue
                px, py = c["pos"]
                cv2.circle(frame, (px, py), 5, (0, 0, 0), 1)
                _put_text(frame, f"A{c['area']:.0f} R{c['circularity']:.2f}", (px + 6, py - 4), 0.30, (0, 0, 0))
        # 入围候选（绿色/紫色）
        if candidates:
            for c in candidates:
                px, py = c["pos"]
                color = (255, 0, 255) if c.get("blacklisted") else (0, 200, 0)
                cv2.circle(frame, (px, py), 8, color, 1)
                _put_text(frame, f"A{c['area']:.0f} R{c['circularity']:.2f}", (px + 9, py - 4), 0.32, color)

    def _draw_racing_hud(self, frame, ri, lane=None, detections=None, all_raw=None, lite=False):
        """赛车 HUD：帧号、检测统计、决策原因、方向箭头、车道位置条"""
        if not ri:
            return
        h, w = frame.shape[:2]
        dir_val = ri.get("direction", 0)
        reason = ri.get("reason", "")
        n_coins = ri.get("n_coins", 0)
        n_cars = ri.get("n_cars", 0)
        n_bonus = ri.get("n_bonus", 0)
        fid = ri.get("frame_id", 0)

        # ── 左上：帧号 + 检测统计（合并为一行） ──
        if lite:
            _put_text(frame, f"#{fid}  coin:{n_coins} car:{n_cars} bonus:{n_bonus}",
                      (10, 24), 0.5, (255, 255, 255))
        else:
            n_raw = len(all_raw) if all_raw else 0
            n_filt = len(detections) if detections else 0
            _put_text(frame, f"#{fid}  raw:{n_raw} filt:{n_filt}  coin:{n_coins} car:{n_cars} bonus:{n_bonus}",
                      (10, 24), 0.45, (255, 255, 255))

        # ── 底部：转向条（缩短 50%） ──
        stick = ri.get("stick", dir_val * 32767) if ri else dir_val * 32767
        bar_y = h - 55
        half_bar = int(w * 0.175)  # 原来 0.35，减半
        bar_cx = w // 2
        bar_x1, bar_x2 = bar_cx - half_bar, bar_cx + half_bar
        bar_w = bar_x2 - bar_x1
        bar_cy = bar_y

        # 背景槽
        cv2.rectangle(frame, (bar_x1, bar_cy - 4), (bar_x2, bar_cy + 4), (60, 60, 60), -1)
        cv2.line(frame, (bar_cx, bar_cy - 8), (bar_cx, bar_cy + 8), (80, 80, 80), 1)

        # 摇杆位置点
        norm = max(-32768, min(32767, stick))
        pos = int(bar_x1 + (norm + 32768) / 65536 * bar_w)
        pos = max(bar_x1, min(bar_x2 - 1, pos))
        color = (0, 255, 255) if stick < -5000 else (0, 200, 0) if abs(stick) <= 5000 else (255, 200, 0)
        cv2.circle(frame, (pos, bar_cy), 6, color, -1)
        cv2.circle(frame, (pos, bar_cy), 6, (255, 255, 255), 1)

        # 数值文字
        _put_text(frame, f"{stick}", (bar_cx - 20, bar_cy + 22), 0.4, color)

        # 方向标签
        if stick < -5000:
            _put_text(frame, "←", (bar_x1 - 24, bar_cy + 6), 0.6, (0, 255, 255))
        elif stick > 5000:
            _put_text(frame, "→", (bar_x2 + 12, bar_cy + 6), 0.6, (255, 200, 0))

        # ── 底部居中：决策原因 + 详细说明（转向条上方） ──
        reason_color = self._REASON_COLORS.get(reason, (255, 255, 255))
        detail = ri.get("detail", "")
        info_y = bar_cy - 28
        (tw_r, _), _ = cv2.getTextSize(reason, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1)
        _put_text(frame, reason, (bar_cx - tw_r // 2, info_y), 0.6, reason_color)
        if detail:
            (tw_d, _), _ = cv2.getTextSize(detail, cv2.FONT_HERSHEY_SIMPLEX, 0.4, 1)
            _put_text(frame, detail, (bar_cx - tw_d // 2, info_y + 18), 0.4, (200, 200, 200))

        # ── 右下：油门深度 + 竖向条 ──
        if "throttle" in ri:
            thr = ri["throttle"]
            thr_color = (0, 255, 0) if thr >= 255 else (0, 255, 255) if thr >= 180 else (0, 0, 255)
            # 文字
            _put_text(frame, f"RT={thr}", (w - 80, h - 28), 0.4, thr_color)
            # 竖向条：高 60px，从下往上填充
            bar_x = w - 28
            bar_top = h - 90
            bar_bot = h - 30
            bar_h = bar_bot - bar_top
            cv2.rectangle(frame, (bar_x, bar_top), (bar_x + 8, bar_bot), (60, 60, 60), -1)
            fill_h = int(bar_h * thr / 255)
            if fill_h > 0:
                cv2.rectangle(frame, (bar_x, bar_bot - fill_h), (bar_x + 8, bar_bot), thr_color, -1)
            cv2.rectangle(frame, (bar_x, bar_top), (bar_x + 8, bar_bot), (120, 120, 120), 1)

        # ── 右下：alpha 平滑系数 ──
        if "steer_alpha" in ri:
            a = ri["steer_alpha"]
            _put_text(frame, f"a={a:.2f}", (w - 80, h - 100), 0.35, (160, 160, 160))

    # ==================================================================
    #  组合渲染
    # ==================================================================

    def _render_full(self, img: np.ndarray, **kw) -> np.ndarray:
        """全量标注绘制（存盘用），返回 BGR 帧"""
        frame = img.copy()
        h, w = frame.shape[:2]
        label = kw.get("label", "")
        lane = kw.get("lane")
        ri = kw.get("racing_info")

        # 距离区域分割线（画在最底层）
        if ri:
            self._draw_racing_zones(frame, ri.get("zone_lines"), ri.get("horizon_locked", False))

        # 导航场景元素
        self._draw_nav_candidates(frame, kw.get("candidates"), kw.get("all_candidates"))
        self._draw_button(frame, kw.get("button_pos"))
        self._draw_cursor(frame, kw.get("cursor_pos"), kw.get("cursor_area", 0.0),
                          kw.get("cursor_score", 0.0), kw.get("dist"))
        self._draw_templates(frame, kw.get("template_rects"))

        # 赛车场景元素
        self._draw_raw_dets(frame, kw.get("all_raw_dets"), kw.get("detections"))
        self._draw_yolo_dets(frame, kw.get("detections"))
        self._draw_lane(frame, lane)

        # 赛车 HUD（含帧号+统计，不重复绘制顶部栏）
        if ri:
            self._draw_racing_hud(frame, ri, lane, kw.get("detections"), kw.get("all_raw_dets"))
        else:
            # 非赛车场景：顶部信息栏
            info_line = f"#{self.frame_count}"
            if label:
                info_line += f" | {label}"
            _put_text(frame, info_line, (10, 22), 0.5, (255, 255, 255))
            if kw.get("cursor_score", 0) > 0:
                _put_text(frame, f"score={kw['cursor_score']:.3f}", (10, 42), 0.4, (200, 200, 200))

        return frame

    def _render_peep(self, img: np.ndarray, **kw) -> np.ndarray:
        """精简绘制（PEEP 实时预览用），返回 BGR 帧"""
        frame = img.copy()
        h, w = frame.shape[:2]
        label = kw.get("label", "")
        lane = kw.get("lane")
        ri = kw.get("racing_info")

        # 距离区域分割线（精简版）
        if ri:
            self._draw_racing_zones(frame, ri.get("zone_lines"), ri.get("horizon_locked", False))

        # YOLO 检测框（精简：无置信度文字）
        self._draw_yolo_dets(frame, kw.get("detections"), lite=True)
        # 标线（精简：无边缘散点和标注文字）
        self._draw_lane(frame, lane, lite=True)

        # 导航场景元素
        self._draw_cursor(frame, kw.get("cursor_pos"), lite=True)
        self._draw_button(frame, kw.get("button_pos"), lite=True)

        if ri:
            # 赛车 HUD（精简）
            self._draw_racing_hud(frame, ri, lane, lite=True)
        else:
            # 非赛车场景：顶部信息栏
            info_parts = [f"#{self.frame_count}"]
            if label:
                info_parts.append(label)
            _put_text(frame, " | ".join(info_parts), (10, 30), 0.75, (255, 255, 255), stroke=3)

            if kw.get("detections"):
                n_coins = sum(1 for d in kw["detections"] if d.get("class_name") == "coin")
                n_cars = sum(1 for d in kw["detections"] if d.get("class_name") == "car")
                n_bonus = sum(1 for d in kw["detections"] if d.get("class_name") == "bonus_car")
                _put_text(frame, f"coin:{n_coins}  car:{n_cars}  bonus:{n_bonus}", (10, 54), 0.45, (180, 180, 180))

            # 方向文字
            dir_text, dir_color = "", (0, 255, 0)
            if self._is_racing(label) or "d_L" in label:
                dir_text, dir_color = "<< LEFT", (0, 255, 255)
            elif "d_R" in label:
                dir_text, dir_color = "RIGHT >>", (0, 255, 255)
            elif "d_S" in label:
                dir_text, dir_color = "^ STRAIGHT", (0, 200, 0)
            elif label and "timeout" in label:
                dir_text, dir_color = "TIMEOUT", (0, 0, 220)
            if dir_text:
                (tw, _), _ = cv2.getTextSize(dir_text, cv2.FONT_HERSHEY_SIMPLEX, 1.2, 3)
                _put_text(frame, dir_text, ((w - tw) // 2, h - 30), 1.2, dir_color, stroke=3)

        return frame

    # ==================================================================
    #  统一入口
    # ==================================================================

    def save_frame(
        self,
        frame_rgb: np.ndarray,
        cursor_pos: tuple[int, int] | None = None,
        cursor_area: float = 0.0,
        cursor_score: float = 0.0,
        button_pos: tuple[int, int] | None = None,
        candidates: list[dict] | None = None,
        all_candidates: list[dict] | None = None,
        dist: float | None = None,
        label: str = "",
        template_rects: list[dict] | None = None,
        detections: list[dict] | None = None,
        lane: dict | None = None,
        save_to_disk: bool = True,
        racing_info: dict | None = None,
        all_raw_dets: list[dict] | None = None,
    ):
        """保存一帧调试截图

        save_to_disk: False 时跳过 cv2.imwrite。
        PEEP 预览帧独立于存盘帧——PEEP 使用精简绘制，存盘使用全量绘制。

        颜色约定（全量绘制）：
          🔴 红 — 选中的光标
          🟢 绿 — 入围候选（通过硬过滤，参与评分）
          🟣 紫 — 静止拉黑（连续3帧不动，被跳过评分）
          ⚫ 黑 — 被硬过滤拉黑的探测项
          🔵 亮蓝 — 按钮目标
        """
        if not self.enabled and not self.peep_enabled:
            return
        if self.session_dir is None and not self.peep_enabled:
            return

        self.frame_count += 1
        img_bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)

        kwargs = dict(
            cursor_pos=cursor_pos, cursor_area=cursor_area, cursor_score=cursor_score,
            button_pos=button_pos, candidates=candidates, all_candidates=all_candidates,
            dist=dist, label=label, template_rects=template_rects,
            detections=detections, lane=lane, racing_info=racing_info,
            all_raw_dets=all_raw_dets,
        )

        # 存盘：全量绘制
        if self.enabled and self.session_dir is not None and save_to_disk:
            debug_img = self._render_full(img_bgr, **kwargs)
            fname = f"{self.frame_count:03d}.png"
            cv2.imwrite(str(self.session_dir / fname), debug_img)

        # PEEP：精简绘制
        if self.peep_enabled:
            peep_img = self._render_peep(img_bgr, **kwargs)
            with self._frame_lock:
                self._latest_frame = peep_img
