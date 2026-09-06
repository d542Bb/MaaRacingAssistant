#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P1 实机 trace 覆盖审计（采集达标判定，纯标准库、只读）。

读取 debug/treasure/<session>/trace.jsonl（或 --trace 指定文件）中的
`event=decision` 行，统计决策覆盖矩阵，判定"攒的 trace 够不够做
P1d 真实数据回归"。

覆盖分两级：
- 必选：正常游玩完整几轮必然出现的阶段/分支；缺任何一项 → 不达标
- 可选：难以自然触发或已因活动改版退化的分支（如彩蛋弹窗、fatal 终止）；
  缺失仅提示，不阻断

同时汇总 dual_track_equal：运行期实时双轨比对出现 False = 等价性已破，
直接不达标（比离线回放更强的失败信号）。

退出码：0 覆盖达标；1 有缺口 / 双轨出现 False；2 数据/环境错误。
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

from maaracing_assistant.core.paths import debug_dir  # noqa: E402

DEFAULT_ROOT = debug_dir() / "treasure"

STAGES_REQUIRED = (
    "hall", "activity", "session", "appraiser", "bid",
    "settle", "popup", "matching",
)
SETTLE_RETRY_FRAMES = 10  # 与 tuning.policy.settle_skip_retry_frames 对齐


@dataclass
class CoverageReport:
    sessions: list[str] = field(default_factory=list)
    decision_rows: int = 0
    seen: set[str] = field(default_factory=set)
    dual_track_false: list[tuple[str, int]] = field(default_factory=list)
    dual_track_none: int = 0
    counts: dict[str, int] = field(default_factory=dict)

    def mark(self, key: str) -> None:
        self.seen.add(key)
        self.counts[key] = self.counts.get(key, 0) + 1


def _branch_keys(fp: dict[str, Any]) -> list[str]:
    """单帧 facts_projection → 命中的覆盖项。"""
    keys: list[str] = []
    stage = fp.get("stage")
    if stage:
        keys.append(f"stage={stage}")
    popup = fp.get("popup_kind")
    cooldown = fp.get("cooldown") or 0
    if stage == "settle":
        income = fp.get("settle_income")
        clicked = bool(fp.get("clicked_once"))
        elapsed = fp.get("retry_elapsed")
        if not clicked and income is None:
            keys.append("settle_first(跳过动画首点)")
        elif income is not None:
            keys.append("settle_ready(数据齐→真领取)")
        elif elapsed is not None and elapsed >= SETTLE_RETRY_FRAMES:
            if (fp.get("retry_count") or 0) >= 3:
                keys.append("settle_fatal(重试耗尽)")  # 可选：正常不触发
            else:
                keys.append("settle_retry_window(点击无响应重试)")
        else:
            keys.append("settle_waiting(跳过后等OCR)")
    elif stage == "popup":
        if popup == "daily_high_banner":
            keys.append("popup_daily_high(今日最高)")
        elif popup == "egg_reward_title" or fp.get("egg_reading"):
            keys.append("popup_egg(彩蛋)")  # 可选：改版后可能不再出现
        else:
            keys.append("popup_blind(等级提升/盲点)")
    elif stage == "bid":
        dec = fp.get("bidding_decision")
        keys.append("bid_action(出价决策)" if dec and dec.get("key") else "bid_waiting(等待)")
    elif stage == "session":
        dec = fp.get("session_decision")
        keys.append("session_action(开始匹配)" if dec and dec.get("key") else "session_waiting")
    elif stage == "appraiser":
        dec = fp.get("appraiser_decision")
        keys.append("appraiser_action(选师)" if dec and dec.get("key") else "appraiser_waiting")
    if cooldown > 0:
        keys.append("cooldown_any(冷却期)")
        if stage != "popup":
            keys.append("cooldown_cross_stage(跨阶段冷却)")
    return keys


REQUIRED = [
    *(f"stage={s}" for s in STAGES_REQUIRED),
    "settle_first(跳过动画首点)",
    "settle_ready(数据齐→真领取)",
    "settle_waiting(跳过后等OCR)",
    "popup_daily_high(今日最高)",
    "popup_blind(等级提升/盲点)",
    "bid_action(出价决策)",
    "bid_waiting(等待)",
    "session_action(开始匹配)",
    "appraiser_action(选师)",
    "cooldown_any(冷却期)",
    "cooldown_cross_stage(跨阶段冷却)",
]
OPTIONAL = [
    "popup_egg(彩蛋)",
    "settle_fatal(重试耗尽)",
    # 防御性计时分支：仅在点击成功后 10 帧收入仍未读出时触发（正常 OCR 1~3 帧读出），
    # 正常游玩难以自然命中；等价性由单测 settle 变体矩阵 + 合成回放覆盖。
    "settle_retry_window(点击无响应重试)",
    # 游戏改版（2026-08）后中标结算页 UI 变化，现有锚点最高分仅 ~0.46（改版前阈值 0.8）
    # → 检测器在该页全盲、阶段不产出；等待锚点重新截图校准后恢复必选。
    "stage=auction_result",
]


