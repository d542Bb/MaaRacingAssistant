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

from maaracing_assistant.controller import MaaRacingAssistantController
from maaracing_assistant.logger import logger
from maaracing_assistant.window_utils import has_physical_controller


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
        self.root.resizable(True, True)
        self.root.minsize(480, 400)

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

        # DEBUG / PEEP 模式
        debug_frame = ttk.Frame(self.root)
        debug_frame.pack(fill=X, padx=20, pady=2)
        self.debug_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            debug_frame, text="DEBUG 每帧截图", variable=self.debug_var,
            bootstyle="warning-toolbutton"
        ).pack(side=LEFT)
        self.peep_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            debug_frame, text="PEEP 实时预览", variable=self.peep_var,
            command=self._toggle_peep,
            bootstyle="info-toolbutton"
        ).pack(side=LEFT, padx=(8, 0))

        # 断点选择
        bp_frame = ttk.LabelFrame(self.root, text="断点选择（双击列表项跳转）")
        bp_frame.pack(fill=X, padx=20, pady=(8, 0))
        bp_inner = ttk.Frame(bp_frame)
        bp_inner.pack(fill=X, padx=5, pady=5)
        self.stage_var = tk.StringVar(value=self.controller.STAGE_ORDER[0])
        self.stage_listbox = tk.Listbox(
            bp_inner, height=7, font=("Microsoft YaHei", 9),
            selectbackground="#0078D7", selectforeground="white",
            exportselection=False,
        )
        for s in self.controller.STAGE_ORDER:
            self.stage_listbox.insert(tk.END, f"  {s}")
        self.stage_listbox.select_set(0)
        self.stage_listbox.pack(side=LEFT, fill=X, expand=True)
        # 滚动条
        sb = ttk.Scrollbar(bp_inner, orient=tk.VERTICAL, command=self.stage_listbox.yview)
        sb.pack(side=RIGHT, fill=Y)
        self.stage_listbox.config(yscrollcommand=sb.set)
        # 双击跳转
        self.stage_listbox.bind("<Double-Button-1>", self._on_stage_select)
        # 当前阶段状态
        self.stage_status_var = tk.StringVar(value="")
        stage_status_label = ttk.Label(
            bp_frame, textvariable=self.stage_status_var,
            font=("Microsoft YaHei", 9), bootstyle="info"
        )
        stage_status_label.pack(anchor=W, padx=8, pady=(0, 4))

        # 按钮区
        btn_frame = ttk.Frame(self.root)
        btn_frame.pack(pady=10)

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

    def _on_stage_select(self, event=None):
        """双击断点列表项→选中并打印到日志"""
        selection = self.stage_listbox.curselection()
        if selection:
            stage = self.controller.STAGE_ORDER[selection[0]]
            logger.log(f"已选择断点: {stage}")

    def _poll_logs(self):
        lines = logger.get_lines()
        if len(lines) > self._last_log_count:
            for line in lines[self._last_log_count:]:
                self._append_log(line)
            self._last_log_count = len(lines)

        # 更新当前阶段显示
        stage = self.controller.current_stage
        if stage:
            self.stage_status_var.set(f"▶ 当前: {stage}")
        elif self.running:
            self.stage_status_var.set("▶ 当前: 停止中...")
        else:
            self.stage_status_var.set("")
        # 根据当前阶段高亮列表项
        if stage and stage in self.controller.STAGE_ORDER:
            idx = self.controller.STAGE_ORDER.index(stage)
            self.stage_listbox.selection_clear(0, tk.END)
            self.stage_listbox.selection_set(idx)
            self.stage_listbox.see(idx)

        self.root.after(200, self._poll_logs)

    def _on_start(self):
        if not self.controller.check_model():
            self.status_var.set("模型未找到")
            self.status_label.config(bootstyle="danger")
            return

        if has_physical_controller():
            dlg = tk.Toplevel(self.root)
            dlg.title("检测到物理手柄")
            icon_path = Path(__file__).parent.parent / "assets" / "icon.ico"
            if icon_path.exists():
                dlg.iconbitmap(str(icon_path))
            tk.Label(dlg, text="请断开所有物理手柄后再运行",
                     font=("Microsoft YaHei", 11), padx=30, pady=20).pack()
            tk.Button(dlg, text="确定", command=dlg.destroy, width=10).pack(pady=(0, 15))
            dlg.transient(self.root)
            dlg.grab_set()
            dlg.wait_window()
            return

        # 读取断点选择
        selection = self.stage_listbox.curselection()
        if selection:
            start_from = self.controller.STAGE_ORDER[selection[0]]
        else:
            start_from = self.controller.STAGE_ORDER[0]

        if start_from != self.controller.STAGE_ORDER[0]:
            logger.log(f"断点模式: 从「{start_from}」开始运行")

        self.running = True
        self.start_btn.config(state=DISABLED)
        self.stop_btn.config(state=NORMAL)
        self.status_var.set("运行中")
        self.status_label.config(bootstyle="success")

        # 同步 DEBUG 开关
        self.controller.set_debug_mode(self.debug_var.get())
        if self.debug_var.get():
            logger.log("DEBUG 模式开启：每帧截图保存到 debug/navigate/", "INFO")

        self.worker_thread = threading.Thread(target=self._worker, args=(start_from,), daemon=True)
        self.worker_thread.start()

    def _worker(self, start_from: str = ""):
        try:
            self.controller.start(start_from=start_from)
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
        self.controller.debug.disable_peep()
        self.peep_var.set(False)
        self.stage_status_var.set("")

    def _toggle_peep(self):
        if self.peep_var.get():
            self.controller.debug.enable_peep()
        else:
            self.controller.debug.disable_peep()

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
    icon_path = Path(__file__).parent.parent / "assets" / "icon.ico"
    if icon_path.exists():
        root.wm_iconbitmap(str(icon_path))

    root.mainloop()


if __name__ == "__main__":
    main()
