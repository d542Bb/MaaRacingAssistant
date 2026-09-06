#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S1 detector 契约测试：DetectResult 兼容性与 R4 模板缓存失效。"""
from __future__ import annotations

import time
from pathlib import Path

import cv2
import numpy as np

from maaracing_assistant.plugins.treasure.detector import DetectResult, TreasureStageDetector


def test_detect_result_keeps_legacy_unpacking():
    result = DetectResult(
        stage="第2回合出价",
        round_no=2,
        scores={"round_big_banner": 0.91},
        hit_anchor="round_big_banner",
        active_used=("round_big_banner",),
        hit_template="round2_banner.png",
        threshold=0.75,
    )
    stage, round_no = result
    assert (stage, round_no) == ("第2回合出价", 2)
    assert result.scores["round_big_banner"] == 0.91
    assert result.hit_anchor == "round_big_banner"


def test_template_cache_reloads_after_file_mtime_or_size_change(tmp_path: Path):
    detector = TreasureStageDetector.__new__(TreasureStageDetector)
    detector.tpl_dir = tmp_path
    detector._tpl_cache = {}
    detector.match_scales = (1.0,)

    path = tmp_path / "sample.png"
    first = np.full((8, 8, 3), 30, dtype=np.uint8)
    cv2.imwrite(str(path), first)
    loaded_first = detector._load_gray("sample")
    assert loaded_first is not None
    assert abs(int(loaded_first.mean()) - 30) <= 1

    # Windows 文件系统的 mtime 分辨率可能较粗，主动等待并改变 size，确保指纹变化。
    time.sleep(0.01)
    second = np.full((11, 8, 3), 200, dtype=np.uint8)
    cv2.imwrite(str(path), second)
    loaded_second = detector._load_gray("sample")
    assert loaded_second is not None
    assert loaded_second.shape == (11, 8)
    assert abs(int(loaded_second.mean()) - 200) <= 1


def test_template_cache_retries_missing_file_when_it_appears(tmp_path: Path):
    detector = TreasureStageDetector.__new__(TreasureStageDetector)
    detector.tpl_dir = tmp_path
    detector._tpl_cache = {}
    detector.match_scales = (1.0,)

    assert detector._load_gray("later") is None
    image = np.full((8, 8, 3), 77, dtype=np.uint8)
    cv2.imwrite(str(tmp_path / "later.png"), image)
    loaded = detector._load_gray("later")
    assert loaded is not None
    assert int(loaded.mean()) == 77


# ---- banner_result 阈值解析回归（2026-09-06 be18268 缩进 bug 修复）----
# 修复前：plan_spec/per_tpl_th/threshold 解析块被误缩进到 `if best_name is None:` 块内
# （块首 return None，后续赋值不可达）→ best_name 非 None 时 `if best_score < threshold:`
# 必抛 UnboundLocalError。本组测试验证修复后 threshold 的**优先级**而非仅"不抛异常"：
#   per-template（win 0.60 / plan template_thresholds）> ROI 通用 > 全局 match_threshold。

import types  # noqa: E402


def _make_banner_detector(plan=None, roi_threshold=None, match_threshold=0.75,
                          tpl_names=("result_auction_win_banner.png",)):
    """构造绕过 __init__ 的 detector，注入 banner_result 所需最小状态。"""
    det = TreasureStageDetector.__new__(TreasureStageDetector)
    det.plan = plan
    det.match_threshold = match_threshold
    det.ROI = {"result_banner": (0.0, 0.0, 1.0, 1.0)}
    det.ROI_TPL = {"result_banner": list(tpl_names)}
    det.match_scales = (1.0,)
    det._tpl_cache = {}
    det.tpl_dir = None  # _match_local 被 mock，不读真实文件
    # banner_result 读取模块级 _roi_thresholds；此处透传注入
    import maaracing_assistant.plugins.treasure.detector as _det_mod
    _det_mod._roi_thresholds = {"result_banner": roi_threshold} if roi_threshold is not None else {}
    return det


class _FakePlanSpec:
    def __init__(self, threshold, template_thresholds):
        self.threshold = threshold
        self.arbitration = {"template_thresholds": template_thresholds}


class _FakePlan:
    def __init__(self, spec_by_key):
        self.spec = spec_by_key


