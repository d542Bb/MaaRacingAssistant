#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""最小验证单元：游戏静音是否真生效。

背景：audio_volume.set_game_volume 走「默认音频会话」（GetSimpleAudioVolume(NULL, pid)），
API 返回 True、读回也是 0.0，但游戏声音可能仍在——怀疑游戏实际发声走的是
【非默认会话】或【另一个进程】。本脚本用于定位真相。

用法（在游戏运行、有声音时执行）：
    python tools/verify_mute.py            # 模式A：静音默认会话 + 读回 + 会话枚举对比，人工听
    python tools/verify_mute.py --probe    # 模式B：逐个会话静音探测（每个 1.5s），
                                           #       人工听哪个会话静音时游戏无声

判定：
- 模式A 静音后游戏仍响 → 游戏不走默认会话 → 跑 --probe 定位；
- 模式B 听到某会话静音时游戏无声 → 记录该会话序号/名称，据此改 audio_volume。
"""
from __future__ import annotations

import argparse
import ctypes
import sys
import time
from ctypes import WINFUNCTYPE, byref, c_float, c_long, c_ulong, c_void_p, c_wchar_p
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

from maaracing_assistant.core.audio_volume import (  # noqa: E402
    CLSID_MMDeviceEnumerator, COM, IID_IAudioSessionControl2,
    IID_IAudioSessionEnumerator, IID_IAudioSessionManager2,
    IID_IMMDeviceEnumerator, IID_ISimpleAudioVolume,
    _Activate, _GetCount, _GetDefaultAudioEndpoint, _GetProcessId,
    _GetSession, _IUnknown_QI, _release, get_game_volume, set_game_volume,
)
from maaracing_assistant.core.window_utils import find_game_hwnd  # noqa: E402

# ---- 会话枚举 vtable（audio_volume 已改为按 PID 直达，枚举原型在脚本内自包含） ----
# IAudioSessionManager2: GetSessionEnumerator = 5
_GetSessionEnumerator = WINFUNCTYPE(
    c_long, ctypes.POINTER(COM), ctypes.POINTER(ctypes.POINTER(COM)))
# IAudioSessionEnumerator: GetCount=3 / GetSession=4
_GetCount = WINFUNCTYPE(c_long, ctypes.POINTER(COM), ctypes.POINTER(ctypes.c_int))
_GetSession = WINFUNCTYPE(
    c_long, ctypes.POINTER(COM), ctypes.c_int, ctypes.POINTER(ctypes.POINTER(COM)))

# ---- 本脚本用到的额外 vtable ----
# ISimpleAudioVolume: SetMute = 5
_SetMute = WINFUNCTYPE(c_long, ctypes.POINTER(COM), c_long, ctypes.POINTER(ctypes.c_void_p))
# IAudioSessionControl: GetDisplayName = 4
_GetDisplayName = WINFUNCTYPE(c_long, ctypes.POINTER(COM), ctypes.POINTER(c_wchar_p))
# IAudioSessionControl: GetState = 3
_GetState = WINFUNCTYPE(c_long, ctypes.POINTER(COM), ctypes.POINTER(c_long))

ole32 = ctypes.WinDLL("ole32", use_last_error=True)
ole32.CoInitializeEx.argtypes = [c_void_p, c_ulong]
ole32.CoInitializeEx.restype = c_long
ole32.CoCreateInstance.argtypes = [ctypes.c_void_p, c_void_p, c_ulong, c_void_p, ctypes.POINTER(c_void_p)]
ole32.CoCreateInstance.restype = c_long
ole32.CoTaskMemFree.argtypes = [c_void_p]
ole32.CoTaskMemFree.restype = None


def _list_sessions():
    """枚举全部渲染会话 → [(display_name, state, volume, mute, pid)]。"""
    out = []
    if ole32.CoInitializeEx(None, 0x2) not in (0, 1):
        return out
    dev_enum = c_void_p()
    ole32.CoCreateInstance(byref(CLSID_MMDeviceEnumerator), None, 0x17,
                           byref(IID_IMMDeviceEnumerator), byref(dev_enum))
    enum_iface = ctypes.cast(dev_enum, ctypes.POINTER(COM))
    out_device = ctypes.POINTER(COM)()
    _GetDefaultAudioEndpoint(enum_iface.contents.vtbl[4])(enum_iface, 0, 1, byref(out_device))
    mgr = c_void_p()
    _Activate(out_device.contents.vtbl[3])(out_device, byref(IID_IAudioSessionManager2), 0x17, None, byref(mgr))
    mgr_iface = ctypes.cast(mgr, ctypes.POINTER(COM))
    enum_out = ctypes.POINTER(COM)()
    _GetSessionEnumerator(mgr_iface.contents.vtbl[5])(mgr_iface, byref(enum_out))
    enum_if = ctypes.cast(enum_out, ctypes.POINTER(COM))
    count = ctypes.c_int(0)
    _GetCount(enum_if.contents.vtbl[3])(enum_if, byref(count))
    for i in range(count.value):
        one = ctypes.POINTER(COM)()
        if _GetSession(enum_if.contents.vtbl[4])(enum_if, i, byref(one)) != 0 or not one:
            continue
        name = c_wchar_p()
        _GetDisplayName(one.contents.vtbl[4])(one, byref(name))
        st = c_long(0)
        _GetState(one.contents.vtbl[3])(one, byref(st))
        # 进程 PID（IAudioSessionControl2.GetProcessId = vtbl[14]）
        pid = -1
        ctl2 = c_void_p()
        if _IUnknown_QI(one.contents.vtbl[0])(one, byref(IID_IAudioSessionControl2), byref(ctl2)) == 0 \
                and ctl2.value:
            ctl2_iface = ctypes.cast(ctl2, ctypes.POINTER(COM))
            p = c_ulong(0)
            if _GetProcessId(ctl2_iface.contents.vtbl[14])(ctl2_iface, byref(p)) == 0:
                pid = p.value
            _release(ctl2_iface)
        vol_f, mute = -1.0, -1
        vol_out = c_void_p()
        if _IUnknown_QI(one.contents.vtbl[0])(one, byref(IID_ISimpleAudioVolume), byref(vol_out)) == 0 \
                and vol_out.value:
            vol_iface = ctypes.cast(vol_out, ctypes.POINTER(COM))
            f = c_float(0)
            m = c_long(0)
            # ISimpleAudioVolume: GetMasterVolume=4 / GetMute=6
            _GetMasterVolume(vol_iface.contents.vtbl[4])(vol_iface, byref(f))
            _GetMute(vol_iface.contents.vtbl[6])(vol_iface, byref(m))
            vol_f, mute = f.value, m.value
            _release(vol_iface)
        out.append((name.value or "", st.value, vol_f, mute, pid))
        if name.value:
            ole32.CoTaskMemFree(ctypes.cast(name, c_void_p))
        _release(one)
    _release(ctypes.cast(enum_out, ctypes.POINTER(COM)))
    _release(ctypes.cast(mgr, ctypes.POINTER(COM)))
    _release(ctypes.cast(out_device, ctypes.POINTER(COM)))
    _release(ctypes.cast(dev_enum, ctypes.POINTER(COM)))
    ole32.CoUninitialize()
    return out


def _session_set_mute(index: int, sessions, mute: bool) -> bool:
    """按枚举序号静音/取消静音第 index 个会话。返回是否成功。"""
    ok = False
    if ole32.CoInitializeEx(None, 0x2) not in (0, 1):
        return False
    dev_enum = c_void_p()
    ole32.CoCreateInstance(byref(CLSID_MMDeviceEnumerator), None, 0x17,
                           byref(IID_IMMDeviceEnumerator), byref(dev_enum))
    enum_iface = ctypes.cast(dev_enum, ctypes.POINTER(COM))
    out_device = ctypes.POINTER(COM)()
    _GetDefaultAudioEndpoint(enum_iface.contents.vtbl[4])(enum_iface, 0, 1, byref(out_device))
    mgr = c_void_p()
    _Activate(out_device.contents.vtbl[3])(out_device, byref(IID_IAudioSessionManager2), 0x17, None, byref(mgr))
    mgr_iface = ctypes.cast(mgr, ctypes.POINTER(COM))
    enum_out = ctypes.POINTER(COM)()
    _GetSessionEnumerator(mgr_iface.contents.vtbl[5])(mgr_iface, byref(enum_out))
    enum_if = ctypes.cast(enum_out, ctypes.POINTER(COM))
    one = ctypes.POINTER(COM)()
    if _GetSession(enum_if.contents.vtbl[4])(enum_if, index, byref(one)) == 0 and one:
        vol_out = c_void_p()
        if _IUnknown_QI(one.contents.vtbl[0])(one, byref(IID_ISimpleAudioVolume), byref(vol_out)) == 0 \
                and vol_out.value:
            vol_iface = ctypes.cast(vol_out, ctypes.POINTER(COM))
            ok = _SetMute(vol_iface.contents.vtbl[5])(vol_iface, 1 if mute else 0, None) == 0
            _release(vol_iface)
        _release(one)
    _release(ctypes.cast(enum_out, ctypes.POINTER(COM)))
    _release(ctypes.cast(mgr, ctypes.POINTER(COM)))
    _release(ctypes.cast(out_device, ctypes.POINTER(COM)))
    _release(ctypes.cast(dev_enum, ctypes.POINTER(COM)))
    ole32.CoUninitialize()
    return ok


# ISimpleAudioVolume: GetMasterVolume=4 / GetMute=6（本脚本独立声明，避免依赖顺序）
_GetMasterVolume = WINFUNCTYPE(c_long, ctypes.POINTER(COM), ctypes.POINTER(c_float))
_GetMute = WINFUNCTYPE(c_long, ctypes.POINTER(COM), ctypes.POINTER(c_long))


def dump(title: str, sessions) -> None:
    print(f"\n=== {title}（共 {len(sessions)} 个会话）===")
    for i, (name, st, vol, mute, pid) in enumerate(sessions):
        st_s = {0: "inactive", 1: "active", 2: "expired"}.get(st, str(st))
        pid_s = f"pid={pid}" if pid >= 0 else "pid=?"
        print(f"  [{i}] name={name!r} state={st_s} vol={vol} mute={mute} {pid_s}")


def main() -> int:
    parser = argparse.ArgumentParser(description="游戏静音最小验证单元")
    parser.add_argument("--probe", action="store_true",
                        help="逐会话静音探测：每个会话静音 1.5 秒，人工听哪个是游戏的")
    parser.add_argument("--hold", action="store_true", help="结束后暂停等待回车")
    args = parser.parse_args()

    hwnd = find_game_hwnd()
    if not hwnd:
        print("未找到游戏窗口，请先启动游戏", file=sys.stderr)
        return 1
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetWindowThreadProcessId.argtypes = [c_void_p, ctypes.POINTER(c_ulong)]
    user32.GetWindowThreadProcessId.restype = c_ulong
    pid = c_ulong(0)
    user32.GetWindowThreadProcessId(hwnd, byref(pid))
    try:
        import psutil
        pname = psutil.Process(pid.value).name()
    except Exception:
        pname = "?"
    print(f"游戏窗口 hWnd={hwnd} PID={pid.value} 进程名={pname}")

    if args.probe:
        sessions = _list_sessions()
        dump("探测前", sessions)
        print("\n逐会话探测：每个会话静音 1.5 秒，注意听游戏声音何时消失（0 表示该会话是游戏的）")
        for i, (name, st, vol, mute, spid) in enumerate(sessions):
            if vol <= 0:
                print(f"  [{i}] vol={vol} 跳过（本就无声）")
                continue
            ok = _session_set_mute(i, sessions, True)
            print(f"  [{i}] 静音中… name={name!r} pid={spid} vol={vol}（游戏无声=这个会话）", flush=True)
            time.sleep(1.5)
            _session_set_mute(i, sessions, False)
            print(f"  [{i}] 已恢复（ok={ok}）")
        print("\n探测完成：请告诉我哪个序号静音时游戏无声")
    else:
        before = _list_sessions()
        dump("静音前", before)
        print("\n[动作] 按 PID 匹配静音游戏全部会话…")
        n = set_game_volume(hwnd, 0.0)
        print(f"  set_game_volume(0.0) 命中会话数 = {n}")
        print(f"  读回匹配会话音量 = {get_game_volume(hwnd)}")
        print("  现在请听游戏声音是否消失")
        time.sleep(2.0)
        after = _list_sessions()
        dump("静音后（对照静音前的会话列表）", after)
        print("\n[动作] 恢复游戏音量为 100%…")
        n2 = set_game_volume(hwnd, 1.0)
        print(f"  set_game_volume(1.0) 命中会话数 = {n2}")
        print(f"  读回匹配会话音量 = {get_game_volume(hwnd)}")
        print("\n判定：命中会话数 > 0 且读回为 0.0 → 静音已作用于游戏真实会话；"
              "若游戏仍响，把上面会话列表（含 pid 列）发我")

    if args.hold:
        try:
            input("\n按回车退出…")
        except EOFError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
