# -*- coding: utf-8 -*-
"""BidStrategy V2/V3 出价策略单元测试（pytest 断言版）。

替代原 maaracing_assistant/modules/test_bid_strategy.py 的纯打印冒烟脚本：
原脚本只有 print 无断言，无法作为 CI 通过/失败判定；本测试以真实运行结果为基线，
把决策类型与出价锁定为回归断言，防止后续改动悄悄破坏策略行为。
"""

from __future__ import annotations

from bid_strategy import (
    BidContext,
    BidDecision,
    BidStrategy,
    RoundSnapshot,
    DECISION_OBSERVE,
    DECISION_WIN,
    DECISION_TARGET_SECOND,
    DECISION_PASS,
    BALANCE_UNKNOWN,
)


def snap(round_no, h, our, opp_bids, epoch=1, my_rank=4):
    """构造快照：my_rank=4 表示我方在槽4，对手槽1~3。"""
    return RoundSnapshot(
        epoch=epoch,
        round_no=round_no,
        h=h,
        our_bid=our,
        opponent_bids=tuple(opp_bids),
        opponent_ids=(1, 2, 3),
    )


def ctx(round_no, h_seen, last, balance, our_last=None):
    return BidContext(
        round_no=round_no,
        h_seen=tuple(h_seen),
        last_round=last,
        balance=balance,
        our_last_bid=our_last,
    )


def assert_decision(name, dec: BidDecision, expect_decision: str, expect_price: int):
    assert dec.decision == expect_decision, (
        f"{name}: 期望 decision={expect_decision}，实际={dec.decision}（{dec.reason}）"
    )
    assert dec.price == expect_price, (
        f"{name}: 期望 price={expect_price}，实际={dec.price}（{dec.reason}）"
    )


# ----------------------------------------------------------------------
# R1/R2 观察
# ----------------------------------------------------------------------
def test_r1_observe():
    dec = BidStrategy().decide(ctx(1, (30000,), None, 1000000))
    assert_decision("R1 observe", dec, DECISION_OBSERVE, 30000)


def test_r2_observe():
    dec = BidStrategy().decide(ctx(2, (30000, 40000), None, 1000000))
    assert_decision("R2 observe", dec, DECISION_OBSERVE, 40000)


def test_r1_balance_short():
    # H>余额 → observe 出 min(H,余额)=100000
    dec = BidStrategy().decide(ctx(1, (500000,), None, 100000))
    assert_decision("R1 H>余额", dec, DECISION_OBSERVE, 100000)


# ----------------------------------------------------------------------
# 捡漏 win（无人烧钱：opp_max < V̂）
# ----------------------------------------------------------------------
def test_r4_cool_pick_bargain_becomes_second():
    # H=(30000,35000,40000)，上轮第一=40000 未烧钱 → 捡漏失败转卡第二
    dec = BidStrategy().decide(
        ctx(4, (30000, 35000, 40000),
            snap(3, 40000, 40000, (20000, 30000, 40000)), 1000000)
    )
    assert_decision("R4 冷静局捡漏转卡第二", dec, DECISION_TARGET_SECOND, 36001)


def test_r4_hot_price_pick_bargain_win():
    # 高价冷静局：H max=200000 → V̂=256000, max_win=230400；上轮第一=150000
    dec = BidStrategy().decide(
        ctx(4, (180000, 190000, 200000),
            snap(3, 200000, 150000, (120000, 140000, 150000)), 1000000)
    )
    assert_decision("R4 高价冷静捡漏→win", dec, DECISION_WIN, 209551)


# ----------------------------------------------------------------------
# 卡第二吃分红（有人烧钱：opp_max > V̂）
# ----------------------------------------------------------------------
def test_r4_firefight_clamp_second():
    # 有人烧钱(80000>51200) → 卡第二，挤不下用紧贴价
    dec = BidStrategy().decide(
        ctx(4, (30000, 35000, 40000),
            snap(3, 40000, 30000, (30000, 50000, 80000)), 1000000)
    )
    assert_decision("R4 有人烧钱→卡第二(紧贴)", dec, DECISION_TARGET_SECOND, 48001)


def test_r4_medium_firefight_second():
    dec = BidStrategy().decide(
        ctx(4, (30000, 35000, 40000),
            snap(3, 40000, 20000, (20000, 30000, 60000)), 1000000)
    )
    assert_decision("R4 烧钱中等→卡第二(紧贴)", dec, DECISION_TARGET_SECOND, 36001)


def test_r4_slight_firefight_second():
    dec = BidStrategy().decide(
        ctx(4, (30000, 35000, 40000),
            snap(3, 40000, 20000, (20000, 30000, 52000)), 1000000)
    )
    assert_decision("R4 轻微烧钱→卡第二", dec, DECISION_TARGET_SECOND, 36001)


