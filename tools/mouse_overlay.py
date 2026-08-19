#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""鼠标位置 Overlay 小工具（纯 Win32 / 零依赖）。

在虚拟屏幕（含多显示器）上绘制一个跟随鼠标的亮绿色十字准星 + 坐标标签，
供 MCP 截图时让 AI 看见鼠标实际位置与绝对坐标。

原理：
- 一个覆盖整个虚拟屏幕的分层窗口（WS_EX_LAYERED），置顶、不激活、点击穿透
- 用 UpdateLayeredWindow + 颜色键(colorkey) 把背景(洋红)挖空成透明
- 准星/文字先画一层深色偏移阴影，再画亮绿主体，形成淡阴影立体感
- 通过 GetCursorPos 轮询鼠标屏幕坐标，定时重绘
- 系统托盘图标提供菜单：显示/隐藏准星、退出

用法：
    python tools/mouse_overlay.py

退出：托盘图标右键 → 退出（Esc / 鼠标右键因点击穿透与不激活已不可用）。
"""
import ctypes
import ctypes.wintypes as wt

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32
shell32 = ctypes.windll.shell32

# ---- 常量 ----
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_POPUP = 0x80000000
WS_VISIBLE = 0x10000000

ULW_COLORKEY = 0x00000001
DIB_RGB_COLORS = 0
BI_RGB = 0
PS_SOLID = 0
TRANSPARENT = 1

# 背景颜色键（该颜色将被挖空成透明）
KEY = (0xFF, 0x00, 0xFF)          # 洋红 RGB
# 准星主体：亮绿色 #50FF50（B,G,R 顺序）
FG = (0x50, 0xFF, 0x50)
# 淡阴影：深绿（B,G,R 顺序）
SHADOW = (0x10, 0x30, 0x20)
# 阴影偏移量（px）
SHADOW_DX = 1
SHADOW_DY = 1
# 准星半臂长
HALF = 12
# 轮询/重绘间隔（ms）
POLL_MS = 30
# 每隔多少 tick 强制拉回最前一次（30ms × 10 = 每 300ms）
TOPMOST_TICKS = 10

# ---- 托盘相关 ----
WM_APP = 0x8000
WM_TRAY = WM_APP + 1
WM_RBUTTONUP = 0x0205
WM_LBUTTONDBLCLK = 0x0203
ID_TRAY = 1
ID_TOGGLE = 1001
ID_EXIT = 1002
NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
IDI_APPLICATION = 32512
MF_STRING = 0x00000000
TPM_RETURNCMD = 0x0100
TPM_RIGHTBUTTON = 0x0002
SW_HIDE = 0
SW_SHOW = 5

# ---- 窗口消息 ----
WM_DESTROY = 0x0002
WM_TIMER = 0x0113

instances = {}


class WNDCLASSW(ctypes.Structure):
    """wintypes 未提供，自行定义（32/64 位域宽一致）。"""
    _fields_ = [
        ("style", ctypes.c_uint),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", ctypes.c_void_p),
        ("hIcon", ctypes.c_void_p),
        ("hCursor", ctypes.c_void_p),
        ("hbrBackground", ctypes.c_void_p),
        ("lpszMenuName", ctypes.c_wchar_p),
        ("lpszClassName", ctypes.c_wchar_p),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", ctypes.c_ushort),
        ("biBitCount", ctypes.c_ushort),
        ("biCompression", ctypes.c_uint),
        ("biSizeImage", ctypes.c_uint),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", ctypes.c_uint),
        ("biClrImportant", ctypes.c_uint),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER)]


class TRAYUNION(ctypes.Union):
    _fields_ = [("uTimeout", ctypes.c_uint), ("uVersion", ctypes.c_uint)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("hWnd", wt.HWND),
        ("uID", ctypes.c_uint),
        ("uFlags", ctypes.c_uint),
        ("uCallbackMessage", ctypes.c_uint),
        ("hIcon", wt.HICON),
        ("szTip", ctypes.c_wchar * 128),
        ("dwState", ctypes.c_uint),
        ("dwStateMask", ctypes.c_uint),
        ("szInfo", ctypes.c_wchar * 256),
        ("du", TRAYUNION),
        ("szInfoTitle", ctypes.c_wchar * 64),
        ("dwInfoFlags", ctypes.c_uint),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", wt.HICON),
    ]


# ---- User32 / GDI 原型 ----
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
user32.GetCursorPos.restype = wt.BOOL
user32.CreateWindowExW.argtypes = [
    wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
user32.CreateWindowExW.restype = wt.HWND
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.SetWindowPos.argtypes = [
    wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
user32.SetTimer.argtypes = [wt.HWND, ctypes.c_size_t, wt.UINT, ctypes.c_size_t]
user32.UpdateLayeredWindow.argtypes = [
    wt.HWND, wt.HDC, ctypes.POINTER(wt.POINT), ctypes.POINTER(wt.SIZE),
    wt.HDC, ctypes.POINTER(wt.POINT), wt.COLORREF,
    ctypes.c_void_p, wt.DWORD]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
user32.GetMessageW.restype = wt.BOOL
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.FillRect.argtypes = [wt.HDC, ctypes.POINTER(wt.RECT), wt.HBRUSH]
# 托盘
shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconW.restype = wt.BOOL
user32.LoadIconW.argtypes = [wt.HINSTANCE, ctypes.c_void_p]
user32.LoadIconW.restype = wt.HICON
user32.DestroyIcon.argtypes = [wt.HICON]
user32.CreatePopupMenu.argtypes = []
user32.CreatePopupMenu.restype = wt.HMENU
user32.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR]
user32.AppendMenuW.restype = wt.BOOL
user32.TrackPopupMenu.argtypes = [
    wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wt.HWND, ctypes.c_void_p]
user32.TrackPopupMenu.restype = ctypes.c_int
user32.DestroyMenu.argtypes = [wt.HMENU]
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]

gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateDIBSection.restype = wt.HBITMAP
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wt.HDC]
gdi32.CreateSolidBrush.argtypes = [wt.COLORREF]
gdi32.CreateSolidBrush.restype = wt.HBRUSH
gdi32.CreatePen.argtypes = [ctypes.c_int, ctypes.c_int, wt.COLORREF]
gdi32.CreatePen.restype = wt.HPEN
gdi32.MoveToEx.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.POINTER(wt.POINT)]
gdi32.LineTo.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
gdi32.SetBkMode.argtypes = [wt.HDC, ctypes.c_int]
gdi32.SetTextColor.argtypes = [wt.HDC, wt.COLORREF]
gdi32.TextOutW.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, wt.LPCWSTR, ctypes.c_int]
gdi32.Ellipse.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]

gdi32.CreateDIBSection.argtypes = [
    wt.HDC, ctypes.POINTER(BITMAPINFO), wt.UINT,
    ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD]


def get_virtual_screen():
    x = user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
    y = user32.GetSystemMetrics(77)
    w = user32.GetSystemMetrics(78)
    h = user32.GetSystemMetrics(79)
    return x, y, w, h


def get_cursor_pos():
    pt = wt.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def _color(rgb):
    return rgb[0] | (rgb[1] << 8) | (rgb[2] << 16)


class MouseOverlay:
    def __init__(self):
        self.vx, self.vy, self.vw, self.vh = get_virtual_screen()
        self.cx, self.cy = 0, 0
        self._tx, self._ty = 0, 0
        self.hinst = kernel32.GetModuleHandleW(None)
        self._visible = True
        self._ticks = 0

        wc = WNDCLASSW()
        wc.lpfnWndProc = ctypes.cast(_wnd_proc, ctypes.c_void_p)
        wc.hInstance = self.hinst
        wc.lpszClassName = "MouseOverlayWnd"
        wc.hCursor = None
        self.atom = user32.RegisterClassW(ctypes.byref(wc))

        self.hwnd = user32.CreateWindowExW(
            WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
            "MouseOverlayWnd", "MouseOverlay",
            WS_POPUP | WS_VISIBLE,
            self.vx, self.vy, self.vw, self.vh,
            None, None, self.hinst, None)
        user32.SetWindowPos(
            self.hwnd, -1, self.vx, self.vy, self.vw, self.vh,
            0x0010 | 0x0040)  # SWP_NOACTIVATE | SWP_SHOWWINDOW
        user32.SetTimer(self.hwnd, 1, POLL_MS, 0)
        instances[self.hwnd] = self
        self._add_tray()
        self._render()

    # ---- 托盘 ----

    def _add_tray(self):
        self._tray_icon = user32.LoadIconW(None, IDI_APPLICATION)
        self._nid = NOTIFYICONDATAW()
        self._nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        self._nid.hWnd = self.hwnd
        self._nid.uID = ID_TRAY
        self._nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        self._nid.uCallbackMessage = WM_TRAY
        self._nid.hIcon = self._tray_icon
        self._nid.szTip = "MouseOverlay 准星"
        shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self._nid))

    def _remove_tray(self):
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
        if self._tray_icon:
            user32.DestroyIcon(self._tray_icon)
            self._tray_icon = None

    def _show_tray_menu(self):
        user32.SetForegroundWindow(self.hwnd)
        menu = user32.CreatePopupMenu()
        label = "隐藏准星" if self._visible else "显示准星"
        user32.AppendMenuW(menu, MF_STRING, ID_TOGGLE, label)
        user32.AppendMenuW(menu, MF_STRING, ID_EXIT, "退出")
        mx, my = get_cursor_pos()
        cmd = user32.TrackPopupMenu(
            menu, TPM_RIGHTBUTTON | TPM_RETURNCMD, mx, my, 0, self.hwnd, None)
        user32.DestroyMenu(menu)
        return cmd

    def _handle_tray(self, lparam):
        if lparam == WM_RBUTTONUP:
            cmd = self._show_tray_menu()
            if cmd == ID_EXIT:
                self.quit()
            elif cmd == ID_TOGGLE:
                self.toggle_visible()
        elif lparam == WM_LBUTTONDBLCLK:
            self.toggle_visible()

    def toggle_visible(self):
        self._visible = not self._visible
        user32.ShowWindow(self.hwnd, SW_SHOW if self._visible else SW_HIDE)

    # ---- 绘制 ----

    def _render(self):
        """重绘分层窗口：洋红背景挖空 + 阴影 + 亮绿准星 + 坐标文本。"""
        dst = wt.POINT(0, 0)
        src = wt.POINT(0, 0)
        size = wt.SIZE(self.vw, self.vh)

        hdc = gdi32.CreateCompatibleDC(None)
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = self.vw
        bmi.bmiHeader.biHeight = -self.vh   # top-down
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = BI_RGB
        bits = ctypes.c_void_p()
        hbmp = gdi32.CreateDIBSection(
            hdc, ctypes.byref(bmi), DIB_RGB_COLORS,
            ctypes.byref(bits), None, 0)
        old = gdi32.SelectObject(hdc, hbmp)

        key = _color(KEY)

        # 背景填洋红（将被挖空）
        brush = gdi32.CreateSolidBrush(key)
        rect = wt.RECT(0, 0, self.vw, self.vh)
        user32.FillRect(hdc, ctypes.byref(rect), brush)
        gdi32.DeleteObject(brush)

        # 计算坐标文本位置（跟随鼠标，自动避让屏幕边缘）
        text = f"({self.cx + self.vx},{self.cy + self.vy})"
        tx = self.cx + HALF + 8
        ty = self.cy - HALF - 20
        text_w = len(text) * 9
        if tx + text_w > self.vw - 4:
            tx = self.cx - HALF - 8 - text_w
        if ty < 2:
            ty = self.cy + HALF + 4
        self._tx, self._ty = tx, ty

        # 先画深色阴影（偏移），再画亮绿主体
        self._draw(hdc, text, SHADOW_DX, SHADOW_DY, _color(SHADOW))
        self._draw(hdc, text, 0, 0, _color(FG))

        user32.UpdateLayeredWindow(
            self.hwnd, None, ctypes.byref(dst), ctypes.byref(size),
            hdc, ctypes.byref(src), key, None, ULW_COLORKEY)

        gdi32.SelectObject(hdc, old)
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(hdc)

    def _draw(self, hdc, text, dx, dy, color):
        """在 hdc 上以指定颜色(+偏移)绘制准星、中心点与坐标文本。"""
        cx = self.cx + dx
        cy = self.cy + dy

        pen = gdi32.CreatePen(PS_SOLID, 2, color)
        gdi32.SelectObject(hdc, pen)
        gdi32.MoveToEx(hdc, cx - HALF, cy, None)
        gdi32.LineTo(hdc, cx + HALF, cy)
        gdi32.MoveToEx(hdc, cx, cy - HALF, None)
        gdi32.LineTo(hdc, cx, cy + HALF)
        gdi32.DeleteObject(pen)

        dot_brush = gdi32.CreateSolidBrush(color)
        gdi32.SelectObject(hdc, dot_brush)
        gdi32.Ellipse(hdc, cx - 3, cy - 3, cx + 3, cy + 3)
        gdi32.DeleteObject(dot_brush)

        gdi32.SetBkMode(hdc, TRANSPARENT)
        gdi32.SetTextColor(hdc, color)
        gdi32.TextOutW(hdc, self._tx + dx, self._ty + dy, text, len(text))

    # ---- 事件 ----

    def on_mouse(self):
        x, y = get_cursor_pos()
        nc_x, nc_y = x - self.vx, y - self.vy
        if nc_x != self.cx or nc_y != self.cy:
            self.cx, self.cy = nc_x, nc_y
            self._render()
        # 周期性把窗口拉回最前，防止被其他置顶窗口覆盖
        self._ticks += 1
        if self._ticks >= TOPMOST_TICKS:
            self._ticks = 0
            self._keep_topmost()

    def _keep_topmost(self):
        if not self._visible:
            return
        user32.SetWindowPos(
            self.hwnd, -1, self.vx, self.vy, self.vw, self.vh,
            0x0002 | 0x0001 | 0x0010)  # SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE

    def quit(self):
        user32.PostMessageW(self.hwnd, WM_DESTROY, 0, 0)

    def run(self):
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))


@ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
def _wnd_proc(hwnd, msg, wparam, lparam):
    self = instances.get(hwnd)
    if msg == WM_TIMER:
        if self:
            self.on_mouse()
        return 0
    if msg == WM_TRAY:
        if self:
            self._handle_tray(lparam)
        return 0
    if msg == WM_DESTROY:
        if self:
            self._remove_tray()
        instances.pop(hwnd, None)
        user32.PostQuitMessage(0)
        return 0
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


if __name__ == "__main__":
    MouseOverlay().run()