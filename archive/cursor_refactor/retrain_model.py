#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
综合重训器 —— 融合「长脉冲速度模型」与「微脉冲标定」两组数据，
用两层拟合估计过原点线性模型：v(mag) = k·mag（mag≥死区），0（mag<死区）。

数据来源（离线，无需游戏）：
    models/stick_speed_model.json   长脉冲速度样本（mag, speed）
    models/micro_pulse.json         微脉冲矩阵（mag, frames, T_ms, dist_px）

数学方法：
    层1（每个幅度内）: 微脉冲的 (T, dist) 点做过原点回归 dist = speed·T
        → 每个 mag 得速度精估 speed 与标准误 σ（同时检验截距≈0，验证无起始延迟）
    层2（跨幅度）   : 合并「微脉冲精估点 + 长脉冲速度点」，加权过原点最小二乘
        v = k·mag，权重 w = 1/σ²。微脉冲点精度高权重高，长脉冲点延伸线性范围。
    模型选择         : 对比线性 vs 二次（看二次项是否显著、ΔR² 是否值得 3 参数）

输出：
    models/stick_speed_model.json（覆盖为新格式：k / deadzone / 诊断 / 合并样本）
    models/stick_speed_fit.png

用法：
    python cursor_refactor/retrain_model.py
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

OUT_DIR = Path(__file__).resolve().parent / "models"
LONG_PULSE_SIGMA = 16.7   # 长脉冲单点速度的标准误(px/s)：取原模型 rmse 为粗估
DEADZONE_FALLBACK = 5000  # 已知最小有效幅度（3000 不动/5000 动，阈值在两者间）


# ======================================================================
#  层1：微脉冲每幅度内过原点回归
# ======================================================================

def fit_speed_per_mag(mp: dict) -> list[dict]:
    """对每个 mag，用其全部 (T_ms, dist_px) 点做过原点回归。

    speed = Σ(T·d) / Σ(T²)；σ_speed = sqrt( Σ(d - speed·T)² / (n-1) / Σ(T²) )
    另算带截距回归的截距（应≈0，验证无起始延迟）。
    """
    by_mag: dict[int, list] = {}
    for row in mp["matrix"]:
        if not row.get("moved"):
            continue
        # 关键：T 必须用「秒」→ speed 单位 px/s，与长脉冲一致
        by_mag.setdefault(row["mag"], []).append((row["T_ms"] / 1000.0, row["dist_px"]))

    out = []
    for mag, pts in sorted(by_mag.items()):
        n = len(pts)
        if n < 3:
            continue
        T = np.array([p[0] for p in pts], dtype=float)
        d = np.array([p[1] for p in pts], dtype=float)
        # 过原点回归
        tt = float((T * T).sum())
        td = float((T * d).sum())
        if tt < 1e-9:
            continue
        speed = td / tt
        resid = d - speed * T
        s2 = float((resid * resid).sum()) / (n - 1)
        sigma = math.sqrt(s2 / tt) if s2 > 0 else 0.0
        # 带截距回归（诊断用）
        slope_b, inter_b = np.polyfit(T, d, 1)
        # 过原点 R²
        ss_res = float((resid * resid).sum())
        ss_tot = float(((d - d.mean()) ** 2).sum())
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0
        out.append({
            "mag": mag, "speed_px_s": float(speed), "sigma": sigma,
            "n_points": n, "intercept_px": float(inter_b),
            "slope_with_intercept": float(slope_b), "r2_origin": float(r2),
        })
    return out


# ======================================================================
#  层2：跨幅度加权过原点线性 + 二次对比
# ======================================================================

