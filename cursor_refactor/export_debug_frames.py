#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
debug 帧导出工具 —— 从会话录制目录按帧号离线导出「识别叠加 debug 图」。

数据来源（cursor_monitor.py 运行时自动生成）：
    cursor_refactor/captures/run_YYYYmmdd_HHMMSS/
        frame_000001.jpg    # 原始帧（无叠加）
        cursor_log.jsonl    # 逐帧识别日志

用法（项目根下）：
    连续跟踪回放全量导出（= 实际运行表现，推荐核对用）:
        python cursor_refactor/export_debug_frames.py --replay
    自动扫描异常帧并导出（基于旧日志，注意日志由录制时算法产生）:
        python cursor_refactor/export_debug_frames.py --anomalies
    按帧号导出（单帧/区间可混用，无先验单帧重算）:
        python cursor_refactor/export_debug_frames.py --frames 120,340-346
    全量导出:
        python cursor_refactor/export_debug_frames.py --all
    指定会话 / 上下文帧数:
        --session captures/run_xxx  --context 3

导出结果：
    --replay → <session>/debug_replay/replay_XXXXXX.jpg（带时间连续性先验）
    其余     → <session>/debug/debug_XXXXXX.jpg（单帧无先验重算）
    终端打印异常摘要（帧号 + 原因），可按摘要继续用 --frames 精查。

异常判据（基于 JSONL 日志）：
    丢检     state=none（前帧有选中 → 标 lost，更可疑）
    跳变     相邻帧选中光标位移 > 80px（30fps 巡航不该瞬移）
    低分     选中分数 < 0.75（判定处于边缘）
    切换     判定态变化（三态互转 / 有↔无）
    边缘候选 存在 reject 候选且分数 > 0.55（误杀/误纳风险）
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cursor_monitor import detect_cursor, select_cursor, draw_overlay, _put

CAPTURES_ROOT = Path(__file__).resolve().parent / "captures"

JUMP_THRES = 80.0     # 相邻帧选中位移阈值（px）
LOW_SCORE = 0.75      # 选中分数下限
EDGE_SCORE = 0.55     # reject 候选边缘分数上限
DEBUG_JPEG_Q = 92     # debug 图快速编码（文字清晰 + 体积小）

STATE_EN = {
    "normal": "NORMAL", "interactive": "INTER",
    "pressed": "PRESSED", "reject": "REJ", "none": "NONE",
}


def find_latest_session() -> Path | None:
    if not CAPTURES_ROOT.exists():
        return None
    runs = sorted(p for p in CAPTURES_ROOT.iterdir()
                  if p.is_dir() and p.name.startswith("run_"))
    return runs[-1] if runs else None


def load_log(session: Path):
    log_path = session / "cursor_log.jsonl"
    if not log_path.exists():
        print(f"[错误] 未找到日志: {log_path}")
        return None
    records = []
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def parse_frames(spec: str) -> set[int]:
    """解析 "12,34-40" → {12, 34, 35, ..., 40}"""
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return out


def _selected_cand(rec: dict):
    idx = rec.get("sel", -1)
    cands = rec.get("cands", [])
    if 0 <= idx < len(cands):
        return cands[idx]
    return None


def detect_anomalies(records: list[dict]) -> dict[int, list[str]]:
    """扫描逐帧日志，返回 {seq: [异常原因, ...]}。"""
    anomalies: dict[int, list[str]] = {}
    prev = None
    for rec in records:
        reasons: list[str] = []
        sel = _selected_cand(rec)
        if sel is None:
            reasons.append("丢检")
            if prev is not None and prev.get("state") != "none":
                reasons.append("lost(前帧有)")
        else:
            if sel.get("score", 0.0) < LOW_SCORE:
                reasons.append(f"低分({sel['score']:.2f})")
            prev_sel = _selected_cand(prev) if prev else None
            if prev_sel is not None:
                dist = math.hypot(sel["x"] - prev_sel["x"], sel["y"] - prev_sel["y"])
                if dist > JUMP_THRES:
                    reasons.append(f"跳变({dist:.0f}px)")
            if prev is not None and prev.get("state") != rec.get("state"):
                reasons.append(f"切换({prev.get('state')}→{rec.get('state')})")
        for c in rec.get("cands", []):
            if c.get("st") == "reject" and c.get("score", 0.0) > EDGE_SCORE:
                reasons.append(f"边缘候选({c['score']:.2f})")
                break
        if reasons:
            anomalies[rec["seq"]] = reasons
        prev = rec
    return anomalies


def export_frames(session: Path, seqs: set[int], records_by_seq: dict[int, dict]):
    out_dir = session / "debug"
    out_dir.mkdir(exist_ok=True)
    ok = miss = 0
    for seq in sorted(seqs):
        src = session / f"frame_{seq:06d}.jpg"
        if not src.exists():
            print(f"  [警告] 缺帧 {src.name}，跳过")
            miss += 1
            continue
        bgr = cv2.imread(str(src))
        if bgr is None:
            print(f"  [警告] 解码失败 {src.name}，跳过")
            miss += 1
            continue

        img_rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        targets, selected = detect_cursor(img_rgb)
        overlay = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR)
        draw_overlay(overlay, targets, selected)

        rec = records_by_seq.get(seq)
        log_state = STATE_EN.get(rec["state"], "?") if rec else "?"
        calc_state = STATE_EN.get(selected.state, "REJ") if selected else "NONE"
        hud = f"#{seq}  t={rec['t']:.2f}s  log={log_state}  re-calc={calc_state}"
        _put(overlay, hud, (10, 46), (0, 220, 255), 0.5, 1)

        cv2.imwrite(str(out_dir / f"debug_{seq:06d}.jpg"), overlay,
                    [cv2.IMWRITE_JPEG_QUALITY, DEBUG_JPEG_Q])
        ok += 1
    return ok, miss


