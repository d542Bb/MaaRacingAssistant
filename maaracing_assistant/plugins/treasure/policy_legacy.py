#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
鉴宝「决策意图路由」的遗留实现（P1b/P1d 双轨重放用，过渡期产物）。

目标：把 module.py 里 `_decide_action` 的**纯决策**部分抽成接口一致的
`PolicyPort` 实现——输入同一份 immutable `DecisionFacts`，输出 `Decision`，
行为与旧代码逐帧等价，**但副作用（cooldown 递减 / settle 重试计数 /
指纹重 arm / raise）全部剥离**（由 module 的应用器统一执行）。

注意：本模块只做等价承载，不做任何新策略。P1d 双轨回归通过它证明
「PolicyEngine 与旧行为一致」；P1e 收敛后本文件随 LegacyPolicy 一起删除。
"""
from __future__ import annotations

from typing import Any, Mapping

from maaracing_assistant.core.navkit import (
    DEFAULT_FALLBACK_HINT,
    DEFAULT_FALLBACK_KEY,
    Decision,
    DecisionFacts,
)

__all__ = ["TreasureLegacyPolicy"]


class TreasureLegacyPolicy:
    """旧 `_decide_action` 的纯决策等价物。

    构造注入旧实现依赖的静态中心与调参常量（双轨重放时由调用方喂同一份值，
    保证两路输入严格一致）。
    """

    def __init__(
        self,
        *,
        popup_continue_center: tuple[float, float] = (0.5, 0.5),
        settle_collect_center: tuple[float, float] | None = None,
        popup_high_continue_key: str = "popup_high_continue",
        popup_reward_continue_key: str = "popup_reward_continue",
        settle_skip_retry_frames: int = 10,
        settle_skip_retry_max: int = 3,
        daily_high_timeout_frames: int = 8,
        egg_ocr_timeout_frames: int = 8,
    ) -> None:
        self._popup_center = popup_continue_center
        self._settle_center = settle_collect_center
        self._high_key = popup_high_continue_key
        self._reward_key = popup_reward_continue_key
        self._retry_frames = int(settle_skip_retry_frames)
        self._retry_max = int(settle_skip_retry_max)
        self._high_timeout = int(daily_high_timeout_frames)
        self._egg_timeout = int(egg_ocr_timeout_frames)

    def decide(self, facts: DecisionFacts) -> Decision:
        stage = facts.get("stage")
        if stage is not None:
            decision = self._stage_decision(stage, facts)
            if decision is not None:
                return decision
        return Decision(
            DEFAULT_FALLBACK_KEY,
            f"{stage or '等待阶段切换'} 中...（等待界面稳定）",
        )

    # ------------------------------------------------------------------
    # 旧 _decide_action 逐分支映射（side effect 见 Decision.side_effects）
    # ------------------------------------------------------------------

    def _stage_decision(self, stage: str, facts: DecisionFacts) -> Decision | None:
        # 弹窗点击冷却：本帧决策前若 cooldown>0 → 不出其它意图。
        # （module 采集 facts 前已完成"结算弹窗阶段感知提前解锁"清零，与旧代码一致）
        cooldown = facts.get("cooldown") or 0
        if cooldown > 0:
            return Decision(
                "popup_click_cooldown",
                f"弹窗点击后冷却（剩 {cooldown - 1} 帧）...",
                side_effects=("popup_cooldown_decr",),
            )

        if stage == "hall":
            return Decision("hall_peak_appraise_card", "进入巅峰鉴宝活动页")
        if stage == "activity":
            return Decision("goto_appraise_btn", "前往鉴宝")

        if stage == "session":
            dec = facts.get("session_decision")
            if dec and isinstance(dec, Mapping) and dec.get("key"):
                return Decision(
                    str(dec["key"]), str(dec.get("hint") or dec["key"]),
                    source="session_decision",
                )
            return Decision("session_waiting", "鉴宝大厅(选择场次)，等待识别场次按钮...")

        if stage == "appraiser":
            dec = facts.get("appraiser_decision")
            if dec and isinstance(dec, Mapping) and dec.get("key"):
                return Decision(
                    str(dec["key"]), str(dec.get("hint") or dec["key"]),
                    source="appraiser_decision",
                )
            return Decision("appraiser_waiting", "选择鉴宝师阶段，等待识别...")

        if stage.startswith("bid"):
            dec = facts.get("bidding_decision")
            if dec and isinstance(dec, Mapping) and dec.get("key"):
                return Decision(
                    str(dec["key"]), str(dec.get("hint") or dec["key"]),
                    source="bidding_decision",
                )
            hint = "等待出价按钮亮起..."
            if isinstance(dec, Mapping) and dec.get("hint"):
                hint = str(dec["hint"])
            return Decision("bid_waiting", hint)

        if stage == "settle":
            return self._settle_decision(facts)

        if stage == "popup":
            return self._popup_decision(facts)

        return None

    def _settle_decision(self, facts: DecisionFacts) -> Decision:
        income = facts.get("settle_income")
        if income is not None:
            return Decision(
                "settle_collect_red_btn",
                f"领取分红（本场收入 {int(income):,}）",
            )
        if not facts.get("clicked_once"):
            return Decision(
                "settle_collect_red_btn",
                "点领取跳过数据动画（之后等 OCR 读完整再准星再指）",
            )
        elapsed = facts.get("retry_elapsed")
        if elapsed is not None and elapsed >= self._retry_frames:
            if (facts.get("retry_count") or 0) >= self._retry_max:
                return Decision(
                    "settle_collect_red_btn",
                    f"跳过动画点击重试 {self._retry_max} 次后仍读不到本场收入"
                    f"（点击疑似始终无响应），终止模块",
                    fatal=(
                        f"领取分红跳过动画点击重试 {self._retry_max} 次仍无响应"
                        f"（本场收入始终未读出），请检查游戏界面/点击方式后重新开始"
                    ),
                )
            payload: dict[str, Any] = {}
            if self._settle_center is not None:
                payload["center"] = self._settle_center
            return Decision(
                "settle_collect_red_btn",
                f"跳过动画点击疑似无响应，第 {(facts.get('retry_count') or 0) + 1} 次重试...",
                payload=payload,
                side_effects=("settle_skip_retry",),
            )
        return Decision("dividend_waiting", "已跳动画，等待 OCR 读本场收入/利润...（数据齐后准星再指领取）")

    def _popup_decision(self, facts: DecisionFacts) -> Decision:
        popup_kind = facts.get("popup_kind")
        elapsed = facts.get("reward_elapsed")
        center: Mapping[str, Any] = {"center": self._popup_center}
        # 今日最高积分上涨
        if popup_kind == "daily_high_banner":
            if facts.get("daily_high_score") is not None:
                return Decision(self._high_key, "今日最高积分上涨 → 点屏幕继续", payload=center)
            if elapsed is not None and elapsed >= self._high_timeout:
                return Decision(self._high_key, "今日最高积分读取超时 → 跳过", payload=center)
            return Decision("popup_waiting", "今日最高积分上涨：等待识别积分...")
        # 奖励结算（彩蛋）
        if facts.get("egg_reading") or popup_kind == "egg_reward_title":
            if facts.get("egg_read_done") or (
                elapsed is not None and elapsed >= self._egg_timeout
            ):
                return Decision(self._reward_key, "彩蛋结算 → 点屏幕继续", payload=center)
            return Decision("popup_waiting", "奖励结算：等待识别彩蛋数量...")
        # 等级提升（无 ROI）或弹窗切换动画：每 3 帧盲点一次
        if (facts.get("skip_cycle") or 0) == 0:
            return Decision(self._high_key, "等级提升弹窗(盲点) → 点屏幕跳过", payload=center)
        return Decision("popup_waiting", "等待弹窗切换...")