def iter_trace_files(root: Path, traces: list[Path]) -> Iterable[tuple[str, Path]]:
    if traces:
        for p in traces:
            yield p.parent.name if p.parent != root else p.name, p
        return
    if not root.is_dir():
        return
    for session in sorted(p for p in root.iterdir() if p.is_dir()):
        t = session / "trace.jsonl"
        if t.is_file():
            yield session.name, t


def audit(roots: list[Path]) -> CoverageReport:
    report = CoverageReport()
    for root in roots:
        for name, path in iter_trace_files(root, []):
            report.sessions.append(f"{name}({path.parent})" if path.parent != path else name)
            with open(path, "r", encoding="utf-8") as f:
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
                    report.decision_rows += 1
                    snap = row.get("decision_snapshot") or {}
                    fp = snap.get("facts_projection") or {}
                    for key in _branch_keys(fp):
                        report.mark(key)
                    eq = row.get("dual_track_equal")
                    if eq is False:
                        report.dual_track_false.append(
                            (name, int(row.get("frame", 0)))
                        )
                    elif eq is None:
                        report.dual_track_none += 1
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="P1 实机 trace 覆盖审计")
    parser.add_argument(
        "--root", type=Path, default=None,
        help="debug 会话根目录（默认 %%APPDATA%%/MaaRacingAssistant/debug/treasure）",
    )
    args = parser.parse_args()
    roots = [args.root] if args.root else [DEFAULT_ROOT]

    report = audit(roots)
    if not report.sessions:
        print(f"[coverage] 未找到任何 trace.jsonl（root={roots[0]}）。"
              f"请先实机运行鉴宝模块（trace 常开自动落盘）。", file=sys.stderr)
        return 2
    if report.decision_rows == 0:
        print("[coverage] trace 中没有 event=decision 行——"
              "这些 trace 生成于 P1 之前，请用当前版本重新实机采集。", file=sys.stderr)
        return 2

    missing = [k for k in REQUIRED if k not in report.seen]
    opt_missing = [k for k in OPTIONAL if k not in report.seen]

    lines = [
        f"[coverage] 会话 {len(report.sessions)} 个，decision 帧 {report.decision_rows}：",
        f"  {', '.join(report.sessions)}",
        "",
        f"  必选覆盖 {len(REQUIRED) - len(missing)}/{len(REQUIRED)}，"
        f"可选覆盖 {len(OPTIONAL) - len(opt_missing)}/{len(OPTIONAL)}",
    ]
    for k in REQUIRED:
        mark = "✓" if k in report.seen else "✗"
        lines.append(f"    {mark} {k}" + (f"  ×{report.counts[k]}" if k in report.counts else "  [缺失]"))
    for k in OPTIONAL:
        mark = "✓" if k in report.seen else "○"
        note = "" if k in report.seen else "  [缺失-可选，不阻断]"
        lines.append(f"    {mark} {k}" + (f"  ×{report.counts[k]}" if k in report.counts else note))

    lines.append("")
    if report.dual_track_false:
        lines.append(f"  ✗ dual_track_equal=False 共 {len(report.dual_track_false)} 帧："
                     f"{report.dual_track_false[:10]}")
        lines.append("    运行期双轨已发现决策不一致——禁止进入 P1e，先修复再采集。")
    else:
        lines.append(f"  ✓ dual_track_equal 全程 True"
                     f"（{report.decision_rows - report.dual_track_none} 帧比对，"
                     f"{report.dual_track_none} 帧 v2 回退无双轨）")

    print("\n".join(lines))
    if report.dual_track_false or missing:
        print("\n[coverage] 不达标："
              + ("双轨不一致；" if report.dual_track_false else "")
              + (f"缺必选项 {missing}" if missing else ""))
        return 1
    print("\n[coverage] 达标：可执行 python tools/navkit/replay_policy.py --trace <trace.jsonl> 做离线复核。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
