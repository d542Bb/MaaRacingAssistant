#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
坐标参考系诊断脚本（一次性使用）
功能：
1. 连接游戏窗口
2. 分别用 MAA post_screencap() 和 ctypes GDI 截图
3. 打印两者的分辨率、原点位置
4. 在截图上绘制坐标轴和中心点，保存到 debug/diagnose/
5. 测试左摇杆小幅推动，观察光标响应方向

运行方式：管理员权限运行
  python diagnose_coords.py
"""

import sys
import time
import cv2
import numpy as np
import ctypes
from ctypes import wintypes
from pathlib import Path

from maa.controller import Win32Controller
from maa.toolkit import Toolkit
import vgamepad as vg


def find_game_hwnd():
    try:
        Toolkit.init_option(str(Path(__file__).parent))
    except Exception:
        pass

    windows = Toolkit.find_desktop_windows()
    for win in windows:
        if win.class_name == "UnrealWindow":
            print(f"[找到窗口] class={win.class_name}, title={win.window_name}, hwnd={win.hwnd}")
            return win.hwnd

    keywords = ["巅峰极速", "g112", "Racing Master"]
    for win in windows:
        for kw in keywords:
            if kw in win.window_name:
                print(f"[找到窗口] class={win.class_name}, title={win.window_name}, hwnd={win.hwnd}")
                return win.hwnd

    print("[错误] 未找到游戏窗口")
    for win in windows[:10]:
        print(f"  hwnd={win.hwnd}, class={win.class_name}, title={win.window_name}")
    return 0


def screencap_maa(ctrl):
    """MAA 截图"""
    try:
        job = ctrl.post_screencap()
        job.wait()
        img = job.get()
        if img is None:
            return None
        if hasattr(img, "numpy"):
            arr = img.numpy()
        elif isinstance(img, np.ndarray):
            arr = img
        else:
            arr = np.array(img)
        if arr is None or arr.size == 0 or arr.ndim < 3:
            return None
        return arr
    except Exception as e:
        print(f"[MAA截图异常] {e}")
        return None


def screencap_ctypes(hwnd):
    """ctypes GDI 截图（窗口客户区）"""
    try:
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        rect = ctypes.wintypes.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(rect))
        w, h = rect.right, rect.bottom
        if w <= 0 or h <= 0:
            return None, (0, 0)

        hwnd_dc = user32.GetDC(hwnd)
        if not hwnd_dc:
            return None, (0, 0)
        try:
            mem_dc = gdi32.CreateCompatibleDC(hwnd_dc)
            if not mem_dc:
                return None, (0, 0)
            try:
                bitmap = gdi32.CreateCompatibleBitmap(hwnd_dc, w, h)
                if not bitmap:
                    return None, (0, 0)
                try:
                    gdi32.SelectObject(mem_dc, bitmap)
                    gdi32.BitBlt(mem_dc, 0, 0, w, h, hwnd_dc, 0, 0, 0x00CC0020)
                    bmp_info = ctypes.create_string_buffer(40)
                    gdi32.GetObjectA(bitmap, 40, bmp_info)
                    bpp = 32
                    stride = ((w * bpp + 31) // 32) * 4
                    buf = ctypes.create_string_buffer(stride * h)
                    gdi32.GetBitmapBits(bitmap, len(buf), buf)
                    arr = np.frombuffer(buf, dtype=np.uint8).reshape((h, w, 4))[:, :, :3]
                    arr = cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)
                    return arr, (w, h)
                finally:
                    gdi32.DeleteObject(bitmap)
            finally:
                gdi32.DeleteDC(mem_dc)
        finally:
            user32.ReleaseDC(hwnd, hwnd_dc)
    except Exception as e:
        print(f"[ctypes截图异常] {e}")
        return None, (0, 0)


def draw_coordinate_info(img, title):
    """在图像上绘制坐标轴、原点、中心点、尺寸信息"""
    h, w = img.shape[:2]
    vis = img.copy()

    # 红色十字：原点 (0,0)
    cv2.drawMarker(vis, (0, 0), (255, 0, 0), cv2.MARKER_CROSS, 40, 2)
    cv2.putText(vis, "Origin(0,0)", (5, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 0, 0), 2)

    # 绿色十字：中心点
    cx, cy = w // 2, h // 2
    cv2.drawMarker(vis, (cx, cy), (0, 255, 0), cv2.MARKER_CROSS, 40, 2)
    cv2.putText(vis, f"Center({cx},{cy})", (cx + 10, cy - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

    # 蓝色坐标轴
    cv2.line(vis, (0, cy), (w, cy), (0, 0, 255), 1)
    cv2.line(vis, (cx, 0), (cx, h), (0, 0, 255), 1)

    # 黄色：四个象限标注
    cv2.putText(vis, "Q1", (w - 50, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
    cv2.putText(vis, "Q2", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
    cv2.putText(vis, "Q3", (10, h - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
    cv2.putText(vis, "Q4", (w - 50, h - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)

    # 底部尺寸信息
    info = f"{title} | Size={w}x{h}"
    cv2.putText(vis, info, (10, h - 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

    return vis


def test_joystick_directions(gpad):
    """测试左摇杆四个方向，观察光标移动方向"""
    print("\n=== 摇杆方向测试 ===")
    print("请在 5 秒内观察游戏内光标移动方向")
    time.sleep(1)

    tests = [
        ("右", 20000, 0),
        ("左", -20000, 0),
        ("下", 0, 20000),
        ("上", 0, -20000),
    ]

    for name, x, y in tests:
        print(f"  推摇杆 [{name}] x={x}, y={y} ...", end=" ")
        gpad.left_joystick(x_value=x, y_value=y)
        gpad.update()
        time.sleep(0.5)
        gpad.left_joystick(x_value=0, y_value=0)
        gpad.update()
        print("完成")
        time.sleep(0.5)

    print("=== 摇杆测试结束 ===\n")


def main():
    print("=" * 60)
    print("坐标参考系诊断脚本")
    print("=" * 60)

    # 1. 找窗口
    hwnd = find_game_hwnd()
    if hwnd == 0:
        sys.exit(1)

    # 2. 连接 MAA
    print(f"\n[连接] Win32Controller(hWnd={hwnd})")
    ctrl = Win32Controller(hWnd=hwnd)
    if not ctrl.post_connection().wait():
        print("[错误] MAA 连接失败，请检查管理员权限")
        sys.exit(1)
    print("[连接] MAA 连接成功")

    # 3. 截图对比
    print("\n--- 截图对比 ---")

    arr_maa = screencap_maa(ctrl)
    arr_ctypes, ctypes_size = screencap_ctypes(hwnd)

    if arr_maa is not None:
        h, w = arr_maa.shape[:2]
        print(f"  MAA 截图:     {w}x{h}")
    else:
        print("  MAA 截图:     失败")

    if arr_ctypes is not None:
        cw, ch = ctypes_size
        print(f"  ctypes 截图:  {cw}x{ch}")
    else:
        print("  ctypes 截图:  失败")

    # 4. 保存调试图
    debug_dir = Path(__file__).parent / "debug" / "diagnose"
    debug_dir.mkdir(parents=True, exist_ok=True)

    if arr_maa is not None:
        vis_maa = draw_coordinate_info(arr_maa, "MAA")
        path_maa = debug_dir / "maa_screenshot.png"
        cv2.imwrite(str(path_maa), cv2.cvtColor(vis_maa, cv2.COLOR_RGB2BGR))
        print(f"  MAA 截图已保存: {path_maa}")

    if arr_ctypes is not None:
        vis_ctypes = draw_coordinate_info(arr_ctypes, "ctypes")
        path_ctypes = debug_dir / "ctypes_screenshot.png"
        cv2.imwrite(str(path_ctypes), cv2.cvtColor(vis_ctypes, cv2.COLOR_RGB2BGR))
        print(f"  ctypes 截图已保存: {path_ctypes}")

    # 5. 坐标系分析
    print("\n--- 坐标系分析 ---")
    if arr_maa is not None and arr_ctypes is not None:
        h, w = arr_maa.shape[:2]
        cw, ch = ctypes_size
        scale_x = cw / w
        scale_y = ch / h
        print(f"  窗口客户区(ctypes): {cw}x{ch}")
        print(f"  MAA 截图尺寸:       {w}x{h}")
        print(f"  缩放比例:           X={scale_x:.3f}, Y={scale_y:.3f}")
        if abs(scale_x - scale_y) < 0.01:
            print(f"  结论: 等比例缩放，统一缩放系数 ≈ {scale_x:.3f}")
        else:
            print(f"  警告: X/Y 缩放比例不一致！")
    else:
        print("  截图不完整，无法计算缩放比例")

    # 6. 测试按钮位置百分比
    if arr_maa is not None:
        h, w = arr_maa.shape[:2]
        btn_x = int(w * 0.898)
        btn_y = int(h * 0.751)
        print(f"\n  按钮百分比(0.898, 0.751) 在 MAA 截图中 = ({btn_x}, {btn_y})")
        if arr_ctypes is not None:
            cw, ch = ctypes_size
            btn_cx = int(cw * 0.898)
            btn_cy = int(ch * 0.751)
            print(f"  按钮百分比(0.898, 0.751) 在 ctypes 中   = ({btn_cx}, {btn_cy})")

    # 7. 摇杆方向测试（可选）
    print("\n是否测试摇杆方向？输入 y 开始，其他跳过:")
    choice = input("> ").strip().lower()
    if choice == "y":
        gpad = vg.VX360Gamepad()
        try:
            # 归零
            for _ in range(3):
                gpad.reset()
                gpad.left_joystick(x_value=0, y_value=0)
                gpad.update()
                time.sleep(0.05)
            test_joystick_directions(gpad)
        finally:
            gpad.reset()
            gpad.update()
            del gpad

    print("\n诊断完成。请检查 debug/diagnose/ 下的截图。")
    print("=" * 60)


if __name__ == "__main__":
    main()
