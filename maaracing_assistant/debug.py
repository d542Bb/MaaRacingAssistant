#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MaaRacingAssistant — 调试可视化模块
每帧截图标注：探测轮廓(黄) / 入围候选(绿) / 选中光标(红) / 按钮目标

两套渲染：
  • 存盘模式（enabled）→ 全量绘制，保存到磁盘
  • PEEP 模式（peep_enabled）→ 精简绘制，仅关键逻辑
"""

from __future__ import annotations

import threading
import cv2
import numpy as np
from pathlib import Path
from datetime import datetime


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

    # ---------- 场景类型判断 ----------

    @staticmethod
    def _is_racing(label: str) -> bool:
        return label.startswith("race_") if label else False

    # ==================================================================
    #  全量绘制 — 存盘用
    # ==================================================================

    def _render_full(self, img: np.ndarray, **kw) -> np.ndarray:
        """全量标注绘制，返回 BGR 帧"""
        frame = img.copy()
        h, w = frame.shape[:2]

        cursor_pos = kw.get("cursor_pos")
        cursor_area = kw.get("cursor_area", 0.0)
        cursor_score = kw.get("cursor_score", 0.0)
        button_pos = kw.get("button_pos")
        candidates = kw.get("candidates")
        all_candidates = kw.get("all_candidates")
        dist = kw.get("dist")
        label = kw.get("label", "")
        template_rects = kw.get("template_rects")
        detections = kw.get("detections")
        lane = kw.get("lane")

        # ── 被过滤拉黑的轮廓（黑色） ──
        if all_candidates:
            cand_set = {c["pos"] for c in (candidates or [])}
            for c in all_candidates:
                if c["pos"] in cand_set:
                    continue
                px, py = c["pos"]
                cv2.circle(frame, (px, py), 5, (0, 0, 0), 1)
                cv2.putText(
                    frame,
                    f"A{c['area']:.0f} R{c['circularity']:.2f}",
                    (px + 6, py - 4),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.30,
                    (0, 0, 0),
                    1,
                )

        # ── 入围候选（绿色/紫色） ──
        if candidates:
            for c in candidates:
                px, py = c["pos"]
                color = (255, 0, 255) if c.get("blacklisted") else (0, 200, 0)
                cv2.circle(frame, (px, py), 8, color, 1)
                cv2.putText(
                    frame,
                    f"A{c['area']:.0f} R{c['circularity']:.2f}",
                    (px + 9, py - 4),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.32,
                    color,
                    1,
                )

        # ── 按钮目标（蓝色大圈 + 十字） ──
        if button_pos:
            bx, by = button_pos
            cv2.circle(frame, (bx, by), 14, (235, 206, 135), 2)
            cv2.line(frame, (bx - 10, by), (bx + 10, by), (235, 206, 135), 1)
            cv2.line(frame, (bx, by - 10), (bx, by + 10), (235, 206, 135), 1)
            cv2.putText(
                frame,
                f"btn({bx},{by})",
                (bx + 16, by + 4),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.4,
                (235, 206, 135),
                1,
            )

        # ── 选中的光标位置（红色大圈 + 十字） ──
        if cursor_pos:
            cx, cy = cursor_pos
            cv2.circle(frame, (cx, cy), 12, (0, 0, 220), 2)
            cv2.line(frame, (cx - 8, cy), (cx + 8, cy), (0, 0, 220), 1)
            cv2.line(frame, (cx, cy - 8), (cx, cy + 8), (0, 0, 220), 1)
            info = f"CURSOR({cx},{cy}) A={cursor_area:.0f} S={cursor_score:.3f}"
            if dist is not None:
                info += f" D={dist:.0f}"
            cv2.putText(
                frame,
                info,
                (cx + 14, cy - 6),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.4,
                (0, 0, 220),
                1,
            )
        else:
            cv2.putText(
                frame,
                "NO CURSOR",
                (w // 2 - 50, h // 2),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (0, 0, 220),
                2,
            )

        # ── 模板匹配矩形（青色） ──
        if template_rects:
            for tr in template_rects:
                cx, cy = tr["pos"]
                tw, th = tr["size"]
                x1 = cx - tw // 2
                y1 = cy - th // 2
                cv2.rectangle(frame, (x1, y1), (x1 + tw, y1 + th), (255, 255, 0), 2)
                cv2.putText(
                    frame,
                    f"TPL {tr.get('name','')} {tr['confidence']:.2f}",
                    (x1, y1 - 5),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.4,
                    (255, 255, 0),
                    1,
                )

        # ── YOLO 检测框 ──
        if detections:
            for det in detections:
                x1, y1, x2, y2 = det["box"]
                cls_name = det.get("class_name", "?")
                color = self._YOLO_COLORS.get(cls_name, (255, 255, 255))
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                label_text = f"{cls_name} {det['confidence']:.2f}"
                cv2.putText(
                    frame, label_text, (x1, y1 - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1,
                )

        # ── 黄色标线检测结果 ──
        if lane:
            lx, rx = lane["left"], lane["right"]
            cv2.line(frame, (lx, 0), (lx, h), (0, 255, 255), 3)
            cv2.line(frame, (rx, 0), (rx, h), (0, 255, 255), 3)
            cx = lane.get("center", (lx + rx) // 2)
            cv2.line(frame, (cx, 0), (cx, h), (0, 255, 0), 1)
            cv2.putText(
                frame, f"L={lx}", (lx + 4, 20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1,
            )
            cv2.putText(
                frame, f"R={rx}", (rx - 60, 20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1,
            )

        # ── 顶部信息栏 ──
        info_line = f"#{self.frame_count}"
        if label:
            info_line += f" | {label}"
        cv2.putText(
            frame, info_line, (10, 22),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1,
        )
        if cursor_score > 0:
            cv2.putText(
                frame, f"score={cursor_score:.3f}", (10, 42),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1,
            )

        return frame

    # ==================================================================
    #  精简绘制 — PEEP 实时预览用
    # ==================================================================

    def _render_peep(self, img: np.ndarray, **kw) -> np.ndarray:
        """关键逻辑精简绘制，返回 BGR 帧"""
        frame = img.copy()
        h, w = frame.shape[:2]

        cursor_pos = kw.get("cursor_pos")
        button_pos = kw.get("button_pos")
        label = kw.get("label", "")
        detections = kw.get("detections")
        lane = kw.get("lane")

        # ── YOLO 检测框（不画置信度文本，减少视觉噪音） ──
        if detections:
            for det in detections:
                x1, y1, x2, y2 = det["box"]
                cls_name = det.get("class_name", "?")
                color = self._YOLO_COLORS.get(cls_name, (255, 255, 255))
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                cv2.putText(
                    frame, cls_name, (x1, y1 - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1,
                )

        # ── 黄色标线 ──
        if lane:
            lx, rx = lane["left"], lane["right"]
            cv2.line(frame, (lx, 0), (lx, h), (0, 255, 255), 2)
            cv2.line(frame, (rx, 0), (rx, h), (0, 255, 255), 2)
            cx = lane.get("center", (lx + rx) // 2)
            cv2.line(frame, (cx, 0), (cx, h), (0, 255, 0), 1)

        # ── 光标位置（精简：红色小圈，不写面积/评分） ──
        if cursor_pos:
            cx, cy = cursor_pos
            cv2.circle(frame, (cx, cy), 8, (0, 0, 220), 2)
            cv2.line(frame, (cx - 6, cy), (cx + 6, cy), (0, 0, 220), 1)
            cv2.line(frame, (cx, cy - 6), (cx, cy + 6), (0, 0, 220), 1)

        # ── 按钮目标（精简：蓝色小圈） ──
        if button_pos:
            bx, by = button_pos
            cv2.circle(frame, (bx, by), 10, (235, 206, 135), 2)
            cv2.line(frame, (bx - 8, by), (bx + 8, by), (235, 206, 135), 1)
            cv2.line(frame, (bx, by - 8), (bx, by + 8), (235, 206, 135), 1)

        # ── 顶部信息栏（大号字体 + 黑色描边） ──
        info_parts = [f"#{self.frame_count}"]
        if label:
            info_parts.append(label)
        info_line = " | ".join(info_parts)

        # 黑色描边提升可读性
        cv2.putText(
            frame, info_line, (10, 30),
            cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 0, 0), 3,
        )
        cv2.putText(
            frame, info_line, (10, 30),
            cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 1,
        )

        # ── 场景类统计 ──
        if detections:
            n_coins = sum(1 for d in detections if d.get("class_name") == "coin")
            n_cars = sum(1 for d in detections if d.get("class_name") == "car")
            n_bonus = sum(1 for d in detections if d.get("class_name") == "bonus_car")
            stats = f"coin:{n_coins}  car:{n_cars}  bonus:{n_bonus}"
            cv2.putText(
                frame, stats, (10, 54),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 180, 180), 1,
            )

        # ── 方向指示器（底部大字） ──
        dir_text = ""
        dir_color = (0, 255, 0)
        if self._is_racing(label) or "d_L" in label:
            dir_text = "← LEFT"
            dir_color = (0, 255, 255)
        elif "d_R" in label:
            dir_text = "RIGHT →"
            dir_color = (0, 255, 255)
        elif "d_S" in label:
            dir_text = "STRAIGHT ↑"
            dir_color = (0, 255, 0)
        elif label and "timeout" in label:
            dir_text = "TIMEOUT"
            dir_color = (0, 0, 220)

        if dir_text:
            (tw, th), _ = cv2.getTextSize(dir_text, cv2.FONT_HERSHEY_SIMPLEX, 1.2, 3)
            tx = (w - tw) // 2
            ty = h - 30
            # 黑色描边
            cv2.putText(
                frame, dir_text, (tx + 1, ty + 1),
                cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 3,
            )
            cv2.putText(
                frame, dir_text, (tx, ty),
                cv2.FONT_HERSHEY_SIMPLEX, 1.2, dir_color, 3,
            )

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

        # 收集所有 kwargs 避免重复传参
        kwargs = dict(
            cursor_pos=cursor_pos,
            cursor_area=cursor_area,
            cursor_score=cursor_score,
            button_pos=button_pos,
            candidates=candidates,
            all_candidates=all_candidates,
            dist=dist,
            label=label,
            template_rects=template_rects,
            detections=detections,
            lane=lane,
        )

        # ═══════════════════════════════════════════════
        # 存盘：全量绘制
        # ═══════════════════════════════════════════════
        if self.enabled and self.session_dir is not None and save_to_disk:
            debug_img = self._render_full(img_bgr, **kwargs)
            fname = f"{self.frame_count:03d}.png"
            cv2.imwrite(str(self.session_dir / fname), debug_img)

        # ═══════════════════════════════════════════════
        # PEEP：精简绘制（独立更新，不影响存盘）
        # ═══════════════════════════════════════════════
        if self.peep_enabled:
            peep_img = self._render_peep(img_bgr, **kwargs)
            with self._frame_lock:
                self._latest_frame = peep_img
