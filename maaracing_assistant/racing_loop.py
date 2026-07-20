#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
赛车控制模块：YOLO 实时目标检测 + 虚拟手柄赛道控制
"""

import time
from typing import Any

import cv2
import numpy as np
import vgamepad as vg

from maa.custom_action import CustomAction
from maa.context import Context

from maaracing_assistant.yolo_detector import YOLODetector
from maaracing_assistant.logger import logger


class RacingLoop(CustomAction):
    def __init__(self, model_path: str, debug=None):
        super().__init__()
        self.det = YOLODetector(model_path)
        self.debug = debug
        self.gpad = None
        self.last_dir = 0
        self.frame_id = 0
        self._running = True
        self._end_reason = ""  # 最近一次 _is_end 匹配的结果原因
        # 跳帧推理缓存
        self._cached_coins: list = []
        self._cached_cars: list = []
        self._cached_bonus: list = []
        self._cached_yolo_debug: list = []

        # 加载结束检测模板（任一匹配即认为本轮结束）
        from pathlib import Path
        self._end_templates: list[tuple[cv2.Mat, str, float]] = []  # (gray, name, threshold)
        proj = Path(__file__).resolve().parent.parent
        for tpl_file, label, threshold in [
            ("store_popup_template.jpg", "商店弹窗", 0.90),
            ("round1_end_template.jpg", "回合1结束", 0.55),
        ]:
            tpl_path = proj / "assets" / "resource" / "image" / tpl_file
            if tpl_path.exists():
                tpl = cv2.imread(str(tpl_path))
                if tpl is not None:
                    self._end_templates.append((cv2.cvtColor(tpl, cv2.COLOR_BGR2GRAY), label, threshold))

    def _create_pad(self):
        """创建新的虚拟手柄并发送归零握手，避免残留偏置"""
        if self.gpad is not None:
            try:
                del self.gpad
            except Exception:
                pass
            self.gpad = None
            time.sleep(0.1)
        self.gpad = vg.VX360Gamepad()
        # 发送 3 次全零报告，清掉驱动层可能的残留状态
        for _ in range(3):
            self.gpad.reset()
            self.gpad.right_trigger(value=0)
            self.gpad.left_trigger(value=0)
            self.gpad.left_joystick(x_value=0, y_value=0)
            self.gpad.right_joystick(x_value=0, y_value=0)
            self.gpad.update()
            time.sleep(0.05)
        logger.log("虚拟手柄已创建并归零")

    def _destroy_pad(self):
        """销毁虚拟手柄，释放设备"""
        if self.gpad is not None:
            try:
                self.gpad.reset()
                self.gpad.update()
            except Exception:
                pass
            try:
                del self.gpad
            except Exception:
                pass
            self.gpad = None
            logger.log("虚拟手柄已销毁")

    def stop(self):
        self._running = False
        self._destroy_pad()
        self.last_dir = 0
        self._cached_coins = []
        self._cached_cars = []
        self._cached_bonus = []
        self._cached_yolo_debug = []

    def _steer(self, direction: int):
        if self.gpad is None:
            return
        # 注意：不在此处控制 RT——RT 由 run() 的入口/finally 统一管理
        if direction == -1:
            self.gpad.left_joystick(x_value=-32768, y_value=0)
        elif direction == 1:
            self.gpad.left_joystick(x_value=32767, y_value=0)
        else:
            self.gpad.left_joystick(x_value=0, y_value=0)
        # 防御：确保右摇杆始终归中（本类不控制视角）
        self.gpad.right_joystick(x_value=0, y_value=0)
        self.gpad.update()

    def _cap(self, ctrl):
        try:
            job = ctrl.post_screencap()
            job.wait()
            img = job.get()
            if img is None:
                logger.log("截图 job.get() 返回 None", "WARNING")
                return None

            if hasattr(img, "numpy"):
                arr = img.numpy()
            elif isinstance(img, np.ndarray):
                arr = img
            elif hasattr(img, "__array__"):
                arr = np.asarray(img)
            else:
                logger.log(f"未知图像类型={type(img).__name__}", "WARNING")
                return None

            if arr is None or arr.size == 0 or arr.ndim < 3:
                logger.log(f"图像格式异常: shape={arr.shape if arr is not None else None}", "WARNING")
                return None
            return arr
        except Exception as e:
            logger.log(f"截图异常: {e}", "ERROR")
            return None

    def _is_end(self, img: np.ndarray) -> bool:
        """检查本轮是否结束：任一模板匹配即算结束"""
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        for tpl_gray, label, threshold in self._end_templates:
            # 检查画面尺寸 >= 模板尺寸，否则 matchTemplate 会崩溃
            if gray.shape[0] < tpl_gray.shape[0] or gray.shape[1] < tpl_gray.shape[1]:
                continue
            result = cv2.matchTemplate(gray, tpl_gray, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(result)
            if max_val > threshold:
                self._end_reason = label
                logger.log(f"检测到结束画面「{label}」，置信度={max_val:.3f}")
                return True
        return False

    def _decide(self, coins, cars, bonus_cars, w: int, h: int) -> int:
        center_x = w // 2
        lane_w = w * 0.12

        # 0️⃣ 最高优先级：跳板车/油罐车 → 对准开
        if bonus_cars:
            target = max(bonus_cars, key=lambda b: b[1])
            cx, cy, bw, bh = target
            deadzone = w * 0.06
            if cx < center_x - deadzone:
                logger.log(f"[YOLO] bonus_car 在左({cx},{cy})，左转对准")
                return -1
            elif cx > center_x + deadzone:
                logger.log(f"[YOLO] bonus_car 在右({cx},{cy})，右转对准")
                return 1
            logger.log(f"[YOLO] bonus_car 在正中({cx},{cy})，直冲")
            return 0

        # 1️⃣ 避让障碍车
        threats = [(cx, cy, cw, ch) for cx, cy, cw, ch in cars if abs(cx - center_x) < lane_w * 1.8]
        if threats:
            tx, ty, tw, th = max(threats, key=lambda t: t[1])
            left_occ = any(cx < center_x and abs(cx - center_x) < lane_w * 2.2 for cx, cy, cw, ch in cars)
            right_occ = any(cx > center_x and abs(cx - center_x) < lane_w * 2.2 for cx, cy, cw, ch in cars)

            if tx < center_x - lane_w * 0.3:
                dir = 1 if not right_occ else (-1 if not left_occ else 1)
                logger.log(f"[YOLO] 障碍车在左({tx},{ty})，{'右' if dir == 1 else '左'}避让" + (f"(右道被占{'→左' if dir == -1 else ''})" if dir == -1 else ""))
                return dir
            elif tx > center_x + lane_w * 0.3:
                dir = -1 if not left_occ else (1 if not right_occ else -1)
                logger.log(f"[YOLO] 障碍车在右({tx},{ty})，{'左' if dir == -1 else '右'}避让" + (f"(左道被占{'→右' if dir == 1 else ''})" if dir == 1 else ""))
                return dir
            else:
                if not left_occ and right_occ:
                    logger.log(f"[YOLO] 障碍车正前({tx},{ty})，左避让")
                    return -1
                elif not right_occ and left_occ:
                    logger.log(f"[YOLO] 障碍车正前({tx},{ty})，右避让")
                    return 1
                elif not left_occ and not right_occ:
                    logger.log(f"[YOLO] 障碍车正前({tx},{ty})，左避让(两侧空)")
                    return -1
                logger.log(f"[YOLO] 障碍车正前({tx},{ty})，两侧被占，直行硬刚")
                return 0

        if coins:
            cx = max(coins, key=lambda c: c[1])[0]
            deadzone = w * 0.04
            if cx < center_x - deadzone:
                logger.log(f"[YOLO] 金币在左({cx})，左转吃币")
                return -1
            elif cx > center_x + deadzone:
                logger.log(f"[YOLO] 金币在右({cx})，右转吃币")
                return 1
            return 0

        if self.frame_id % 15 == 0:
            logger.log("[YOLO] 无目标，直行")
        return 0

    def _run_impl(self, ctrl) -> bool:
        """赛车控制核心逻辑（被 run / run_direct 共用）"""
        logger.log("赛车控制启动")
        self._running = True
        self.frame_id = 0  # 重试时重置帧计数
        if self.debug is not None:
            self.debug.start_session("racing")
        self._create_pad()

        # ── 常量 ──
        YOLO_INTERVAL = 3          # 每 3 游戏帧做一次 YOLO 推理
        SLOW_CHECK = 15            # 每秒（~15fps）检一次商店/结束
        LABEL_LOG = 30             # 每 30 帧打一次日志
        DISK_SAVE = 15             # 每 15 帧写一次磁盘(≈5次YOLO帧)

        # 起步：按住 RT 加速（游戏内部有倒计时，车不会立即动）
        assert self.gpad is not None, "手柄未创建"
        self.gpad.right_trigger(value=255)
        self.gpad.update()

        try:
            while self._running:
                t0 = time.time()
                img = self._cap(ctrl)
                if img is None:
                    time.sleep(0.05)
                    continue

                self.frame_id += 1
                h, w = img.shape[:2]

                # ── 1Hz 检测：本轮结束（商店弹窗 / 回合1结束画面） ──
                if self.frame_id % SLOW_CHECK == 0 and self._is_end(img):
                    self._steer(0)
                    return True

                # ── 跳帧 YOLO 推理 ──
                if self.frame_id % YOLO_INTERVAL == 0:
                    coins, cars, bonus_cars, yolo_debug = self.det(img)
                    self._cached_coins = coins
                    self._cached_cars = cars
                    self._cached_bonus = bonus_cars
                    self._cached_yolo_debug = yolo_debug
                coins = self._cached_coins
                cars = self._cached_cars
                bonus_cars = self._cached_bonus
                yolo_debug = self._cached_yolo_debug

                direction = self._decide(coins, cars, bonus_cars, w, h)

                if self.frame_id % LABEL_LOG == 5:
                    logger.log(f"[YOLO] 帧#{self.frame_id} 金币={len(coins)} 障碍车={len(cars)} 跳板车={len(bonus_cars)} 方向={'左' if direction<0 else '右' if direction>0 else '直'}")

                if direction != self.last_dir:
                    self._steer(direction)
                    self.last_dir = direction

                # ── 调试帧 ──
                if self.debug is not None and (self.debug.enabled or self.debug.peep_enabled):
                    save_disk = self.debug.enabled  # 调试模式每帧存盘，不跳帧
                    self.debug.save_frame(
                        img, detections=yolo_debug,
                        label=f"race_f{self.frame_id}_d{'L' if direction==-1 else 'S' if direction==0 else 'R'}",
                        save_to_disk=save_disk,
                    )

                elapsed = time.time() - t0
                sleep = max(0, 1 / 15 - elapsed)
                if sleep:
                    time.sleep(sleep)
        finally:
            self._destroy_pad()
            self.last_dir = 0
            logger.log("赛车控制停止")
        return False

    def run(self, context: Context, argv: dict) -> bool:  # type: ignore[override]
        """MAA Pipeline CustomAction 入口（保留兼容）"""
        return self._run_impl(context.controller)

    def run_direct(self, ctrl) -> bool:
        """绕过 MAA Pipeline 直接运行赛车控制"""
        return self._run_impl(ctrl)
