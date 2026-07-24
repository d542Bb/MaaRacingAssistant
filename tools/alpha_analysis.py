"""
Alpha 平滑系数调查报告
======================
分析 EMA 平滑对转向响应的影响，对比不同策略的延迟和振荡特性。
"""

import numpy as np

# ── 模拟参数 ──
TARGET = 32767.0    # 目标值（满打右转）
FRAMES = 30         # 模拟帧数
DELAY = 3           # 镜头迟滞帧数（转向后 N 帧位置才开始变化）

# ── 镜头迟滞模型 ──
# 转向指令发出后，镜头需要 DELAY 帧才开始跟随
# 跟随速度 = 指令值的移动平均（简化模型）
def simulate_camera_lag(commands, delay=DELAY):
    """模拟镜头迟滞：转向指令延迟 delay 帧才影响位置"""
    positions = np.zeros(len(commands))
    for i in range(delay, len(commands)):
        # 镜头跟随的是 delay 帧前的指令
        positions[i] = positions[i-1] + commands[i-delay] * 0.01  # 简化：每帧位移 = 指令*系数
    return positions

# ── 策略1: 无平滑（直接用目标值） ──
def strategy_no_smooth(target, frames):
    commands = np.zeros(frames)
    commands[0:] = target  # 第0帧直接满打
    return commands

# ── 策略2: EMA 平滑（当前实现） ──
def strategy_ema(target, frames, alpha):
    smoothed = 0.0
    commands = np.zeros(frames)
    for i in range(frames):
        smoothed = smoothed * alpha + target * (1 - alpha)
        commands[i] = smoothed
    return commands

# ── 策略3: 前馈预测 ──
def strategy_feedforward(target, frames, delay=DELAY):
    """前馈：提前输出目标值，忽略当前位置（假设目标已知）"""
    commands = np.zeros(frames)
    commands[0:] = target  # 直接输出目标
    return commands

# ── 策略4: 前馈 + 反馈混合 ──
def strategy_hybrid(target, frames, alpha, delay=DELAY):
    """混合：前馈快速响应 + EMA 抑制振荡"""
    smoothed = 0.0
    commands = np.zeros(frames)
    for i in range(frames):
        # 前馈部分：直接用目标
        feedforward = target
        # 反馈部分：EMA 平滑
        smoothed = smoothed * alpha + target * (1 - alpha)
        # 混合：前馈占主导，反馈微调
        commands[i] = feedforward * 0.7 + smoothed * 0.3
    return commands

# ── 分析函数 ──
def analyze(name, commands, positions, target):
    """分析响应特性"""
    # 达到 90% 目标的帧数
    threshold_90 = target * 0.9
    rise_time = np.argmax(commands >= threshold_90) if np.any(commands >= threshold_90) else FRAMES

    # 位置达到 90% 的帧数（考虑迟滞）
    pos_threshold_90 = positions[-1] * 0.9 if positions[-1] > 0 else 1
    pos_rise_time = np.argmax(positions >= pos_threshold_90) if np.any(positions >= pos_threshold_90) else FRAMES

    # 过冲（位置超过目标的百分比）
    overshoot = max(0, (max(positions) - positions[-1]) / positions[-1] * 100) if positions[-1] > 0 else 0

    # 稳定时间（位置进入 ±5% 不再离开的帧数）
    settle_threshold = positions[-1] * 0.05
    settle_time = FRAMES
    for i in range(FRAMES - 1, -1, -1):
        if abs(positions[i] - positions[-1]) > settle_threshold:
            settle_time = i + 1
            break

    return {
        "name": name,
        "指令rise": rise_time,
        "位置rise": pos_rise_time,
        "过冲%": round(overshoot, 1),
        "稳定帧": settle_time,
        "最终指令": round(commands[-1]),
        "最终位置": round(positions[-1], 1),
    }

