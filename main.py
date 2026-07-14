#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MaaRacingAssistant v0.2.0
巅峰极速 · 极速狂飙 自动刷分
MAA Framework + YOLOv8 ONNX + vgamepad
"""

__version__ = "0.2.0"

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

    # ---------- 基础设施 ----------

    def check_model(self) -> bool:
        return self.model_path.exists()

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

    def _match_settings_page(self, img: np.ndarray, template: np.ndarray, threshold: float = 0.70) -> bool:
        """检测是否为设置页面（彩色模板匹配，左上半区，固定尺度）"""
        h, w = img.shape[:2]
        roi = (0, 0, int(w * 0.5), int(h * 0.5))  # 左上半区 50%x50%
        pos, conf, scale = self._find_template(
            img, template, threshold=threshold,
            scales=[1.0],  # 固定尺度，不做缩放
            roi=roi, use_gray=False)  # 彩色匹配保留颜色特征
        logger.log(f"设置页面匹配: 置信度={conf:.3f} > {threshold:.2f}? {pos is not None}")
        return pos is not None

    def _find_cursor_by_shape(self, img: np.ndarray, debug: bool = False) -> tuple:
        """
        基于几何形状识别白色圆形光标。
        对屏幕边缘的部分圆做容忍（圆度阈值动态放宽）。
        返回: (位置(x,y), 圆度, 面积) 或 (None, 0, 0)
        """
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)

        # 提取高亮区域（纯白色/近白色光标）
        _, binary = cv2.threshold(gray, 240, 255, cv2.THRESH_BINARY)

        # 可选：保存调试图像
        if debug:
            debug_dir = self.proj / "debug" / "diagnose"
            debug_dir.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(debug_dir / "cursor_binary.png"), binary)

        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        best_cursor = None
        best_score = 0.0

        h_img, w_img = img.shape[:2]
        # 面积约束：根据图像尺寸动态调整，覆盖直径约 15~80px 的圆
        min_area = max(100, int(h_img * w_img * 0.00008))
        max_area = min(6000, int(h_img * w_img * 0.006))

        candidates = []
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

            candidates.append({
                "pos": (x + w // 2, y + h // 2),
                "area": area,
                "circularity": circularity,
                "aspect": aspect_ratio,
                "rect": (x, y, w, h),
                "near_edge": near_edge
            })

        for cand in candidates:
            circ = cand["circularity"]
            asp = cand["aspect"]
            near_edge = cand["near_edge"]

            # 边缘容忍：靠近边界时放宽圆度要求（部分圆 naturally 圆度低）
            if near_edge:
                min_circ = 0.45  # 1/4 圆角落场景
            else:
                min_circ = 0.78  # 完整圆硬约束

            if circ < min_circ or asp < 0.70:
                continue

            # 评分：圆度权重最高，面积适中加分，边缘候选降低圆度权重避免误判
            area_score = 1.0 - abs(cand["area"] - 260) / 400  # 260=实际光标面积中心
            area_score = max(0.0, min(1.0, area_score))
            circ_weight = 0.5 if near_edge else 0.6
            score = circ * circ_weight + asp * 0.2 + area_score * 0.2

            if score > best_score:
                best_score = score
                best_cursor = (cand["pos"], circ, cand["area"])

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
        gpad = vg.VX360Gamepad()

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
            try:
                gpad.reset()
                gpad.update()
                del gpad
            except Exception:
                pass

    # ---------- 主界面导航（光标追踪）----------

    def _move_cursor_to_target(self, cursor_pos: tuple, target_pos: tuple,
                               gpad: vg.VX360Gamepad, stop_distance: int = 25) -> bool:
        """控制左摇杆移动光标到目标（慢速防过冲），返回 True 表示已对齐"""
        cx, cy = cursor_pos
        tx, ty = target_pos
        dx = tx - cx
        dy = ty - cy
        dist = (dx * dx + dy * dy) ** 0.5

        if dist < stop_distance:
            logger.log(f"光标已对齐: 距离={dist:.1f} < {stop_distance}", "DEBUG")
            return True

        # 菜单光标灵敏度高，用小幅值慢速移动防止过冲
        MAX_AXIS = 8000
        speed = max(0.6, min(1.0, dist / 200))  # 最低 0.6 防死区（游戏需要 ~13% 摇杆幅度）
        lx = int(dx / dist * MAX_AXIS * speed)
        ly = int(-dy / dist * MAX_AXIS * speed)  # Y 取反：vgamepad 正值=上，但 dy>0 表示目标在下
        gpad.left_joystick(x_value=lx, y_value=ly)
        gpad.update()
        logger.log(f"移动光标: dx={dx}, dy={dy}, dist={dist:.1f}, 摇杆=({lx},{ly})", "DEBUG")
        return False

    def navigate_to_button(self, center_first: bool = False) -> bool:
        """识别光标并移动到「极速狂飙」按钮（按钮位置按百分比固定计算），按 A 确认
        center_first: 首次调用时向右下推摇杆归中光标"""
        # 按钮位置按百分比硬编码（基于当前窗口分辨率自适应）
        BUTTON_PCT = (0.898, 0.751)

        logger.log("开始导航：识别光标...")
        gpad = vg.VX360Gamepad()

        try:
            # 仅首次：光标默认在左上角，向右下推摇杆进入画面
            if center_first:
                logger.log("光标归中：向右下推摇杆进入画面...", "DEBUG")
                # 分两阶段推：先大幅右推确保进入 X 范围，再推下确保进入 Y 范围
                gpad.left_joystick(x_value=12000, y_value=-12000)
                gpad.update()
                time.sleep(0.6)
                gpad.left_joystick(x_value=0, y_value=0)
                gpad.update()
                time.sleep(0.4)

            cursor_lost_start = None  # 光标首次丢失的时间戳

            for attempt in range(30):
                if not self._running:
                    logger.log("收到停止信号，中断导航")
                    return False

                arr = self._screencap()
                if arr is None:
                    self._interruptible_sleep(0.5)
                    continue

                h, w = arr.shape[:2]
                # 按钮坐标 = 截图尺寸 × 百分比
                button_pos = (int(w * BUTTON_PCT[0]), int(h * BUTTON_PCT[1]))
                logger.log(f"按钮目标位置: {button_pos} (基于 {w}x{h} × {BUTTON_PCT})", "DEBUG")

                cursor_pos, cursor_circ, cursor_area = self._find_cursor_by_shape(arr)

                if cursor_pos is None:
                    logger.log(f"未找到光标，重试", "DEBUG")
                    if cursor_lost_start is None:
                        cursor_lost_start = time.time()
                    elif time.time() - cursor_lost_start >= 2.0:
                        logger.log("光标丢失超过2秒，放弃本次导航（手柄断开→光标复位）", "WARNING")
                        return False  # finally 会销毁手柄，游戏自动复位光标
                    # 2 秒内先小幅推摇杆（可能还在边缘外）
                    if attempt < 3:
                        gpad.left_joystick(x_value=8000, y_value=-8000)
                        gpad.update()
                        time.sleep(0.2)
                        gpad.left_joystick(x_value=0, y_value=0)
                        gpad.update()
                    self._interruptible_sleep(0.5)
                    continue

                # 找到光标 → 重置丢失计时
                cursor_lost_start = None

                logger.log(f"导航光标 {cursor_pos} → 按钮 {button_pos}  (dx={button_pos[0]-cursor_pos[0]}, dy={button_pos[1]-cursor_pos[1]})", "DEBUG")

                if self._move_cursor_to_target(cursor_pos, button_pos, gpad, stop_distance=25):
                    gpad.left_joystick(x_value=0, y_value=0)
                    gpad.update()
                    time.sleep(0.2)
                    logger.log("按 A 确认")
                    self._press_button(gpad, vg.XUSB_BUTTON.XUSB_GAMEPAD_A, duration=0.3)
                    self._interruptible_sleep(1.5)
                    logger.log("导航完成，已按 A 确认")
                    return True

                time.sleep(0.05)

            logger.log("导航超时", "WARNING")
            return False
        finally:
            try:
                gpad.reset()
                gpad.update()
                del gpad
            except Exception:
                pass

    # ---------- 连接与启停 ----------

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

        # 1. 归位：按 B 直到回到主界面
        self.homing()

        # 2. 导航：移动光标到"极速狂飙"按钮并按 A（失败则重新归位重试）
        for retry in range(3):
            if not self._running:
                break
            if self.navigate_to_button(center_first=(retry == 0)):
                break
            logger.log(f"导航失败，第{retry+1}次重试（重新归位）")
            self.homing()
        else:
            if self._running:
                logger.log("导航失败已达最大重试次数，跳过", "WARNING")

        # 3. 进入 Pipeline 循环
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
        if self.tasker:
            try:
                self.tasker.post_stop()
                logger.log("Pipeline 已中断")
            except Exception as e:
                logger.log(f"中断 Pipeline 时出错: {e}", "ERROR")
        logger.log("收到停止信号")