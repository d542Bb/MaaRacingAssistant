#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
窗口查找与物理手柄检测工具
"""

import ctypes
from ctypes import wintypes
from pathlib import Path

from maa.toolkit import Toolkit

from maaracing_assistant.logger import logger


def hwnd_from_pid(pid: int) -> int:
    user32 = ctypes.windll.user32
    _cache = {}

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        found_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(found_pid))
        if found_pid.value == pid:
            _cache["hwnd"] = hwnd
            return False
        return True

    user32.EnumWindows(callback, 0)
    return _cache.get("hwnd", 0)


def has_physical_controller() -> bool:
    """检测是否有物理 Xbox 手柄已连接（在创建虚拟手柄前调用）"""
    try:
        for dll_name in ["xinput1_4.dll", "xinput9_1_0.dll", "xinput1_3.dll"]:
            try:
                dll = ctypes.windll[dll_name]
                break
            except Exception:
                continue
        else:
            return False

        buf = ctypes.create_string_buffer(16)
        for i in range(4):
            if dll.XInputGetState(i, buf) == 0:
                return True
        return False
    except Exception:
        return False


def find_game_hwnd() -> int:
    proj_root = Path(__file__).parent.parent
    try:
        Toolkit.init_option(str(proj_root))
    except Exception:
        pass

    windows = Toolkit.find_desktop_windows()

    for win in windows:
        if win.class_name == "UnrealWindow":
            hwnd = int(win.hwnd)
            logger.log(f"找到窗口(类名): hWnd={hwnd}, title={win.window_name}")
            return hwnd

    keywords = ["巅峰极速", "g112", "Racing Master"]
    for win in windows:
        for kw in keywords:
            if kw in win.window_name:
                hwnd = int(win.hwnd)
                logger.log(f"找到窗口(标题): hWnd={hwnd}, title={win.window_name}")
                return hwnd

    GAME_PID = 0
    if GAME_PID:
        hwnd = hwnd_from_pid(GAME_PID)
        if hwnd:
            logger.log(f"找到窗口(PID): hWnd={hwnd}")
            return hwnd

    logger.log("未找到游戏窗口，可用窗口前10个:", "ERROR")
    for win in windows[:10]:
        logger.log(f"  hWnd={win.hwnd}, class={win.class_name}, title={win.window_name}", "ERROR")

    return 0