def fit_global(pts: list[dict]) -> dict:
    """用 pts 拟合加权过原点线性 v=k·mag，权重 1/σ²。

    只用于「微脉冲精估点」拟合 k（最高精度段 5000-16000）。
    对比二次(含截距)验证线性是否充分。
    """
    mag = np.array([p["mag"] for p in pts], dtype=float)
    v = np.array([p["speed"] for p in pts], dtype=float)
    w = np.array([1.0 / max(p["sigma"], 0.5) ** 2 for p in pts], dtype=float)

    k = float((w * mag * v).sum() / (w * mag * mag).sum())
    v_pred = k * mag
    wm = float((w * v).sum() / w.sum())
    ss_res = float((w * (v - v_pred) ** 2).sum())
    ss_tot = float((w * (v - wm) ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0
    s2 = ss_res / (len(pts) - 1)
    sigma_k = math.sqrt((1.0 / float((w * mag * mag).sum())) * s2) if s2 > 0 else 0.0

    W = np.diag(w)
    X = np.stack([mag ** 2, mag, np.ones_like(mag)], axis=1)
    try:
        beta = np.linalg.solve(X.T @ W @ X, X.T @ W @ v)
        vq = X @ beta
        ss_res_q = float((w * (v - vq) ** 2).sum())
        r2_q = 1.0 - ss_res_q / ss_tot if ss_tot > 0 else 1.0
        quad = {"a": float(beta[0]), "r2_quad": float(r2_q),
                "r2_gain": float(r2_q - r2)}
    except np.linalg.LinAlgError:
        quad = {"a": None, "r2_quad": None, "r2_gain": None}

    return {"k": k, "sigma_k": sigma_k, "r2": r2, "n": len(pts), "quad": quad}


# ======================================================================
#  输出
# ======================================================================

def save_plot(model: dict, pts: list[dict], path: Path):
    W, H = 900, 520
    pad_l, pad_r, pad_t, pad_b = 70, 30, 40, 60
    pw, ph = W - pad_l - pad_r, H - pad_t - pad_b
    img = np.full((H, W, 3), 245, dtype=np.uint8)
    mags = [p["mag"] for p in pts]
    vs = [p["speed"] for p in pts]
    mx = max(mags) * 1.05
    my = max(vs) * 1.1

    def px(x, y):
        return int(pad_l + x / mx * pw), int(pad_t + ph - y / my * ph)

    for p, v in zip(pts, vs):
        c = (20, 90, 220) if p["src"] == "micro" else (200, 120, 20)
        x, y = px(p["mag"], p["speed"])
        cv2.circle(img, (x, y), 6, c, -1, cv2.LINE_AA)
    k = model["k"]
    for i in range(200):
        x0, x1 = mx * i / 200, mx * (i + 1) / 200
        cv2.line(img, px(x0, k * x0), px(x1, k * x1), (30, 160, 60), 2, cv2.LINE_AA)
    dz = model["deadzone"]
    if dz > 0:
        x, _ = px(dz, 0)
        cv2.line(img, (x, pad_t), (x, pad_t + ph), (0, 0, 255), 1)
    cv2.putText(img, f"v = {k:.5f}·mag   r2={model['fit']['r2']:.4f}  "
                     f"σk={model['sigma_k']:.5f}",
                (pad_l, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (20, 60, 20), 1, cv2.LINE_AA)
    cv2.putText(img, "blue=micro-pulse  brown=long-pulse", (pad_l, 46),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (60, 60, 60), 1, cv2.LINE_AA)
    cv2.putText(img, "magnitude", (pad_l, H - 18),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 1, cv2.LINE_AA)
    cv2.putText(img, "speed px/s", (16, pad_t + 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 1, cv2.LINE_AA)
    cv2.imwrite(str(path), img)


def main():
    mp_path = OUT_DIR / "micro_pulse.json"
    sp_path = OUT_DIR / "stick_speed_model.json"
    if not mp_path.exists() or not sp_path.exists():
        print(f"[错误] 需要 {mp_path} 与 {sp_path}，请先完成采集")
        return 1
    mp = json.loads(mp_path.read_text(encoding="utf-8"))
    sp = json.loads(sp_path.read_text(encoding="utf-8"))

    # ---- 层1：微脉冲每幅度精估 ----
    micro_pts = fit_speed_per_mag(mp)
    if not micro_pts:
        print("[错误] 微脉冲数据无效")
        return 1

    # ---- 合并长脉冲点（带粗估 σ）----
    # 过滤：①死区外(m>=5000) ②非 px/ms 泄漏(v>=30) ③非 micro 残留
    # （旧模型 samples 可能混入上次写入的 micro 点，其 speed 与精估值完全一致）
    micro_speeds = {round(p["speed_px_s"], 4) for p in micro_pts}
    long_pts = []
    for m, v in zip(sp["samples"]["mag"], sp["samples"]["speed"]):
        if m >= 5000 and v >= 30 and round(v, 4) not in micro_speeds:
            long_pts.append({"mag": m, "speed": v, "sigma": LONG_PULSE_SIGMA, "src": "long"})
    for p in micro_pts:
        long_pts.append({"mag": p["mag"], "speed": p["speed_px_s"],
                         "sigma": max(p["sigma"], 0.5), "src": "micro"})

    # ---- 层2a：k 只由微脉冲精估点拟合（最高精度段）----
    micro_fit_pts = [{"mag": p["mag"], "speed": p["speed_px_s"],
                      "sigma": p["sigma"]} for p in micro_pts]
    fit = fit_global(micro_fit_pts)

    # ---- 层1 诊断表 ----
    print("\n=== 层1：微脉冲每幅度速度精估（T 用秒，speed 单位 px/s） ===")
    print(f"{'mag':>6} {'speed(px/s)':>12} {'σ':>8} {'截距px':>9} {'过原点R²':>9} {'n':>3}")
    for p in micro_pts:
        print(f"{p['mag']:>6} {p['speed_px_s']:>12.2f} {p['sigma']:>8.2f} "
              f"{p['intercept_px']:>9.3f} {p['r2_origin']:>9.4f} {p['n_points']:>3}")

    # ---- 死区 ----
    deadzone = int(sp.get("deadzone", DEADZONE_FALLBACK))

    # ---- 层2b：长脉冲点用作线性延伸验证（不参与拟合）----
    long_only = [p for p in long_pts if p["src"] == "long"]
    print("\n=== 层2：加权过原点线性 v = k·mag（k 由微脉冲精估点拟合） ===")
    print(f"  k = {fit['k']:.6f}  σk = {fit['sigma_k']:.6f}  R² = {fit['r2']:.4f}  "
          f"点数 = {fit['n']}")
    q = fit["quad"]
    if q["a"] is not None:
        print(f"  二次对比: 二次项a={q['a']:.2e}  R²二次={q['r2_quad']:.4f}  "
              f"ΔR²={q['r2_gain']:+.5f}")
        verdict = "线性已足够（二次无实质增益）" if abs(q["r2_gain"]) < 0.005 else "二次更优，需复核"
        print(f"  → {verdict}")

    print("\n=== 层2b：长脉冲高幅度点对 k 的验证（线性是否延伸到 32000） ===")
    print(f"{'mag':>6} {'实测':>8} {'预测k·mag':>10} {'残差':>8} {'偏差%':>7}")
    max_res = 0.0
    for p in sorted(long_only, key=lambda x: x["mag"]):
        pred = fit["k"] * p["mag"]
        res = p["speed"] - pred
        pct = 100.0 * res / pred if abs(pred) > 1e-6 else float("nan")
        max_res = max(max_res, abs(res))
        print(f"{p['mag']:>6} {p['speed']:>8.1f} {pred:>10.1f} {res:>+8.1f} {pct:>+6.1f}%")

    # ---- 输出 ----
    model = {
        "model": "speed_px_s = k * mag  (mag>=deadzone), 0 (mag<deadzone)",
        "k": fit["k"],
        "sigma_k": fit["sigma_k"],
        "deadzone": deadzone,
        "max_axis": 32767,
        "safe_margin": 120,
        "resolution": sp.get("resolution", [1282, 759]),
        "fit": {
            "method": "2-level: per-mag origin-OLS speed, then weighted origin-OLS v=k*mag",
            "weighting": "w=1/sigma^2",
            "n_points": fit["n"],
            "r2": fit["r2"],
            "quad": q,
            "max_abs_residual_px_s": max_res,
        },
        "per_mag": micro_pts,
        "samples": {"mag": [p["mag"] for p in long_pts],
                    "speed": [p["speed"] for p in long_pts],
                    "src": [p["src"] for p in long_pts]},
        "captured_at": datetime.now().isoformat(timespec="seconds"),
    }
    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / "stick_speed_model.json").write_text(
        json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
    save_plot(model, long_pts, OUT_DIR / "stick_speed_fit.png")
    print(f"\n新模型已保存: {OUT_DIR / 'stick_speed_model.json'}")
    print(f"拟合图:       {OUT_DIR / 'stick_speed_fit.png'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
