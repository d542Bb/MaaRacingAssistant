#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
窗口查找与物理手柄检测工具 + Win32 交互原语（DPI / 坐标换算 / 真实点击 / 按键计数 / 窗口调整）。
"""

import ctypes
import time
from ctypes import wintypes
from pathlib import Path

from maa.toolkit import Toolkit

from maaracing_assistant.logger import logger

# ---- Win32 常量 ----
_UD = ctypes.windll.user32
_SHCORE = ctypes.windll.shcore

_GWL_STYLE = -16
_GWL_EXSTYLE = -20
# GWL_STYLE 返回值是 32 位 LONG（64 位进程下 GetWindowLongW 语义不变）
_UD.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
_UD.GetWindowLongW.restype = ctypes.c_long
_UD.AdjustWindowRectEx.argtypes = [
    ctypes.POINTER(wintypes.RECT), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_UD.AdjustWindowRectEx.restype = wintypes.BOOL
_UD.SetWindowPos.argtypes = [
    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, wintypes.UINT]
_UD.SetWindowPos.restype = wintypes.BOOL
_UD.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
_UD.SetCursorPos.restype = wintypes.BOOL
_UD.GetForegroundWindow.restype = wintypes.HWND
_UD.GetAsyncKeyState.argtypes = [ctypes.c_int]
_UD.GetAsyncKeyState.restype = ctypes.c_short

# SendInput（INPUT/MOUSEINPUT 结构，dwExtraInfo 为 ULONG_PTR=指针宽）
INPUT_MOUSE = 0
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
# GetAsyncKeyState 的「当前按下」高位
_ASYNC_DOWN = 0x8000


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),  # ULONG_PTR（64 位下 8 字节）
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


_UD.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
_UD.SendInput.restype = wintypes.UINT


# =====================================================================
#  DPI awareness（进程级，须尽早调用；子进程不继承父进程配置）
# =====================================================================

def ensure_dpi_aware() -> bool:
    """显式建立 DPI awareness：首选 Per-Monitor(2)，失败回退 System DPI。返回是否成功。

    注意：DPI awareness 是进程级语义，Python sidecar 不会自动继承 C# shell 的配置，
    必须在创建任何窗口 / 初始化 Win32 坐标 API 之前调用（幂等，重复调用失败会忽略）。
    """
    try:
        # PROCESS_PER_MONITOR_DPI_AWARE = 2
        if _SHCORE.SetProcessDpiAwareness(2) == 0:
            return True
    except Exception:
        pass
    try:
        return bool(_UD.SetProcessDPIAware())
    except Exception:
        return False


# =====================================================================
#  客户区尺寸 / 屏幕原点（坐标换算的基础）
# =====================================================================

def window_client_origin(hwnd: int) -> tuple[int, int] | None:
    """客户区左上角在全屏坐标系中的位置（物理像素）。失败返回 None。"""
    pt = wintypes.POINT(0, 0)
    if not hwnd or not _UD.ClientToScreen(hwnd, ctypes.byref(pt)):
        return None
    return pt.x, pt.y


def window_client_size(hwnd: int) -> tuple[int, int] | None:
    """客户区物理尺寸 (w, h)。失败返回 None。"""
    rect = wintypes.RECT()
    if not hwnd or not _UD.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    return rect.right - rect.left, rect.bottom - rect.top


def norm_to_screen(hwnd: int, cx: float, cy: float, client_w: int, client_h: int) -> tuple[int, int] | None:
    """归一化意图坐标 (cx, cy) → 全屏物理坐标。

    以客户区物理尺寸为锚（与截图帧对齐），并按像素索引 clamp 防 (1,1) 落出客户区。
    """
    origin = window_client_origin(hwnd)
    if origin is None:
        return None
    ox, oy = origin
    x = min(client_w - 1, max(0, round(cx * client_w)))
    y = min(client_h - 1, max(0, round(cy * client_h)))
    return ox + x, oy + y


def verify_frame_client(hwnd: int, frame_w: int, frame_h: int) -> None:
    """启动时校验「截图帧尺寸 vs 客户区物理尺寸」：不等则映射可能偏移，WARNING 提示。"""
    size = window_client_size(hwnd)
    if size is None:
        return
    cw, ch = size
    if cw != frame_w or ch != frame_h:
        logger.log(
            f"[坐标] 截图帧尺寸({frame_w}x{frame_h}) ≠ 客户区物理尺寸({cw}x{ch})，"
            "归一化→屏幕映射可能偏移（本链路应 1:1）", "WARNING",
        )


# =====================================================================
#  真实点击（可见移动 → 停顿 → SendInput 左键）
# =====================================================================

def set_cursor_visible(sx: int, sy: int) -> bool:
    """把可见鼠标指针移到全屏坐标 (sx, sy)。"""
    if not sx or not sy:
        return False
    return bool(_UD.SetCursorPos(sx, sy))


def send_left_click(down_up_gap_ms: int = 30) -> bool:
    """SendInput 注入鼠标左键 按下→抬起（间隔 down_up_gap_ms）。返回是否成功。

    dx=dy=0 且无 MOUSEEVENTF_ABSOLUTE → 使用当前光标位置。
    """
    def _event(flags: int) -> bool:
        inp = _INPUT()
        inp.type = INPUT_MOUSE
        inp.u.mi.dwFlags = flags
        inp.u.mi.dx = 0
        inp.u.mi.dy = 0
        return _UD.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT)) == 1

    if not _event(MOUSEEVENTF_LEFTDOWN):
        return False
    time.sleep(max(0.02, down_up_gap_ms / 1000.0))
    return _event(MOUSEEVENTF_LEFTUP)


def is_foreground(hwnd: int) -> bool:
    """目标窗口是否为当前前台窗口（点击安全校验用，不主动抢前台）。"""
    if not hwnd:
        return True  # 无句柄时不拦截（正常运行时句柄必有效）
    return _UD.GetForegroundWindow() == hwnd


def count_pressed_keys() -> int:
    """统计当前同时按下的键盘按键数（GetAsyncKeyState，系统级）。

    只统计键盘键（跳过 0x01~0x06 鼠标键与保留码 0x0A）；命中 2 个即提前返回。
    """
    n = 0
    for vk in range(0x08, 0xFF):
        if vk == 0x0A:  # 保留
            continue
        if _UD.GetAsyncKeyState(vk) & _ASYNC_DOWN:
            n += 1
            if n >= 2:
                return n
    return n


# =====================================================================
#  窗口尺寸调整（客户区 < 1280×720 时调大，≥ 则不动）
# =====================================================================

def ensure_game_window_min(hwnd: int, min_w: int = 1280, min_h: int = 720) -> bool:
    """客户区小于 min_w×min_h 时，用 AdjustWindowRectEx + SetWindowPos 把窗口调大。

    返回是否执行过调整；窗口无效 / 已是 ≥ 最小尺寸 → False 且不动。
    """
    size = window_client_size(hwnd)
    if size is None:
        logger.log("[坐标] 获取客户区尺寸失败，跳过窗口调整", "WARNING")
        return False
    cw, ch = size
    if cw >= min_w and ch >= min_h:
        return False

    style = _UD.GetWindowLongW(hwnd, _GWL_STYLE)
    ex_style = _UD.GetWindowLongW(hwnd, _GWL_EXSTYLE)
    rect = wintypes.RECT(0, 0, min_w, min_h)
    if not _UD.AdjustWindowRectEx(ctypes.byref(rect), style, False, ex_style):
        rect = wintypes.RECT(0, 0, min_w, min_h)
    win_w = rect.right - rect.left
    win_h = rect.bottom - rect.top

    ok = bool(_UD.SetWindowPos(
        hwnd, None, 0, 0, win_w, win_h,
        SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE,
    ))
    if ok:
        logger.log(
            f"[坐标] 游戏客户区 {cw}x{ch} < 1280x720 → 已调窗口到客户区 ≥{min_w}x{min_h}"
            f"（窗口外框 {win_w}x{win_h}）", "INFO",
        )
    else:
        logger.log("[坐标] 调整游戏窗口大小失败（SetWindowPos 返回 False）", "WARNING")
    return ok


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
