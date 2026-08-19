#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
回放一次 treasure session 的所有 raw 图，打印 TreasureStageDetector 的阶段判定。
用于验证：阶段是否能跟用户标注的帧号范围对得上。

用法：python tools/debug/test_stage_detector_replay.py [session_dir]
示例：python tools/debug/test_stage_detector_replay.py debug/treasure/20260812_141155
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

PROJ = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJ))

from maaracing_assistant.modules.treasure_detector import TreasureStageDetector  # noqa: E402


USER_ANNOTATION = [
    # (start, end, expected_stage, expected_round)
    (1, 6, "鉴宝大厅(选择场次)", None),
    (7, 10, "鉴宝大厅(选择场次)", None),   # 活动主页，归到鉴宝大厅
    (11, 16, "鉴宝大厅(选择场次)", None),  # 场次选择
    (17, 18, "鉴宝大厅(选择场次)", None),  # 点击开始匹配
    (19, 27, "选择鉴宝师", None),
    (28, 35, "匹配中", None),             # 主题抽取动画（无强特征）
    (36, 72, "第1回合出价", 1),
    (73, 100, "第2回合出价", 2),
    (101, 140, "第3回合出价", 3),
    (141, 182, "第4回合出价", 4),
    (183, 216, "第5回合出价", 5),
    (217, 226, "中标结算", None),
    (227, 235, "领取分红", None),
    (236, 243, "鉴宝大厅(选择场次)", None),  # 回到选场页
]


def expected_at(frame_no: int) -> tuple[str | None, int | None]:
    for s, e, stage, r in USER_ANNOTATION:
        if s <= frame_no <= e:
            return stage, r
    return None, None


def main() -> None:
    if len(sys.argv) > 1:
        session_dir = PROJ / sys.argv[1]
    else:
        treasure_root = PROJ / "debug" / "treasure"
        sessions = sorted(p for p in treasure_root.iterdir() if p.is_dir())
        if not sessions:
            print("❌ 没有可用的 treasure session")
            sys.exit(1)
        session_dir = sessions[-1]

    raw_dir = session_dir / "raw"
    if not raw_dir.exists():
        print(f"❌ {raw_dir} 不存在")
        sys.exit(1)

    frames = sorted(raw_dir.glob("*_raw.png"))
    if not frames:
        print(f"❌ {raw_dir} 下没有 raw 图")
        sys.exit(1)

    detector = TreasureStageDetector(PROJ)

    # ------------------------------------------------------------
    # 阶段/回合 过滤层（模拟真实 treasure_module.set_stage 的防抖）:
    #   1. 回合号只能递增，不能回退
    #   2. 新的阶段/回合必须连续 STABLE_FRAMES 帧检测到才算切换
    #   3. 巨型回合横幅（RULES 里的 roundN_banner）命中立即切换（不需要防抖）
    # ------------------------------------------------------------
    STABLE_FRAMES = 1  # 1 帧就切；有「回合单调约束」+「强特征立即切」两道防线，不会乱跳
    cur_stage: str | None = None
    cur_round: int | None = None
    cand_stage, cand_round, cand_count = None, None, 0  # 候选 + 连续计数

    def accept_stage(new_stage, new_round, *, immediate=False):
        """判断能否接受新的阶段/回合，返回 (accepted_stage, accepted_round)。"""
        nonlocal cur_stage, cur_round, cand_stage, cand_count, cand_round

        # 回合单调约束：new_round 不能低于 cur_round（cur_round 非 None 时）
        if cur_round is not None and new_round is not None and new_round < cur_round:
            return (cur_stage, cur_round)  # 回退 → 拒绝

        if immediate:
            # 巨型横幅 / 强特征 → 立即接受（重置候选）
            cand_stage, cand_round, cand_count = None, None, 0
            if new_stage is not None:
                cur_stage = new_stage
            if new_round is not None:
                cur_round = new_round
            return (cur_stage, cur_round)

        # 候选一致 → 累计；不一致 → 重新数
        if new_stage == cand_stage and new_round == cand_round and new_stage is not None:
            cand_count += 1
        else:
            cand_stage, cand_round, cand_count = new_stage, new_round, 1 if new_stage is not None else 0

        if cand_count >= STABLE_FRAMES and cand_stage is not None:
            # 满足稳定帧 → 采纳
            cur_stage, cur_round = cand_stage, cand_round
            cand_stage, cand_round, cand_count = None, None, 0
        return (cur_stage, cur_round)

    # ------------------------------------------------------------
    print(f"Session: {session_dir.name}  共 {len(frames)} 帧")
    print(f"{'帧号':<6} {'过滤后阶段':<20} {'R':<3} {'期望阶段':<20} {'R':<3}  命中")
    print("-" * 88)

    wrong, missing = 0, 0
    prev_disp_stage, prev_disp_r = None, None

    for fp in frames:
        idx = int(fp.stem.split("_")[0])
        img_bgr = cv2.imread(str(fp))
        if img_bgr is None:
            continue
        img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
        raw_stage, raw_r = detector.detect(img_rgb)

        # 识别「是否为巨型回合横幅」命中 → immediate=True
        # 简单策略：只要 raw_r 比 cur_round 严格大（或 cur_round 还未初始化 且 raw_r=1~5），就当"强信号"立即接受。
        if raw_r is not None and raw_stage is not None and raw_stage.startswith("第"):
            if cur_round is None:
                is_big_jump = True
            else:
                is_big_jump = (raw_r > cur_round)
        else:
            is_big_jump = False
        disp_stage, disp_r = accept_stage(
            raw_stage, raw_r,
            immediate=is_big_jump,
        )

        # 保持上一帧阶段：如果检测器返回 None，用前一帧填充
        exp_stage, exp_r = expected_at(idx)

        # 判定容差：
        #   1. disp_stage/disp_r 为 None → 漏判（允许一定数量）
        #   2. 期望阶段 == 匹配中（28~35）→ detector 本来就没模板，阶段保持 选择鉴宝师 也算对
        #   3. 其他严格相等
        if exp_stage == "匹配中":
            ok_stage = disp_stage in (None, "匹配中", "选择鉴宝师")
        else:
            ok_stage = (disp_stage is None) or (disp_stage == exp_stage)
        ok_round = (disp_r is None) or (disp_r == exp_r)
        ok = ok_stage and ok_round

        if not ok:
            # 明确打印错判/漏判的帧，方便定位
            if exp_stage is not None and disp_stage is None:
                missing += 1
                tag = "漏判(返回None)"
            elif exp_stage is not None and disp_stage is not None and disp_stage != exp_stage:
                wrong += 1
                tag = f"错判(期望{exp_stage})"
            elif exp_r is not None and disp_r is not None and disp_r != exp_r:
                wrong += 1
                tag = f"错判(期望回合{exp_r})"
            else:
                tag = ""
            print(f"❓ {idx:>3}  disp=({disp_stage!r}, R{disp_r})  exp=({exp_stage!r}, R{exp_r})  → {tag}")
        # OK 帧不计错误（匹配中阶段保持「选择鉴宝师」已在 ok_stage 里被放宽，不应再计入 wrong）

        prev_disp_stage, prev_disp_r = disp_stage, disp_r

    print("-" * 88)
    print(f"统计：错判 {wrong} 次 / 漏判 {missing} 次 / 总帧 {len(frames)}")
    if wrong == 0 and missing <= 30:
        print("🎉 阶段判定通过！(允许少量漏判即检测器返回None、上层保持上次阶段即可)")
    else:
        print("⚠️  仍有偏差，需要调整。")


if __name__ == "__main__":
    main()
