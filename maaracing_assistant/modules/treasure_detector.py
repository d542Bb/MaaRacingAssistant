#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
巅峰鉴宝阶段自动检测器（基于 cv2.matchTemplate 模板匹配，无 OCR，毫秒级）。

设计原则：
  1. **局部 ROI 匹配**：每个模板只在「它理论上会出现的那一小块区域」内搜索，
     避免大图里其他相似文字造成假阳性。
  2. **模板只选「独有的稳定元素」**：巨型文字横幅 / 独有的卡片文字标题 /
     右上角「第 N 回合」完整小字（整段 89×25，因为每个 N 的整段字结构不同）。
  3. **强特征优先**：结算页的「竞拍失败」「最终竞拍价格」绝对唯一，优先级最高。
  4. **不要求全覆盖**：模糊阶段（匹配中、主题抽取动画）返回 None，
     上层状态机按「最近一次稳定阶段 + 时间推移」自行推进即可。

用法：
    detector = TreasureStageDetector(proj)
    stage, round_no = detector.detect(frame_rgb)
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from maaracing_assistant.logger import logger


MATCH_THRESHOLD = 0.75  # TM_CCOEFF_NORMED

# ============================================================
# 搜索 ROI：从 assets/resource/image/treasure/treasure_rois.json 读取
# （调试台 tools/treasure_debug_studio 负责可视化校准并保存该文件）。
# rect 为归一化坐标 (x1n, y1n, x2n, y2n)，匹配时直接乘当前输入帧 W/H。
# 注意：不提供硬编码 fallback——JSON 缺失/失败时如实报告并跳过，
#       避免用残缺默认值掩盖真实配置导致阶段漏检。
# ============================================================


def _load_rois(proj: Path) -> dict:
    """读取调试台保存的 ROI 配置；缺失/失败时打 WARNING 并返回空 dict（跳过全部 ROI）。

    返回: {roi_key: (x1n, y1n, x2n, y2n)}
    并在同模块 _roi_thresholds 中写入 {roi_key: threshold|None} 供 detect 使用。
    """
    global _roi_thresholds
    _roi_thresholds = {}
    path = proj / "assets" / "resource" / "image" / "treasure" / "treasure_rois.json"
    if not path.exists():
        logger.log(f"[鉴宝检测器] 未找到 {path.name}，无法配置任何 ROI，阶段检测将跳过", "WARNING")
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.log(f"[鉴宝检测器] 读取 {path.name} 失败({e})，阶段检测将跳过", "WARNING")
        return {}
    rois = {}
    for key, val in data.get("stage", {}).items():
        if isinstance(val, dict) and isinstance(val.get("rect"), list) \
                and len(val["rect"]) == 4:
            rois[key] = tuple(float(n) for n in val["rect"])
            # ROI 级自定义阈值（调试台调整并保存进 JSON），缺省 None → 主程序走 MATCH_THRESHOLD 或 per-template 覆盖
            th = val.get("threshold")
            if isinstance(th, (int, float)) and not isinstance(th, bool) and 0.0 <= th <= 1.0:
                _roi_thresholds[key] = float(th)
            else:
                _roi_thresholds[key] = None
    if not rois:
        logger.log("[鉴宝检测器] JSON 里没有可用 stage ROI，阶段检测将跳过", "WARNING")
        return {}
    logger.log(f"[鉴宝检测器] 已从 {path.name} 加载 {len(rois)} 个 ROI: {', '.join(rois)}", "INFO")
    custom = [k for k, v in _roi_thresholds.items() if v is not None]
    if custom:
        logger.log(f"[鉴宝检测器] 以下 ROI 使用自定义阈值: " +
                   ", ".join(f"{k}={_roi_thresholds[k]:.3f}" for k in custom), "INFO")
    return rois


_roi_thresholds: dict[str, float | None] = {}  # {roi_key: threshold}，由 _load_rois 填充


@dataclass(frozen=True)
class _Rule:
    template: str
    roi_key: str
    stage: str
    round_no: int | None = None


