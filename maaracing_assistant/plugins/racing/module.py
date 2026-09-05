#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
极速狂飙活动模块：大厅导航（归位/入口/开始挑战）+ 对局循环（找对手/弹窗/上阵/比赛）。
MAA Resource/Tasker/RacingLoop 归属模块内部创建与管理。
"""

from __future__ import annotations

import time

from maa.tasker import Tasker
from maa.resource import Resource

from maaracing_assistant.core.base import ActivityContext, ActivityModule
from maaracing_assistant.core.stage_tracker import StageTracker
from maaracing_assistant.plugins.racing import MODEL_PATH, RES_DIR
from maaracing_assistant.plugins.racing.renderer import RacingDebugRenderer
from maaracing_assistant.plugins.racing.navigation import ButtonDef, Navigation
from maaracing_assistant.plugins.racing.loop import RacingLoop
from maaracing_assistant.core.pipeline_logger import PipelineLogger
from maaracing_assistant.core.logger import logger


class RacingModule(ActivityModule):
    """极速狂飙：导航 + YOLO 比赛循环"""

    ID = "racing"
    NAME = "极速狂飙"
    REQUIRES_GAMEPAD_EXCLUSIVE = True
    REQUIRES = frozenset({"capture", "gamepad"})
    # 插件自带必需资源（相对插件目录）：YOLO 模型随插件分发，缺失时启动前拦截
    REQUIRED_ASSETS = ("resources/onnx/model.onnx",)

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

    def __init__(self, ctx: ActivityContext):
        super().__init__(ctx)
        self._current_stage: str | None = None
        self.nav = None          # 导航引擎（start 时创建）
        self.racing_loop = None  # 比赛控制器（首次比赛前创建）
        self._tasker = None      # MAA 任务器（模块自有）
        self._resource = None    # MAA 资源（模块自有）
        self._in_match = False   # 是否已进入对局（防止异常回退到大厅）

    @property
    def current_stage(self) -> str | None:
        """当前阶段名，None 表示尚未开始"""
        return self._current_stage

    def _ensure_pipeline(self):
        """首次比赛前创建 MAA Resource/Tasker/RacingLoop（模块自有，之后复用）"""
        if self._tasker is not None:
            return
        tasker = Tasker()
        resource = Resource()
        # gpad 不在构造注入：每局入赛前经 ctx.gamepad._get_gpad() 重新取并 bind_gpad
        # （避免 RacingLoop 跨局复用 + 每局 reset_device 销毁后指向失效 pad）
        self.racing_loop = RacingLoop(str(MODEL_PATH), debug=self.ctx.debug)
        resource.register_custom_action("RacingLoop", self.racing_loop)
        resource.post_bundle(str(RES_DIR)).wait()
        # MAA 绑定经 ctx 窄入口（内部持有 Win32Controller，模块不接触高权限对象）
        self.ctx.bind_tasker(tasker, resource)
        tasker.add_context_sink(PipelineLogger())
        self._tasker, self._resource = tasker, resource

    def start(self, start_from: str | None = None) -> None:
        """启动极速狂飙流程（阻塞，运行于 worker 线程）
        start_from: STAGE_ORDER 中的阶段名，供 GUI 断点选择

        流程分层：
          大厅层: 归位 → 导航一(极速狂飙入口) → 导航二(开始挑战)
          对局层: 导航三(寻找对手) → 弹窗 → 确认上阵 → 比赛 → 循环
        """
        # 连接已由 AppController.start_module 保证，这里幂等兜底
        if not self.ctx.connect():
            return
        # 第三个参数传 ctx：navigation.py 依赖私有接口，由 ActivityContext 提供兼容桥接
        self.nav = Navigation(self.ctx.proj, self.ctx.debug, self.ctx)
        # 安装调试渲染器：生命周期由 Context 的 ExitStack 接管（controller 结束时 close 释放）
        self.ctx.enter_context(
            self.ctx.debug_renderer.renderer(RacingDebugRenderer(self.ctx.debug)))

        try:
            # 解析断点：断点换算收敛到统一底座 StageTracker（P4 线一）。
            # 注意保持观测一致——非法 start_from / None 仍走 else 回退 0（起点阶段），
            # 不像 resolve_start_from 对非法值抛错；先 in 判断保证只对合法值调 index。
            self._tracker = StageTracker(self.STAGE_ORDER)
            if start_from and start_from in self.STAGE_ORDER:
                skip_until = self._tracker.resolve_start_from(start_from)
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

            while self.ctx.lifecycle.running:
                # ── 导航一（极速狂飙入口）──
                self._current_stage = self.STAGE_ORDER[1]
                nav1_ok = False
                if skip_until > 1:
                    logger.log(f"跳过「导航一」(断点: {start_from})")
                    nav1_ok = True
                else:
                    for retry in range(3):
                        if not self.ctx.lifecycle.running:
                            break
                        if self.nav.navigate_to_button(BTN_极速狂飙入口):
                            nav1_ok = True
                            break
                        logger.log(f"导航一失败，第{retry+1}次重试——销毁手柄复位")
                        self.ctx.gamepad.reset_device()
                        self.ctx.lifecycle.sleep(2.0)
                        self.nav.homing()
                if not nav1_ok:
                    if self.ctx.lifecycle.running:
                        logger.log("导航一失败已达最大重试次数，跳过", "WARNING")
                    break

                # ══════════════════════════════════════════════
                # 对局层：导航二(开始挑战) → 对局内(导航三→弹窗→确认→比赛) → 循环
                # ══════════════════════════════════════════════
                while self.ctx.lifecycle.running:
                    # ── 导航二（开始挑战）—— 关口：进入对局前可回退大厅 ──
                    self._current_stage = self.STAGE_ORDER[2]
                    nav2_ok = False
                    if skip_until > 2:
                        logger.log(f"跳过「导航二」(断点: {start_from})")
                        nav2_ok = True
                    else:
                        for retry in range(6):
                            if not self.ctx.lifecycle.running:
                                break
                            if self.nav.navigate_to_button(BTN_开始挑战):
                                nav2_ok = True
                                break
                            logger.log(f"导航二失败，第{retry+1}次原地重试——销毁手柄复位")
                            self.ctx.gamepad.reset_device()
                            self.ctx.lifecycle.sleep(2.0)
                            # 首次进入对局失败时穿插导航一兜底
                            if not self._in_match and retry == 2:
                                logger.log("导航二连续3次失败，重新导航一", "WARNING")
                                self.nav.homing()
                                if not self.nav.navigate_to_button(BTN_极速狂飙入口):
                                    logger.log("重新导航一也失败，放弃", "WARNING")
                                    break
                    if not nav2_ok:
                        if self.ctx.lifecycle.running:
                            if not self._in_match:
                                logger.log("导航二最终失败，回到大厅层", "WARNING")
                                skip_until = 0
                                break  # 跳出对局层，回大厅从导航一开始
                            logger.log("导航二失败（对局中），停止流程", "WARNING")
                        self.ctx.lifecycle.request_stop()
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
                            if not self.ctx.lifecycle.running:
                                break
                            logger.log(f"等待寻找对手页面...（第{retry+1}次）")
                            if not self.nav._wait_for_template("find_opponent_template", timeout=15):
                                logger.log("寻找对手页面未出现，销毁手柄重试", "WARNING")
                                self.ctx.gamepad.reset_device()
                                self.ctx.lifecycle.sleep(2.0)
                                continue
                            if self.nav.navigate_to_button(BTN_寻找对手):
                                nav3_ok = True
                                break
                            logger.log(f"导航三失败，第{retry+1}次原地重试")
                            self.ctx.gamepad.reset_device()
                            self.ctx.lifecycle.sleep(2.0)
                    if not nav3_ok:
                        if self.ctx.lifecycle.running:
                            logger.log("导航三反复失败，停止流程", "WARNING")
                        self.ctx.lifecycle.request_stop()  # 对局层异常，直接停止
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
                        with self.ctx.gamepad.acquire() as gpad:
                            self.nav._ensure_cursor(gpad)
                            self.nav.navigate_to_button(BTN_确认上阵)
                        self.ctx.lifecycle.sleep(0.5)

                    # ── 比赛（直接运行，绕过 MAA CustomAction）──
                    self._current_stage = self.STAGE_ORDER[6]
                    if self.ctx.lifecycle.running:
                        # 比赛开始前销毁导航/上局手柄，避免第二个手柄导致游戏不识別；随后每局
                        # 重新取 controller 底层手柄并注入 RacingLoop（reset 后旧 pad 已失效，
                        # 跨局复用必须不断 re-inject）。
                        self.ctx.gamepad.reset_device()
                        race_ok = False
                        for race_retry in range(3):
                            if not self.ctx.lifecycle.running:
                                break
                            t0 = time.time()
                            try:
                                self._ensure_pipeline()
                                assert self.racing_loop is not None  # _ensure_pipeline 已创建
                                from maaracing_assistant.core.capabilities import VGamepadAdapter
                                gpad = VGamepadAdapter(self.ctx.gamepad._app._get_gpad())
                                self.racing_loop.bind_gpad(gpad)
                                self.racing_loop.run_direct(self.ctx.capture)
                                elapsed = time.time() - t0
                                if elapsed < 3:
                                    logger.log(f"比赛仅运行{elapsed:.1f}秒（第{race_retry+1}/3次），判定异常重试", "WARNING")
                                    self.ctx.lifecycle.sleep(1)
                                    continue
                                logger.log(f"本轮完成（{elapsed:.1f}秒），结束原因：{self.racing_loop._end_reason}")
                                # 根据结束原因分流
                                if self.ctx.lifecycle.running and self.racing_loop._end_reason == "商店弹窗":
                                    self.nav.handle_store_popup()
                                race_ok = True
                                break
                            except Exception as e:
                                elapsed = time.time() - t0
                                logger.log(f"比赛异常: {e}（第{race_retry+1}/3次重试）", "ERROR")
                                self.ctx.lifecycle.sleep(1)
                        if not race_ok:
                            if self.ctx.lifecycle.running:
                                logger.log("比赛异常已达最大重试次数，停止流程", "WARNING")
                            self.ctx.lifecycle.request_stop()
                            break
                        self.ctx.lifecycle.sleep(2)

                    # 断点只在首轮生效，后续循环走完整流程
                    skip_until = 0
                    self._in_match = False  # 完整一局结束，重置对局标记
                    # 比赛完成 → 继续对局层循环（从导航二开始）
                    continue

                # 对局层跳出 → 如果还在运行则回大厅
                if self.ctx.lifecycle.running:
                    skip_until = 0
                    continue

            logger.log("循环已停止")
        except Exception as e:
            logger.log(f"模块执行异常: {e}", "ERROR")
        finally:
            self._current_stage = None
            self.ctx.gamepad.reset_device()

    def stop(self):
        """幂等停止：先停比赛循环，再中断 MAA Pipeline"""
        if self.racing_loop is not None:
            try:
                self.racing_loop.stop()
            except Exception:
                pass
        if self._tasker is not None:
            try:
                self._tasker.post_stop()
            except Exception:
                pass

    def cleanup(self):
        """幂等释放模块资源（renderer 由 Context.close 释放，gamepad 归还已在 start 的 finally 处理）"""
        self.racing_loop = None
        self._tasker = None
        self._resource = None
