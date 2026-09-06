#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""图像 → dataURL / 预览编码（DebugStudio Core · 与内容无关）。

把灰度/彩色图像编码成 base64 dataURL，供调试台前端并排预览模板与 ROI 裁剪。
纯通用工具，不持有任何模块领域知识。
"""
from __future__ import annotations

import base64

import cv2
import numpy as np


def _encode_as_png(img: np.ndarray, max_w: int) -> str:
    h, w = img.shape[:2]
    scale = min(1.0, max_w / w) if w else 0.0
    frame = img
    if scale < 1.0 and w > 0:
        frame = cv2.resize(img, (int(w * scale), int(h * scale)))
    ok, buf = cv2.imencode(".png", frame)
    if not ok:
        return ""
    b64 = base64.b64encode(buf.tobytes()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def gray_to_dataurl(gray: np.ndarray, max_w: int = 320) -> str:
    """灰度图编码为 dataURL（模板/ROI 裁剪预览用）。"""
    return _encode_as_png(gray, max_w=max_w)


def bgr_to_dataurl(bgr: np.ndarray, max_w: int = 320) -> str:
    """BGR 彩图编码为 dataURL。"""
    return _encode_as_png(bgr, max_w=max_w)