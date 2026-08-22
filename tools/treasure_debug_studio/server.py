#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""鉴宝视觉调试台（后端）。

轻量级可视化工具：把「改 Python → 跑脚本 → 看数字 → 再改」压缩成
「拖框 → 实时看分数 → 跨帧测试 → 保存 JSON」。

启动:
    cd tools/treasure_debug_studio
    python server.py
浏览器打开 http://localhost:8765

仅用 Python 标准库 http.server + 项目已有的 cv2 / numpy，不新增依赖。

安全/健壮约定：
  * 图片/模板 API 只接受白名单内的相对名（session + 文件名），不接受任意 filesystem path。
  * POST /api/rois 原子保存：先写临时文件再 os.replace，避免写一半损坏配置。
  * reference_size 仅是元数据（供前端按比例显示），匹配时直接乘当前输入帧 W/H，不做映射。
"""
from __future__ import annotations

import base64
import json
import os
import re
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import cv2
import numpy as np

# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------
PROJ = Path(__file__).resolve().parent.parent.parent
TPL_DIR = PROJ / "assets" / "resource" / "image" / "treasure"
DEBUG_ROOT = PROJ / "debug" / "treasure"
ROIS_FILE = TPL_DIR / "treasure_rois.json"
STATIC_DIR = Path(__file__).resolve().parent / "static"
PORT = 8765

# 文件名白名单正则：只允许 debug/treasure/<会话>/raw/<NNNN>_raw.{png,jpg,jpeg,webp}
# raw 存盘格式由 treasure_module._tick_once 决定（当前为 JPG q95）；
# 调试台必须能列出并读图，否则整页黑屏。为避免将来换格式又要改白名单，
# 这里同时放行 png/jpg/webp，后端按扩展名回推真实文件。
_RAW_RE = re.compile(r"^\d{4}_raw\.(png|jpg|jpeg|webp)$")
_SESSION_RE = re.compile(r"^\d{8}_\d{6}$")  # 如 20260812_183611
_TPL_RE = re.compile(r"^[\w\-]+\.png$")

# 匹配参数（与 detector 一致）
MATCH_THRESHOLD = 0.75
# 多尺度匹配档位（迁移自彩蛋识别器 treasure_eggs.MATCH_SCALES）：
# 模板与屏幕实际渲染尺寸可能不一致（尤其蛋卡/横幅），逐档缩放取全局最优命中框，
# 让调试台「命中位置」框和运行时识别结果一致。
MATCH_SCALES = (0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 1.00, 1.05, 1.10, 1.15, 1.20, 1.25, 1.30)

_tpl_cache: dict[str, np.ndarray | None] = {}
_tpl_cache_lock = threading.Lock()

# OCR 引擎懒加载（调试台进程内单例，导入失败则标记不可用，API 返回 error）
_ocr_instance = None
_ocr_init_attempted = False
_ocr_init_lock = threading.Lock()


# ---------------------------------------------------------------------------
# 数据模型工具
# ---------------------------------------------------------------------------
# 允许的分类集合；顶层 `_schema_ver`/`reference_size` 等元数据字段一律跳过。
# 回合小字（round_label_area）已并入 ocr 分类（OCR 识别回合号，不再用模板像素差）。
# appraisers = 偏好鉴宝师（顺位匹配，prio 小的优先；rect=搜索区，threshold=命中阈值）。
# eggs = 彩蛋识别（三色蛋图标模板匹配 + 图标下方 OCR 计数；rect=共享搜索区，threshold=命中阈值，
#        `_count_*` 为段级计数区偏移元数据）。egg 条目的模板缺失时该色跳过。
CATEGORIES = ("stage", "actions", "ocr", "appraisers", "eggs")

# v2 缺省配置的可选归属（不预置 round_label_area.png 背景）
DEFAULT_ACTIONS = {
    "bid_confirm_red_btn": {"rect": [0.0, 0.0, 0.0, 0.0], "templates": ["bid_confirm_red_btn.png"]},
    "confirm_red_btn": {"rect": [0.0, 0.0, 0.0, 0.0], "templates": ["confirm_red_btn.png"]},
    "settle_collect_red_btn": {"rect": [0.0, 0.0, 0.0, 0.0], "templates": ["settle_collect_red_btn.png"]},
}
DEFAULT_OCR = {
    "bid_result_amount_box": {"rect": [0.0, 0.0, 0.0, 0.0], "templates": []},
    "bid_player1": {"rect": [0.0, 0.0, 0.0, 0.0], "templates": []},
    "bid_player2": {"rect": [0.0, 0.0, 0.0, 0.0], "templates": []},
    "bid_player3": {"rect": [0.0, 0.0, 0.0, 0.0], "templates": []},
    "bid_player4": {"rect": [0.0, 0.0, 0.0, 0.0], "templates": []},
    "player_name1": {"rect": [0.0, 0.0, 0.0, 0.0], "templates": []},
    "player_name2": {"rect": [0.0, 0.0, 0.0, 0.0], "templates": []},
    "player_name3": {"rect": [0.0, 0.0, 0.0, 0.0], "templates": []},
    "player_name4": {"rect": [0.0, 0.0, 0.0, 0.0], "templates": []},
}
# 偏好鉴宝师缺省段（与 treasure_module._APPRAISER_TEMPLATE_DEFS 对齐；仅文件缺段时补填）
DEFAULT_APPRAISERS = {
    "appraiser_p1_caroline": {"prio": 1, "rect": [0.03, 0.18, 0.97, 0.92],
                              "templates": ["appraiser_p1_caroline.png"], "threshold": 0.72},
    "appraiser_p2_shotaro": {"prio": 2, "rect": [0.03, 0.18, 0.97, 0.92],
                             "templates": ["appraiser_p2_shotaro.png"], "threshold": 0.72},
}


def migrate_rois(data: dict) -> dict:
    """v1 → v2 显式迁移（幂等）。仅当 _schema_ver < 2 时执行。

    - 把旧 stage.round_label_area 迁到 ocr.round_label_area（rect 原样保留，templates 清空——
      回合号改为 OCR 识别，不再关联模板图）。
    - 把旧 stage.bid_confirm_red_btn 迁到 actions.bid_confirm_red_btn。
    - 若新位置已存在则删旧，避免重复引用。
    """
    if data.get("_schema_ver", 1) >= 2:
        return data
    stage = data.get("stage")
    if not isinstance(stage, dict):
        stage = {}
    # round_label_area → ocr（回合号 OCR 识别，无需模板）
    old_rla = stage.pop("round_label_area", None)
    ocr = data.get("ocr")
    if not isinstance(ocr, dict):
        ocr = {}
        data["ocr"] = ocr
    if isinstance(old_rla, dict):
        ocr.setdefault("round_label_area", {
            "rect": old_rla.get("rect", [0.0, 0.0, 0.0, 0.0]),
            "templates": [],
        })
    # 兼容 v2 早期把 round_label_area 放在 round_labels 段的情况 → 也并入 ocr
    old_rl = data.get("round_labels")
    if isinstance(old_rl, dict):
        rla = old_rl.get("round_label_area")
        if isinstance(rla, dict):
            ocr.setdefault("round_label_area", {
                "rect": rla.get("rect", [0.0, 0.0, 0.0, 0.0]),
                "templates": [],
            })
        data.pop("round_labels", None)
    # bid_confirm_red_btn → actions
    old_bcrb = stage.pop("bid_confirm_red_btn", None)
    actions = data.get("actions")
    if not isinstance(actions, dict):
        actions = {}
        data["actions"] = actions
    if isinstance(old_bcrb, dict) and "bid_confirm_red_btn" not in actions:
        actions["bid_confirm_red_btn"] = old_bcrb
    data["_schema_ver"] = 2
    return data


def ensure_default_rois() -> dict:
    """确保 treasure_rois.json 存在且为 v2。不存在则先建一份 v1 等价默认，再跑迁移/补缺省。"""
    if not ROIS_FILE.exists():
        default = {
            "_schema_ver": 1,
            "reference_size": [1280, 720],
            "stage": {
                "settle_title": {"rect": [0.66, 0.12, 0.86, 0.21], "templates": ["settle_final_price_title.png"]},
                "result_banner": {"rect": [0.30, 0.42, 0.70, 0.58], "templates": ["result_auction_fail_banner.png"]},
                "smart_bid_btn": {"rect": [0.64, 0.76, 0.80, 0.87], "templates": ["bid_smart_btn.png"]},
                "round_big_banner": {"rect": [0.40, 0.42, 0.60, 0.57],
                                     "templates": ["round1_banner.png", "round2_banner.png", "round3_banner.png",
                                                   "round4_banner.png", "round5_banner.png"]},
                "appraiser_title": {"rect": [0.38, 0.12, 0.62, 0.21], "templates": ["select_appraiser_title.png"]},
                "participation_card": {"rect": [0.03, 0.73, 0.19, 0.85], "templates": ["hall_participation_card.png"]},
                "hall_peak_appraise_card": {"rect": [0.03, 0.73, 0.19, 0.85], "templates": ["hall_peak_appraise_card.png"]},
                "goto_appraise_btn": {"rect": [0.64, 0.80, 0.88, 0.90], "templates": ["act_goto_appraise_btn.png"]},
                "hall_session_cards": {"rect": [0.078, 0.028, 0.425, 0.205], "templates": ["hall_session_cards.png"]},
                "round_label_area": {"rect": [0.38, 0.10, 0.60, 0.20], "templates": []},
            },
            "ocr": DEFAULT_OCR,
        }
        # 与既有文件分支统一：先迁移 v1→v2，再补齐缺失分类段，保证四段齐全
        data = _fill_defaults(migrate_rois(default))
        save_rois(data)
        return data
    data = load_rois()
    data = _fill_defaults(migrate_rois(data))
    save_rois(data)
    return data


def _fill_defaults(data: dict) -> dict:
    """对缺失的分类段补缺省归属（幂等，不覆盖已存在内容）。"""
    for cat in CATEGORIES:
        if not isinstance(data.get(cat), dict):
            data[cat] = {}
    actions = data["actions"]
    for key, val in DEFAULT_ACTIONS.items():
        actions.setdefault(key, val)
    ocr = data["ocr"]
    for key, val in DEFAULT_OCR.items():
        ocr.setdefault(key, val)
    appraisers = data["appraisers"]
    for key, val in DEFAULT_APPRAISERS.items():
        appraisers.setdefault(key, val)
    data.setdefault("reference_size", [1280, 720])
    data.setdefault("_schema_ver", 2)
    return data


def load_rois() -> dict:
    with open(ROIS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_rois(data: dict) -> None:
    """原子保存：写临时文件 → os.replace。校验通过才落盘。"""
    validate_rois(data)
    TPL_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(TPL_DIR), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, ROIS_FILE)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def validate_rois(data: dict) -> None:
    if not isinstance(data, dict):
        raise ValueError("ROI 配置必须是 JSON 对象")
    if "reference_size" not in data:
        raise ValueError("缺少 reference_size")
    for cat in CATEGORIES:
        seg = data.get(cat)
        if not isinstance(seg, dict):
            raise ValueError(f"缺少 {cat} 段")
        for key, val in seg.items():
            # 段内 `_` 前缀键为元数据（如 _comment），跳过不校验
            if key.startswith("_"):
                continue
            if not isinstance(val, dict):
                raise ValueError(f"{cat}.{key} 必须是对象")
            rect = val.get("rect")
            if not (isinstance(rect, list) and len(rect) == 4
                    and all(isinstance(n, (int, float)) and 0.0 <= n <= 1.0 for n in rect)):
                raise ValueError(f"{cat}.{key}.rect 必须是 4 个 [0,1] 数字")
            tpls = val.get("templates", [])
            if not isinstance(tpls, list):
                raise ValueError(f"{cat}.{key}.templates 必须是数组")
            for t in tpls:
                if not isinstance(t, str) or not _TPL_RE.match(t):
                    raise ValueError(f"{cat}.{key}.templates 含非法模板名: {t!r}")
            # 可选 threshold：若存在则必须为 [0,1] 数字（缺省 0.75，不写即默认）
            th = val.get("threshold", None)
            if th is not None:
                if not isinstance(th, (int, float)) or isinstance(th, bool) or th < 0.0 or th > 1.0:
                    raise ValueError(f"{cat}.{key}.threshold 必须是 [0,1] 数字或省略（默认 0.75）")


# ---------------------------------------------------------------------------
# 匹配核心（复刻 detector 的 match_local，返回更丰富的诊断信息）
# ---------------------------------------------------------------------------
def load_gray(name: str) -> np.ndarray | None:
    if not _TPL_RE.match(name):
        return None
    with _tpl_cache_lock:
        if name in _tpl_cache:
            return _tpl_cache[name]
    path = TPL_DIR / name
    if not path.exists():
        return None
    img = cv2.imread(str(path))
    if img is None:
        return None
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    with _tpl_cache_lock:
        _tpl_cache[name] = g
    return g


def match_local(gray_big, gray_tpl, rect):
    """ROI 内多尺度模板匹配，返回全局最优（分数最高）尺度的命中框。

    迁移自 EggRewardRecognizer._match_best：模板在 0.70×~1.30× 逐档缩放匹配，
    容忍模板与屏幕实际渲染尺寸偏差；返回 best_scale 供前端标注。
    响应字段与旧单尺度版本兼容：size_ok / score / pixel_box / crop_size /
    tpl_size（现为最优尺度下的模板尺寸）/ hit_box / hit_norm，另加 best_scale。
    """
    H, W = gray_big.shape[:2]
    x1n, y1n, x2n, y2n = rect
    x1, y1 = max(0, int(x1n * W)), max(0, int(y1n * H))
    x2, y2 = min(W, int(x2n * W)), min(H, int(y2n * H))
    if x2 <= x1 or y2 <= y1:
        return {"size_ok": False, "score": -1.0, "reason": "empty_crop"}
    crop = gray_big[y1:y2, x1:x2]
    th0, tw0 = gray_tpl.shape[:2]
    ch, cw = crop.shape[:2]
    best = None  # (score, scale, scaled_th, scaled_tw, match_x_in_roi, match_y_in_roi)
    for s in MATCH_SCALES:
        nw = max(4, int(round(tw0 * s)))
        nh = max(4, int(round(th0 * s)))
        if nh > ch or nw > cw:
            continue
        if nw == tw0 and nh == th0:
            tpl_s = gray_tpl
        else:
            interp = cv2.INTER_AREA if s < 1.0 else cv2.INTER_CUBIC
            try:
                tpl_s = cv2.resize(gray_tpl, (nw, nh), interpolation=interp)
            except Exception:
                continue
        try:
            res = cv2.matchTemplate(crop, tpl_s, cv2.TM_CCOEFF_NORMED)
        except Exception:
            continue
        _, max_val, _, max_loc = cv2.minMaxLoc(res)
        if best is None or float(max_val) > best[0]:
            best = (float(max_val), s, nh, nw, int(max_loc[0]), int(max_loc[1]))
    if best is None:
        return {
            "size_ok": False, "score": -1.0,
            "pixel_box": [x1, y1, x2, y2], "crop_size": [cw, ch], "tpl_size": [tw0, th0],
            "reason": "roi_too_small",
        }
    score, scale, th, tw, mx, my = best
    # 命中框像素坐标（ROI 内偏移 + ROI 原点）；归一化坐标供前端在画布上叠加显示
    hit_box = [x1 + mx, y1 + my, x1 + mx + tw, y1 + my + th]
    hit_norm = [max(0.0, min(1.0, hit_box[0] / W)), max(0.0, min(1.0, hit_box[1] / H)),
                max(0.0, min(1.0, hit_box[2] / W)), max(0.0, min(1.0, hit_box[3] / H))]
    return {
        "size_ok": True, "score": float(score), "best_scale": float(scale),
        "pixel_box": [x1, y1, x2, y2], "crop_size": [cw, ch],
        "tpl_size": [tw, th], "hit_box": hit_box, "hit_norm": hit_norm,
        "reason": "ok",
    }


def gray_image_to_dataurl(gray: np.ndarray, max_w: int = 320) -> str:
    """灰度图编码为 base64 dataURL，供前端并排预览。"""
    h, w = gray.shape[:2]
    scale = min(1.0, max_w / w)
    if scale < 1.0:
        gray = cv2.resize(gray, (int(w * scale), int(h * scale)))
    ok, buf = cv2.imencode(".png", gray)
    if not ok:
        return ""
    b64 = base64.b64encode(buf.tobytes()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def bgr_image_to_dataurl(bgr: np.ndarray, max_w: int = 320) -> str:
    """BGR 彩图编码为 base64 dataURL。"""
    h, w = bgr.shape[:2]
    scale = min(1.0, max_w / w)
    if scale < 1.0:
        bgr = cv2.resize(bgr, (int(w * scale), int(h * scale)))
    ok, buf = cv2.imencode(".png", bgr)
    if not ok:
        return ""
    b64 = base64.b64encode(buf.tobytes()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _get_ocr_instance():
    """懒加载 TreasureOcr，失败一次后不再重试。"""
    global _ocr_instance, _ocr_init_attempted
    if _ocr_init_attempted:
        return _ocr_instance
    with _ocr_init_lock:
        if _ocr_init_attempted:
            return _ocr_instance
        try:
            import sys as _sys
            sys_path = str(PROJ)
            if sys_path not in _sys.path:
                _sys.path.insert(0, sys_path)
            from maaracing_assistant.plugins.treasure.ocr import TreasureOcr
            _ocr_instance = TreasureOcr(PROJ)
        except Exception as e:  # noqa: BLE001
            _ocr_instance = None
            print(f"[调试台] TreasureOcr 初始化失败: {e}")
        finally:
            _ocr_init_attempted = True
    return _ocr_instance


# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002 —— 基类签名如此
        # 精简标准日志，避免刷屏
        pass

    # ---- 静态文件 ----
    def _serve_static(self, rel: str):
        if rel in ("", "/"):
            rel = "index.html"
        # 防目录穿越
        file = (STATIC_DIR / rel).resolve()
        if not str(file).startswith(str(STATIC_DIR.resolve())) or not file.is_file():
            self.send_error(404)
            return
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
        }.get(file.suffix, "application/octet-stream")
        body = file.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(body)

    # ---- 响应工具 ----
    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    # ---- session / 文件白名单 ----
    def _list_sessions(self):
        if not DEBUG_ROOT.exists():
            return []
        return sorted(
            (p.name for p in DEBUG_ROOT.iterdir()
             if p.is_dir() and _SESSION_RE.match(p.name) and (p / "raw").is_dir()),
            reverse=True,
        )

    def _template_status(self):
        """以磁盘实际 .png 文件为 listed 唯一来源；referenced 来自当前 JSON。
        unassigned = listed − flatten(referenced)；dangling = flatten(referenced) − listed。"""
        listed = sorted(
            (p.name for p in TPL_DIR.iterdir() if p.is_file() and _TPL_RE.match(p.name))
        ) if TPL_DIR.is_dir() else []
        data = load_rois()
        referenced: dict[str, dict[str, list[str]]] = {}
        flat: set[str] = set()
        for cat in CATEGORIES:
            refs: dict[str, list[str]] = {}
            for key, val in (data.get(cat) or {}).items():
                if isinstance(val, dict):
                    tpls = [t for t in val.get("templates", []) if isinstance(t, str) and _TPL_RE.match(t)]
                    refs[key] = tpls
                    flat.update(tpls)
            referenced[cat] = refs
        listed_set = set(listed)
        return {
            "listed": listed,
            "referenced": referenced,
            "unassigned": sorted(listed_set - flat),
            "dangling": sorted(flat - listed_set),
        }

    def _list_raw_files(self, session: str):
        if not _SESSION_RE.match(session):
            return []
        raw = DEBUG_ROOT / session / "raw"
        if not raw.is_dir():
            return []
        return sorted(
            (p.name for p in raw.iterdir() if p.is_file() and _RAW_RE.match(p.name)),
        )

    def _resolve_raw(self, session: str, name: str) -> Path | None:
        if not (_SESSION_RE.match(session) and _RAW_RE.match(name)):
            return None
        p = (DEBUG_ROOT / session / "raw" / name).resolve()
        base = (DEBUG_ROOT / session / "raw").resolve()
        if p.is_file() and str(p).startswith(str(base)):
            return p
        return None

    # ---- GET ----
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path.startswith("/api/"):
            if path == "/api/list_sessions":
                self._send_json(self._list_sessions())
            elif path == "/api/list_images":
                session = qs.get("session", [None])[0]
                if session is None:
                    self._send_json({"error": "missing session"}, 400)
                    return
                self._send_json(self._list_raw_files(session))
            elif path == "/api/list_templates":
                if not TPL_DIR.is_dir():
                    self._send_json([])
                    return
                lst = sorted(p.name for p in TPL_DIR.iterdir()
                             if p.is_file() and _TPL_RE.match(p.name))
                self._send_json(lst)
            elif path == "/api/template_status":
                self._send_json(self._template_status())
            elif path == "/api/image":
                session = qs.get("session", [None])[0]
                name = qs.get("name", [None])[0]
                if session is None or name is None:
                    self._send_json({"error": "missing session/name"}, 400)
                    return
                p = self._resolve_raw(session, name)
                if p is None:
                    self.send_error(404)
                    return
                body = p.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == "/api/template":
                name = qs.get("name", [None])[0]
                if name is None or not _TPL_RE.match(name):
                    self.send_error(404)
                    return
                p = TPL_DIR / name
                if not p.is_file():
                    self.send_error(404)
                    return
                body = p.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == "/api/rois":
                self._send_json(load_rois())
            else:
                self.send_error(404)
            return

        # 静态资源
        self._serve_static(path.lstrip("/"))

    # ---- POST ----
    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/rois":
            data = self._read_body()
            try:
                save_rois(data)
                self._send_json({"ok": True, "path": str(ROIS_FILE)})
            except ValueError as e:
                self._send_json({"ok": False, "error": str(e)}, 400)
            except Exception as e:
                self._send_json({"ok": False, "error": f"保存失败: {e}"}, 500)
            return

        if path == "/api/template_upload":
            body = self._read_body()
            name = body.get("name", "")
            data_url = body.get("dataUrl", "")
            # 文件名白名单 + 强制 .png 后缀（模板引用统一 .png；其他格式由前端归一化文件名）
            if not _TPL_RE.match(name) or not name.lower().endswith(".png"):
                self._send_json({"ok": False, "error": "模板名非法，仅允许 xxx.png"}, 400)
                return
            if not data_url.startswith("data:image/"):
                self._send_json({"ok": False, "error": "仅支持图片格式（PNG/JPG/WebP/BMP 等）"}, 400)
                return
            b64 = data_url.split(",", 1)[1]
            raw_bytes = base64.b64decode(b64)
            if len(raw_bytes) > 5 * 1024 * 1024:
                self._send_json({"ok": False, "error": "图片超过 5MB"}, 400)
                return
            # 任意输入格式（png/jpg/webp/bmp…）→ 统一解码为 BGR → 再编码为 PNG 存盘，
            # 内部模板始终为 .png，引用逻辑不变
            arr = np.frombuffer(raw_bytes, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is None:
                self._send_json({"ok": False, "error": "无法解码为图片"}, 400)
                return
            ok, png_buf = cv2.imencode(".png", img)
            if not ok:
                self._send_json({"ok": False, "error": "图片编码失败"}, 500)
                return
            png_bytes = png_buf.tobytes()
            # 原子写盘
            TPL_DIR.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(TPL_DIR), suffix=".tmp")
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(png_bytes)
                os.replace(tmp, TPL_DIR / name)
            except Exception as e:
                if os.path.exists(tmp):
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
                self._send_json({"ok": False, "error": f"写入失败: {e}"}, 500)
                return
            # 更新模板缓存
            with _tpl_cache_lock:
                _tpl_cache.pop(name, None)
            self._send_json({"ok": True, "name": name, "size": [img.shape[1], img.shape[0]]})
            return

        if path == "/api/crop_to_template":
            body = self._read_body()
            session = body.get("session")
            image_name = body.get("image")
            rect = body.get("rect")
            target_tpl = body.get("target")  # 要写入的目标模板文件名
            if not (session and image_name and rect and target_tpl):
                self._send_json({"error": "缺 session/image/rect/target"}, 400)
                return
            if not _TPL_RE.match(target_tpl) or not target_tpl.lower().endswith(".png"):
                self._send_json({"ok": False, "error": "目标模板名非法"}, 400)
                return
            p = self._resolve_raw(session, image_name)
            if p is None:
                self._send_json({"error": "截图不存在"}, 404)
                return
            img = cv2.imread(str(p))
            if img is None:
                self._send_json({"error": "截图无法读取"}, 500)
                return
            H, W = img.shape[:2]
            x1n, y1n, x2n, y2n = rect
            x1, y1 = max(0, int(x1n * W)), max(0, int(y1n * H))
            x2, y2 = min(W, int(x2n * W)), min(H, int(y2n * H))
            if x2 <= x1 or y2 <= y1:
                self._send_json({"ok": False, "error": "ROI 为空"}, 400)
                return
            crop = img[y1:y2, x1:x2]
            # 编码为 PNG
            ok, buf = cv2.imencode(".png", crop)
            if not ok:
                self._send_json({"ok": False, "error": "PNG 编码失败"}, 500)
                return
            png_bytes = buf.tobytes()
            # 原子写盘
            TPL_DIR.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(TPL_DIR), suffix=".tmp")
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(png_bytes)
                os.replace(tmp, TPL_DIR / target_tpl)
            except Exception as e:
                if os.path.exists(tmp):
                    try: os.remove(tmp)
                    except OSError: pass
                self._send_json({"ok": False, "error": f"写入失败: {e}"}, 500)
                return
            # 更新缓存
            with _tpl_cache_lock:
                _tpl_cache.pop(target_tpl, None)
            self._send_json({
                "ok": True, "name": target_tpl,
                "size": [crop.shape[1], crop.shape[0]],
                "rect_px": [x1, y1, x2, y2],
            })
            return

        if path == "/api/match_score":
            body = self._read_body()
            session = body.get("session")
            name = body.get("name")
            rect = body.get("rect")
            tpl = body.get("template")
            if not (session and name and rect and tpl):
                self._send_json({"error": "缺 session/name/rect/template"}, 400)
                return
            p = self._resolve_raw(session, name)
            g = load_gray(tpl)
            if p is None or g is None:
                self._send_json({"error": "图或模板不存在"}, 404)
                return
            img = cv2.imread(str(p))
            assert img is not None  # p 已存在，imread 失败仅当文件损坏
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            res = match_local(gray, g, rect)
            res["crop_preview"] = ""
            res["tpl_preview"] = ""
            if res.get("size_ok"):
                x1, y1, x2, y2 = res["pixel_box"]
                res["crop_preview"] = gray_image_to_dataurl(gray[y1:y2, x1:x2])
            res["tpl_preview"] = gray_image_to_dataurl(g)
            self._send_json(res)
            return

        if path == "/api/cross_frame_test":
            body = self._read_body()
            session = body.get("session")
            rect = body.get("rect")
            tpl = body.get("template")
            threshold = body.get("threshold")
            if not (isinstance(threshold, (int, float)) and 0.0 <= threshold <= 1.0):
                threshold = MATCH_THRESHOLD  # 缺省 0.75
            else:
                threshold = float(threshold)
            if not (session and rect and tpl):
                self._send_json({"error": "缺 session/rect/template"}, 400)
                return
            g = load_gray(tpl)
            if g is None:
                self._send_json({"error": "模板不存在"}, 404)
                return
            files = self._list_raw_files(session)
            scores = []
            for name in files:
                p = self._resolve_raw(session, name)
                if p is None:
                    continue
                img = cv2.imread(str(p))
                if img is None:
                    continue
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                res = match_local(gray, g, rect)
                s = res.get("score", -1.0)
                scores.append((name, s))
            if not scores:
                self._send_json({"error": "无可用帧"}, 404)
                return
            vals = [s for _, s in scores if s >= 0]
            total = len(scores)
            resp = {"total_frames": total, "histogram": [], "threshold": threshold}
            if vals:
                vals_sorted = sorted(vals)
                n = len(vals_sorted)
                resp["max"] = round(vals_sorted[-1], 3)
                resp["p95"] = round(vals_sorted[min(n - 1, int(n * 0.95))], 3)
                resp["median"] = round(vals_sorted[n // 2], 3)
                resp["frames_ge_060"] = sum(1 for v in vals if v >= 0.60)
                resp["frames_ge_070"] = sum(1 for v in vals if v >= 0.70)
                resp["frames_ge_080"] = sum(1 for v in vals if v >= 0.80)
                resp["frames_ge_threshold"] = sum(1 for v in vals if v >= threshold)
                # 直方图：10 bins × 0.1 宽，覆盖 [0,1)，超过 1 的并入最后一档
                bins = [[round(i * 0.1, 1), round((i + 1) * 0.1, 1), 0] for i in range(10)]
                for v in vals:
                    bi = min(9, int(v * 10))
                    bins[bi][2] += 1
                resp["histogram"] = bins
                # Top-10 最佳帧
                best = sorted([(n_, s) for n_, s in scores if s >= 0],
                              key=lambda x: x[1], reverse=True)[:10]
                resp["best_frames"] = [{"name": n_, "score": round(s, 3)} for n_, s in best]
            self._send_json(resp)
            return

        if path == "/api/eggs_recognize":
            body = self._read_body()
            session = body.get("session")
            name = body.get("name")
            if not (session and name):
                self._send_json({"error": "缺 session/name"}, 400)
                return
            p = self._resolve_raw(session, name)
            if p is None:
                self._send_json({"error": "截图不存在"}, 404)
                return
            img = cv2.imread(str(p))
            if img is None:
                self._send_json({"error": "截图无法读取"}, 500)
                return
            try:
                import sys as _sys
                sys_path = str(PROJ)
                if sys_path not in _sys.path:
                    _sys.path.insert(0, sys_path)
                from maaracing_assistant.plugins.treasure.eggs import EggRewardRecognizer
                rec = EggRewardRecognizer(PROJ, ocr=_get_ocr_instance())
            except Exception as e:
                self._send_json({"error": f"彩蛋识别器不可用: {e}"})
                return
            if not rec.configured:
                self._send_json({
                    "configured": False,
                    "error": "eggs 段未配置完整模板/rect（缺 egg_*.png 或 rect 非法）",
                })
                return
            try:
                frame_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                res = rec.recognize(frame_rgb) or {"counts": {"red": 0, "yellow": 0, "blue": 0}, "eggs": []}
                res["configured"] = True
                self._send_json(res)
            except Exception as e:
                self._send_json({"error": f"彩蛋识别异常: {e}"})
            return

        if path == "/api/ocr_recognize":
            body = self._read_body()
            session = body.get("session")
            image_name = body.get("image")
            key = body.get("key")
            rect = body.get("rect")
            if not (session and image_name and key and rect):
                self._send_json({"error": "缺 session/image/key/rect"}, 400)
                return
            p = self._resolve_raw(session, image_name)
            if p is None:
                self._send_json({"error": "截图不存在"}, 404)
                return
            img = cv2.imread(str(p))
            if img is None:
                self._send_json({"error": "截图无法读取"}, 500)
                return
            H, W = img.shape[:2]
            x1n, y1n, x2n, y2n = rect
            x1, y1 = max(0, int(x1n * W)), max(0, int(y1n * H))
            x2, y2 = min(W, int(x2n * W)), min(H, int(y2n * H))
            if x2 <= x1 or y2 <= y1:
                self._send_json({"error": "ROI 为空", "crop_preview": ""}, 400)
                return
            crop_bgr = img[y1:y2, x1:x2]
            crop_preview = bgr_image_to_dataurl(crop_bgr, max_w=480)

            ocr = _get_ocr_instance()
            if ocr is None:
                self._send_json({
                    "crop_preview": crop_preview,
                    "crop_size": [crop_bgr.shape[1], crop_bgr.shape[0]],
                    "error": "TreasureOcr 不可用（导入/初始化失败，终端有详细日志）",
                })
                return
            try:
                t0 = cv2.getTickCount()
                frame_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                # 关键：直接对前端传过来的 rect（归一化坐标）做单块识别，
                # 而不是去 recognize_amounts() 里等 JSON 已配置区域 → 手动拖出来的临时 ROI 也能立刻出结果，
                # 同时避免了对无关 8 个区域做冗余识别（之前 9 块全跑 → 单块 15ms，耗时 856ms 也是这个原因）。
                info = ocr.recognize_single(frame_rgb, rect) or {}
                t1 = cv2.getTickCount()
                dur_ms = int((t1 - t0) * 1000 / cv2.getTickFrequency())
                raw_lines = info.get("raw_lines") or []
                # 文本 → 每行单独展示（去空）
                lines = [ln.strip() for ln in raw_lines if isinstance(ln, str) and ln.strip()]
                if not lines:
                    t = info.get("text") or ""
                    if t:
                        lines = [ln.strip() for ln in t.split("\n") if ln.strip()]
                amount = info.get("amount")
                cw, ch = crop_bgr.shape[1], crop_bgr.shape[0]
                # 尺寸建议：基于 PP-OCR rec 内部把图高统一 resize 到 48px 的事实
                # （具体分析见 treasure_ocr.py TARGET_ROI_HEIGHT 注释）
                # 经验阈值：
                #   • ROI 整体高 < 36px → 预估字符高 < 23px，模型内部需上采样 → 🔴 风险极高
                #   • ROI 整体高 < 48px → 预估字符高 < 31px，模型几乎无降采样余量 → 🟡 偏小
                #   • ROI 整体高 ≥ 72px → 预估字符高 ≥ 46px，降采样 1.5x → ✅ 充足
                size_warn = ""
                est_char_h = int(ch * 0.65)
                if ch < 36:
                    size_warn = (
                        f"🔴 尺寸不足：ROI 高仅 {ch}px，估算字符高约 {est_char_h}px。\n"
                        f"PP-OCR 识别模型输入高固定 48px，当前字符像素太少，笔画会被模型内部上采样糊掉。\n"
                        f"建议：把 ROI 框上下各向外扩 10~15px，或整体高度拉到 ≥ 72px。"
                    )
                elif ch < 48:
                    size_warn = (
                        f"🟡 尺寸偏小：ROI 高 {ch}px，估算字符高约 {est_char_h}px。\n"
                        f"能识别但不稳定，尤其逗号和数字 8/0 容易认错。\n"
                        f"建议：高度拉到 ≥ 72px 更稳。"
                    )
                elif cw < 20:
                    size_warn = "建议：ROI 宽度仅 {}px，可能没有包含任何文字。".format(cw)
                self._send_json({
                    "crop_preview": crop_preview,
                    "crop_size": [cw, ch],
                    "lines": lines,
                    "text": info.get("text") or "",
                    "amount": amount,
                    "amounts": info.get("amounts") or [],   # 每段单独解析的金额列表（bid_history用）
                    "duration_ms": dur_ms,
                    "size_warning": size_warn,
                    "error": None,
                })
            except Exception as e:  # noqa: BLE001
                self._send_json({
                    "crop_preview": crop_preview,
                    "crop_size": [crop_bgr.shape[1], crop_bgr.shape[0]],
                    "lines": [],
                    "text": "",
                    "amount": None,
                    "amounts": [],
                    "duration_ms": 0,
                    "error": f"OCR 调用异常: {e}",
                })
            return

        self._send_json({"error": f"unknown POST endpoint: {path}"}, 404)


def main():
    ensure_default_rois()
    # 输出到 stdout，方便用户在终端看到启动信息
    print(f"鉴宝视觉调试台已启动: http://localhost:{PORT}")
    print(f"ROI 配置文件: {ROIS_FILE}")
    print(f"截图根目录  : {DEBUG_ROOT}")
    print(f"模板目录    : {TPL_DIR}")
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        server.server_close()


if __name__ == "__main__":
    main()