#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
主控制器模块：模块生命周期管理（AppController 角色）、窗口连接、截图与手柄共享能力提供。
活动流程（导航/比赛）已迁移至 modules/racing_module.py 的 RacingModule。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import cv2
import numpy as np

from maa.controller import Win32Controller
from maa.define import MaaWin32ScreencapMethodEnum

from maaracing_assistant.core.debug import NavigationDebugger
from maaracing_assistant.core.paths import user_data_dir
from maaracing_assistant.core.vgamepad_lazy import gamepad_available as _vgamepad_available
from maaracing_assistant.core.vgamepad_lazy import vg
from maaracing_assistant.core.window_utils import (
    activate_window,
    count_pressed_keys,
    ensure_dpi_aware,
    find_game_hwnd,
    is_window_on_screen,
    resize_game_window_720p,
    terminate_process_by_hwnd,
)
from maaracing_assistant.core.logger import logger
from maaracing_assistant.core.base import ActivityContext, ModuleDependencyError
from maaracing_assistant.core.registry import create_module


class MaaRacingAssistantController:
    """主控制器（AppController）：生命周期编排 + 共享能力提供，活动流程已迁入模块"""

    def __init__(self, capture_backend: str = "wgc_latest"):
        # DPI awareness：进程级语义，须在创建窗口 / 初始化坐标 API 前显式建立（不继承 shell 配置）
        ensure_dpi_aware()
        self.proj = Path(__file__).parent.parent
        self.model_path = self.proj / "assets" / "model" / "model.onnx"
        self.controller = None  # MAA Win32Controller（连接后有效，未连接为 None）
        self._hwnd = 0  # 已连接的游戏窗口句柄（未连接为 0）
        self._gpad = None  # 虚拟手柄，首次使用时创建，不复位不销毁
        self._gp_avail = None  # vgamepad 可用性缓存（None=未探测）
        self.debug = NavigationDebugger(user_data_dir() / "debug")
        self._debug_mode = False  # 调试模式开关（由 GUI 控制）
        self._capture_backend = capture_backend
        self._running = False  # 模块运行标志（start_module 生命周期内为 True）
        self._active_module = None  # 当前活动模块实例（生命周期由 start_module 管理）
        self._ctx = None  # ActivityContext 懒创建
        self.stop_event = threading.Event()  # 停止信号（唯一 clear 位置在 start_module）
        self._lifecycle_lock = threading.Lock()  # 生命周期互斥锁
        # 紧急停止快捷键开关：开启后同时按下任意 ≥2 个按键 → 立即停止逻辑
        self._emergency_stop_enabled = False
        self._emergency_thread: threading.Thread | None = None
        # 运行结束后自动关闭（GUI「运行结束后」卡片）：仅在流程"正常完成"时生效，
        # 报错退出 / 手动停止（stop_event 置位）不触发。
        self._auto_close_game = False  # 结束后关闭游戏进程
        self._auto_exit_mra = False    # 结束后退出 MRA 程序
        self._last_run_natural = False  # 上次 start_module 是否正常跑完（非报错、非手动停止）

    # ---------- 模块生命周期 ----------

    @property
    def active_module(self):
        """当前活动模块实例（None 表示无模块在运行）"""
        return self._active_module

    @active_module.setter
    def active_module(self, module):
        self._active_module = module

    @property
    def ctx(self) -> ActivityContext:
        """活动上下文门面（懒创建，绑定本控制器）"""
        if self._ctx is None:
            self._ctx = ActivityContext(self)
        return self._ctx

    @property
    def current_stage(self) -> str:
        """返回当前执行阶段名称（委托给活动模块）"""
        return self.active_module.current_stage if self.active_module else ""

    @property
    def is_running(self) -> bool:
        """是否仍在运行（由 stop_event 控制）"""
        return not self.stop_event.is_set()

    @property
    def module_active(self) -> bool:
        """是否有活动模块正在运行（start_module 生命周期内为 True）"""
        return self._running and self._active_module is not None

    def start_module(self, module_id: str, start_from: str | None = None,
                     module_config: dict | None = None):
        """GUI 唯一入口：创建并运行指定活动模块（阻塞，运行于 worker 线程）"""
        with self._lifecycle_lock:
            if self.active_module is not None:
                raise RuntimeError("已有模块在运行")
            self.stop_event.clear()          # ★ 唯一 clear 位置
            module = create_module(module_id, self.ctx)
            if start_from and start_from not in module.STAGE_ORDER:
                raise ValueError(f"断点 {start_from} 不属于模块 {module_id} 的阶段")
            # fail-fast：启动前验证模块声明的能力是否可用（固有能力 lifecycle 隐式满足）
            missing = module.REQUIRES - self.ctx.capabilities
            if missing:
                raise ModuleDependencyError(
                    f"{module_id}: missing capabilities: {sorted(missing)}"
                )
            # 模块配置注入（GUI 设置的循环上限、策略 profile 等）：
            # 模块未定义 set_module_config 时静默跳过（如 racing 暂不支持）。
            if module_config:
                setter = getattr(module, "set_module_config", None)
                if callable(setter):
                    try:
                        setter(dict(module_config))
                        logger.log(f"[controller] 模块{module_id!r}配置已注入: {sorted(module_config.keys())}")
                    except Exception as exc:  # noqa: BLE001
                        logger.log(f"[controller] 模块{module_id!r}配置注入失败: {exc}", "WARNING")
            self.active_module = module
        self._running = True
        self._last_run_natural = False
        try:
            module.start(start_from)
            # 仅当 start 正常返回（未抛异常）才标记"自然完成"；
            # 手动停止（stop_event 置位）或报错退出不满足，避免误触发自动关闭。
            self._last_run_natural = True
        except Exception as e:
            logger.log(f"模块执行异常: {e}", "ERROR")
        finally:
            try:
                module.cleanup()
            except Exception:
                pass
            # 释放模块期间 Context 登记的所有资源（renderer 等）。
            # close() 调用权只在编排层；close 后置空，下次 start_module 新建全新 Context/ExitStack，
            # 保证重复启停不累积 renderer/gamepad ownership。
            if self._ctx is not None:
                try:
                    self._ctx.close()
                except Exception as e:
                    logger.log(f"Context 关闭异常: {e}", "ERROR")
                self._ctx = None
            with self._lifecycle_lock:
                self.active_module = None
            self._running = False
            # 运行结束后行为（GUI「运行结束后」卡片）：仅"正常完成"生效 ——
            # 报错退出（natural=False）或手动停止（stop_event 置位）不触发。
            if self._last_run_natural and not self.stop_event.is_set():
                self._maybe_auto_shutdown()

    def stop(self):
        """停止当前活动模块（幂等）"""
        self.stop_event.set()
        if self.active_module:
            try:
                self.active_module.stop()
            except Exception as e:
                logger.log(f"停止模块异常: {e}", "ERROR")

    # ---------- 运行结束后自动关闭（GUI「运行结束后」卡片；仅正常完成生效）----------

    @property
    def auto_close_game(self) -> bool:
        """是否在流程正常结束后自动关闭游戏进程（设置开关）"""
        return self._auto_close_game

    @property
    def auto_exit_mra(self) -> bool:
        """是否在流程正常结束后自动退出 MRA 程序（设置开关）"""
        return self._auto_exit_mra

    @property
    def last_run_natural(self) -> bool:
        """上次 start_module 是否"正常跑完"（非报错、非手动停止）。供 sidecar 决定是否发退出事件"""
        return self._last_run_natural and not self.stop_event.is_set()

    def set_auto_shutdown(self, close_game: bool | None = None, exit_mra: bool | None = None) -> None:
        """设置「运行结束后」自动关闭的开关（GUI 设置卡片）。"""
        if close_game is not None:
            self._auto_close_game = bool(close_game)
        if exit_mra is not None:
            self._auto_exit_mra = bool(exit_mra)

    def _maybe_auto_shutdown(self) -> None:
        """流程正常结束后执行：按开关关闭游戏进程（退出 MRA 由 sidecar 依 last_run_natural 发起，以保证时序）"""
        if not self._auto_close_game:
            return
        hwnd = self._hwnd or find_game_hwnd()
        if not hwnd:
            logger.log("运行结束自动关闭：未能定位游戏窗口，跳过关闭游戏进程", "WARNING")
            return
        try:
            logger.log(f"运行结束自动关闭：正在关闭游戏进程 (hwnd={hwnd})…", "INFO")
            terminate_process_by_hwnd(hwnd)
            logger.log("运行结束自动关闭：游戏进程已关闭", "INFO")
        except Exception as e:  # noqa: BLE001
            logger.log(f"运行结束自动关闭：关闭游戏进程失败: {e}", "WARNING")

    # ---------- 紧急停止快捷键（GUI 调试选项卡开关控制）----------

    def set_emergency_stop(self, enabled: bool):
        """开/关紧急停止：开启后同时按下键盘任意 ≥2 个按键立即停止逻辑（全局生效）。

        轮询线程为 daemon，随进程退出；关闭开关时线程在下一次循环检测到标志退出。
        """
        self._emergency_stop_enabled = enabled
        if enabled and self._emergency_thread is None:
            self._emergency_thread = threading.Thread(
                target=self._emergency_stop_loop,
                name="emergency-stop",
                daemon=True,
            )
            self._emergency_thread.start()
            logger.log("紧急停止快捷键已启用：同时按下任意 2 个及以上按键将立即停止逻辑", "INFO")
        elif not enabled:
            self._emergency_thread = None  # 线程检测到标志关闭后自行退出

    def _emergency_stop_loop(self):
        """轮询系统按键状态：≥2 键同时按下 → 停止逻辑。

        仅在「逻辑运行中」触发 stop + ERROR 日志；未运行（只开了开关但没点开始）
        时检测到多按键静默跳过，避免「还没启动就满屏 ERROR」。
        开关保持开启（直到用户在 GUI 手动关闭）：触发一次不自动关闭，
        否则"跑完一轮后想再停"会因开关已关而失效（实测教训）。
        触发后冷却 1s，避免按住不松导致 stop() 连发刷日志。
        """
        while self._emergency_stop_enabled:
            try:
                if count_pressed_keys() >= 2:
                    if self._running:
                        logger.log("紧急停止：检测到同时按下 ≥2 个按键，立即停止逻辑", "ERROR")
                        self.stop()
                    # 无论是否运行：冷却 1s，避免按住不松时反复触发
                    time.sleep(1.0)
                    continue
            except Exception:
                pass
            time.sleep(0.05)

    # ---------- 共享能力 ----------

    def set_debug_mode(self, enabled: bool):
        """开启/关闭调试截图模式"""
        self._debug_mode = enabled
        self.debug.enabled = enabled

    def check_model(self) -> bool:
        return self.model_path.exists()

    def connect(self) -> bool:
        """幂等窗口连接：仅创建 Win32Controller 并保存句柄，MAA 资源归活动模块创建"""
        if self.controller is not None:
            return True

        hwnd = find_game_hwnd()
        if hwnd == 0:
            logger.log("未找到游戏窗口", "ERROR")
            return False

        self.controller = Win32Controller(hWnd=hwnd, screencap_method=MaaWin32ScreencapMethodEnum.FramePool)

        # 连接等待加超时保护：post_connection().wait() 在异常窗口状态下可能无限阻塞，
        # 会导致 worker 卡死在 connect 处、GUI 一直显示「停止中」。
        conn_ok = [False]
        conn_done = threading.Event()

        def _wait_connection():
            try:
                assert self.controller is not None  # 130 行已赋值，连接中不置空
                conn_ok[0] = bool(self.controller.post_connection().wait())
            except Exception:
                conn_ok[0] = False
            finally:
                conn_done.set()

        threading.Thread(target=_wait_connection, daemon=True).start()
        if not conn_done.wait(10.0):
            logger.log("连接窗口超时(10s)，请检查游戏是否正常运行/管理员权限", "ERROR")
            self.controller = None
            return False
        if not conn_ok[0]:
            logger.log("连接失败，请检查游戏是否运行/管理员权限", "ERROR")
            self.controller = None
            return False

        self._hwnd = hwnd
        logger.log(f"已连接窗口 (hWnd={hwnd})")

        # 按下开始后的窗口准备：切换到游戏窗口前台（用户明确操作，区别于运行中点击的"不抢前台"策略）
        # 切前台失败仅 WARNING 提示（不终止）：逻辑继续运行，点击时若游戏非前台会被前台校验取消
        if not activate_window(hwnd):
            logger.log("切换到游戏窗口前台失败（Windows 前台锁定）——逻辑继续运行，点击需游戏在前台", "WARNING")

        # 统一把所有模块的游戏窗口客户区调整为 720p（截图/模板/ROI 均按 720p 归一化）。
        # 调整失败不阻断：退化到原尺寸并交由后续 16:9 / 屏幕内校验兜底告警。
        resize_game_window_720p(hwnd)

        # 窗口屏幕内校验：窗口完全不可见（被最小化且无法自动还原 / 被拖出屏幕）→ 无法点击 → 报错并终止模块
        if not is_window_on_screen(hwnd):
            logger.log(
                "游戏窗口不在任何屏幕的可视范围内（被最小化且无法自动还原，或被拖出屏幕），无法点击。"
                "模块已终止，请将窗口还原并移回屏幕后重新开始", "ERROR",
            )
            self.controller = None
            self._hwnd = 0
            return False

        return True

    # ---------- 手柄管理 ----------

    def gamepad_available(self) -> bool:
        """vgamepad 是否可用（ViGEmBus 驱动是否就绪）。结果缓存，避免重复探测。"""
        if self._gp_avail is None:
            try:
                self._gp_avail = bool(_vgamepad_available())
            except Exception:  # noqa: BLE001
                self._gp_avail = False
        return self._gp_avail

    def _get_gpad(self) -> vg.VX360Gamepad:
        """获取虚拟手柄（懒创建 + 保持复用，不销毁重建）"""
        if self._gpad is None:
            self._gpad = vg.VX360Gamepad()
            self._gpad.reset()
            self._gpad.update()
            time.sleep(0.2)
            logger.log("虚拟手柄已创建", "DEBUG")
        return self._gpad

    def _reset_gpad(self):
        """重置手柄：摇杆归零 + 按钮释放，但不销毁"""
        if self._gpad is not None:
            try:
                self._gpad.reset()
                self._gpad.update()
            except Exception:
                pass

    def _destroy_gpad(self):
        """销毁虚拟手柄，释放资源"""
        if self._gpad is not None:
            try:
                self._gpad.reset()
                self._gpad.update()
            except Exception:
                pass
            try:
                del self._gpad
            except Exception:
                pass
            self._gpad = None
            logger.log("虚拟手柄已销毁", "DEBUG")

    # ---------- 截图 ----------

    def _screencap(self):
        """截图并返回 RGB ndarray，失败返回 None"""
        if self.controller is None:
            logger.log("控制器未连接", "WARNING")
            return None
        try:
            job = self.controller.post_screencap()
            job.wait()
            img = job.get()
            if img is None:
                logger.log("job.get() 返回 None", "WARNING")
                return self._screencap_ctypes()

            if hasattr(img, "numpy"):
                # MAA Image 等类型自带 numpy()，用 getattr 规避类型推断
                arr = np.asarray(getattr(img, "numpy")())
            elif isinstance(img, np.ndarray):
                arr = img
            elif hasattr(img, "__array__"):
                arr = np.asarray(img)
            else:
                logger.log(f"未知图像类型={type(img).__name__}", "WARNING")
                return self._screencap_ctypes()

            if arr is None or arr.size == 0 or arr.ndim < 3:
                logger.log(f"图像格式异常: size={arr.size if arr is not None else 0}, "
                           f"ndim={arr.ndim if arr is not None else 0}", "WARNING")
                return self._screencap_ctypes()
            # MAA PostScreencap 返回 BGR（OpenCV 默认），转 RGB 供下游
            arr = cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)
            return arr
        except Exception as e:
            logger.log(f"截图异常: {e}", "ERROR")
            return None

    def _screencap_ctypes(self):
        """ctypes 截图兜底（已废弃：统一走 MAA DXGI_DesktopDup_Window）。"""
        return None

    # ---------- 工具方法 ----------

    def _interruptible_sleep(self, seconds: float):
        """可中断的 sleep，每 0.1 秒检查 _running 与 stop_event 状态"""
        for _ in range(int(seconds / 0.1)):
            if not self._running or self.stop_event.is_set():
                return
            time.sleep(0.1)
