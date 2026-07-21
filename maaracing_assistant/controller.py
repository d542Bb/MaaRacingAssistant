#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
主控制器模块：MAA 框架集成、导航编排、比赛调度
"""

import time
import ctypes
from ctypes import wintypes
from pathlib import Path

import cv2
import numpy as np
import vgamepad as vg

from maa.tasker import Tasker
from maa.resource import Resource
from maa.controller import Win32Controller
from maa.define import MaaWin32ScreencapMethodEnum

from maaracing_assistant.navigation import ButtonDef, Navigation
from maaracing_assistant.racing_loop import RacingLoop
from maaracing_assistant.pipeline_logger import PipelineLogger
from maaracing_assistant.debug import NavigationDebugger
from maaracing_assistant.window_utils import find_game_hwnd
from maaracing_assistant.logger import logger


class MaaRacingAssistantController:
    # 阶段顺序（GUI 断点选择用）
    STAGE_ORDER = [
        "归位",
        "导航一(极速狂飙入口)",
        "导航二(开始挑战)",
        "导航三(寻找对手)",
        "商店弹窗处理",
        "确认上阵",
        "比赛(Pipeline)",
    ]

    def __init__(self):
        self.proj = Path(__file__).parent.parent
        self.model_path = self.proj / "assets" / "model" / "model.onnx"
        self.tasker = None
        self.resource = None
        self.controller = None
        self.racing_loop = None
        self._running = False
        self._gpad = None  # 虚拟手柄，首次使用时创建，不复位不销毁
        self.debug = NavigationDebugger(self.proj)
        self._debug_mode = False  # 调试模式开关（由 GUI 控制）
        self._current_stage = ""  # 当前执行阶段名（供 GUI 显示）
        self.nav = Navigation(self.proj, self.debug, self)  # 导航引擎
        self._in_match = False  # 是否已进入对局（防止异常回退到大厅）

    @property
    def current_stage(self) -> str:
        """返回当前执行阶段名称"""
        return self._current_stage

    def set_debug_mode(self, enabled: bool):
        """开启/关闭调试截图模式"""
        self._debug_mode = enabled
        self.debug.enabled = enabled

    # ---------- 手柄管理 ----------

    def check_model(self) -> bool:
        return self.model_path.exists()

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
                arr = img.numpy()
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
        """使用 ctypes 直接截取窗口图像（MAA 截图失败时的备用方案）"""
        try:
            hwnd = self.controller.hWnd if self.controller is not None and hasattr(self.controller, "hWnd") else 0
            if not hwnd:
                return None

            user32 = ctypes.windll.user32
            gdi32 = ctypes.windll.gdi32
            rect = wintypes.RECT()
            user32.GetClientRect(hwnd, ctypes.byref(rect))
            w, h = rect.right, rect.bottom
            if w <= 0 or h <= 0:
                return None

            hwnd_dc = user32.GetDC(hwnd)
            if not hwnd_dc:
                return None
            try:
                mem_dc = gdi32.CreateCompatibleDC(hwnd_dc)
                if not mem_dc:
                    return None
                try:
                    bitmap = gdi32.CreateCompatibleBitmap(hwnd_dc, w, h)
                    if not bitmap:
                        return None
                    try:
                        gdi32.SelectObject(mem_dc, bitmap)
                        gdi32.BitBlt(mem_dc, 0, 0, w, h, hwnd_dc, 0, 0, 0x00CC0020)
                        bmp_info = ctypes.create_string_buffer(40)
                        gdi32.GetObjectA(bitmap, 40, bmp_info)
                        bpp = 32
                        stride = ((w * bpp + 31) // 32) * 4
                        buf = ctypes.create_string_buffer(stride * h)
                        gdi32.GetBitmapBits(bitmap, len(buf), buf)
                        arr = np.frombuffer(bytes(buf), dtype=np.uint8).reshape((h, w, 4))[:, :, :3]
                        arr = cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)
                        logger.log(f"ctypes截图成功: {w}x{h}", "DEBUG")
                        return arr
                    finally:
                        gdi32.DeleteObject(bitmap)
                finally:
                    gdi32.DeleteDC(mem_dc)
            finally:
                user32.ReleaseDC(hwnd, hwnd_dc)
        except Exception as e:
            logger.log(f"ctypes截图异常: {e}", "ERROR")
            return None

    # ---------- 工具方法（供 start() 使用）----------

    def _interruptible_sleep(self, seconds: float):
        """可中断的 sleep，每 0.1 秒检查 _running 状态"""
        for _ in range(int(seconds / 0.1)):
            if not self._running:
                return
            time.sleep(0.1)

    # ---------- 连接与启停 ----------

    def connect(self) -> bool:
        hwnd = find_game_hwnd()
        if hwnd == 0:
            logger.log("未找到游戏窗口", "ERROR")
            return False

        self.controller = Win32Controller(hWnd=hwnd, screencap_method=MaaWin32ScreencapMethodEnum.PrintWindow)

        if not self.controller.post_connection().wait():
            logger.log("连接失败，请检查游戏是否运行/管理员权限", "ERROR")
            return False

        logger.log(f"已连接窗口 (hWnd={hwnd})")

        self.tasker = Tasker()
        self.resource = Resource()

        self.racing_loop = RacingLoop(str(self.model_path), debug=self.debug)
        self.resource.register_custom_action("RacingLoop", self.racing_loop)

        self.resource.post_bundle(self.proj / "assets" / "resource").wait()
        self.tasker.bind(self.resource, self.controller)

        self.tasker.add_context_sink(PipelineLogger())

        return True

    def start(self, start_from: str = ""):
        """启动循环，从指定阶段开始（空字符串表示从第一个阶段开始）
        start_from: STAGE_ORDER 中的阶段名，供 GUI 断点选择

        流程分层：
          大厅层: 归位 → 导航一(极速狂飙入口) → 导航二(开始挑战)
          对局层: 导航三(寻找对手) → 弹窗 → 确认上阵 → 比赛 → 循环
        """
        if not self.check_model():
            logger.log(f"模型不存在: {self.model_path}", "ERROR")
            return

        if not self.connect():
            return

        self._running = True

        # 解析断点
        if start_from and start_from in self.STAGE_ORDER:
            skip_until = self.STAGE_ORDER.index(start_from)
            logger.log(f"从断点开始: 「{start_from}」(跳过前{skip_until}个阶段)")
        else:
            skip_until = 0
            start_from = self.STAGE_ORDER[0]

        logger.log("开始循环")

        BTN_极速狂飙入口 = ButtonDef("极速狂飙入口", (0.880, 0.720), "activity_page_template", True, 50)
        BTN_开始挑战 = ButtonDef("开始挑战", (0.855, 0.898), "activity_page_template", False, 12)
        BTN_寻找对手 = ButtonDef("寻找对手", (0.804, 0.753), "find_opponent_template", False, 25)

        # ══════════════════════════════════════════════
        # 大厅层：归位 → 导航一 → 进入对局循环
        # ══════════════════════════════════════════════

        # ── 归位 ──
        self._current_stage = self.STAGE_ORDER[0]
        if skip_until <= 0:
            self.nav.homing()
        else:
            logger.log(f"跳过「归位」(断点: {start_from})")

        while self._running:
            # ── 导航一（极速狂飙入口）──
            self._current_stage = self.STAGE_ORDER[1]
            nav1_ok = False
            if skip_until > 1:
                logger.log(f"跳过「导航一」(断点: {start_from})")
                nav1_ok = True
            else:
                for retry in range(3):
                    if not self._running:
                        break
                    if self.nav.navigate_to_button(BTN_极速狂飙入口):
                        nav1_ok = True
                        break
                    logger.log(f"导航一失败，第{retry+1}次重试——销毁手柄复位")
                    self._destroy_gpad()
                    self._interruptible_sleep(2.0)
                    self.nav.homing()
            if not nav1_ok:
                if self._running:
                    logger.log("导航一失败已达最大重试次数，跳过", "WARNING")
                break

            # ══════════════════════════════════════════════
            # 对局层：导航二(开始挑战) → 对局内(导航三→弹窗→确认→比赛) → 循环
            # ══════════════════════════════════════════════
            while self._running:
                # ── 导航二（开始挑战）—— 关口：进入对局前可回退大厅 ──
                self._current_stage = self.STAGE_ORDER[2]
                nav2_ok = False
                if skip_until > 2:
                    logger.log(f"跳过「导航二」(断点: {start_from})")
                    nav2_ok = True
                else:
                    for retry in range(6):
                        if not self._running:
                            break
                        if self.nav.navigate_to_button(BTN_开始挑战):
                            nav2_ok = True
                            break
                        logger.log(f"导航二失败，第{retry+1}次原地重试——销毁手柄复位")
                        self._destroy_gpad()
                        self._interruptible_sleep(2.0)
                        # 首次进入对局失败时穿插导航一兜底
                        if not self._in_match and retry == 2:
                            logger.log("导航二连续3次失败，重新导航一", "WARNING")
                            self.nav.homing()
                            if not self.nav.navigate_to_button(BTN_极速狂飙入口):
                                logger.log("重新导航一也失败，放弃", "WARNING")
                                break
                if not nav2_ok:
                    if self._running:
                        if not self._in_match:
                            logger.log("导航二最终失败，回到大厅层", "WARNING")
                            skip_until = 0
                            break  # 跳出对局层，回大厅从导航一开始
                        logger.log("导航二失败（对局中），停止流程", "WARNING")
                    self._running = False
                    break

                # 导航二成功 → 标记已进入对局
                self._in_match = True

                # ── 导航三（寻找对手）—— 进入对局后 ──
                self._current_stage = self.STAGE_ORDER[3]
                nav3_ok = False
                if skip_until > 3:
                    logger.log(f"跳过「导航三」(断点: {start_from})")
                    nav3_ok = True
                else:
                    for retry in range(6):
                        if not self._running:
                            break
                        logger.log(f"等待寻找对手页面...（第{retry+1}次）")
                        if not self.nav._wait_for_template("find_opponent_template", timeout=15):
                            logger.log("寻找对手页面未出现，销毁手柄重试", "WARNING")
                            self._destroy_gpad()
                            self._interruptible_sleep(2.0)
                            continue
                        if self.nav.navigate_to_button(BTN_寻找对手):
                            nav3_ok = True
                            break
                        logger.log(f"导航三失败，第{retry+1}次原地重试")
                        self._destroy_gpad()
                        self._interruptible_sleep(2.0)
                if not nav3_ok:
                    if self._running:
                        logger.log("导航三反复失败，停止流程", "WARNING")
                    self._running = False  # 对局层异常，直接停止
                    break

                # ── 商店弹窗处理 ──
                self._current_stage = self.STAGE_ORDER[4]
                if skip_until > 4:
                    logger.log(f"跳过「商店弹窗」(断点: {start_from})")
                else:
                    self.nav.handle_store_popup()

                # ── 确认上阵 ──
                self._current_stage = self.STAGE_ORDER[5]
                if skip_until > 5:
                    logger.log(f"跳过「确认上阵」(断点: {start_from})")
                else:
                    BTN_确认上阵 = ButtonDef("确认上阵", (0.823, 0.931), "", True, 25)
                    gpad = self._get_gpad()
                    self.nav._ensure_cursor(gpad)
                    self.nav.navigate_to_button(BTN_确认上阵)
                    self._interruptible_sleep(0.5)

                # ── 比赛（直接运行，绕过 MAA CustomAction）──
                self._current_stage = self.STAGE_ORDER[6]
                if self._running:
                    # 销毁导航手柄，避免 RacingLoop 创建第二个手柄导致游戏不识別
                    self._destroy_gpad()
                    race_ok = False
                    for race_retry in range(3):
                        if not self._running:
                            break
                        t0 = time.time()
                        try:
                            self.racing_loop.run_direct(self.controller)
                            elapsed = time.time() - t0
                            if elapsed < 3:
                                logger.log(f"比赛仅运行{elapsed:.1f}秒（第{race_retry+1}/3次），判定异常重试", "WARNING")
                                self._interruptible_sleep(1)
                                continue
                            logger.log(f"本轮完成（{elapsed:.1f}秒），结束原因：{self.racing_loop._end_reason}")
                            # 根据结束原因分流
                            if self._running and self.racing_loop._end_reason == "商店弹窗":
                                self.nav.handle_store_popup()
                            race_ok = True
                            break
                        except Exception as e:
                            elapsed = time.time() - t0
                            logger.log(f"比赛异常: {e}（第{race_retry+1}/3次重试）", "ERROR")
                            self._interruptible_sleep(1)
                    if not race_ok:
                        if self._running:
                            logger.log("比赛异常已达最大重试次数，停止流程", "WARNING")
                        self._running = False
                        break
                    self._interruptible_sleep(2)

                # 断点只在首轮生效，后续循环走完整流程
                skip_until = 0
                self._in_match = False  # 完整一局结束，重置对局标记
                # 比赛完成 → 继续对局层循环（从导航二开始）
                continue

            # 对局层跳出 → 如果还在运行则回大厅
            if self._running:
                skip_until = 0
                continue

        logger.log("循环已停止")
        self._current_stage = ""
        self._destroy_gpad()

    def stop(self):
        self._running = False
        if self.racing_loop:
            self.racing_loop.stop()
        if self.tasker:
            try:
                self.tasker.post_stop()
                logger.log("Pipeline 已中断")
            except Exception as e:
                logger.log(f"中断 Pipeline 时出错: {e}", "ERROR")
        self._destroy_gpad()  # 销毁虚拟手柄，释放资源
        logger.log("收到停止信号")
