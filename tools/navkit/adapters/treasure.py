#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""鉴宝 NavKit adapter（统一计划 P3，首次认领）。

adapter 的职责：声明鉴宝模块「有哪些可校准类别 + 缺省归属 + 路径布局 + 领域端点」，
并复用 core 的 session/categories/reader/renderer 完成浏览与匹配。generic studio
只 dispatch 端点，不在此理解 OCR/出价内容；OCR/彩蛋的**领域识别逻辑**注册为
adapter 端点（server 转发），供 racing 等未来模块复用同一 server 骨架。

结构落点（迁移自 NavKit 控制台/server.py）：
    - 类别：stage / actions / ocr / appraisers / eggs
    - ROI 文件：plugins/treasure/resources/config/treasure_rois.json
    - 截图根：debug/treasure/（会话目录）
    - 领域端点：/api/ocr_recognize（RapidOCR 单 ROI）、/api/eggs_recognize（彩蛋识别）
"""
from __future__ import annotations

from pathlib import Path

from tools.navkit.core.categories import CategoryDefs
from tools.navkit.core.session import SessionBrowser

from maaracing_assistant.core.paths import debug_dir

PROJ = Path(__file__).resolve().parent.parent.parent.parent

CATEGORIES: tuple[str, ...] = ("stage", "actions", "ocr", "appraisers", "eggs")

# v2 缺省归属（与 NavKit 控制台/server.py 的 DEFAULT_* 保持一致，幂等补填）。
DEFAULT_ACTIONS = {
    "bid_confirm_red_btn": {"rect": [0.0, 0.0, 0.0, 0.0], "templates": ["bid_confirm_red_btn.png"]},
    "confirm_red_btn": {"rect": [0.0, 0.0, 0.0, 0.0], "templates": ["confirm_red_btn.png"]},
    "settle_collect_red_btn": {"rect": [0.0, 0.0, 0.0, 0.0], "templates": ["settle_collect_red_btn.png"]},
}
DEFAULT_APPRAISERS = {
    "appraiser_p1_caroline": {"prio": 1, "rect": [0.03, 0.18, 0.97, 0.92],
                              "templates": ["appraiser_p1_caroline.png"], "threshold": 0.72},
    "appraiser_p2_shotaro": {"prio": 2, "rect": [0.03, 0.18, 0.97, 0.92],
                             "templates": ["appraiser_p2_shotaro.png"], "threshold": 0.72},
}
DEFAULT_ITEMS = {
    "actions": DEFAULT_ACTIONS,
    "appraisers": DEFAULT_APPRAISERS,
}


def make_category_defs() -> CategoryDefs:
    return CategoryDefs(CATEGORIES, name="treasure", default_items=DEFAULT_ITEMS)


def rois_path() -> Path:
    return PROJ / "maaracing_assistant" / "plugins" / "treasure" / "resources" / "treasure_rois.json"


def session_dir() -> Path:
    """鉴宝 debug 截图根：%APPDATA%/MaaRacingAssistant/debug/treasure。

    与 treasure_module._prepare_debug_dirs 的写盘目录（debug_dir()/treasure）
    严格一致——旧实现的 `PROJ/debug/treasure` 与用户数据目录解耦才会导致调不到会话。
    """
    return debug_dir() / "treasure"


def make_session_browser() -> SessionBrowser:
    return SessionBrowser(session_dir())


def template_dir() -> Path:
    return PROJ / "maaracing_assistant" / "plugins" / "treasure" / "resources" / "image"


# ---------------------------------------------------------------------------
# 领域端点：OCR / 彩蛋（迁移自 NavKit 控制台/server.py；server only dispatch）
# ---------------------------------------------------------------------------
_ocr_instance = None
_ocr_init_attempted = False
_ocr_lock = __import__("threading").Lock()


def _get_ocr():
    """懒加载 TreasureOcr，失败一次不再重试。"""
    global _ocr_instance, _ocr_init_attempted
    if _ocr_init_attempted:
        return _ocr_instance
    with _ocr_lock:
        if _ocr_init_attempted:
            return _ocr_instance
        try:
            import sys as _sys
            if str(PROJ) not in _sys.path:
                _sys.path.insert(0, str(PROJ))
            from maaracing_assistant.plugins.treasure.ocr import TreasureOcr
            _ocr_instance = TreasureOcr(PROJ)
        except Exception as e:  # noqa: BLE001
            _ocr_instance = None
            print(f"[NavKit] TreasureOcr 初始化失败: {e}")
        finally:
            _ocr_init_attempted = True
    return _ocr_instance


def register_endpoints(state) -> None:
    """注册 treasure 领域端点（OCR/彩蛋）到 server 的 extra_handlers。"""
    state.extra_handlers.setdefault("POST", {})["/api/ocr_recognize"] = _handle_ocr
    state.extra_handlers.setdefault("POST", {})["/api/eggs_recognize"] = _handle_eggs


def _handle_ocr(handler, body: dict) -> None:
    """/api/ocr_recognize：单 ROI 用 RapidOCR 识别文字/金额 + 尺寸建议。"""
    import cv2
    _, crop_bgr, crop_preview, ocr = _crop_for_roi(handler, body, max_w=480)
    if crop_bgr is None:
        handler._send_json({"error": "截图不存在或 ROI 为空", "crop_preview": ""}, 400)
        return
    cw, ch = crop_bgr.shape[1], crop_bgr.shape[0]
    if ocr is None:
        handler._send_json({
            "crop_preview": crop_preview, "crop_size": [cw, ch],
            "error": "TreasureOcr 不可用（导入/初始化失败，终端有详细日志）",
        })
        return
    try:
        # rect 归一化 → 单块识别（手动拖出的临时 ROI 也能立刻出结果，且不做全量冗余识别）
        frame_rgb = cv2.cvtColor(handler._read_bgr(body) or _crop_frame(crop_bgr), cv2.COLOR_BGR2RGB)
        info = ocr.recognize_single(frame_rgb, body.get("rect")) or {}
        raw_lines = info.get("raw_lines") or []
        lines = [ln.strip() for ln in raw_lines if isinstance(ln, str) and ln.strip()]
        if not lines:
            t = info.get("text") or ""
            if t:
                lines = [ln.strip() for ln in t.split("\n") if ln.strip()]
        est_char_h = int(ch * 0.65)
        size_warn = ""
        if ch < 36:
            size_warn = ("🔴 尺寸不足：ROI 高仅 {}px，估算字符高约 {}px。"
                         "PP-OCR 识别模型输入高固定 48px，建议把 ROI 上下外扩 10~15px 或整体高度拉到 ≥72px。").format(ch, est_char_h)
        elif ch < 48:
            size_warn = ("🟡 尺寸偏小：ROI 高 {}px，估算字符高约 {}px，能识别但不稳定。建议高度拉到 ≥72px。").format(ch, est_char_h)
        elif cw < 20:
            size_warn = "建议：ROI 宽度仅 {}px，可能没包含任何文字。".format(cw)
        handler._send_json({
            "crop_preview": crop_preview, "crop_size": [cw, ch],
            "lines": lines, "text": info.get("text") or "",
            "amount": info.get("amount"), "amounts": info.get("amounts") or [],
            "duration_ms": info.get("duration_ms", 0),
            "size_warning": size_warn, "error": None,
        })
    except Exception as e:  # noqa: BLE001
        handler._send_json({
            "crop_preview": crop_preview, "crop_size": [cw, ch],
            "lines": [], "text": "", "amount": None, "amounts": [],
            "duration_ms": 0, "error": f"OCR 调用异常: {e}",
        })


def _handle_eggs(handler, body: dict) -> None:
    """/api/eggs_recognize：彩蛋识别（图标匹配 + 计数 OCR）。"""
    import sys as _sys
    if str(PROJ) not in _sys.path:
        _sys.path.insert(0, str(PROJ))
    try:
        from maaracing_assistant.plugins.treasure.eggs import EggRewardRecognizer
        rec = EggRewardRecognizer(PROJ, ocr=_get_ocr())
    except Exception as e:  # noqa: BLE001
        handler._send_json({"error": f"彩蛋识别器不可用: {e}"})
        return
    if not rec.configured:
        handler._send_json({"configured": False,
                            "error": "eggs 段未配置完整模板/rect（缺 egg_*.png 或 rect 非法）"})
        return
    img = handler._read_bgr(body)
    if img is None:
        handler._send_json({"error": "截图不存在"}, 404)
        return
    try:
        frame_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        res = rec.recognize(frame_rgb) or {"counts": {"red": 0, "yellow": 0, "blue": 0}, "eggs": []}
        res["configured"] = True
        handler._send_json(res)
    except Exception as e:  # noqa: BLE001
        handler._send_json({"error": f"彩蛋识别异常: {e}"})


def _crop_for_roi(handler, body: dict, max_w: int):
    """从 body 的 session/image+rect 抠 ROI，返回 (bgr_crop, crop_bgr, preview, ocr)。"""
    import cv2
    from tools.navkit.core.renderer import bgr_to_dataurl
    img = handler._read_bgr(body)
    if img is None:
        return None, None, "", None
    H, W = img.shape[:2]
    rect = body.get("rect")
    if not rect:
        return img, img, bgr_to_dataurl(img, max_w=max_w), _get_ocr()
    x1n, y1n, x2n, y2n = (float(v) for v in rect)
    x1, y1 = max(0, int(x1n * W)), max(0, int(y1n * H))
    x2, y2 = min(W, int(x2n * W)), min(H, int(y2n * H))
    if x2 <= x1 or y2 <= y1:
        return None, None, "", _get_ocr()
    crop = img[y1:y2, x1:x2]
    return img, crop, bgr_to_dataurl(crop, max_w=max_w), _get_ocr()


def _crop_frame(crop_bgr):
    return crop_bgr