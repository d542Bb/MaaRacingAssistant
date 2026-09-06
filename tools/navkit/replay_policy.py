#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P1d 决策双层等价回放（§5.2：DecisionSnapshot 双层 diff=0 闸门）。

两种输入模式：
1. `--trace <trace.jsonl>`：读取运行期落盘的 `event=decision` 行（含
   facts_projection 与 policy 输出），用**当前** PolicyPlan 与 LegacyPolicy
   从同一份 facts 各重放一遍，三方比对（记录 / plan / legacy）。
2. `--frames N`：无历史 trace 时用合成 facts 状态机模拟（stage 轮转 +
   settle/popup 关键状态遍历）做双轨比对，作为机械等价验收。

退出码：0 一致；1 有差异（阻塞合入）；2 数据/环境错误。
纯标准库 + 项目 .venv（Assets 加载需要）；不读个人隐私数据，报告写 stdout。
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

_PROJ = Path(__file__).resolve().parents[2]
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from maaracing_assistant.core.navkit import (  # noqa: E402
    Assets,
    DecisionFacts,
    DecisionSnapshot,
    PolicyPlan,
    compile_plan,
)
from maaracing_assistant.plugins.treasure.policy_legacy import (  # noqa: E402
    TreasureLegacyPolicy,
)

DEFAULT_ASSETS = (
    _PROJ / "maaracing_assistant" / "plugins" / "treasure" / "resources" / "config"
    / "treasure_assets.json"
)


@dataclass
class Diff:
    frame: int
    stage: str | None
    field: str
    recorded: Any
    replay: Any
    message: str = ""


@dataclass
class ReplayReport:
    total: int = 0
    diffs: list[Diff] = field(default_factory=list)
    # 双层统计：decision = 决策四项比对；state_transfer = 转移签名比对
    # （同引擎同输入 → State_{t+1} 相等的机械依据，见 §5.2 状态转移等价）。
    decision_cmp: int = 0
    state_cmp: int = 0
    state_skipped: int = 0
    fatal_frames: int = 0

    @property
    def ok(self) -> bool:
        return not self.diffs

    def text(self) -> str:
        if self.ok:
            lines = [
                f"[replay] {self.total} 帧双层等价：diff=0 ✓",
                f"  decision:       {self.decision_cmp}/{self.total} 帧 key/payload/side_effects/fatal 全等",
                f"  state_transfer: {self.state_cmp}/{self.total} 帧两路转移签名一致"
                f"（跳过 {self.state_skipped} 帧：点击/OCR 域扰动窗口）",
            ]
            if self.fatal_frames:
                lines.append(f"  fatal: {self.fatal_frames} 帧两路同时终止（等价终止路径）")
            return "\n".join(lines)
        lines = [f"[replay] {self.total} 帧中发现 {len(self.diffs)} 处差异："]
        for d in self.diffs[:50]:
            lines.append(
                f"  frame={d.frame} stage={d.stage} {d.field}: "
                f"recorded={d.recorded!r} replay={d.replay!r} {d.message}"
            )
        if len(self.diffs) > 50:
            lines.append(f"  ... 其余 {len(self.diffs) - 50} 处省略")
        return "\n".join(lines)


def build_plan(assets_path: Path) -> tuple[Assets, PolicyPlan, TreasureLegacyPolicy]:
    assets = Assets.load(assets_path, module="treasure")
    if assets.policies is None:
        raise RuntimeError(f"{assets_path} 缺少 policies 段（P1e 约束）")
    plan = compile_plan(assets.policies, assets.anchors)

    def center_of(aid: str) -> tuple[float, float] | None:
        anchor = assets.anchors.get(aid)
        if anchor is None:
            return None
        x1, y1, x2, y2 = anchor.rect.as_list()
        return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)

    legacy = TreasureLegacyPolicy(
        popup_continue_center=center_of("confirm_red_btn") or (0.5, 0.5),
        settle_collect_center=center_of("settle_collect_red_btn"),
        settle_skip_retry_frames=10,
        settle_skip_retry_max=3,
        daily_high_timeout_frames=8,
        egg_ocr_timeout_frames=8,
    )
    return assets, plan, legacy


def _decision_dict(decision) -> dict[str, Any]:
    return {
        "key": decision.key,
        "source": decision.source,
        "payload": dict(decision.payload),
        "side_effects": list(decision.side_effects),
        "fatal": decision.fatal,
    }


