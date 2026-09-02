# -*- coding: utf-8 -*-
"""P4 迁移等价性单测：racing 断点换算 → StageTracker（线一）。

不 import racing/module.py（会拉入 maa/onnx 等重依赖，且本测试是纯逻辑比对照）。
验证目标：把 racing start() 里手写的
    if start_from and start_from in STAGE_ORDER:
        skip_until = STAGE_ORDER.index(start_from)
    else:
        skip_until = 0; start_from = STAGE_ORDER[0]
替换为 StageTracker.resolve_start_from 后，**对所有输入（合法阶段/None/非法）得出相同
skip_until 与起点阶段** —— 即计划 §十一 的「同输入下观测一致」不变量。

注意关键语义差异必须保留（这是迁移的红线）：
  - 旧逻辑对「非法 start_from」是回退 0 并取起点阶段（不抛错）；
  - resolve_start_from 对非法值抛 InvalidStageError。
  racing 迁移版用「先 in STAGE_ORDER 判断」保护，非法值根本不进 resolve，从而保持
  回退 0 的旧行为。本测试显式对照这一点。
"""
from __future__ import annotations

import pytest

from maaracing_assistant.core.stage_tracker import InvalidStageError, StageTracker

STAGE_ORDER = [
    "归位", "导航一(极速狂飙入口)", "导航二(开始挑战)", "导航三(寻找对手)",
    "商店弹窗处理", "确认上阵", "比赛(Pipeline)",
]


def _legacy_skip_until(start_from: str | None) -> tuple[int, str]:
    """racing 迁移前的手写断点换算（对照基准）。"""
    if start_from and start_from in STAGE_ORDER:
        return STAGE_ORDER.index(start_from), start_from
    return 0, STAGE_ORDER[0]


def _migrated_skip_until(start_from: str | None) -> tuple[int, str]:
    """racing 迁移后的等价换算（先 in 判断保护非法值）。"""
    tracker = StageTracker(STAGE_ORDER)
    if start_from and start_from in STAGE_ORDER:
        return tracker.resolve_start_from(start_from), start_from
    return 0, STAGE_ORDER[0]


class TestBreakpointEquivalence:
    @pytest.mark.parametrize("stage", STAGE_ORDER)
    def test_each_valid_stage_same(self, stage):
        assert _migrated_skip_until(stage) == _legacy_skip_until(stage)

    def test_none_same(self):
        assert _migrated_skip_until(None) == _legacy_skip_until(None) == (0, STAGE_ORDER[0])

    def test_empty_string_same_as_none(self):
        # 迁移前 `start_from` 空串走 else（falsy）→ 回退0；迁移后同样走 else
        assert _migrated_skip_until("") == _legacy_skip_until("")
        assert _migrated_skip_until("") == (0, STAGE_ORDER[0])

    def test_invalid_stage_still_falls_back_zero(self):
        # ★ 红线：非法 start_from 必须保持「回退 0」，不抛错（旧行为）
        resolve = _migrated_skip_until("不存在")
        legacy = _legacy_skip_until("不存在")
        assert resolve == legacy == (0, STAGE_ORDER[0])

    def test_resolve_from_alone_raises_on_invalid(self):
        # 佐证：若不保护直接调 resolve_start_from 会抛错 —— 迁移版靠先 in 判断规避
        tracker = StageTracker(STAGE_ORDER)
        with pytest.raises(InvalidStageError):
            tracker.resolve_start_from("不存在")

    def test_middle_stage_returns_expect_index(self):
        idx, stage = _migrated_skip_until("商店弹窗处理")
        assert idx == STAGE_ORDER.index("商店弹窗处理") == 4
        assert stage == "商店弹窗处理"