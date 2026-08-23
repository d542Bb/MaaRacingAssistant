#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从宝藏局测试会话的 raw 帧里，按归一化坐标裁剪出模板小图，
保存到 plugins/treasure/resources/，供 TreasureStageDetector 做 cv2.matchTemplate。

锚点选择原则（唯一 + 稳定 + 不受文字大小影响）：
  1. 独有的文字大标题（如「第1回合」「竞拍失败」「前往鉴宝」「领取」）
  2. 独有的颜色形状（红色大按钮位置 + 大致内容）
  3. 只裁剪核心像素，不裁太多背景 → 减少假阳

使用：python tools/debug/extract_treasure_templates.py
"""
from __future__ import annotations

from pathlib import Path
import cv2
import numpy as np

PROJ = Path(__file__).resolve().parent.parent.parent
SESSION_DIR = PROJ / "debug" / "treasure" / "20260812_141155"
RAW_DIR = SESSION_DIR / "raw"
OUT_DIR = PROJ / "maaracing_assistant" / "plugins" / "treasure" / "resources"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def crop_norm(frame_path: Path, box_norm: tuple[float, float, float, float]) -> np.ndarray:
    """box_norm = (x1, y1, x2, y2) 归一化坐标"""
    img = cv2.imread(str(frame_path))
    if img is None:
        raise FileNotFoundError(frame_path)
    H, W = img.shape[:2]
    x1, y1, x2, y2 = [int(v * (W if i % 2 == 0 else H)) for i, v in enumerate(box_norm)]
    return img[y1:y2, x1:x2]


def save(tpl: np.ndarray, name: str) -> None:
    out_path = OUT_DIR / f"{name}.png"
    cv2.imwrite(str(out_path), tpl)
    print(f"✓ {out_path.relative_to(PROJ)}  {tpl.shape[1]}x{tpl.shape[0]}")


# ==================== 裁剪清单 ====================

# 1. 活动主页面 —— 右下角红色按钮「前往鉴宝」
save(
    crop_norm(RAW_DIR / "0007_raw.png", (0.660, 0.810, 0.870, 0.895)),
    "act_goto_appraise_btn",
)

# 3. 鉴宝大厅三岛地图 —— 左下角「今日已参与次数」卡片
save(
    crop_norm(RAW_DIR / "0011_raw.png", (0.045, 0.750, 0.170, 0.830)),
    "hall_peak_appraise_card",
)

# 4. 选场页 —— 顶部场次选择卡片区（今日参与次数 3/50 + 实习/专家/大锻银币市场）
save(
    crop_norm(RAW_DIR / "0016_raw.png", (0.078, 0.028, 0.425, 0.205)),
    "hall_session_cards",
)

# 5. 选择鉴宝师 —— 顶部标题「选择鉴宝师」
save(
    crop_norm(RAW_DIR / "0019_raw.png", (0.400, 0.140, 0.595, 0.195)),
    "select_appraiser_title",
)

# 5b. 选择鉴宝师 —— 底部红色「确认」按钮（也可兼做其他确认）
save(
    crop_norm(RAW_DIR / "0019_raw.png", (0.425, 0.870, 0.575, 0.950)),
    "confirm_red_btn",
)

# 6. 第1回合等待出价 —— 中央大横条「第1回合」
save(
    crop_norm(RAW_DIR / "0036_raw.png", (0.425, 0.440, 0.575, 0.560)),
    "round1_banner",
)

# 7. 出价弹框 —— 右下角按钮「智能出价」（最稳定的弹框标识）
save(
    crop_norm(RAW_DIR / "0048_raw.png", (0.670, 0.780, 0.770, 0.850)),
    "bid_smart_btn",
)

# 8. 出价弹框 —— 红色按钮「出价」（左下）
save(
    crop_norm(RAW_DIR / "0048_raw.png", (0.360, 0.775, 0.505, 0.900)),
    "bid_confirm_red_btn",
)

# 9. 展示所有人出价结果 —— 白色金额显示（典型：395,100）—— 这局帧0100展示所有人出价
save(
    crop_norm(RAW_DIR / "0100_raw.png", (0.155, 0.345, 0.265, 0.405)),
    "bid_result_amount_box",
)

# 10. 第2回合横幅
save(
    crop_norm(RAW_DIR / "0073_raw.png", (0.425, 0.440, 0.575, 0.560)),
    "round2_banner",
)

# 11. 第3回合横幅  —— 帧101没有「第3回合」大横条（那局已经在等待出价阶段了），
#     我们用帧067/073之间的数据推测 —— 其实可以直接合成，
#     但更稳妥：用帧0100里右上角的「第2回合」小圆角小字代替（更通用）。
#     先记录 round_label_small（右上角小字「第N回合」）
save(
    crop_norm(RAW_DIR / "0067_raw.png", (0.445, 0.155, 0.510, 0.190)),
    "round_label_small",
)

# 12. 第4回合小字 —— 帧182右上角「第4回合」
save(
    crop_norm(RAW_DIR / "0182_raw.png", (0.445, 0.155, 0.510, 0.190)),
    "round4_label_small",
)

# 13. 第5回合小字 —— 帧213右上角「第5回合」
save(
    crop_norm(RAW_DIR / "0213_raw.png", (0.445, 0.155, 0.510, 0.190)),
    "round5_label_small",
)

# 14. 结果页 —— 大横条「竞拍失败」
save(
    crop_norm(RAW_DIR / "0220_raw.png", (0.310, 0.430, 0.690, 0.575)),
    "result_auction_fail_banner",
)

# 15. 结果领取页 —— 右侧大标题「最终竞拍价格」（结算页独有的）
save(
    crop_norm(RAW_DIR / "0230_raw.png", (0.680, 0.135, 0.830, 0.190)),
    "settle_final_price_title",
)

# 16. 结算页 —— 右下红色「领取」按钮
save(
    crop_norm(RAW_DIR / "0230_raw.png", (0.680, 0.865, 0.835, 0.955)),
    "settle_collect_red_btn",
)

print("\n✅ 所有模板已裁剪完成，保存到:", OUT_DIR.relative_to(PROJ))