def _banner_score(det, score, monkeypatch):
    """banner_result 匹配分数固定为 score；返回 (返回值, 是否抛异常)。"""
    import cv2 as _cv2
    import numpy as _np

    frame = _np.zeros((100, 160, 3), dtype=_np.uint8)

    def _fake_match(gray_big, gray_tpl, *args):
        return score

    monkeypatch.setattr(det, "_match_local", _fake_match)
    # _load_gray 返回任意非 None 模板（不读磁盘）
    monkeypatch.setattr(det, "_load_gray", lambda name: _np.zeros((8, 8), dtype=_np.uint8))
    raised = None
    try:
        result = det.banner_result(frame)
    except Exception as e:  # noqa: BLE001
        raised = e
    return result, raised


def test_banner_result_win_uses_per_template_threshold(monkeypatch):
    """win 模板：plan template_thresholds 中 win=0.60 → 0.65 命中、0.55 不命中。"""
    plan = _FakePlan({
        "result_banner": _FakePlanSpec(
            threshold=0.90, template_thresholds={"result_auction_win_banner.png": 0.60}),
    })
    det = _make_banner_detector(plan=plan, tpl_names=("result_auction_win_banner.png",))

    hit, raised = _banner_score(det, 0.65, monkeypatch)
    assert raised is None
    assert hit == "win"
    miss, raised = _banner_score(det, 0.55, monkeypatch)
    assert raised is None
    assert miss is None  # 0.55 < 0.60 → 不命中（若误用 ROI 阈值 0.90 则 0.65 也应 miss）


def test_banner_result_per_template_threshold_wins_over_roi(monkeypatch):
    """任意模板：plan template_thresholds 命中时优先于 ROI 通用阈值。"""
    plan = _FakePlan({
        "result_banner": _FakePlanSpec(
            threshold=0.90, template_thresholds={"result_auction_fail_banner.png": 0.55}),
    })
    det = _make_banner_detector(plan=plan, tpl_names=("result_auction_fail_banner.png",))

    hit, raised = _banner_score(det, 0.60, monkeypatch)
    assert raised is None
    assert hit == "fail"  # 0.60 ≥ 0.55（per-template），若误用 ROI 0.90 则 miss
    miss, raised = _banner_score(det, 0.50, monkeypatch)
    assert raised is None
    assert miss is None  # 0.50 < 0.55


def test_banner_result_roi_threshold_when_no_per_template(monkeypatch):
    """无 per-template 覆盖 → 回落 ROI 通用阈值（plan_spec.threshold）。"""
    plan = _FakePlan({
        "result_banner": _FakePlanSpec(
            threshold=0.80, template_thresholds={}),
    })
    det = _make_banner_detector(plan=plan, tpl_names=("result_auction_fail_banner.png",))

    hit, raised = _banner_score(det, 0.85, monkeypatch)
    assert raised is None
    assert hit == "fail"  # 0.85 ≥ 0.80（ROI）
    miss, raised = _banner_score(det, 0.75, monkeypatch)
    assert raised is None
    assert miss is None  # 0.75 < 0.80


def test_banner_result_global_threshold_when_no_roi(monkeypatch):
    """无 per-template 且无 ROI 阈值 → 回落全局 match_threshold。"""
    det = _make_banner_detector(plan=None, roi_threshold=None,
                                match_threshold=0.75,
                                tpl_names=("result_auction_fail_banner.png",))

    hit, raised = _banner_score(det, 0.80, monkeypatch)
    assert raised is None
    assert hit == "fail"  # 0.80 ≥ 0.75（全局）
    miss, raised = _banner_score(det, 0.70, monkeypatch)
    assert raised is None
    assert miss is None  # 0.70 < 0.75


def test_banner_result_no_best_name_returns_none(monkeypatch):
    """所有模板加载失败（best_name 为 None）→ 直接 None，不进入阈值解析。"""
    det = _make_banner_detector(plan=None, roi_threshold=0.75,
                                tpl_names=("result_auction_win_banner.png",))

    def _none_load(name):
        return None

    import numpy as _np
    frame = _np.zeros((100, 160, 3), dtype=_np.uint8)
    monkeypatch.setattr(det, "_load_gray", _none_load)
    monkeypatch.setattr(det, "_match_local", lambda *a: 0.9)
    assert det.banner_result(frame) is None