def replay_export(session: Path):
    """连续跟踪回放：逐帧 detect+select（带时间先验，= 实际运行表现）。

    输出 <session>/debug_replay/replay_XXXXXX.jpg，HUD 含帧号/判定/分数/
    位置/连续丢检计数，供人工逐帧核对识别错误。
    """
    frames = sorted(session.glob("frame_*.jpg"))
    if not frames:
        print(f"[错误] 会话内无帧图: {session}")
        return 1
    out_dir = session / "debug_replay"
    out_dir.mkdir(exist_ok=True)

    last_pos, miss_streak = None, 0
    st_count: dict[str, int] = {}
    total = len(frames)
    print(f"回放导出 {total} 帧 → {out_dir}（正在等待，请勿关闭终端）...")
    for i, src in enumerate(frames, 1):
        seq = int(src.stem.split("_")[1])
        bgr = cv2.imread(str(src))
        if bgr is None:
            print(f"  [警告] 解码失败 {src.name}，跳过")
            continue
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        targets, _ = detect_cursor(rgb)
        sel = select_cursor(targets, last_pos, miss_streak)
        if sel is not None:
            last_pos, miss_streak = sel.pos, 0
        else:
            miss_streak += 1

        overlay = bgr.copy()
        draw_overlay(overlay, targets, sel)
        st = sel.state if sel is not None else "none"
        st_count[st] = st_count.get(st, 0) + 1
        hud = f"#{seq} {STATE_EN.get(st, st)}"
        if sel is not None:
            hud += f" S={sel.score:.2f} ({sel.pos[0]},{sel.pos[1]})"
        hud += f" miss={miss_streak}"
        _put(overlay, hud, (10, 46), (0, 220, 255), 0.5, 1)
        cv2.imwrite(str(out_dir / f"replay_{seq:06d}.jpg"), overlay,
                    [cv2.IMWRITE_JPEG_QUALITY, DEBUG_JPEG_Q])
        if i % 100 == 0:
            print(f"  ... {i}/{total}")
    print("回放统计（选中态分布）:")
    for st, n in sorted(st_count.items(), key=lambda kv: -kv[1]):
        print(f"  {STATE_EN.get(st, st):<8} {n:<5} {n / total:.1%}")
    print(f"完成: {sum(st_count.values())} 张 → {out_dir}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="从会话录制目录导出 debug 叠加帧")
    ap.add_argument("--session", help="会话目录（默认取 captures 下最新 run_*）")
    ap.add_argument("--frames", help='帧号，如 "120,340-346"')
    ap.add_argument("--anomalies", action="store_true", help="扫描异常帧并连同上下文导出")
    ap.add_argument("--replay", action="store_true",
                    help="连续跟踪回放全量导出（带时间先验，= 实际运行表现）")
    ap.add_argument("--all", action="store_true", help="全量导出")
    ap.add_argument("--context", type=int, default=3, help="异常帧前后各 N 帧上下文（默认 3）")
    args = ap.parse_args()

    session = Path(args.session) if args.session else find_latest_session()
    if session is None or not session.exists():
        print("[错误] 未找到会话目录。请先运行 cursor_monitor.py 生成录制数据。")
        return 1
    print(f"会话: {session}")

    if args.replay:
        return replay_export(session)

    records = load_log(session)
    if not records:
        return 1
    print(f"日志: {len(records)} 帧，时长 {records[-1]['t']:.1f}s，"
          f"首帧 t={records[0]['t']:.2f}s")

    records_by_seq = {r["seq"]: r for r in records}
    targets: set[int] = set()

    if args.all:
        targets.update(records_by_seq.keys())
    if args.frames:
        targets.update(parse_frames(args.frames))
    if args.anomalies:
        anomalies = detect_anomalies(records)
        n_lost = sum(1 for rs in anomalies.values() if any(r.startswith("丢检") for r in rs))
        n_jump = sum(1 for rs in anomalies.values() if any(r.startswith("跳变") for r in rs))
        print(f"异常帧 {len(anomalies)} 个（丢检 {n_lost} / 跳变 {n_jump}）:")
        for seq, reasons in sorted(anomalies.items()):
            rec = records_by_seq[seq]
            print(f"  #{seq:<6} t={rec['t']:>7.2f}s  {rec['state']:<12} {' / '.join(reasons)}")
        for seq in anomalies:
            for s in range(seq - args.context, seq + args.context + 1):
                if s in records_by_seq:
                    targets.add(s)

    if not targets:
        print("未选择任何帧。请用 --anomalies / --frames / --all 之一。")
        return 1

    ok, miss = export_frames(session, targets, records_by_seq)
    print(f"导出完成: {ok} 张 → {session / 'debug'}（缺帧 {miss}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
