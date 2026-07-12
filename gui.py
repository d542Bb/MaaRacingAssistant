#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os
import ctypes
import tkinter as tk
from tkinter import scrolledtext
import threading
import time
from pathlib import Path
import ttkbootstrap as ttk
from ttkbootstrap.constants import *
from main import MaaRacingAssistantController, logger


def is_admin() -> bool:
    """检查当前是否以管理员权限运行"""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False
    
def run_as_admin():
    """以管理员权限重启自身（无控制台窗口）"""
    import subprocess
    
    # 用 pythonw.exe 代替 python.exe，不显示控制台
    python_exe = os.path.abspath(sys.executable)
    if python_exe.endswith("python.exe"):
        pythonw_exe = python_exe.replace("python.exe", "pythonw.exe")
    else:
        pythonw_exe = python_exe
    
    script_path = os.path.abspath(sys.argv[0])
    work_dir = os.path.abspath(os.getcwd())
    
    ctypes.windll.shell32.ShellExecuteW(
        None,
        "runas",
        pythonw_exe,          # ← 用 pythonw.exe
        f'"{script_path}"',
        work_dir,
        1
    )
    sys.exit(0)

class MRAGUI:
    def __init__(self, root: ttk.Window):
        self.root = root
        self.root.title("MRA")
        self.root.geometry("600x500")
        self.root.resizable(False, False)

        self.controller = MaaRacingAssistantController()
        self.running = False
        self.worker_thread = None

        self._build_ui()
        self._start_log_polling()

    def _build_ui(self):
        # 标题
        title = ttk.Label(self.root, text="MaaRacingAssistant", font=("Microsoft YaHei", 20, "bold"))
        title.pack(pady=(20, 5))

        desc = ttk.Label(
            self.root,
            text="巅峰极速 · 极速狂飙 自动刷分\n回合1赛车(YOLO+手柄) → 回合2放弃 → 循环",
            font=("Microsoft YaHei", 10),
            justify="center"
        )
        desc.pack(pady=(0, 15))

        # 权限提示
        admin_status = "管理员" if is_admin() else "普通用户"
        admin_style = "success" if is_admin() else "warning"
        admin_label = ttk.Label(self.root, text=f"当前权限: {admin_status}", bootstyle=admin_style, font=("Microsoft YaHei", 9))
        admin_label.pack(pady=(0, 5))

        # 状态栏
        self.status_var = tk.StringVar(value="就绪")
        status_frame = ttk.Frame(self.root)
        status_frame.pack(fill=X, padx=20, pady=5)
        ttk.Label(status_frame, text="状态:").pack(side=LEFT)
        self.status_label = ttk.Label(status_frame, textvariable=self.status_var, bootstyle="info")
        self.status_label.pack(side=LEFT, padx=5)

        # 模型状态
        model_frame = ttk.Frame(self.root)
        model_frame.pack(fill=X, padx=20, pady=5)
        model_ok = self.controller.check_model()
        model_text = f"YOLO模型: {'已就绪' if model_ok else '未找到'}"
        model_style = "success" if model_ok else "danger"
        ttk.Label(model_frame, text=model_text, bootstyle=model_style).pack(side=LEFT)

        # 按钮区
        btn_frame = ttk.Frame(self.root)
        btn_frame.pack(pady=15)

        self.start_btn = ttk.Button(
            btn_frame,
            text="▶ 开始",
            command=self._on_start,
            bootstyle="success",
            width=12
        )
        self.start_btn.pack(side=LEFT, padx=5)

        self.stop_btn = ttk.Button(
            btn_frame,
            text="⏹ 停止",
            command=self._on_stop,
            bootstyle="danger",
            width=12,
            state=DISABLED
        )
        self.stop_btn.pack(side=LEFT, padx=5)

        # 日志框
        log_frame = ttk.LabelFrame(self.root, text="运行日志")
        log_frame.pack(fill=BOTH, expand=True, padx=20, pady=(0, 20))

        self.log_text = scrolledtext.ScrolledText(
            log_frame,
            wrap=tk.WORD,
            font=("Consolas", 10),
            height=12,
            state=DISABLED
        )
        self.log_text.pack(fill=BOTH, expand=True)

    def _append_log(self, text: str):
        self.log_text.config(state=NORMAL)
        self.log_text.insert(END, text + "\n")
        self.log_text.see(END)
        self.log_text.config(state=DISABLED)

    def _start_log_polling(self):
        self._last_log_count = 0
        self._poll_logs()

    def _poll_logs(self):
        lines = logger.get_lines()
        if len(lines) > self._last_log_count:
            for line in lines[self._last_log_count:]:
                self._append_log(line)
            self._last_log_count = len(lines)
        self.root.after(200, self._poll_logs)

    def _on_start(self):
        if not self.controller.check_model():
            self.status_var.set("模型未找到")
            self.status_label.config(bootstyle="danger")
            return

        self.running = True
        self.start_btn.config(state=DISABLED)
        self.stop_btn.config(state=NORMAL)
        self.status_var.set("运行中")
        self.status_label.config(bootstyle="success")

        self.worker_thread = threading.Thread(target=self._worker, daemon=True)
        self.worker_thread.start()

    def _worker(self):
        try:
            self.controller.start()
        except Exception as e:
            logger.log(f"线程异常: {e}", "ERROR")
        finally:
            self.root.after(0, self._on_worker_done)

    def _on_worker_done(self):
        self.running = False
        self.start_btn.config(state=NORMAL)
        self.stop_btn.config(state=DISABLED)
        self.status_var.set("已停止")
        self.status_label.config(bootstyle="secondary")

    def _on_stop(self):
        self.controller.stop()
        self.status_var.set("正在停止...")
        self.status_label.config(bootstyle="warning")


def main():
    if not is_admin():
        print("需要管理员权限，正在申请...")
        run_as_admin()
        return

    root = ttk.Window(themename="litera")
    app = MRAGUI(root)
    
    # 在 mainloop 之前设置图标
    icon_path = Path(__file__).parent / "assets" / "icon.ico"
    if icon_path.exists():
        root.wm_iconbitmap(str(icon_path))
    
    root.mainloop()


if __name__ == "__main__":
    main()