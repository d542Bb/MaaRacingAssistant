#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""鉴宝调试诊断脚本：基于真实截图跑分析，不靠推测。

功能：
  1. 读取 raw/ 截图，打印分辨率 → 和模板尺寸对比
  2. 复刻 TreasureStageDetector 的完整匹配流程，打印每个规则分数
  3. 对第 0001.png 做 ROI 可视化标注：把每个规则的搜索区域画框，保存到 debug/
  4. 逐帧对比画面变化，打印：变化像素占比、到底是哪块区域在变
  5. 统计 screen_change 的事件间间隔，验证是否真的有冷却必要

用法：
  python tools/_diagnose_treasure.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

# ---- 项目路径初始化 ----
PROJ = Path(__file__).resolve().parent.parent.parent
SESSION_DIR = PROJ / "debug" / "treasure" / "20260812_183611"
RAW_DIR = SESSION_DIR / "raw"
EVENT_DIR = SESSION_DIR / "event"
TPL_DIR = PROJ / "assets" / "resource" / "image" / "treasure"
OUT_DIR = PROJ / "debug" / "_diagnose_treasure"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ---- 复刻 detector 常量 ----
MATCH_THRESHOLD = 0.75
ROI = {
    "settle_title":       (0.66, 0.12, 0.86, 0.21),
    "result_banner":      (0.30, 0.42, 0.70, 0.58),
    "smart_bid_btn":      (0.64, 0.76, 0.80, 0.87),
    "round_big_banner":   (0.40, 0.42, 0.60, 0.57),
    "appraiser_title":    (0.38, 0.12, 0.62, 0.21),
    "participation_card": (0.03, 0.73, 0.19, 0.85),
    "hall_peak_appraise_card": (0.03, 0.73, 0.19, 0.85),
    "goto_appraise_btn":  (0.64, 0.80, 0.88, 0.90),
    "round_label_area":   (0.38, 0.10, 0.60, 0.20),
}
_RULES_UNIQUE_ROI = [
    ("settle_final_price_title",     "settle_title",       "领取分红"),
    ("result_auction_fail_banner",   "result_banner",      "中标结算"),
    ("bid_smart_btn",                "smart_bid_btn",      "__round_phase__"),
    ("select_appraiser_title",       "appraiser_title",    "选择鉴宝师"),
    ("hall_peak_appraise_card",      "hall_peak_appraise_card", "游戏大厅"),
    ("act_goto_appraise_btn",        "goto_appraise_btn",  "鉴宝大厅(选择场次)"),
]
_ROUND_BANNERS = [(1, "round1_banner"), (2, "round2_banner"), (3, "round3_banner"),
                  (4, "round4_banner"), (5, "round5_banner")]
_ROUND_LABEL_TPLS = [
    (1, "round_label_small"), (2, "round2_label_small"), (3, "round3_label_small"),
    (4, "round4_label_small"), (5, "round5_label_small"),
]


def load_gray(path: Path) -> np.ndarray | None:
    if not path.exists():
        return None
    img = cv2.imread(str(path))
    if img is None:
        return None
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def crop_norm(gray_big, x1n, y1n, x2n, y2n):
    H, W = gray_big.shape[:2]
    x1, y1 = max(0, int(x1n * W)), max(0, int(y1n * H))
    x2, y2 = min(W, int(x2n * W)), min(H, int(y2n * H))
    if x2 <= x1 or y2 <= y1:
        return None, (0, 0, 0, 0)
    return gray_big[y1:y2, x1:x2], (x1, y1, x2, y2)