def _norm(value: Any) -> Any:
    """trace 序列化会把 tuple 变 list；比较前递归归一化。"""
    if isinstance(value, tuple):
        return [_norm(v) for v in value]
    if isinstance(value, list):
        return [_norm(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _norm(v) for k, v in value.items()}
    return value


def _compare_decisions(
    frame: int,
    stage: str | None,
    snap: dict[str, Any],
    d_plan: Any,
    d_legacy: Any,
    diffs: list[Diff],
) -> None:
    recorded = snap.get("decision") or {}
    recorded_fatal = snap.get("fatal")

    def push(field: str, a: Any, b: Any, message: str = "") -> None:
        if _norm(a) != _norm(b):
            diffs.append(Diff(frame, stage, field, a, b, message))

    push("decision.key", recorded.get("key"), d_plan.key)
    push("decision.payload", recorded.get("payload") or {}, dict(d_plan.payload))
    push("decision.side_effects", recorded.get("side_effects") or [], list(d_plan.side_effects))
    push("decision.fatal", recorded_fatal, d_plan.fatal)
    push("dual_track", d_plan.key, d_legacy.key, "plan 与 legacy 不一致")


def replay_trace(trace_path: Path, plan: PolicyPlan, legacy: TreasureLegacyPolicy) -> ReplayReport:
    report = ReplayReport()
    with open(trace_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("event") != "decision":
                continue
            snap = row.get("decision_snapshot") or {}
            report.total += 1
            facts_proj = snap.get("facts_projection") or {}
            frame = int(row.get("frame", 0))
            stage = facts_proj.get("stage")
            facts = DecisionFacts(values=dict(facts_proj))
            d_plan = plan.decide(facts)
            d_legacy = legacy.decide(facts)
            _compare_decisions(frame, stage, snap, d_plan, d_legacy, report.diffs)
            report.decision_cmp += 1
            # 转移签名：两路 Decision 全等 + 引擎同一份确定性代码（副作用应用器留码）
            # → State_{t+1} 必然相等（§5.2 状态转移等价的机械依据）。
            if _transfer_sig(d_plan) == _transfer_sig(d_legacy):
                report.state_cmp += 1
            else:
                report.diffs.append(
                    Diff(frame, stage, "state_transfer",
                         _transfer_sig(d_legacy), _transfer_sig(d_plan),
                         "两路转移签名不一致（State_{t+1} 将分叉）")
                )
            if row.get("dual_track_equal") is False:
                report.diffs.append(
                    Diff(frame, stage, "dual_track_equal",
                         False, True, "运行期双轨比对已标记不一致")
                )
    return report


def _transfer_sig(decision: Any) -> tuple[str, Any, tuple[str, ...], str | None]:
    """State_{t+1} 转移签名 = (key, payload, side_effects, fatal)。

    引擎的副作用应用器是唯一一份确定性代码（module._apply_decision_effects），
    两路 Decision 该签名全等 → 应用同一状态更新 → State_{t+1} 相等。
    注意：侧输入（点击执行/OCR 投递）属引擎外部，replay 不含（点击/OCR 域扰动窗口）。
    """
    return (decision.key, dict(decision.payload), tuple(decision.side_effects), decision.fatal)


def _synthetic_facts(state: dict[str, Any]) -> DecisionFacts:
    frame = int(state["frame"])
    facts = {
        "stage": state["stage"],
        "popup_kind": state.get("popup_kind"),
        "session_decision": state.get("session_decision"),
        "appraiser_decision": state.get("appraiser_decision"),
        "bidding_decision": state.get("bidding_decision"),
        "settle_income": state.get("settle_income"),
        "clicked_once": state.get("clicked_once", False),
        "retry_count": state.get("retry_count", 0),
        "retry_elapsed": state.get("retry_elapsed"),
        "cooldown": state.get("cooldown", 0),
        "daily_high_score": state.get("daily_high_score"),
        "egg_reading": state.get("egg_reading", False),
        "egg_read_done": state.get("egg_read_done", False),
        "reward_elapsed": state.get("reward_elapsed"),
        "skip_cycle": frame % 3,
        "frame_counter": frame,
    }
    return DecisionFacts(values=facts)


def replay_synthetic(frames: int, plan: PolicyPlan, legacy: TreasureLegacyPolicy) -> ReplayReport:
    """合成状态机：多场景时间轴 + 全局 cooldown 计数器，双轨逐帧双层比对。

    覆盖评审点名的边界：出价多阶段 / settle 重试与超时 / 弹窗（今日最高/彩蛋/
    等级盲点）/ 冷却（全局短路 + 递减 + 跨阶段）/ OCR 与点击执行域扰动窗口。
    以时间为轴的字段（retry_elapsed / reward_elapsed）由 _synthetic_facts 按
    frame 派生，与 module 语义一致。
    """
    report = ReplayReport()
    scenarios = [
        ("hall", 0),
        ("activity", 0),
        ("session", 0),
        ("appraiser", 0),
        ("bid", 0),
        ("settle", 0),
        ("popup", 0),
        ("matching", 0),
        ("auction_result", 0),
    ]
    total_sc = max(1, len(scenarios))
    slot = max(1, frames // total_sc)
    idx = 0
    cooldown = 0
    for frame in range(1, frames + 1):
        sc_name, _ = scenarios[idx % total_sc]
        idx += 1
        local = frame % slot
        state: dict[str, Any] = {"frame": frame, "stage": sc_name}

        if sc_name == "hall":
            cooldown = 0
        elif sc_name == "bid":
            # 出价多阶段：S0 转场（无决策）→ S1 等待 → S2 主按钮 → S3 面板（智能/确认）
            phase = local % 4
            if phase in (0, 1):
                state["bidding_decision"] = None
            elif phase == 2:
                state["bidding_decision"] = {
                    "key": "bid_main_red_btn", "hint": "点出价", "center": (0.5, 0.5),
                }
            else:
                state["bidding_decision"] = {
                    "key": "bid_smart_btn", "hint": "智能出价", "center": (0.5, 0.5),
                    "state": "S3_edit_type",
                }
        elif sc_name == "settle":
            # 时间轴：首点(income 空) → 数据齐(income≥0) → 重试窗口（点击后收入未读出）
            elapsed = local - 2  # local=2 起视为点击后
            state["clicked_once"] = local >= 2
            if local < 2:
                state["settle_income"] = None
            elif local < 6:
                state["settle_income"] = None  # 点击后动画/OCR 窗口
                state["retry_count"] = local // 2
                state["retry_elapsed"] = elapsed if elapsed >= 0 else None
            else:
                state["settle_income"] = 5000
            if local >= 6 and local % 4 == 0:
                cooldown = 3  # 真领取后进入弹窗链冷却
        elif sc_name == "popup":
            kind_cycle = local % 5
            state["popup_kind"] = (
                "daily_high_banner" if kind_cycle == 1 else
                "egg_reward_title" if kind_cycle == 2 else None
            )
            state["daily_high_score"] = 12345 if kind_cycle == 1 and local % 2 == 0 else None
            state["egg_reading"] = kind_cycle == 2
            state["egg_read_done"] = kind_cycle == 2 and local % 2 == 0
            # 超时边界：reward_elapsed 递增跨越 daily_high/egg 的 8 帧阈值
            state["reward_elapsed"] = local % 13
        elif sc_name == "session":
            state["session_decision"] = (
                {"key": "session_start_match_btn", "hint": "开始匹配", "center": (0.5, 0.5)}
                if local % 3 == 0 else None
            )
        elif sc_name == "appraiser":
            state["appraiser_decision"] = (
                {"key": "appraiser_p1_caroline", "hint": "选她", "center": (0.5, 0.5)}
                if local % 3 == 0 else None
            )

        # 全局 cooldown 短路（等价旧 _decide_action 的冷却前置扣减）：跨阶段生效
        if cooldown > 0:
            state["cooldown"] = cooldown
            cooldown -= 1
        else:
            state["cooldown"] = 0
        # 执行域扰动窗口：每 17 帧模拟一次点击提交/OCR 投递（不含引擎副作用，跳过转移签名）
        disturbed = (frame % 17) < 3

        report.total += 1
        facts = _synthetic_facts(state)
        d_plan = plan.decide(facts)
        d_legacy = legacy.decide(facts)
        for field, a, b, msg in (
            ("decision.key", d_legacy.key, d_plan.key, "双轨不一致"),
            ("decision.payload", dict(d_legacy.payload), dict(d_plan.payload), ""),
            ("decision.fatal", d_legacy.fatal, d_plan.fatal, ""),
            ("decision.side_effects", tuple(d_legacy.side_effects), tuple(d_plan.side_effects), ""),
        ):
            if a != b:
                report.diffs.append(Diff(frame, sc_name, field, a, b, msg))
        report.decision_cmp += 1
        if d_plan.fatal is not None:
            report.fatal_frames += 1
        if disturbed:
            report.state_skipped += 1
        elif _transfer_sig(d_plan) == _transfer_sig(d_legacy):
            report.state_cmp += 1
        else:
            report.diffs.append(
                Diff(frame, sc_name, "state_transfer",
                     _transfer_sig(d_legacy), _transfer_sig(d_plan),
                     "两路转移签名不一致（State_{t+1} 将分叉）")
            )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="P1d 决策双层等价回放")
    parser.add_argument("--trace", type=Path, default=None, help="trace.jsonl 路径")
    parser.add_argument("--frames", type=int, default=0, help="无 trace 时合成模拟帧数")
    parser.add_argument("--assets", type=Path, default=DEFAULT_ASSETS, help="v3 资产路径")
    args = parser.parse_args()

    try:
        _, plan, legacy = build_plan(args.assets)
    except Exception as exc:
        print(f"[replay] 环境/数据错误：{exc}", file=sys.stderr)
        return 2

    if args.trace is not None:
        if not args.trace.is_file():
            print(f"[replay] trace 文件不存在：{args.trace}", file=sys.stderr)
            return 2
        report = replay_trace(args.trace, plan, legacy)
    elif args.frames > 0:
        report = replay_synthetic(args.frames, plan, legacy)
    else:
        parser.error("需要 --trace 或 --frames 之一")
        return 2

    print(report.text())
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