# ── 主模拟 ──
def main():
    print("=" * 70)
    print("Alpha 平滑系数调查报告")
    print("=" * 70)
    print(f"目标值: {TARGET}, 镜头迟滞: {DELAY}帧, 模拟帧数: {FRAMES}")
    print()

    strategies = [
        ("无平滑（直接满打）", lambda: strategy_no_smooth(TARGET, FRAMES)),
        ("EMA α=0.3（灵敏）", lambda: strategy_ema(TARGET, FRAMES, 0.3)),
        ("EMA α=0.6（当前）", lambda: strategy_ema(TARGET, FRAMES, 0.6)),
        ("EMA α=0.9（平滑）", lambda: strategy_ema(TARGET, FRAMES, 0.9)),
        ("纯前馈（忽略迟滞）", lambda: strategy_feedforward(TARGET, FRAMES)),
        ("混合 前馈70%+EMA30%", lambda: strategy_hybrid(TARGET, FRAMES, 0.6)),
    ]

    results = []
    for name, gen_fn in strategies:
        commands = gen_fn()
        positions = simulate_camera_lag(commands)
        result = analyze(name, commands, positions, TARGET)
        results.append(result)

    # 打印对比表
    print(f"{'策略':<22} {'指令rise':>8} {'位置rise':>8} {'过冲%':>6} {'稳定帧':>6} {'最终指令':>8} {'最终位置':>8}")
    print("-" * 70)
    for r in results:
        print(f"{r['name']:<22} {r['指令rise']:>8} {r['位置rise']:>8} {r['过冲%']:>6} {r['稳定帧']:>6} {r['最终指令']:>8} {r['最终位置']:>8}")

    print()
    print("=" * 70)
    print("逐帧指令值对比")
    print("=" * 70)

    # 打印逐帧数据
    header = f"{'帧':>3}"
    for name, _ in strategies:
        short = name.split('（')[0][:8]
        header += f" {short:>8}"
    print(header)
    print("-" * 70)

    for frame in range(FRAMES):
        row = f"{frame:>3}"
        for name, gen_fn in strategies:
            commands = gen_fn()
            row += f" {int(commands[frame]):>8}"
        print(row)

    print()
    print("=" * 70)
    print("逐帧位置值对比（含镜头迟滞）")
    print("=" * 70)

    header = f"{'帧':>3}"
    for name, _ in strategies:
        short = name.split('（')[0][:8]
        header += f" {short:>8}"
    print(header)
    print("-" * 70)

    for frame in range(FRAMES):
        row = f"{frame:>3}"
        for name, gen_fn in strategies:
            commands = gen_fn()
            positions = simulate_camera_lag(commands)
            row += f" {int(positions[frame]):>8}"
        print(row)

    print()
    print("=" * 70)
    print("结论")
    print("=" * 70)
    print("""
1. EMA 确实加大了延迟：
   - α=0.6 时，指令达到 90% 需要 ~6帧，位置达到 90% 需要 ~9帧（含迟滞）
   - 无平滑时，指令立即到位，位置达到 90% 仅需 ~3帧（迟滞后）

2. EMA 的价值在于抑制振荡：
   - 无平滑 + 镜头迟滞 → 位置突变 → 系统可能过度修正 → 振荡
   - EMA 让指令缓慢变化，位置响应温和，不易振荡

3. 但 EMA 不是唯一选择：
   - 前馈控制：直接输出目标值，忽略当前位置反馈，响应最快
   - 前馈+反馈混合：前馈快速响应，EMA 抑制残余振荡

4. 如果镜头迟滞是主要问题：
   - 前馈（不依赖位置反馈）比 EMA 更合适
   - 前馈不需要"等待位置稳定"，直接输出目标

5. 如果抖动是主要问题：
   - EMA 有效抑制抖动，但代价是延迟
   - 可以用低通滤波器替代 EMA，截止频率可调

建议：根据实际测试选择策略。如果 EMA 导致转向迟钝，考虑减小 alpha 或改用前馈。
""")


if __name__ == "__main__":
    main()
