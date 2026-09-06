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