def match_local(gray_big, gray_tpl, x1n, y1n, x2n, y2n):
    crop, box = crop_norm(gray_big, x1n, y1n, x2n, y2n)
    if crop is None:
        return 0.0, box, None
    th, tw = gray_tpl.shape[:2]
    ch, cw = crop.shape[:2]
    if th > ch or tw > cw:
        return -1.0, box, (tw, th, cw, ch)   # -1 表示 ROI 尺寸不足
    try:
        res = cv2.matchTemplate(crop, gray_tpl, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, _ = cv2.minMaxLoc(res)
        return float(max_val), box, None
    except Exception:
        return -2.0, box, None


# ============================================================
# Part 1: 基础信息
# ============================================================
def part1_basic_info():
    print("\n" + "=" * 70)
    print("Part 1: 基础信息（截图 vs 模板分辨率）")
    print("=" * 70)
    # 截图分辨率
    raw_0001 = RAW_DIR / "0001_raw.png"
    if raw_0001.exists():
        img = cv2.imread(str(raw_0001))
        if img is not None:
            print(f"  [截图 0001] 尺寸: {img.shape[1]} × {img.shape[0]} (W×H)")
            print(f"  宽高比: {img.shape[1]/img.shape[0]:.4f} (16:9≈1.7778)")
        else:
            print(f"  [WARN] 0001_raw.png 读取失败（文件损坏？）")
    else:
        print(f"  [WARN] 找不到 0001_raw.png: {raw_0001}")
    # 模板尺寸
    print("\n  [模板尺寸]")
    for tpl_name in sorted(set(
        r[0] for r in _RULES_UNIQUE_ROI
    ) | set(r[1] for r in _ROUND_BANNERS) | set(r[1] for r in _ROUND_LABEL_TPLS)):
        tpl = load_gray(TPL_DIR / f"{tpl_name}.png")
        if tpl is None:
            print(f"    ❌ {tpl_name}.png → 不存在或读取失败")
        else:
            th, tw = tpl.shape
            print(f"    ✅ {tpl_name}.png → {tw} × {th}")


# ============================================================
# Part 2: 完整匹配分数（取前 3 张截图，覆盖 session 开头）
# ============================================================
def part2_full_match_scores():
    print("\n" + "=" * 70)
    print("Part 2: 模板匹配分数（第 0001/0020/0100/0179 张截图）")
    print("=" * 70)

    samples = ["0001_raw.png", "0020_raw.png", "0100_raw.png", "0179_raw.png"]
    tpl_cache: dict[str, np.ndarray | None] = {}
    for name in set(r[0] for r in _RULES_UNIQUE_ROI) | set(r[1] for r in _ROUND_BANNERS):
        tpl_cache[name] = load_gray(TPL_DIR / f"{name}.png")

    for sf in samples:
        path = RAW_DIR / sf
        if not path.exists():
            print(f"\n  [{sf}] 文件不存在 → 跳过")
            continue
        frame = cv2.imread(str(path))
        if frame is None:
            print(f"\n  [{sf}] 读取失败 → 跳过")
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        H, W = gray.shape
        print(f"\n  [{sf}] 画面 {W}×{H}")
        print("  ├─ Step1: 独立 ROI 规则")
        any_hit = False
        for (tpl_name, roi_key, stage_name) in _RULES_UNIQUE_ROI:
            tpl = tpl_cache.get(tpl_name)
            if tpl is None:
                print(f"  │   {tpl_name:<30s} → 模板未加载")
                continue
            score, box, sz_err = match_local(gray, tpl, *ROI[roi_key])
            tag = "✅" if score >= MATCH_THRESHOLD else ("⚠️ " if score >= 0.5 else ("❌SIZE" if score == -1.0 else "  "))
            extra = ""
            if score == -1.0 and sz_err is not None:
                tw, th, cw, ch = sz_err
                extra = f"  [ROI不足 tpl {tw}×{th} > crop {cw}×{ch}]"
            elif score >= 0:
                extra = f"  阈值差={score - MATCH_THRESHOLD:+.3f}"
            print(f"  │   {tag} {tpl_name:<28s} score={score:6.3f}  stage=[{stage_name}]{extra}")
            if score >= MATCH_THRESHOLD:
                any_hit = True
        print("  │")
        print("  ├─ Step2: 回合大字横幅（同区竞争）")
        best_r, best_s, second_s = None, 0.0, 0.0
        for r, tpl_name in _ROUND_BANNERS:
            tpl = tpl_cache.get(tpl_name)
            if tpl is None:
                continue
            s, _, sz_err = match_local(gray, tpl, *ROI["round_big_banner"])
            if s < 0:
                print(f"  │   R{r} {tpl_name:<28s} → ROI尺寸不足 {sz_err}")
                continue
            tag = "⭐" if s == max(0, best_s) else "  "
            print(f"  │   {tag} R{r:<2d} {tpl_name:<26s} score={s:6.3f}")
            if s > best_s:
                second_s = best_s
                best_s, best_r = s, r
            elif s > second_s:
                second_s = s
        margin_ok = (best_s - second_s) >= 0.03
        hit = best_s >= MATCH_THRESHOLD and margin_ok
        print(f"  │   → Best: R{best_r} score={best_s:.3f}, 2nd={second_s:.3f}, margin={best_s-second_s:.3f} → {'✅ 命中' if hit else '未命中'}")
        print("  └─")


# ============================================================
# Part 3: ROI 可视化（在 0001_raw 上把每个 ROI 画框，另存）
# ============================================================
def part3_roi_visualization():
    print("\n" + "=" * 70)
    print("Part 3: ROI 可视化 → 保存到 debug/_diagnose_treasure/0001_roi_overview.png")
    print("=" * 70)
    raw = RAW_DIR / "0001_raw.png"
    if not raw.exists():
        print("  [SKIP] 0001_raw.png 不存在")
        return
    img = cv2.imread(str(raw))
    assert img is not None  # 文件已存在，读取失败仅当损坏
    H, W = img.shape[:2]
    # 每个规则画彩色矩形 + 标注
    colors = {
        "settle_title":       (0, 255, 0),
        "result_banner":      (255, 0, 0),
        "smart_bid_btn":      (0, 0, 255),
        "round_big_banner":   (0, 255, 255),
        "appraiser_title":    (255, 0, 255),
        "participation_card": (255, 255, 0),
        "hall_peak_appraise_card": (0, 255, 255),
        "goto_appraise_btn":  (128, 0, 255),
        "round_label_area":   (0, 128, 255),
    }
    for key, (x1n, y1n, x2n, y2n) in ROI.items():
        x1, y1 = int(x1n * W), int(y1n * H)
        x2, y2 = int(x2n * W), int(y2n * H)
        c = colors.get(key, (128, 128, 128))
        cv2.rectangle(img, (x1, y1), (x2, y2), c, 2)
        cv2.putText(img, key, (x1, max(16, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, c, 1, cv2.LINE_AA)
    out = OUT_DIR / "0001_roi_overview.png"
    cv2.imwrite(str(out), img)
    # 单独切出鉴宝大厅两个 ROI
    for roi_key in ["hall_peak_appraise_card", "goto_appraise_btn"]:
        crop, box = crop_norm(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), *ROI[roi_key])
        if crop is not None:
            cv2.imwrite(str(OUT_DIR / f"crop_{roi_key}.png"), crop)
            x1, y1, x2, y2 = box
            print(f"  {roi_key}: 像素 [{x1},{y1}] → [{x2},{y2}]  = {x2-x1} × {y2-y1}")
    # 再把模板和 crop 放一起对比
    for tpl_name, roi_key, _ in [r for r in _RULES_UNIQUE_ROI if r[2] == "鉴宝大厅(选择场次)"]:
        tpl = load_gray(TPL_DIR / f"{tpl_name}.png")
        raw_img = cv2.imread(str(RAW_DIR / "0001_raw.png"))
        assert raw_img is not None  # 0001_raw.png 已确认存在
        crop, _ = crop_norm(cv2.cvtColor(raw_img, cv2.COLOR_BGR2GRAY), *ROI[roi_key])
        if tpl is not None and crop is not None:
            th, tw = tpl.shape
            ch, cw = crop.shape
            # 并排放在一张图上，高度取 max
            max_h = max(th, ch)
            canvas = np.zeros((max_h, tw + cw + 20), dtype=np.uint8) + 200
            canvas[:th, :tw] = tpl
            canvas[:ch, tw+20:tw+20+cw] = crop
            cv2.imwrite(str(OUT_DIR / f"compare_{tpl_name}.png"), canvas)
            print(f"  对比 {tpl_name}: 模板 {tw}×{th}  vs  crop {cw}×{ch} → 保存 compare_{tpl_name}.png")


# ============================================================
# Part 4: 画面变化分析（逐帧 diff，量化 + 定位变化区域）
# ============================================================
def part4_change_analysis():
    print("\n" + "=" * 70)
    print("Part 4: 画面变化分析（当前参数 vs 真实数据）")
    print("=" * 70)

    raw_files = sorted(RAW_DIR.glob("*_raw.png"))
    if len(raw_files) < 2:
        print(f"  [SKIP] raw 文件不足: {len(raw_files)}")
        return

    # 当前参数
    PIXEL_TH = 25
    AREA_RATIO = 0.01
    print(f"  当前参数: PIXEL_THRESH={PIXEL_TH}, AREA_RATIO={AREA_RATIO} (1%)")
    print(f"  总帧数: {len(raw_files)}")

    ratios_all = []
    triggered_idx = []   # 按当前参数会触发的帧号（1-based）
    prev_gray = None
    for i, fp in enumerate(raw_files, 1):
        frame = cv2.imread(str(fp))
        if frame is None:
            continue
        small = cv2.resize(frame, (320, 180))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        if prev_gray is None:
            prev_gray = gray
            continue
        diff = cv2.absdiff(gray, prev_gray)
        changed = int(np.sum(diff > PIXEL_TH))
        ratio = changed / gray.size
        ratios_all.append(ratio)
        if ratio > AREA_RATIO:
            triggered_idx.append(i)
        prev_gray = gray

    # 统计
    trigger_count = len(triggered_idx)
    total_pairs = len(ratios_all)
    print(f"\n  按当前参数会触发 {trigger_count}/{total_pairs} 次 ({trigger_count/total_pairs*100:.1f}%)")
    print(f"  实际日志里触发了 70 次 / 179 帧 ≈ 39%")
    print(f"  按当前算法诊断结果：触发率 {trigger_count/total_pairs*100:.1f}%（{trigger_count}次）")

    # 各种阈值方案对比
    print(f"\n  ── 不同阈值方案触发率对比 ──")
    print(f"  {'方案':<20s} {'PIXEL_TH':>8s} {'AREA_RATIO':>11s} {'触发次数':>8s} {'触发率':>8s}")
    scenarios = [
        ("当前",              25, 0.01),
        ("建议A(先试这个)",   35, 0.03),
        ("建议B(更保守)",     40, 0.05),
        ("极端保守",          50, 0.08),
    ]
    for name, pth, arth in scenarios:
        cnt = sum(1 for r in ratios_all if (
            # 重新用指定参数评估（这里只有 ratio by pixel_th=25，所以需要重算）
            False
        ))
    # 上面那个需要重算，这里简单重跑一遍：
    print(f"  {'方案':<20s} {'PIXEL_TH':>8s} {'AREA_RATIO':>11s} {'触发次数':>8s} {'触发率':>8s}")
    prev_gray = None
    scenarios_results = {name: 0 for name, _, _ in scenarios}
    for fp in raw_files:
        frame = cv2.imread(str(fp))
        if frame is None: continue
        small = cv2.resize(frame, (320, 180))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        if prev_gray is None:
            prev_gray = gray
            continue
        for name, pth, arth in scenarios:
            diff2 = cv2.absdiff(gray, prev_gray)
            r2 = int(np.sum(diff2 > pth)) / gray.size
            if r2 > arth:
                scenarios_results[name] += 1
        prev_gray = gray
    for name, pth, arth in scenarios:
        c = scenarios_results[name]
        print(f"  {name:<20s} {pth:>8d} {arth:>11.2f} {c:>8d} {c/total_pairs*100:>7.1f}%")

    # 变化区域分布分析：前10对触发帧，画heatmap
    print(f"\n  ── 变化区域定位（基于 PIXEL_TH=25 的变化像素空间分布）──")
    prev_gray = None
    heatmap = np.zeros((180, 320), dtype=np.float32)
    pair_cnt = 0
    for fp in raw_files[:min(50, len(raw_files))]:
        frame = cv2.imread(str(fp))
        if frame is None: continue
        small = cv2.resize(frame, (320, 180))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        if prev_gray is None:
            prev_gray = gray
            continue
        diff = cv2.absdiff(gray, prev_gray)
        heatmap += (diff > PIXEL_TH).astype(np.float32)
        pair_cnt += 1
        prev_gray = gray
    if pair_cnt > 0:
        heatmap /= pair_cnt
        # 分 4 象限统计：上/下/左/右
        up = heatmap[:90, :].mean()
        dn = heatmap[90:, :].mean()
        lf = heatmap[:, :160].mean()
        rt = heatmap[:, 160:].mean()
        print(f"  上半区变化密度: {up*100:.2f}%")
        print(f"  下半区变化密度: {dn*100:.2f}%")
        print(f"  左半区变化密度: {lf*100:.2f}%")
        print(f"  右半区变化密度: {rt*100:.2f}%")
        # 最大的 5 个变化热点块（16×16 网格）
        print(f"  Top-5 变化最密集 40×36 网格块：")
        gh, gw = 36, 40   # 180/5=36, 320/8=40
        blocks = []
        for by in range(5):
            for bx in range(8):
                blk = heatmap[by*gh:(by+1)*gh, bx*gw:(bx+1)*gw].mean()
                blocks.append((blk, bx, by))
        blocks.sort(reverse=True)
        for i, (v, bx, by) in enumerate(blocks[:5]):
            # 还原到归一化坐标
            x1n, y1n = bx/8, by/5
            x2n, y2n = (bx+1)/8, (by+1)/5
            print(f"    #{i+1} 密度={v*100:.2f}%  区域 norm: ({x1n:.2f},{y1n:.2f})→({x2n:.2f},{y2n:.2f})  像素: ({bx*40},{by*36})→({(bx+1)*40},{(by+1)*36})")
        # 保存 heatmap 图
        hm_color = cv2.applyColorMap((heatmap * 255 * 5).clip(0, 255).astype(np.uint8), cv2.COLORMAP_JET)
        cv2.imwrite(str(OUT_DIR / "change_heatmap_first50.png"), hm_color)
        print(f"  → 热力图另存: debug/_diagnose_treasure/change_heatmap_first50.png")


# ============================================================
# Part 5: 事件间间隔分析（看 70 次 screen_change 的时间分布）
# ============================================================
def part5_event_intervals():
    print("\n" + "=" * 70)
    print("Part 5: screen_change 事件间隔（实际日志的 70 次）")
    print("=" * 70)
    # 从 MRA log 里提取时间戳太麻烦，这里直接从 EVT 文件时间戳来
    evts = sorted(EVENT_DIR.glob("EVT_*.png"))
    if len(evts) < 2:
        print(f"  [SKIP] 事件不足，只有 {len(evts)} 个文件")
        return
    import os
    ts = [os.path.getmtime(str(p)) for p in evts]
    gaps = [ts[i+1] - ts[i] for i in range(len(ts)-1)]
    print(f"  事件数: {len(evts)}")
    print(f"  总时长: {ts[-1] - ts[0]:.1f} 秒")
    print(f"  平均间隔: {sum(gaps)/len(gaps):.2f} 秒")
    print(f"  中位数间隔: {sorted(gaps)[len(gaps)//2]:.2f} 秒")
    short = sum(1 for g in gaps if g < 2.0)
    print(f"  <2 秒间隔的对数: {short}/{len(gaps)} ({short/len(gaps)*100:.0f}%) — 如果加 5 秒冷却会去掉")


def main():
    print(f"项目根: {PROJ}")
    print(f"会话目录: {SESSION_DIR}")
    print(f"输出目录: {OUT_DIR}")
    part1_basic_info()
    part2_full_match_scores()
    part3_roi_visualization()
    part4_change_analysis()
    part5_event_intervals()
    print("\n" + "=" * 70)
    print("诊断完成。所有可视化输出都在: debug/_diagnose_treasure/")
    print("=" * 70)


if __name__ == "__main__":
    main()