# ROI → 阶段语义映射。模板列表完全由 JSON 的 templates 数组决定（_ROI_TPL），
# 这里只定义"这个 ROI 代表哪个阶段"、优先级、以及多模板互斥时的领先要求。
#   - __round_phase__：命中后走回合识别（智能出价按钮 / 回合横幅）
#   - round_from_template：True 时回合号从命中的模板文件名解析（如 round3_banner.png → 3）
#   - margin：多模板互斥时，最高分需领先次高分 ≥ margin 才算命中
_ROI_STAGE: dict[str, dict] = {
    # 结算后弹窗（领取分红后可能出现）。三个弹窗（今日最高/等级提升/彩蛋）合并为单一
    # 阶段「结算弹窗」，靠 _last_hit_roi_key 区分具体弹窗：
    #   - daily_high_banner 命中 → 今日最高积分上涨（需先 OCR 读积分再点）
    #   - egg_reward_title 命中 → 奖励结算彩蛋（需先 OCR 读蛋数量再点）
    #   - 都没命中（等级提升遮满全屏，无 ROI）→ 盲点跳过
    # 优先级（110/105）高于大厅（50）：弹窗在时一定先命中弹窗，弹窗全关后大厅才可见。
    # ②鉴宝等级提升 不识别（无 ROI），弹窗遮满全屏 → 三种弹窗模板都匹配不到 → 上层盲点。
    "daily_high_banner":  {"stage": "结算弹窗", "priority": 110},
    "egg_reward_title":   {"stage": "结算弹窗", "priority": 105},
    # 结算页
    "settle_title":       {"stage": "领取分红",           "priority": 100},
    "result_banner":      {"stage": "中标结算",           "priority": 90,
                           # 竞拍成功横幅带彩条特效，匹配分偏低，单独放宽阈值防误判
                           "thresholds": {"result_auction_win_banner": 0.60}},
    # 出价面板的智能出价按钮（常亮，但没有回合号 → 走小字像素差识别兜底）
    "smart_bid_btn":      {"stage": "__round_phase__",    "priority": 80},
    # 回合巨型横幅（5 张互斥模板，取最高分且领先次高 ≥ margin）
    "round_big_banner":   {"stage": "__round_phase__",    "priority": 70,
                           "round_from_template": True, "margin": 0.03},
    # 进入对局前
    "appraiser_title":    {"stage": "选择鉴宝师",         "priority": 60},
    "is_matching_btn":    {"stage": "匹配中",             "priority": 60},
    "participation_card": {"stage": "游戏大厅",           "priority": 50},
    "hall_peak_appraise_card": {"stage": "游戏大厅",       "priority": 50},
    "goto_appraise_btn":  {"stage": "活动页面",           "priority": 50},
    "hall_session_cards": {"stage": "鉴宝大厅(选择场次)", "priority": 50},
}

_ROUND_RE = re.compile(r"round(\d+)", re.IGNORECASE)


def _load_schema(proj: Path) -> dict:
    """读取完整 v2（或旧）schema；失败返回空 dict。"""
    path = proj / "assets" / "resource" / "image" / "treasure" / "treasure_rois.json"
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _load_roi_templates(proj: Path) -> dict[str, list[str]]:
    """读取每个 ROI 的模板列表（JSON templates 数组）；缺失时返回空列表（该 ROI 跳过）。

    不提供硬编码兜底：JSON 未给某 ROI 配模板时如实返回空，由 detect 跳过该 ROI，
    避免用默认模板掩盖配置缺失导致阶段误判。"""
    path = proj / "assets" / "resource" / "image" / "treasure" / "treasure_rois.json"
    data: dict = {}
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    stage = data.get("stage") or {}
    out: dict[str, list[str]] = {}
    for key in _ROI_STAGE:
        val = stage.get(key)
        tpls: list[str] = []
        if isinstance(val, dict) and isinstance(val.get("templates"), list):
            tpls = [t for t in val["templates"] if isinstance(t, str) and t]
        out[key] = tpls
    return out


