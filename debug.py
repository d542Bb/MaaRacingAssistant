#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MaaRacingAssistant — 调试可视化模块
每帧截图标注：探测轮廓(黄) / 入围候选(绿) / 选中光标(红) / 按钮目标
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
            cv2.setWindowTitle(self._peep_window, self._peep_window)
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
        """开始一次导航调试会话"""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        # 使用中文标签 + 时间戳作为文件夹名
        self.session_dir = self.proj_dir / "debug" / "navigate" / f"{label}_{ts}"
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.frame_count = 0

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
    ):
        """保存一帧调试截图，标注识别结果

        颜色约定：
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
        img = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
        h, w = img.shape[:2]

        # ── 画被过滤拉黑的轮廓（黑色） ──
        if all_candidates:
            cand_set = {c["pos"] for c in (candidates or [])}
            for c in all_candidates:
                if c["pos"] in cand_set:
                    continue  # 已入围，交给绿色段画
                px, py = c["pos"]
                cv2.circle(img, (px, py), 5, (0, 0, 0), 1)
                cv2.putText(
                    img,
                    f"A{c['area']:.0f} R{c['circularity']:.2f}",
                    (px + 6, py - 4),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.30,
                    (0, 0, 0),
                    1,
                )

        # ── 画入围候选（绿色/紫色）覆盖黄色 ──
        if candidates:
            for c in candidates:
                px, py = c["pos"]
                if c.get("blacklisted"):
                    color = (255, 0, 255)  # 紫色：静止拉黑
                else:
                    color = (0, 200, 0)    # 绿色：正常候选
                cv2.circle(img, (px, py), 8, color, 1)
                cv2.putText(
                    img,
                    f"A{c['area']:.0f} R{c['circularity']:.2f}",
                    (px + 9, py - 4),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.32,
                    color,
                    1,
                )

        # ── 画按钮目标（蓝色大圈 + 十字） ──
        if button_pos:
            bx, by = button_pos
            cv2.circle(img, (bx, by), 14, (235, 206, 135), 2)
            cv2.line(img, (bx - 10, by), (bx + 10, by), (235, 206, 135), 1)
            cv2.line(img, (bx, by - 10), (bx, by + 10), (235, 206, 135), 1)
            cv2.putText(
                img,
                f"btn({bx},{by})",
                (bx + 16, by + 4),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.4,
                (235, 206, 135),
                1,
            )

        # ── 画选中的光标位置（红色大圈 + 十字） ──
        if cursor_pos:
            cx, cy = cursor_pos
            cv2.circle(img, (cx, cy), 12, (0, 0, 220), 2)
            cv2.line(img, (cx - 8, cy), (cx + 8, cy), (0, 0, 220), 1)
            cv2.line(img, (cx, cy - 8), (cx, cy + 8), (0, 0, 220), 1)
            info = f"CURSOR({cx},{cy}) A={cursor_area:.0f} S={cursor_score:.3f}"
            if dist is not None:
                info += f" D={dist:.0f}"
            cv2.putText(
                img,
                info,
                (cx + 14, cy - 6),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.4,
                (0, 0, 220),
                1,
            )
        else:
            cv2.putText(
                img,
                "NO CURSOR",
                (w // 2 - 50, h // 2),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (0, 0, 220),
                2,
            )

        # ── 画模板匹配矩形（青色） ──
        if template_rects:
            for tr in template_rects:
                cx, cy = tr["pos"]
                tw, th = tr["size"]
                x1 = cx - tw // 2
                y1 = cy - th // 2
                cv2.rectangle(img, (x1, y1), (x1 + tw, y1 + th), (255, 255, 0), 2)
                cv2.putText(img, f"TPL {tr.get('name','')} {tr['confidence']:.2f}",
                            (x1, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 0), 1)

        # ── 画 YOLO 检测框 ──
        if detections:
            colors = {
                "coin": (0, 215, 255),      # 金色
                "car": (0, 0, 220),          # 红色
                "bonus_car": (220, 0, 220),  # 紫色
            }
            for det in detections:
                x1, y1, x2, y2 = det["box"]
                cls_name = det.get("class_name", "?")
                color = colors.get(cls_name, (255, 255, 255))
                cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
                label_text = f"{cls_name} {det['confidence']:.2f}"
                cv2.putText(img, label_text, (x1, y1 - 5),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)

        # ── 顶部信息栏 ──
        info_line = f"#{self.frame_count}"
        if label:
            info_line += f" | {label}"
        cv2.putText(img, info_line, (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
        if cursor_score > 0:
            score_line = f"score={cursor_score:.3f}"
            cv2.putText(img, score_line, (10, 42), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1)

        # 保存到磁盘（仅存盘模式开启时）
        if self.enabled and self.session_dir is not None:
            fname = f"{self.frame_count:03d}.png"
            cv2.imwrite(str(self.session_dir / fname), img)

        # 缓存帧供 PEEP 实时预览
        if self.peep_enabled:
            with self._frame_lock:
                self._latest_frame = img.copy()