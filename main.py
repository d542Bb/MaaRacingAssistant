#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MaaRacingAssistant
巅峰极速 · 极速狂飙 自动刷分
MAA Framework + YOLOv8 ONNX + vgamepad
"""

import sys
import time
import cv2
import numpy as np
import onnxruntime as ort
import ctypes
from ctypes import wintypes
from pathlib import Path
from datetime import datetime

from maa.tasker import Tasker
from maa.resource import Resource
from maa.controller import Win32Controller
from maa.custom_action import CustomAction
from maa.context import Context, ContextEventSink
from maa.event_sink import NotificationType
from maa.toolkit import Toolkit

import vgamepad as vg


# ==================== 日志 ====================
class Logger:
    def __init__(self, log_dir: Path):
        log_dir.mkdir(exist_ok=True)
        self.log_file = log_dir / f"maazs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        self._lines = []

    def log(self, msg: str, level: str = "INFO"):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] [{level}] {msg}"
        self._lines.append(line)
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def get_lines(self):
        return self._lines


logger = Logger(Path(__file__).parent / "logs")


# ==================== Pipeline 日志监听 ====================
class PipelineLogger(ContextEventSink):
    """监听 MAA pipeline 每步的识别和动作事件并打印日志"""

    def _task_name(self, detail) -> str:
        return getattr(detail, "name", str(detail))

    def _task_desc(self, name: str) -> str:
        """给任务名加上中文描述"""
        descs = {
            "极速狂飙入口": '找"开始挑战"',
            "回合1准备": '找"寻找对手"',
            "回合1比赛": "YOLO 赛车控制",
            "回合1结束": '找"继续"',
            "回合2准备": '找"放弃本轮"',
            "确认放弃": '找"继续放弃"',
        }
        return descs.get(name, name)

    def on_node_recognition(self, context, noti_type, detail):
        ts = NotificationType(noti_type).name
        name = self._task_name(detail)
        desc = self._task_desc(name)
        hit = getattr(detail, "hit", None)
        if ts == "Succeeded" and hit is not None:
            logger.log(f"[Pipeline] {name}({desc}) → 识别{'✅命中' if hit else '❌未找到'}")
        elif ts in ("Starting", "Succeeded"):
            logger.log(f"[Pipeline] {ts}: {name}({desc})")

    def on_node_action(self, context, noti_type, detail):
        ts = NotificationType(noti_type).name
        name = self._task_name(detail)
        desc = self._task_desc(name)
        success = getattr(detail, "success", None)
        if ts == "Succeeded" and success is not None:
            logger.log(f"[Pipeline] {name}({desc}) → 动作{'✅成功' if success else '❌失败'}")
        else:
            logger.log(f"[Pipeline] {ts} 动作: {name}({desc})")


# ==================== 窗口查找 ====================
def hwnd_from_pid(pid: int) -> int:
    user32 = ctypes.windll.user32
    _cache = {}

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        found_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(found_pid))
        if found_pid.value == pid:
            _cache["hwnd"] = hwnd
            return False
        return True

    user32.EnumWindows(callback, 0)
    return _cache.get("hwnd", 0)


def find_game_hwnd() -> int:
    try:
        Toolkit.init_option(str(Path(__file__).parent))
    except Exception:
        pass

    windows = Toolkit.find_desktop_windows()

    for win in windows:
        if win.class_name == "UnrealWindow":
            logger.log(f"找到窗口(类名): hWnd={win.hwnd}, title={win.window_name}")
            return win.hwnd

    keywords = ["巅峰极速", "g112", "Racing Master"]
    for win in windows:
        for kw in keywords:
            if kw in win.window_name:
                logger.log(f"找到窗口(标题): hWnd={win.hwnd}, title={win.window_name}")
                return win.hwnd

    GAME_PID = 0
    if GAME_PID:
        hwnd = hwnd_from_pid(GAME_PID)
        if hwnd:
            logger.log(f"找到窗口(PID): hWnd={hwnd}")
            return hwnd

    logger.log("未找到游戏窗口，可用窗口前10个:", "ERROR")
    for win in windows[:10]:
        logger.log(f"  hWnd={win.hwnd}, class={win.class_name}, title={win.window_name}", "ERROR")

    return 0


# ==================== YOLO 推理器 ====================
class YOLODetector:
    def __init__(self, model_path: str, conf: float = 0.5, iou: float = 0.45):
        try:
            self.session = ort.InferenceSession(
                model_path,
                providers=["CUDAExecutionProvider", "CPUExecutionProvider"]
            )
            logger.log("YOLO 使用 GPU (CUDA)")
        except Exception:
            self.session = ort.InferenceSession(
                model_path,
                providers=["CPUExecutionProvider"]
            )
            logger.log("YOLO 使用 CPU (CUDA 不可用)")

        self.input_name = self.session.get_inputs()[0].name
        self.conf = conf
        self.iou = iou
        self.input_size = 640

    def __call__(self, img_rgb: np.ndarray):
        orig_h, orig_w = img_rgb.shape[:2]
        scale = min(self.input_size / orig_h, self.input_size / orig_w)
        nh, nw = int(orig_h * scale), int(orig_w * scale)
        pad_y = (self.input_size - nh) // 2
        pad_x = (self.input_size - nw) // 2

        padded = np.full((self.input_size, self.input_size, 3), 114, dtype=np.uint8)
        padded[pad_y : pad_y + nh, pad_x : pad_x + nw] = cv2.resize(img_rgb, (nw, nh), interpolation=cv2.INTER_LINEAR)
        blob = padded.transpose(2, 0, 1)[None].astype(np.float32) / 255.0

        outputs = self.session.run(None, {self.input_name: blob})[0]
        preds = outputs[0].transpose(1, 0)

        xywh = preds[:, :4]
        cls_conf = preds[:, 4:]
        max_scores = np.max(cls_conf, axis=1)
        max_classes = np.argmax(cls_conf, axis=1)

        mask = max_scores > self.conf
        if not np.any(mask):
            return [], [], []

        boxes = xywh[mask]
        scores_f = max_scores[mask]
        classes = max_classes[mask]

        xyxy = np.zeros_like(boxes)
        xyxy[:, 0] = boxes[:, 0] - boxes[:, 2] / 2
        xyxy[:, 1] = boxes[:, 1] - boxes[:, 3] / 2
        xyxy[:, 2] = boxes[:, 0] + boxes[:, 2] / 2
        xyxy[:, 3] = boxes[:, 1] + boxes[:, 3] / 2

        indices = cv2.dnn.NMSBoxes(xyxy.tolist(), scores_f.tolist(), self.conf, self.iou)
        if len(indices) == 0:
            return [], [], []

        coins, cars, bonus_cars = [], [], []
        for i in indices:
            i = int(i)
            cls = int(classes[i])
            x1, y1, x2, y2 = xyxy[i]
            x1, x2 = (x1 - pad_x) / scale, (x2 - pad_x) / scale
            y1, y2 = (y1 - pad_y) / scale, (y2 - pad_y) / scale
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(orig_w, x2), min(orig_h, y2)
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            bw, bh = x2 - x1, y2 - y1
            if cls == 0:
                coins.append((int(cx), int(cy), int(bw), int(bh)))
            elif cls == 1:
                cars.append((int(cx), int(cy), int(bw), int(bh)))
            else:
                bonus_cars.append((int(cx), int(cy), int(bw), int(bh)))

        return coins, cars, bonus_cars


# ==================== 赛车控制 ====================
class RacingLoop(CustomAction):
    def __init__(self, model_path: str):
        super().__init__()
        self.det = YOLODetector(model_path)
        self.gpad = vg.VX360Gamepad()
        self.last_dir = 0
        self.frame_id = 0
        self._running = True

    def stop(self):
        self._running = False
        # 松开所有手柄按键（RT + 方向）
        self.gpad.right_trigger(value=0)
        self.gpad.left_joystick(x_value=0, y_value=0)
        self.gpad.update()
        self.last_dir = 0

    def _steer(self, direction: int):
        # 注意：不在此处控制 RT——RT 由 run() 的入口/finally 统一管理
        if direction == -1:
            self.gpad.left_joystick(x_value=-32768, y_value=0)
        elif direction == 1:
            self.gpad.left_joystick(x_value=32767, y_value=0)
        else:
            self.gpad.left_joystick(x_value=0, y_value=0)
        self.gpad.update()

    def _cap(self, ctrl):
        try:
            img = ctrl.post_screencap().wait()
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
            logger.log(f"截图异常: {e}", "ERROR")
            return None

    def _is_shop(self, img: np.ndarray) -> bool:
        h, w = img.shape[:2]
        roi = img[20:120, 20:int(w * 0.25)]
        if roi.size == 0:
            return False
        gray = cv2.cvtColor(roi, cv2.COLOR_RGB2GRAY)
        return np.sum(gray > 200) / gray.size > 0.08 and 50 < np.mean(gray) < 150

    def _click_exit_shop(self, ctrl, img: np.ndarray):
        h, w = img.shape[:2]
        ctrl.post_click(int(w * 0.05), int(h * 0.05)).wait()

    def _is_end(self, img: np.ndarray) -> bool:
        h, w = img.shape[:2]
        roi = img[0:int(h * 0.25), int(w * 0.2):int(w * 0.8)]
        if roi.size == 0:
            return False
        gray = cv2.cvtColor(roi, cv2.COLOR_RGB2GRAY)
        return np.sum(gray > 180) / gray.size > 0.12 and np.var(gray) > 800

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

    def run(self, context: Context, argv: dict) -> bool:
        ctrl = context.controller
        logger.log("赛车控制启动")
        self._running = True

        # 起步：按住 RT 加速
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

                if self.frame_id % 15 == 0 and self._is_shop(img):
                    logger.log("检测到商店，退出")
                    self._steer(0)
                    self._click_exit_shop(ctrl, img)
                    time.sleep(1.5)
                    continue

                if self.frame_id % 30 == 0 and self._is_end(img):
                    logger.log("回合1结束")
                    self._steer(0)
                    return True

                coins, cars, bonus_cars = self.det(img)
                direction = self._decide(coins, cars, bonus_cars, w, h)

                if direction != self.last_dir:
                    self._steer(direction)
                    self.last_dir = direction

                elapsed = time.time() - t0
                sleep = max(0, 1 / 15 - elapsed)
                if sleep:
                    time.sleep(sleep)
        finally:
            # 松开所有按键：RT + 方向归中
            self.gpad.right_trigger(value=0)
            self.gpad.left_joystick(x_value=0, y_value=0)
            self.gpad.update()
            self.last_dir = 0
            logger.log("赛车控制停止")
        return False


# ==================== 主控制类 ====================
class MaaRacingAssistantController:
    def __init__(self):
        self.proj = Path(__file__).parent
        self.model_path = self.proj / "assets" / "model" / "yolov8n_coins_cars.onnx"
        self.tasker = None
        self.resource = None
        self.controller = None
        self.racing_loop = None
        self._running = False

    def check_model(self) -> bool:
        return self.model_path.exists()

    def connect(self) -> bool:
        hwnd = find_game_hwnd()
        if hwnd == 0:
            logger.log("未找到游戏窗口", "ERROR")
            return False

        self.controller = Win32Controller(hWnd=hwnd)

        if not self.controller.post_connection().wait():
            logger.log("连接失败，请检查游戏是否运行/管理员权限", "ERROR")
            return False

        logger.log(f"已连接窗口 (hWnd={hwnd})")

        self.tasker = Tasker()
        self.resource = Resource()
        
        self.resource.post_bundle(self.proj / "assets" / "resource").wait()
        self.tasker.bind(self.resource, self.controller)

        self.racing_loop = RacingLoop(str(self.model_path))
        self.resource.register_custom_action("RacingLoop", self.racing_loop)

        # 注册 pipeline 日志监听
        self.tasker.add_context_sink(PipelineLogger())

        return True

    def start(self):
        if not self.check_model():
            logger.log(f"模型不存在: {self.model_path}", "ERROR")
            return

        if not self.connect():
            return

        self._running = True
        logger.log("开始循环")

        while self._running:
            try:
                self.tasker.post_task("极速狂飙入口").wait()
                logger.log("本轮完成")
                if not self._running:
                    break
                time.sleep(2)
            except Exception as e:
                logger.log(f"异常: {e}", "ERROR")
                time.sleep(5)

        logger.log("循环已停止")

    def stop(self):
        self._running = False
        if self.racing_loop:
            self.racing_loop.stop()
        logger.log("收到停止信号")