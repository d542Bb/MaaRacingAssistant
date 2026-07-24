#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""深入分析记录数据 - 转向规律"""

import csv
import sys

def analyze(csv_path: str):
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    print(f"总帧数: {len(rows)}")

    # 找到有目标且人工转向的帧
    turning_with_target = []
    for i, r in enumerate(rows):
        lx = int(r['lx'])
        area = int(r['target_area'])
        offset = float(r['offset'])
        if abs(lx) > 3000 and area > 0:
            turning_with_target.append({
                'frame': int(r['frame']),
                'lx': lx,
                'offset': offset,
                'area': area,
                'zone': r['zone'],
                'target_cx': float(r['target_cx']),
                'target_cy': float(r['target_cy']),
            })

    print(f"有目标+人工转向帧数: {len(turning_with_target)}")

    # 按 area 分组分析
    print("\n=== 按目标面积分组 ===")
    area_groups = {
        '小 (<200)': [],
        '中 (200-1000)': [],
        '大 (>1000)': [],
    }
    for t in turning_with_target:
        if t['area'] < 200:
            area_groups['小 (<200)'].append(t)
        elif t['area'] < 1000:
            area_groups['中 (200-1000)'].append(t)
        else:
            area_groups['大 (>1000)'].append(t)

    for name, group in area_groups.items():
        if group:
            avg_lx = sum(abs(t['lx']) for t in group) / len(group)
            avg_offset = sum(abs(t['offset']) for t in group) / len(group)
            print(f"{name}: {len(group)}帧, 平均|lx|={avg_lx:.0f}, 平均|offset|={avg_offset:.3f}")

    # 按 offset 分组分析
    print("\n=== 按偏移量分组 ===")
    offset_groups = {
        '微小 (<0.02)': [],
        '小 (0.02-0.05)': [],
        '中 (0.05-0.10)': [],
        '大 (>0.10)': [],
    }
    for t in turning_with_target:
        a_offset = abs(t['offset'])
        if a_offset < 0.02:
            offset_groups['微小 (<0.02)'].append(t)
        elif a_offset < 0.05:
            offset_groups['小 (0.02-0.05)'].append(t)
        elif a_offset < 0.10:
            offset_groups['中 (0.05-0.10)'].append(t)
        else:
            offset_groups['大 (>0.10)'].append(t)

    for name, group in offset_groups.items():
        if group:
            avg_lx = sum(abs(t['lx']) for t in group) / len(group)
            avg_area = sum(t['area'] for t in group) / len(group)
            print(f"{name}: {len(group)}帧, 平均|lx|={avg_lx:.0f}, 平均area={avg_area:.0f}")

    # 分析转向开始时的目标状态
    print("\n=== 转向开始时的目标状态 ===")
    # 找到转向开始的帧（前一帧 lx 较小）
    turn_starts = []
    for i in range(1, len(rows)):
        prev_lx = abs(int(rows[i-1]['lx']))
        curr_lx = abs(int(rows[i]['lx']))
        curr_area = int(rows[i]['target_area'])
        curr_offset = float(rows[i]['offset'])
        if prev_lx < 2000 and curr_lx > 5000 and curr_area > 0:
            turn_starts.append({
                'frame': int(rows[i]['frame']),
                'lx': int(rows[i]['lx']),
                'offset': curr_offset,
                'area': curr_area,
                'zone': rows[i]['zone'],
            })

    print(f"转向开始帧数: {len(turn_starts)}")
    for t in turn_starts[:20]:
        print(f"  帧{t['frame']}: lx={t['lx']} offset={t['offset']:.3f} area={t['area']} zone={t['zone']}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        analyze(sys.argv[1])
    else:
        analyze("logs/record_20260724_164635.csv")
