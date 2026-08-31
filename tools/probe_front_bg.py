#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""最小探测：切前台 + PostMessage 能否激活「进入下一层」的按钮。

背景：后台点击（光标就位+PostMessage）对部分按钮只播动画不激活逻辑，
疑似这些按钮的逻辑层校验前台。本脚本：切游戏前台 → PostMessage 点
「巅峰鉴宝」大厅卡片 → 观察是否进入活动页。

判定：
- 进入活动页 → 前台校验 → 「半后台」方案可行（阶段切换类按钮临时切前台）；
- 仍未进入 → 游戏校验真实输入 → 后台点击对这些按钮彻底无效。
"""
import ctypes
import sys
import time
from ctypes import byref, c_ulong, c_void_p
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

from maaracing_assistant.core.window_utils import (  # noqa: E402
    activate_window, find_game_hwnd,
)
from maaracing_assistant.core.clicker import Clicker  # noqa: E402

# 「巅峰鉴宝」大厅卡片归一化中心（treasure_rois.json hall_peak_appraise_card rect 均值）
HALL_CARD_CENTER = (0.8283333333333334, 0.8474279835390947)


def main() -> int:
    hwnd = find_game_hwnd()
    if not hwnd:
        print("未找到游戏窗口，请先启动游戏", file=sys.stderr)
        return 1

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = c_void_p
    user32.GetWindowThreadProcessId.argtypes = [c_void_p, ctypes.POINTER(c_ulong)]
    user32.GetWindowThreadProcessId.restype = c_ulong
    fg = user32.GetForegroundWindow()
    fg_pid = c_ulong(0)
    user32.GetWindowThreadProcessId(fg, byref(fg_pid))
    print(f"游戏 hWnd={hwnd}；当前前台窗口 hWnd={fg} (pid={fg_pid.value})")

    print("[1/3] 切换游戏窗口到前台…")
    ok = activate_window(hwnd)
    print(f"  activate_window = {ok}")
    time.sleep(0.8)

    print("[2/3] PostMessage 点击「巅峰鉴宝」大厅卡片 (光标就位)…")
    clicker = Clicker(hwnd, "background", background_hold_s=0.15)
    r = clicker.click(HALL_CARD_CENTER[0], HALL_CARD_CENTER[1], move_pause_s=0.4)
    print(f"  后台点击投递 = {r}")

    print("[3/3] 请观察游戏：是否从大厅进入鉴宝活动页？")
    print("  等待 4 秒后脚本结束；若未进入，说明该按钮校验真实输入（非前台校验）")
    time.sleep(4)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