# ----------------------------------------------------------------------
# 区间空 → pass
# ----------------------------------------------------------------------
def test_r5_snapshot_incomplete_pass():
    # 快照含 0（我方不在 1~3 槽信息的场景下对手报价缺失）→ is_complete=False → pass
    dec = BidStrategy().decide(
        ctx(5, (30000, 35000, 40000),
            snap(4, 40000, 30000, (0, 44000, 45000)), 1000000)
    )
    assert_decision("R5 快照不完整→pass", dec, DECISION_PASS, 0)


def test_r5_three_high_tight_second():
    dec = BidStrategy().decide(
        ctx(5, (30000, 35000, 40000),
            snap(4, 40000, 30000, (41000, 42000, 43000)), 1000000)
    )
    assert_decision("R5 三高→紧贴第二", dec, DECISION_TARGET_SECOND, 41001)


# ----------------------------------------------------------------------
# 预算/边界
# ----------------------------------------------------------------------
def test_r1_budget_short():
    dec = BidStrategy().decide(ctx(1, (500000,), None, 100000))
    assert_decision("R1 预算不足", dec, DECISION_OBSERVE, 100000)


def test_r4_no_snapshot_pass():
    dec = BidStrategy().decide(ctx(4, (30000, 35000, 40000), None, 1000000))
    assert_decision("无快照 R4 → pass", dec, DECISION_PASS, 0)


# ----------------------------------------------------------------------
# 策略模式切换（profit / egg）
# ----------------------------------------------------------------------
def test_profit_hot_cool_becomes_second():
    dec = BidStrategy(mode="profit").decide(
        ctx(4, (180000, 190000, 200000),
            snap(3, 200000, 150000, (120000, 150000, 180000)), 1000000)
    )
    assert_decision("profit R4 冷静高价→卡第二", dec, DECISION_TARGET_SECOND, 135501)


def test_egg_hot_cool_win_egg():
    dec = BidStrategy(mode="egg").decide(
        ctx(4, (180000, 190000, 200000),
            snap(3, 200000, 150000, (120000, 150000, 180000)), 1000000)
    )
    assert_decision("egg R4 冷静高价→拍中搏蛋", dec, DECISION_WIN, 243806)


def test_egg_firefight_cap_second():
    dec = BidStrategy(mode="egg").decide(
        ctx(4, (180000, 190000, 200000),
            snap(3, 200000, 180000, (150000, 220000, 300000)), 1000000)
    )
    assert_decision("egg R4 烧钱过猛→卡第二保底", dec, DECISION_TARGET_SECOND, 166001)


def test_egg_small_cap_buys_within_egg_cap():
    # 小兜底仍能拍中（raw_target=243806 ≤ V̂+cap=257000）→ 按实际行为断言 win
    dec = BidStrategy(mode="egg", risk_cap=1000).decide(
        ctx(4, (180000, 190000, 200000),
            snap(3, 200000, 150000, (120000, 150000, 180000)), 1000000)
    )
    assert_decision("egg R4 小兜底→拍中(在买入上限内)", dec, DECISION_WIN, 243806)


def test_profit_small_cap_firefight_second():
    dec = BidStrategy(risk_cap=1000).decide(
        ctx(4, (30000, 35000, 40000),
            snap(3, 40000, 20000, (20000, 30000, 80000)), 1000000)
    )
    assert_decision("profit R4 小兜底烧钱→卡第二", dec, DECISION_TARGET_SECOND, 38001)


# ----------------------------------------------------------------------
# 余额三态（未知 -1 / 真实 0 / 正常）
# ----------------------------------------------------------------------
def test_balance_zero_pick_bargain_pass():
    # 真实余额 0 → 没钱，捡漏无机会，区间空 → pass
    dec = BidStrategy().decide(
        ctx(4, (180000, 190000, 200000),
            snap(3, 200000, 150000, (120000, 150000, 180000)), 0)
    )
    assert_decision("余额0 R4 捡漏→不出", dec, DECISION_PASS, 0)


def test_balance_zero_firefight_pass():
    dec = BidStrategy().decide(
        ctx(4, (30000, 35000, 40000),
            snap(3, 40000, 20000, (20000, 30000, 80000)), 0)
    )
    assert_decision("余额0 R4 烧钱→pass", dec, DECISION_PASS, 0)


def test_balance_unknown_pick_bargain_second():
    # 余额未知(-1) → 兜底视为充足，捡漏失败转卡第二
    dec = BidStrategy().decide(
        ctx(4, (180000, 190000, 200000),
            snap(3, 200000, 150000, (120000, 150000, 180000)), BALANCE_UNKNOWN)
    )
    assert_decision("余额未知 R4 捡漏→卡第二", dec, DECISION_TARGET_SECOND, 135501)


def test_balance_unknown_firefight_second():
    dec = BidStrategy().decide(
        ctx(4, (30000, 35000, 40000),
            snap(3, 40000, 20000, (20000, 30000, 80000)), BALANCE_UNKNOWN)
    )
    assert_decision("余额未知 R4 烧钱→卡第二", dec, DECISION_TARGET_SECOND, 38001)