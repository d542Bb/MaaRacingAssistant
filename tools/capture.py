import cv2
import time
from pathlib import Path
from maa.tasker import Tasker
from maa.resource import Resource
from maa.controller import Win32Controller
from maa.toolkit import Toolkit
import numpy as np


def main():
    out = Path(__file__).parent.parent / "dataset" / "images" / "train"
    out.mkdir(parents=True, exist_ok=True)

    # 修正：尝试多种 init_option 调用方式
    try:
        Toolkit.init_option(str(Path(__file__).parent.parent))
    except Exception:
        try:
            Toolkit.init_option(str(Path(__file__).parent.parent), "{}")
        except Exception:
            pass  # 如果都失败，继续试试

    # 修正：API 是 find_desktop_windows，返回 DesktopWindow 对象列表
    windows = Toolkit.find_desktop_windows()

    hwnd = 0
    for win in windows:
        if win.class_name == "UnrealWindow" or "巅峰极速" in win.window_name or "g112" in win.window_name:
            hwnd = win.hwnd
            print(f"找到窗口: hWnd={win.hwnd}, class={win.class_name}, title={win.window_name}")
            break

    if hwnd == 0:
        print("未找到游戏窗口，可用窗口前10个:")
        for win in windows[:10]:
            print(f"  hWnd={win.hwnd}, class={win.class_name}, title={win.window_name}")
        return

    ctrl = Win32Controller(hWnd=hwnd)
    if not ctrl.post_connection().wait():
        print("连接失败")
        return

    print("=== 截图工具 ===")
    print("按 [S] 保存，按 [Q] 退出")
    count = 0

    while True:
        img = ctrl.post_screencap().wait()
        if img is None:
            time.sleep(0.1)
            continue

        arr = np.array(img)
        if arr.shape[2] == 4:
            arr = cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
        else:
            arr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)

        cv2.imshow("Capture (S=save, Q=quit)", arr)
        key = cv2.waitKey(1) & 0xFF
        if key == ord('s'):
            f = out / f"cap_{count:04d}.png"
            cv2.imwrite(str(f), arr)
            print(f"Saved {f}")
            count += 1
        elif key == ord('q'):
            break

    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()