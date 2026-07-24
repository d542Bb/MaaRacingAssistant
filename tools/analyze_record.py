#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分析记录模式数据"""

import csv
import sys

def analyze(csv_path: str):
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    print(f"总帧数: {len(rows)}")

    # 统计手柄输入范围
    lx_vals = [int(r['lx']) for r in rows]
    ly_vals = [int(r['ly']) for r in rows]
    rt_vals = [int(r['rt']) for r in rows]

    print(f"lx 范围: {min(lx_vals)} ~ {max(lx_vals)}")
    print(f"ly 范围: {min(ly_vals)} ~ {max(ly_vals)}")
    print(f"rt 范围: {min(rt_vals)} ~ {max(rt_vals)}")

    # 找到人工转向的帧（lx 偏离中心较多）
    turning_frames = [(i, r) for i, r in enumerate(rows) if abs(int(r['lx'])) > 5000]
    print(f"\n人工转向帧数 (|lx|>5000): {len(turning_frames)}")

    # 显示一些转向帧示例
    print("\n转向帧示例:")
    for i, r in turning_frames[:15]:
        print(f"  帧{r['frame']}: lx={r['lx']} offset={r['offset']} zone={r['zone']} "
              f"target=({r['target_cx']},{r['target_cy']}) area={r['target_area']}")

    # 找到有金币目标且人工转向的帧
    print("\n有目标+人工转向的帧:")
    for i, r in turning_frames[:20]:
        if int(r['target_area']) > 0:
            print(f"  帧{r['frame']}: lx={r['lx']} offset={r['offset']} "
                  f"target_area={r['target_area']} zone={r['zone']} reason={r['reason']}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        analyze(sys.argv[1])
    else:
        analyze("logs/record_20260724_164635.csv")