class TreasureStageDetector:
    """巅峰鉴宝自动阶段检测器（无状态，局部 ROI 匹配）"""

    def __init__(self, proj: Path, ocr=None):
        self.tpl_dir = proj / "assets" / "resource" / "image" / "treasure"
        self._tpl_cache: dict[str, np.ndarray | None] = {}
        self.ROI = _load_rois(proj)       # ← 从调试台 JSON 读取 ROI 区域（仅 stage 运行时）
        self.ROI_TPL = _load_roi_templates(proj)  # ← 从调试台 JSON 读取每个 stage ROI 的模板列表
        self.schema = _load_schema(proj)  # 完整 v2（或旧）schema，供回合小字等扩展读取
        self._weak_alert_ts: dict[str, float] = {}
        # 回合小字 OCR：识别不到回合号（横幅未命中）时激活一次，用 OCR 读「第N回合」
        # 文字提取回合数。引擎懒加载、失败自动降级，detector 自身不持有也不初始化引擎。
        self._ocr = ocr
        # 横幅权威回合号：仅由 round_big_banner 模板命中时更新（1~5 识别率 100%）。
        # 标准回合（<5）内 smart_bid_btn 命中用此值，不碰小字——小字比横幅早 1 帧变号，
        # 会提前切回合导致上一回合尾帧报价（如第 4 槽）被误标成新回合（丢失+污染）。
        self._last_round: int | None = None
        # 附加回合小字兜底开关：默认关闭，小字完全不参与回合号判定（标准回合 1~5 由横幅
        # 100% 覆盖）。仅当「第 5 回合 4 人报价读全 + 第一名=第二名（平局）+ 未进入结算」时，
        # 由上层 treasure_module 置 True，此时横幅模板（只有 round1~5）识别不到第 6+ 回合，
        # 需要小字 OCR 兜底识别真实回合号（>5，stage 名 clamp 到第 5 回合，raw_r 保留原始号）。
        self._allow_label_fallback: bool = False
        # 最近一次 detect() 命中的 ROI key（"daily_high_banner" / "egg_reward_title" / 其它 /
        # None=无命中）。合并后的「结算弹窗」阶段靠它区分具体弹窗（今日最高/彩蛋 vs 等级提升盲点）。
        self._last_hit_roi_key: str | None = None

    # ---------------- 对外主接口 ----------------
    def detect(self, frame_rgb: np.ndarray) -> tuple[str | None, int | None]:
        H, W = frame_rgb.shape[:2]
        gray = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2GRAY)

        # 按优先级从高到低扫描每个 stage ROI；同一 ROI 内所有模板一起匹配取最高分
        for roi_key in sorted(_ROI_STAGE, key=lambda k: -_ROI_STAGE[k]["priority"]):
            rect = self.ROI.get(roi_key)
            if not rect:
                continue
            st = _ROI_STAGE[roi_key]
            templates = self.ROI_TPL.get(roi_key) or []
            if not templates:
                continue

            # 聚合匹配：同 ROI 多模板都跑一遍，取最高分（及次高分）
            best = None  # (score, template_name)
            second_score = 0.0
            for t in templates:
                gt = self._load_gray(Path(t).stem)
                if gt is None:
                    continue
                s = self._match_local(gray, gt, rect[0], rect[1], rect[2], rect[3], W, H)
                if best is None or s > best[0]:
                    second_score = best[0] if best else 0.0
                    best = (s, t)
                elif s > second_score:
                    second_score = s
            if best is None:
                continue
            score, tpl_name = best

            # 先解析该 ROI+命中模板的实际阈值（优先级同上），供弱匹配告警 + 命中判定共享
            per_tpl_th = st.get("thresholds", {}).get(Path(tpl_name).stem)
            if per_tpl_th is not None:
                threshold = float(per_tpl_th)
            else:
                roi_th = _roi_thresholds.get(roi_key) if _roi_thresholds else None
                threshold = roi_th if isinstance(roi_th, float) else MATCH_THRESHOLD

            # 弱匹配：[threshold - 0.25, threshold) 区间，便于发现「差一点命中」但低于 threshold 的情况
            weak_low = max(0.50, threshold - 0.25)
            if weak_low <= score < threshold and not self._weak_alerted(roi_key):
                logger.log(
                    f"[鉴宝检测器] 弱匹配 {Path(tpl_name).stem} @ {roi_key} "
                    f"score={score:.3f}（阈 {threshold:.3f}），可能是 ROI 偏移或模板过期",
                    "DEBUG",
                )
            margin = st.get("margin", 0.0)
            if margin > 0.0:
                if score < threshold or (score - second_score) < margin:
                    continue
            elif score < threshold:
                continue

            # 命中
            self._last_hit_roi_key = roi_key
            if st["stage"] == "__round_phase__":
                if st.get("round_from_template"):
                    # round_big_banner（回合横幅，1~5 识别率 100%）= 回合号权威来源，
                    # 模板命中的回合号即时生效并更新 _last_round。
                    r = self._round_from_template(tpl_name)
                    if r is not None:
                        self._last_round = r
                        return (f"第{r}回合出价", r)
                    return (None, None)
                # smart_bid_btn（出价面板开，priority 80 先于横幅检查）：标准回合用
                # _last_round（横幅上次结果），不碰小字——小字比横幅早 1 帧变号，
                # R2→R3 切换时小字先读 3 会把 R2 尾帧第 4 槽报价误标成 R3（丢失+污染）。
                if self._last_round is not None and self._last_round < 5:
                    return (f"第{self._last_round}回合出价", self._last_round)
                # 附加回合（第 5 回合平局追加，_allow_label_fallback 由上层激活）：
                # 横幅模板只有 1~5，识别不到 6+，用小字 OCR 读真实回合号。
                # stage 名 clamp 到第 5 回合（在 STAGE_ORDER 内），raw_r 保留原始号
                # 让上层感知附加回合切换（转场期重置/新 epoch）。
                if self._allow_label_fallback:
                    r = self._detect_round_full(frame_rgb, W, H)
                    if r is not None:
                        return (f"第{min(r, 5)}回合出价", r)
                return (None, None)
            return (st["stage"], None)

        # 兜底：横幅/smart 都没命中。标准回合（_last_round < 5）属转场/画面抖动，
        # 返回 None 保持现状（等横幅出现，避免小字提前切号）。
        # 附加回合（_allow_label_fallback=True）时横幅识别不到第 6+ 回合，用小字兜底。
        if self._allow_label_fallback:
            r = self._detect_round_full(frame_rgb, W, H)
            if r is not None:
                return (f"第{min(r, 5)}回合出价", r)
        self._last_hit_roi_key = None  # 无命中：弹窗链阶段区分"等级提升盲点"
        return (None, None)

    def banner_result(self, frame_rgb: np.ndarray) -> str | None:
        """中标结算阶段：判断竞拍结果横幅命中的是「中标」还是「未中标」模板。

        返回 "win" | "fail" | None（ROI/模板未配置、都不命中）。
        仅在 result_banner ROI 内对 win/fail 两个模板匹配取最高分，阈值与 detect()
        保持一致（win 模板有单独放宽阈值 0.60）。供 treasure_module 记录落盘字段。
        """
        rect = self.ROI.get("result_banner")
        tpls = self.ROI_TPL.get("result_banner") or []
        if not rect or not tpls:
            return None
        H, W = frame_rgb.shape[:2]
        gray = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2GRAY)
        best_name: str | None = None
        best_score = -1.0
        for t in tpls:
            gt = self._load_gray(Path(t).stem)
            if gt is None:
                continue
            s = self._match_local(gray, gt, rect[0], rect[1], rect[2], rect[3], W, H)
            if s > best_score:
                best_score = s
                best_name = t
        if best_name is None:
            return None
        # 阈值解析与 detect() 一致：优先 per-模板（win 0.60），再 ROI 通用，最后全局
        per_tpl_th = _ROI_STAGE["result_banner"].get("thresholds", {}).get(Path(best_name).stem)
        if per_tpl_th is not None:
            threshold = float(per_tpl_th)
        else:
            roi_th = _roi_thresholds.get("result_banner") if _roi_thresholds else None
            threshold = roi_th if isinstance(roi_th, float) else MATCH_THRESHOLD
        if best_score < threshold:
            return None
        if "win" in best_name:
            return "win"
        if "fail" in best_name:
            return "fail"
        return None

    @staticmethod
    def _round_from_template(tpl_name: str) -> int | None:
        """从模板文件名解析回合号，如 round3_banner.png → 3。"""
        m = _ROUND_RE.search(tpl_name)
        return int(m.group(1)) if m else None

    # ---------------- 内部工具 ----------------
    @staticmethod
    def _round_no_from_text(text: str) -> int | None:
        """从 OCR 文本提取回合号：支持「第1回合」「Round 2」「1/3」等常见写法。
        回合小字区域只有 1 个字+数字，命中任意数字即返回；允许 1~9（附加回合平局追加可到 6+，
        stage 名 clamp 由调用方做），0/两位数/无数字视为噪声返回 None。"""
        if not text:
            return None
        m = re.search(r"(\d+)", text)
        if not m:
            return None
        r = int(m.group(1))
        return r if 1 <= r <= 9 else None

    def _round_label_rect(self) -> tuple[float, float, float, float] | None:
        """回合小字识别区域：优先 ocr.round_label_area（v2），兼容旧 round_labels 段。
        未配置时返回 None（由 _detect_round_full 跳过该识别）。"""
        if not isinstance(self.schema, dict):
            return None
        for seg_key in ("ocr", "round_labels"):
            seg = self.schema.get(seg_key)
            if not isinstance(seg, dict):
                continue
            rla = seg.get("round_label_area")
            if isinstance(rla, dict) and isinstance(rla.get("rect"), list) and len(rla["rect"]) == 4:
                r4 = rla["rect"]
                return (float(r4[0]), float(r4[1]), float(r4[2]), float(r4[3]))
        return None

    def _weak_alerted(self, roi_key: str) -> bool:
        """弱匹配告警节流：同一 ROI 最多每 30 秒告警一次，避免刷屏。"""
        now = time.time()
        last = self._weak_alert_ts.get(roi_key, 0.0)
        if now - last < 30.0:
            return True
        self._weak_alert_ts[roi_key] = now
        return False

    def _load_gray(self, name_no_ext: str) -> np.ndarray | None:
        if name_no_ext in self._tpl_cache:
            return self._tpl_cache[name_no_ext]
        path = self.tpl_dir / f"{name_no_ext}.png"
        if not path.exists():
            self._tpl_cache[name_no_ext] = None
            return None
        img = cv2.imread(str(path))
        if img is None:
            return None
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        self._tpl_cache[name_no_ext] = g
        return g

    @staticmethod
    def _crop(gray_big, x1n, y1n, x2n, y2n, W, H):
        x1, y1 = max(0, int(x1n * W)), max(0, int(y1n * H))
        x2, y2 = min(W, int(x2n * W)), min(H, int(y2n * H))
        if x2 <= x1 or y2 <= y1:
            return None
        return gray_big[y1:y2, x1:x2]

    @classmethod
    def _match_local(cls, gray_big, gray_tpl, x1n, y1n, x2n, y2n, W, H) -> float:
        crop = cls._crop(gray_big, x1n, y1n, x2n, y2n, W, H)
        if crop is None:
            return 0.0
        th, tw = gray_tpl.shape[:2]
        ch, cw = crop.shape[:2]
        if th > ch or tw > cw:
            now = time.time()
            if now - getattr(cls, "_last_size_warn", 0.0) > 10.0:
                cls._last_size_warn = now
                logger.log(
                    f"[鉴宝检测器] ROI 尺寸不足 (crop {cw}×{ch} < tpl {tw}×{th})，"
                    f"该 ROI 永远无法命中，请用调试台调大",
                    "WARNING",
                )
            return 0.0
        try:
            res = cv2.matchTemplate(crop, gray_tpl, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(res)
            return float(max_val)
        except cv2.error as e:
            logger.log(f"[鉴宝检测器] matchTemplate 失败: {e}", "WARNING")
            return 0.0

    def _detect_round_full(self, frame_rgb, W, H) -> int | None:
        """识别不到回合（横幅未命中）时激活一次：OCR 读回合小字区域 → 提取回合号。

        仅当注入的 OCR 引擎可用时执行；引擎未注入/加载失败/文本无数字均返回 None，
        由调用方（detect）走兜底，不抛异常、不阻塞主流程。
        """
        if self._ocr is None:
            return None
        rect = self._round_label_rect()
        if rect is None:
            return None
        info = self._ocr.recognize_single(frame_rgb, rect)
        if info is None:
            return None
        text = info.get("text") or ""
        return self._round_no_from_text(text)
