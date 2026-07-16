#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MaaRacingAssistant v0.2.0
巅峰极速 · 极速狂飙 自动刷分
MAA Framework + YOLOv8 ONNX + vgamepad
"""

__version__ = "0.4.0"

import sys
import time
from typing import Optional

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
from maa.define import MaaWin32ScreencapMethodEnum
from maa.custom_action import CustomAction
from maa.context import Context, ContextEventSink
from maa.event_sink import NotificationType
from maa.toolkit import Toolkit

import vgamepad as vg

from debug import NavigationDebugger


# ==================== 日志 ====================
class Logger:
    # 日志级别：TRACE < DEBUG < INFO < WARNING < ERROR
    LEVELS = {"TRACE": 0, "DEBUG": 1, "INFO": 2, "WARNING": 3, "ERROR": 4}
    GUI_MIN_LEVEL = "INFO"  # GUI 只显示 INFO 及以上级别

    def __init__(self, log_dir: Path):
        log_dir.mkdir(exist_ok=True)
        self.log_file = log_dir / f"MRA_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        self._lines = []

    def log(self, msg: str, level: str = "INFO"):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] [{level}] {msg}"
        self._lines.append(line)
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def get_lines(self, min_level: str = "INFO"):
        """获取日志，可按级别过滤。GUI 默认只显示 INFO 及以上"""
        min_val = self.LEVELS.get(min_level, 2)
        return [line for line in self._lines
                if self.LEVELS.get(self._extract_level(line), 2) >= min_val]

    def _extract_level(self, line: str) -> str:
        """从日志行中提取级别，如 [INFO] → INFO"""
        parts = line.split("] [")
        if len(parts) >= 2:
            return parts[1].split("]")[0]
        return "INFO"


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


def has_physical_controller() -> bool:
    """检测是否有物理 Xbox 手柄已连接（在创建虚拟手柄前调用）"""
    try:
        for dll_name in ["xinput1_4.dll", "xinput9_1_0.dll", "xinput1_3.dll"]:
            try:
                dll = ctypes.windll[dll_name]
                break
            except Exception:
                continue
        else:
            return False

        buf = ctypes.create_string_buffer(16)
        for i in range(4):
            if dll.XInputGetState(i, buf) == 0:
                return True
        return False
    except Exception:
        return False


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
        self.gpad = None
        self.last_dir = 0
        self.frame_id = 0
        self._running = True

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
        self._create_pad()

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
            self._destroy_pad()
            self.last_dir = 0
            logger.log("赛车控制停止")
        return False



# ==================== 按钮配置 ====================
class ButtonDef:
    """按钮定义——只保留配置项"""
    __slots__ = ('name', 'pct', 'page_template', 'template_should_match', 'close_threshold')
    def __init__(self, name: str, pct: tuple, page_template: str = "",
                 template_should_match: bool = True, close_threshold: int = 65):
        self.name = name
        self.pct = pct                      # 屏幕百分比位置 (x%, y%)
        self.page_template = page_template  # 验证模板图文件名
        self.template_should_match = template_should_match  # True=匹配上算成功, False=消失算成功
        self.close_threshold = close_threshold              # 按 A 的距离阈值


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
        self._gpad = None  # 虚拟手柄，首次使用时创建，不复位不销毁
        self.debug = NavigationDebugger(self.proj)
        self._debug_mode = False  # 调试模式开关（由 GUI 控制）
        self._last_candidates: list[dict] = []  # 最近一帧的光标候选（入围的）
        self._last_all_candidates: list[dict] = []  # 最近一帧所有被探测到的轮廓（debug 黄圈）
        self._last_stick = (0, 0)  # 最近一次推杆方向 (lx, ly)，用于运动一致性评分
        self._prev_frame_positions: set[tuple] = set()  # 上帧候选位置集合，用于静止检测
        self._stationary_blacklist: dict[tuple, int] = {}  # pos → 连续静止帧数，累到 3 拉黑

    def set_debug_mode(self, enabled: bool):
        """开启/关闭调试截图模式"""
        self._debug_mode = enabled
        self.debug.enabled = enabled

    # ---------- 基础设施 ----------

    @staticmethod
    def _dist(p1: tuple, p2: tuple) -> float:
        """两点距离"""
        return ((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2) ** 0.5

    def _stop_stick(self, gpad: vg.VX360Gamepad):
        """摇杆归零"""
        gpad.left_joystick(x_value=0, y_value=0)
        gpad.update()

    def _ensure_cursor(self, gpad: vg.VX360Gamepad):
        """如果截图找不到光标，4方向推摇杆搜索"""
        # 先看当前帧有没有光标
        arr = self._screencap()
        if arr is not None:
            pos, _, _ = self._find_cursor_by_shape(arr)
            if pos is not None:
                logger.log(f"光标已找到: {pos}", "DEBUG")
                return pos
        # 找不到 → 4 方向依次搜索
        for _, x, y in [("右下",12000,-12000),("左下",-12000,-12000),
                           ("右上",12000,12000),("左上",-12000,12000)]:
            if not self._running:
                return None
            gpad.left_joystick(x_value=x, y_value=y)
            gpad.update()
            time.sleep(0.4)
            self._stop_stick(gpad)
            time.sleep(0.3)
            arr = self._screencap()
            if arr is not None:
                pos, _, _ = self._find_cursor_by_shape(arr)
                if pos is not None:
                    return pos
        return None

    def _blind_move(self, gpad: vg.VX360Gamepad, last_pos: tuple, target: tuple, elapsed: float):
        """光标丢失后盲操推摇杆"""
        dx = target[0] - last_pos[0]
        dy = target[1] - last_pos[1]
        dist = (dx * dx + dy * dy) ** 0.5
        total_needed = dist / 310.0  # 满幅 ~310 px/s
        if elapsed >= total_needed + 0.3:
            self._stop_stick(gpad)
            return
        ux = dx / dist
        uy = -dy / dist
        lx = int(ux * 8000)
        ly = int(uy * 8000)
        if lx != 0 and abs(lx) < 4260:
            lx = 4260 if lx > 0 else -4260
        if ly != 0 and abs(ly) < 4260:
            ly = 4260 if ly > 0 else -4260
        gpad.left_joystick(x_value=max(-8000, min(8000, lx)),
                          y_value=max(-8000, min(8000, ly)))
        gpad.update()
        logger.log(f"盲操: 摇杆=({lx},{ly}), 已推{elapsed:.1f}s/需{total_needed:.1f}s", "DEBUG")
        self._interruptible_sleep(0.2)
        self._stop_stick(gpad)

    def _press_and_verify(self, gpad: vg.VX360Gamepad, cursor_area: float,
                          dist_button: float, btn: ButtonDef) -> bool | None:
        """按 A → 验证是否命中
        Returns: True=成功, None=模板权威判定没点上, False=没命中已收缩阈值"""
        self._stop_stick(gpad)
        time.sleep(0.2)
        self._press_button(gpad, vg.XUSB_BUTTON.XUSB_GAMEPAD_A, duration=0.3)
        self._interruptible_sleep(1.0)

        check_arr = self._screencap()
        if check_arr is not None and btn.page_template:
            matched = self._check_page_by_template(btn.page_template)
            if btn.template_should_match and matched:
                logger.log(f"页面已切换（模板「{btn.page_template}」匹配），导航完成")
                return True
            elif not btn.template_should_match and not matched:
                logger.log(f"页面已切换（模板「{btn.page_template}」已消失），导航完成")
                return True
            elif not btn.template_should_match and matched:
                logger.log(f"模板「{btn.page_template}」仍可见，页面未切换，收缩阈值", "WARNING")
                self._nav_close_threshold = max(5, int(getattr(self, '_nav_close_threshold', btn.close_threshold) * 0.65))
                self._interruptible_sleep(0.5)
                return None

        # Fallback: 光标面积变化
        if check_arr is not None:
            check_pos, _, check_area = self._find_cursor_by_shape(check_arr)
            if check_pos is not None:
                area_drop = cursor_area - check_area
                if area_drop > 100:
                    logger.log(f"页面已切换（面积 {cursor_area}→{check_area}），导航完成")
                    return True
                elif btn.template_should_match:
                    logger.log(f"A 键未命中（面积 {cursor_area}→{check_area}），收缩阈值", "WARNING")
            elif dist_button < 45:
                logger.log("页面已切换（光标消失），导航完成")
                return True

        self._nav_close_threshold = max(30, getattr(self, '_nav_close_threshold', btn.close_threshold) - 15)
        self._interruptible_sleep(0.5)
        return False

    def check_model(self) -> bool:
        return self.model_path.exists()

    def _get_gpad(self) -> vg.VX360Gamepad:
        """获取虚拟手柄（懒创建 + 保持复用，不销毁重建）"""
        if self._gpad is None:
            self._gpad = vg.VX360Gamepad()
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
            return arr
        except Exception as e:
            logger.log(f"截图异常: {e}", "ERROR")
            return None

    def _screencap_ctypes(self):
        """使用 ctypes 直接截取窗口图像（MAA 截图失败时的备用方案）"""
        try:
            hwnd = self.controller.hWnd if hasattr(self.controller, "hWnd") else 0
            if not hwnd:
                return None

            user32 = ctypes.windll.user32
            gdi32 = ctypes.windll.gdi32
            rect = ctypes.wintypes.RECT()
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
                        arr = np.frombuffer(buf, dtype=np.uint8).reshape((h, w, 4))[:, :, :3]
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

    def _press_button(self, gpad: vg.VX360Gamepad, button, duration: float = 0.3):
        """按手柄按钮（按下→保持→释放），已内置 gpad.update()"""
        gpad.press_button(button)
        gpad.update()
        time.sleep(duration)
        gpad.release_button(button)
        gpad.update()

    def _interruptible_sleep(self, seconds: float):
        """可中断的 sleep，每 0.1 秒检查 _running 状态"""
        for _ in range(int(seconds / 0.1)):
            if not self._running:
                return
            time.sleep(0.1)

    def _load_template(self, name: str) -> np.ndarray | None:
        """加载模板图片（优先 png，其次 jpg），返回 RGB ndarray"""
        img_dir = self.proj / "assets" / "resource" / "image"
        for ext in (".png", ".jpg"):
            path = img_dir / f"{name}{ext}"
            if path.exists():
                img = cv2.imread(str(path), cv2.IMREAD_COLOR)
                if img is not None:
                    logger.log(f"模板已加载: {path.name} ({img.shape[1]}x{img.shape[0]})", "DEBUG")
                    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        logger.log(f"模板不存在: {name}.png/.jpg", "WARNING")
        return None

    # ---------- 模板匹配 ----------

    def _find_template(self, img: np.ndarray, template: np.ndarray, threshold: float = 0.7,
                       scales=None, roi=None, use_gray: bool = False) -> tuple:
        """
        多尺度模板匹配，返回 (位置, 置信度, 缩放比例)
        位置格式: (x, y)，未找到返回 (None, best_val, best_scale)
        roi: (x, y, w, h) 可选，限制搜索区域（相对于原图坐标）
        use_gray: 灰度匹配，对光照变化更鲁棒
        """
        if scales is None:
            scales = [0.8, 0.9, 1.0, 1.1, 1.2]

        # 裁剪 ROI
        search_img = img
        offset_x, offset_y = 0, 0
        if roi is not None:
            rx, ry, rw, rh = roi
            search_img = img[ry:ry+rh, rx:rx+rw]
            offset_x, offset_y = rx, ry
            logger.log(f"ROI搜索: ({rx},{ry},{rw}x{rh}), 全图={img.shape[1]}x{img.shape[0]}", "DEBUG")

        # 灰度匹配：转为灰度 + 直方图均衡化
        if use_gray:
            if search_img.ndim == 3:
                gray = cv2.cvtColor(search_img, cv2.COLOR_RGB2GRAY)
                search_img = cv2.equalizeHist(gray)
            tpl_gray = cv2.cvtColor(template, cv2.COLOR_RGB2GRAY)
            tpl = cv2.equalizeHist(tpl_gray)
            logger.log("灰度匹配已启用", "DEBUG")
        else:
            tpl = template

        best_val = 0.0
        best_loc = None
        best_scale = 1.0

        for scale in scales:
            resized = cv2.resize(tpl, None, fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR)
            if resized.shape[0] > search_img.shape[0] or resized.shape[1] > search_img.shape[1]:
                continue

            result = cv2.matchTemplate(search_img, resized, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)

            if max_val > best_val:
                best_val = max_val
                best_loc = max_loc
                best_scale = scale

        if best_val >= threshold and best_loc is not None:
            w, h = template.shape[1], template.shape[0]
            cx = int(best_loc[0] + w * best_scale / 2) + offset_x
            cy = int(best_loc[1] + h * best_scale / 2) + offset_y
            logger.log(f"找到模板: 位置({cx},{cy}), 置信度={best_val:.3f}, scale={best_scale:.2f}", "DEBUG")
            return (cx, cy), best_val, best_scale
        logger.log(f"未找到模板: 最高置信度={best_val:.3f} < {threshold:.2f}", "DEBUG")
        return None, best_val, best_scale

    def _match_settings_page(self, img: np.ndarray, template: np.ndarray, threshold: float = 0.65) -> bool:
        """检测是否为设置页面（彩色模板匹配，左上半区，多尺度适应不同窗口比例）"""
        h, w = img.shape[:2]
        roi = (0, 0, int(w * 0.5), int(h * 0.5))  # 左上半区 50%x50%
        # 多尺度：适应全屏/窗口化不同比例下UI元素的大小差异
        pos, conf, scale = self._find_template(
            img, template, threshold=threshold,
            scales=[0.8, 0.9, 1.0, 1.1, 1.2],
            roi=roi, use_gray=False)  # 彩色匹配保留颜色特征
        logger.log(f"设置页面匹配: 置信度={conf:.3f} > {threshold:.2f}? {pos is not None}")
        return pos is not None

    def _find_cursor_by_shape(self, img: np.ndarray, debug: bool = False, *,
                               last_known_pos: tuple | None = None,
                               last_stick: tuple | None = None) -> tuple:
        """
        基于几何形状识别白色圆形光标。
        对屏幕边缘的部分圆做容忍（圆度阈值动态放宽）。
        支持运动一致性评分：传入上一帧位置+摇杆方向，假光标因不移动而被扣分。

        返回: (位置(x,y), 圆度, 面积) 或 (None, 0, 0)
        """
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)

        # 降阈值（185）捕获 hover 态暗中心（193），再用 HSV 饱和度剔除彩色 UI
        _, binary = cv2.threshold(gray, 185, 255, cv2.THRESH_BINARY)

        # 饱和度过滤：光标是灰白色（S≈0），彩色 UI 元素全部挖掉
        hsv = cv2.cvtColor(img, cv2.COLOR_RGB2HSV)
        _, sat_mask = cv2.threshold(hsv[:, :, 1], 30, 255, cv2.THRESH_BINARY_INV)
        binary = cv2.bitwise_and(binary, sat_mask)

        # 可选：保存调试图像
        if debug:
            debug_dir = self.proj / "debug" / "diagnose"
            debug_dir.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(debug_dir / "cursor_binary.png"), binary)

        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        best_cursor = None
        best_score = 0.0

        h_img, w_img = img.shape[:2]
        # 面积约束：真实光标面积约 260（中心 pixel），边缘约 450~500
        min_area = max(100, int(h_img * w_img * 0.00008))
        max_area = min(550, int(h_img * w_img * 0.006))

        self._last_all_candidates = []  # 所有被探测到的轮廓（debug 黄圈）
        candidates = []                 # 通过硬过滤的入围候选（debug 绿圈）

        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < min_area or area > max_area:
                continue

            perimeter = cv2.arcLength(cnt, True)
            if perimeter < 1e-6:
                continue

            # 圆度：完美圆 = 1.0，正方形 ≈ 0.785，细长矩形 → 0
            circularity = 4 * np.pi * area / (perimeter ** 2)

            # 外接矩形
            x, y, w, h = cv2.boundingRect(cnt)
            aspect_ratio = min(w, h) / max(w, h) if max(w, h) > 0 else 0

            # 判断是否靠近图像边界（部分圆场景）
            margin = max(w, h)  # 以轮廓大小为边界容忍度
            near_edge = (x <= margin or y <= margin or
                         x + w >= w_img - margin or y + h >= h_img - margin)

            pos = (x + w // 2, y + h // 2)
            item = {
                "pos": pos,
                "area": area,
                "circularity": circularity,
                "aspect": aspect_ratio,
                "rect": (x, y, w, h),
                "near_edge": near_edge,
            }
            self._last_all_candidates.append(item)

            # ── 硬过滤：面积 < 240 的假光标直接排除 ──
            if area < 240:
                continue

            # 边缘容忍降级：即使边缘也必须>0.65（不再允许 0.45 的低质圆形）
            min_circ = 0.65 if near_edge else 0.82

            if circularity < min_circ or aspect_ratio < 0.70:
                continue

            candidates.append(item)

        for cand in candidates:
            circ = cand["circularity"]
            asp = cand["aspect"]
            near_edge = cand["near_edge"]

            # 双中心面积评分：适应常态~310 和变形~530 两种光标形态
            area_score1 = 1.0 - abs(cand["area"] - 310) / 300
            area_score2 = 1.0 - abs(cand["area"] - 420) / 300
            area_score = max(area_score1, area_score2)
            area_score = max(0.0, min(1.0, area_score))
            circ_weight = 0.5 if near_edge else 0.65
            score = circ * circ_weight + asp * 0.15 + area_score * 0.20

            # ── 假光标静止检测（用自己的位置跨帧对比，不依赖 last_known_pos）──
            if last_stick is not None:
                lx, ly = last_stick
                if lx != 0 or ly != 0:
                    if cand["pos"] in self._prev_frame_positions:
                        cnt = self._stationary_blacklist.get(cand["pos"], 0) + 1
                        self._stationary_blacklist[cand["pos"]] = cnt
                        if cnt >= 3:
                            continue  # 拉黑，不再参与评选
                        score -= cnt * 0.10
                    else:
                        self._stationary_blacklist.pop(cand["pos"], None)

            # ── 运动一致性评分（仅用于奖励方向一致性）──
            if last_known_pos is not None and last_stick is not None:
                lx, ly = last_stick
                if lx != 0 or ly != 0:
                    dx = cand["pos"][0] - last_known_pos[0]
                    dy = cand["pos"][1] - last_known_pos[1]
                    move_dist = (dx * dx + dy * dy) ** 0.5
                    if move_dist > 5:
                        stick_len = (lx * lx + ly * ly) ** 0.5
                        nx, ny = dx / move_dist, dy / move_dist
                        sx, sy = lx / stick_len, -ly / stick_len
                        alignment = nx * sx + ny * sy  # [-1, 1]
                        score += alignment * 0.15

            if score > best_score:
                best_score = score
                best_cursor = (cand["pos"], circ, cand["area"])

        # 候选列表供 debug 可视化
        self._last_candidates = candidates
        # 保存本帧候选位置集合，供下帧静止检测
        self._prev_frame_positions = {c["pos"] for c in candidates}
        self._last_cursor_score = best_score

        # ── 置信度阈值：没有哪个候选足够好，不如承认找不到 ──
        if best_cursor and best_score < 0.70:
            best_cursor = None
            best_score = 0.0

        if best_cursor:
            (cx, cy), circ, area = best_cursor
            logger.log(f"找到光标: ({cx},{cy}), 圆度={circ:.3f}, 面积={area:.1f}, 评分={best_score:.3f}", "DEBUG")
        else:
            if candidates:
                best = max(candidates, key=lambda c: c["circularity"])
                logger.log(f"未找到光标（无满足圆度/面积硬约束的候选）", "DEBUG")
                logger.log(f"  最佳候选: 圆度={best['circularity']:.3f}, 宽高比={best['aspect']:.3f}, 面积={best['area']:.1f}, 边缘={best['near_edge']}", "DEBUG")
            else:
                logger.log("未找到光标（无高亮轮廓）", "DEBUG")

        return best_cursor if best_cursor else (None, 0.0, 0)

    # ---------- 归位（Homing）----------

    def homing(self) -> bool:
        """归位：持续按 B 直到识别到设置页面，再按一次 B 返回主界面"""
        template = self._load_template("settings_page_template")
        if template is None:
            logger.log("设置页面模板不存在，跳过归位", "WARNING")
            return False

        logger.log("开始归位：按 B 直到进入设置页面...")
        gpad = self._get_gpad()

        try:
            for i in range(15):
                if not self._running:
                    logger.log("收到停止信号，中断归位")
                    return False

                arr = self._screencap()
                if arr is not None and self._match_settings_page(arr, template):
                    logger.log(f"归位完成：已识别到设置页面（第{i+1}次按B）")
                    self._press_button(gpad, vg.XUSB_BUTTON.XUSB_GAMEPAD_B, duration=0.3)
                    self._interruptible_sleep(2.0)
                    logger.log("已返回主界面，开始正式循环")
                    return True

                # 第一次截图保存调试截图
                if i == 0 and arr is not None:
                    debug_path = self.proj / "debug" / "homing_debug.png"
                    debug_path.parent.mkdir(exist_ok=True)
                    cv2.imwrite(str(debug_path), cv2.cvtColor(arr, cv2.COLOR_RGB2BGR))
                    logger.log(f"已保存首帧调试截图到 {debug_path}", "DEBUG")

                if arr is None:
                    logger.log("截图失败，继续按 B", "WARNING")
                else:
                    logger.log(f"第{i+1}次按 B...", "DEBUG")

                self._press_button(gpad, vg.XUSB_BUTTON.XUSB_GAMEPAD_B, duration=0.3)
                self._interruptible_sleep(1.5)

            logger.log("归位超时（15次按B未识别到设置页面），继续执行", "WARNING")
            return False
        finally:
            # 只释放按钮，不销毁手柄
            try:
                gpad.release_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_B)
                gpad.update()
            except Exception:
                pass

    # ---------- 主界面导航（光标追踪）----------

    def _move_cursor_to_target(self, cursor_pos: tuple, target_pos: tuple,
                               gpad: vg.VX360Gamepad, stop_distance: int = 25) -> bool:
        """控制左摇杆移动光标到目标，距离自适应保持 + 刹车防过冲

        控制策略：
        1. 远距（>150px）：满幅 200ms 推送 → 快速接近
        2. 中距（70~150px）：中幅 100ms 推送 → 平稳靠近
        3. 近距（<70px）：低幅 60ms 推送 → 精细微调
        4. 每次推送后刹车 50ms（摇杆归零），让光标减速防止过冲
        """
        cx, cy = cursor_pos
        tx, ty = target_pos
        dx = tx - cx
        dy = ty - cy
        dist = (dx * dx + dy * dy) ** 0.5

        if dist < stop_distance:
            logger.log(f"光标已对齐: 距离={dist:.1f} < {stop_distance}", "DEBUG")
            return True

        DEADZONE = 4260   # 游戏摇杆死区 ~13%
        MAX_AXIS = 8000

        # ── 方向分量（单轴追踪：已接近的轴归零，避免死区拉偏）──
        if abs(dy) < 30:
            ux = 1.0 if dx > 0 else -1.0
            uy = 0.0
        elif abs(dx) < 30:
            ux = 0.0
            uy = -1.0 if dy > 0 else 1.0  # vgamepad Y 正值=上
        else:
            ux = dx / dist
            uy = -dy / dist

        # ── 距离自适应参数 ──
        if dist > 150:
            hold_time = 0.2
            speed = max(0.7, min(1.0, dist / 200))
        elif dist > 70:
            hold_time = 0.1
            speed = max(0.55, dist / 200)
        elif dist > 35:
            hold_time = 0.08
            speed = 0.45
        else:
            hold_time = 0.025  # 微调：最短脉冲（~1.5帧），死区推满也只动几个像素
            speed = 0.28

        magnitude = MAX_AXIS * speed

        lx = int(ux * magnitude)
        ly = int(uy * magnitude)

        # ── 死区保障：每个非零轴独立达到死区 ──
        if lx != 0 and abs(lx) < DEADZONE:
            lx = DEADZONE if lx > 0 else -DEADZONE
        if ly != 0 and abs(ly) < DEADZONE:
            ly = DEADZONE if ly > 0 else -DEADZONE
        # 各自限制最大值
        lx = max(-MAX_AXIS, min(MAX_AXIS, lx))
        ly = max(-MAX_AXIS, min(MAX_AXIS, ly))

        # ── 记录推杆方向，供下帧运动一致性评分 ──
        self._last_stick = (lx, ly)

        # ── 推送：设置摇杆 → 保持 → 刹车 ──
        gpad.left_joystick(x_value=lx, y_value=ly)
        gpad.update()
        logger.log(f"移动光标: dx={dx}, dy={dy}, dist={dist:.1f}, 摇杆=({lx},{ly}), 保持={hold_time:.2f}s", "DEBUG")

        self._interruptible_sleep(hold_time)

        # 刹车：摇杆归零让光标减速，防止下周期过冲
        gpad.left_joystick(x_value=0, y_value=0)
        gpad.update()
        brake_time = 0.08 if dist < 35 else 0.05
        self._interruptible_sleep(brake_time)

        return False

    def _check_page_by_template(self, template_name: str) -> bool:
        """用 OpenCV 模板匹配检测页面是否已切换，用于验证导航是否成功"""
        arr = self._screencap()
        if arr is None:
            return False
        template = self._load_template(template_name)
        if template is None:
            return False
        pos, conf, _ = self._find_template(arr, template, threshold=0.7)
        if pos is not None:
            logger.log(f"模板「{template_name}」匹配成功，置信度={conf:.3f}")
            return True
        logger.log(f"模板「{template_name}」未匹配", "DEBUG")
        return False

    def navigate_to_button(self, btn: ButtonDef) -> bool:
        """导航光标到按钮位置并按 A，用模板匹配/面积变化验证成功"""
        logger.log(f"导航到「{btn.name}」...")
        gpad = self._get_gpad()

        # 启动调试可视化（每帧截图标注）
        self.debug.start_session(btn.name)
        # 切页面 → 清空假光标黑名单（UI 变了，旧位置不再适用）
        self._stationary_blacklist.clear()
        self._prev_frame_positions.clear()

        self._ensure_cursor(gpad)  # 找不到就4方向搜索，还找不到进盲操

        cursor_lost_start = None
        last_known_pos = None

        try:
            for attempt in range(30):
                if not self._running:
                    return False

                time.sleep(0.05)  # 等待游戏渲染新帧，避免 MAA 返回缓存
                arr = self._screencap()
                if arr is None:
                    self._interruptible_sleep(0.5)
                    continue

                h, w = arr.shape[:2]
                button_pos = (int(w * btn.pct[0]), int(h * btn.pct[1]))
                # last_stick 传的是上一轮推杆方向（只有 _move_cursor_to_target 会设）
                cursor_pos, _, cursor_area = self._find_cursor_by_shape(
                    arr, last_known_pos=last_known_pos, last_stick=self._last_stick)
                close_th = getattr(self, '_nav_close_threshold', btn.close_threshold)

                if cursor_pos is not None:
                    cursor_lost_start = None

                    # ── 缓存帧检测：光标瞬间弹回左上角且面积缩小 →  跳过本帧 ──
                    if last_known_pos is not None:
                        jump = self._dist(cursor_pos, last_known_pos)
                        if (jump > 250 and cursor_pos[0] < w * 0.3 and cursor_pos[1] < h * 0.2
                                and cursor_area < 250):
                            logger.log(f"跳过缓存帧: {cursor_pos}(面积{cursor_area})←{last_known_pos}, 跳距={jump:.0f}", "DEBUG")
                            self.debug.save_frame(
                                arr, cursor_pos=cursor_pos, cursor_area=cursor_area,
                                cursor_score=getattr(self, '_last_cursor_score', 0),
                                button_pos=button_pos, candidates=self._last_candidates, all_candidates=self._last_all_candidates,
                                label="skip_cache"
                            )
                            time.sleep(0.1)
                            continue

                    last_known_pos = cursor_pos
                    dist = self._dist(cursor_pos, button_pos)
                    logger.log(f"光标 {cursor_pos} → 按钮 {button_pos}  "
                               f"(dx={button_pos[0]-cursor_pos[0]}, dy={button_pos[1]-cursor_pos[1]})", "DEBUG")

                    # 调试截图（每帧标注）
                    self.debug.save_frame(
                        arr, cursor_pos=cursor_pos, cursor_area=cursor_area,
                        cursor_score=getattr(self, '_last_cursor_score', 0),
                        button_pos=button_pos, candidates=self._last_candidates, all_candidates=self._last_all_candidates,
                        dist=dist, label="found"
                    )

                    # 接近 → 按 A + 验证
                    if dist < close_th:
                        logger.log(f"光标接近按钮：距离={dist:.1f}px（阈值={close_th}），按 A", "DEBUG")
                        result = self._press_and_verify(gpad, cursor_area, dist, btn)
                        if result is True:
                            self._nav_close_threshold = btn.close_threshold
                            return True
                        # 按 A 失败 — 不清空 _last_stick，保留推杆方向供下帧运动评分（假光标静止惩罚）
                        continue  # False/None → 已收缩阈值，下一轮重试

                    # 远 → 移动光标（stop_distance 动态跟随阈值，确保能缩到足够近）
                    stop_dist = max(8, int(close_th * 0.55))
                    self._move_cursor_to_target(cursor_pos, button_pos, gpad, stop_distance=stop_dist)
                else:
                    # 光标丢失 → 盲操
                    if cursor_lost_start is None:
                        cursor_lost_start = time.time()
                        logger.log("光标丢失，开始盲操", "DEBUG")

                    if time.time() - cursor_lost_start >= 2.0:
                        logger.log("光标盲操超过2秒，放弃本次导航", "WARNING")
                        self.debug.save_frame(
                            arr, cursor_pos=None, button_pos=button_pos,
                            candidates=self._last_candidates, all_candidates=self._last_all_candidates, label="lost_timeout"
                        )
                        return False

                    if last_known_pos is not None:
                        self._blind_move(gpad, last_known_pos, button_pos, time.time() - cursor_lost_start)
                    else:
                        logger.log("盲操: 无已知位置，向东南搜索", "DEBUG")
                        gpad.left_joystick(x_value=4260, y_value=4260)
                        gpad.update()
                        self._interruptible_sleep(0.3)
                        self._stop_stick(gpad)

                    self.debug.save_frame(
                        arr, cursor_pos=None, button_pos=button_pos,
                        candidates=self._last_candidates, all_candidates=self._last_all_candidates,
                        label=f"blind_{time.time()-cursor_lost_start:.1f}s"
                    )
                    self._interruptible_sleep(0.3)
                    continue

                time.sleep(0.05)

            logger.log("导航超时", "WARNING")
            try:
                self.debug.save_frame(
                    arr, cursor_pos=None,
                    button_pos=button_pos if 'button_pos' in dir() else None,
                    label="timeout"
                )
            except Exception:
                pass
            return False
        finally:
            try:
                gpad.left_joystick(x_value=0, y_value=0)
                gpad.update()
            except Exception:
                pass

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

        self.resource.post_bundle(self.proj / "assets" / "resource").wait()
        self.tasker.bind(self.resource, self.controller)

        self.racing_loop = RacingLoop(str(self.model_path))
        self.resource.register_custom_action("RacingLoop", self.racing_loop)

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

        # ── 整体导航流程（内部重试失败时重启手柄复位）──
        # 流程：归位 → 导航一(主界面按钮) → 导航二(开始挑战) → Pipeline
        while self._running:
            # 1. 归位：按 B 直到回到主界面
            self.homing()

            # 2. 导航一：移动光标到主界面"极速狂飙"入口按钮并按 A
            BTN_极速狂飙入口 = ButtonDef("极速狂飙入口", (0.880, 0.720), "activity_page_template", True, 50)
            nav1_ok = False
            for retry in range(3):
                if not self._running:
                    break
                if self.navigate_to_button(BTN_极速狂飙入口):
                    nav1_ok = True
                    break
                logger.log(f"导航一失败，第{retry+1}次重试——销毁手柄复位")
                self._destroy_gpad()
                self._interruptible_sleep(2.0)  # 等游戏检测到手柄断开、复位光标
                self.homing()  # 重建手柄(_get_gpad) + B键回到主界面
            if not nav1_ok:
                if self._running:
                    logger.log("导航一失败已达最大重试次数，跳过", "WARNING")
                break

            # 3. 导航二：进入活动页面后，移动光标到"开始挑战"按钮并按 A
            BTN_开始挑战 = ButtonDef("开始挑战", (0.855, 0.898), "activity_page_template", False, 12)
            nav2_ok = False
            for retry in range(3):
                if not self._running:
                    break
                if self.navigate_to_button(BTN_开始挑战):
                    nav2_ok = True
                    break
                logger.log(f"导航二失败，第{retry+1}次重试——销毁手柄复位，从头开始")
                self._destroy_gpad()
                self._interruptible_sleep(2.0)
                # 不在这里归位，直接 break 到外层循环从头开始
            if not nav2_ok:
                if self._running:
                    logger.log("导航二已耗尽重试，回到外层循环重新归位", "WARNING")
                continue  # 回到外层 while，重新归位+导航一+导航二

            # 4. 导航二成功 → 进入 Pipeline 循环
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
            # Pipeline 结束（被停止或异常）→ 如果还在运行，重新开始循环
            continue

        logger.log("循环已停止")
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