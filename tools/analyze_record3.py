#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分析转向停止规律"""

import csv
import sys

def analyze(csv_path: str):
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    print(f"总帧数: {len(rows)}")

    # 找到转向停止的帧（前一帧 lx 较大，当前帧 lx 较小）
    turn_stops = []
    for i in range(1, len(rows)):
        prev_lx = abs(int(rows[i-1]['lx']))
        curr_lx = abs(int(rows[i]['lx']))
        prev_area = int(rows[i-1]['target_area'])
        prev_offset = float(rows[i-1]['offset'])
        curr_area = int(rows[i]['target_area'])
        curr_offset = float(rows[i]['offset'])

        if prev_lx > 5000 and curr_lx < 2000 and prev_area > 0:
            turn_stops.append({
                'frame': int(rows[i]['frame']),
                'prev_lx': int(rows[i-1]['lx']),
                'curr_lx': int(rows[i]['lx']),
                'prev_offset': prev_offset,
                'curr_offset': curr_offset,
                'prev_area': prev_area,
                'curr_area': curr_area,
                'zone': rows[i]['zone'],
            })

    print(f"转向停止帧数: {len(turn_stops)}")

    # 分析停止时的 offset 和 area
    print("\n=== 转向停止时的状态 ===")
    for t in turn_stops[:25]:
        print(f"  帧{t['frame']}: lx {t['prev_lx']}→{t['curr_lx']} "
              f"offset {t['prev_offset']:.3f}→{t['curr_offset']:.3f} "
              f"area {t['prev_area']}→{t['curr_area']} zone={t['zone']}")

    # 统计停止时的 offset 分布
    print("\n=== 停止时 offset 分布 ===")
    offsets = [abs(t['curr_offset']) for t in turn_stops]
    if offsets:
        print(f"  最小: {min(offsets):.3f}")
        print(f"  最大: {max(offsets):.3f}")
        print(f"  平均: {sum(offsets)/len(offsets):.3f}")
        print(f"  中位数: {sorted(offsets)[len(offsets)//2]:.3f}")

    # 统计停止时的 area 分布
    print("\n=== 停止时 area 分布 ===")
    areas = [t['curr_area'] for t in turn_stops]
    if areas:
        print(f"  最小: {min(areas)}")
        print(f"  最大: {max(areas)}")
        print(f"  平均: {sum(areas)/len(areas):.0f}")
        print(f"  中位数: {sorted(areas)[len(areas)//2]}")

    # 分析连续转向帧的目标变化
    print("\n=== 连续转向示例（目标从远到近）===")
    # 找一段连续转向的帧
    continuous_turns = []
    current_turn = []
    for i, r in enumerate(rows):
        lx = abs(int(r['lx']))
        area = int(r['target_area'])
        if lx > 5000 and area > 0:
            current_turn.append(r)
        else:
            if len(current_turn) > 5:
                continuous_turns.append(current_turn)
            current_turn = []
    if len(current_turn) > 5:
        continuous_turns.append(current_turn)

    # 显示最长的几段
    continuous_turns.sort(key=len, reverse=True)
    for turn_seq in continuous_turns[:3]:
        print(f"\n  连续转向 {len(turn_seq)} 帧:")
        # 采样显示
        indices = [0, len(turn_seq)//4, len(turn_seq)//2, 3*len(turn_seq)//4, len(turn_seq)-1]
        for idx in indices:
            r = turn_seq[idx]
            print(f"    帧{r['frame']}: lx={r['lx']} offset={r['offset']} "
                  f"area={r['target_area']} zone={r['zone']}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        analyze(sys.argv[1])
    else:
        analyze("logs/record_20260724_164635.csv")
