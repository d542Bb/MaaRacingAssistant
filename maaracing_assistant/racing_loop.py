#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
赛车控制模块：YOLO 实时目标检测 + 虚拟手柄赛道控制
"""

import time
from typing import Any
from pathlib import Path

import cv2
import numpy as np
import vgamepad as vg

from maa.custom_action import CustomAction
from maa.context import Context

from maaracing_assistant.yolo_detector import YOLODetector
from maaracing_assistant.logger import logger


def _read_physical_xinput() -> tuple[int, int, int]:
    """读取物理手柄 XInput 状态，返回 (lx, ly, rt)
    lx/ly: 左摇杆 -32768~32767, rt: 右扳机 0~255
    无手柄或读取失败返回 (0, 0, 0)
    """
    import ctypes

    # XINPUT_GAMEpad 结构体定义
    class XINPUT_GAMEPAD(ctypes.Structure):
        _fields_ = [
            ("wButtons", ctypes.c_ushort),
            ("bLeftTrigger", ctypes.c_ubyte),
            ("bRightTrigger", ctypes.c_ubyte),
            ("sThumbLX", ctypes.c_short),
            ("sThumbLY", ctypes.c_short),
            ("sThumbRX", ctypes.c_short),
            ("sThumbRY", ctypes.c_short),
        ]

    class XINPUT_STATE(ctypes.Structure):
        _fields_ = [
            ("dwPacketNumber", ctypes.c_uint),
            ("Gamepad", XINPUT_GAMEPAD),
        ]

    try:
        xinput = ctypes.windll.xinput1_4
    except Exception:
        try:
            xinput = ctypes.windll.xinput1_3
        except Exception:
            return 0, 0, 0

    state = XINPUT_STATE()
    if xinput.XInputGetState(0, ctypes.byref(state)) != 0:
        return 0, 0, 0

    return state.Gamepad.sThumbLX, state.Gamepad.sThumbLY, state.Gamepad.bRightTrigger


class RacingLoop(CustomAction):
    # 路面 ROI（裁剪掉顶部分数条和底部仪表盘，让 YOLO 专注路面）
    # 1280×720 下 y=28%~78% → (0, 201, 1280, 561)
    ROI = (0, 201, 1280, 561)

    def __init__(self, model_path: str, debug=None, record_mode: bool = False):
        super().__init__()
        self.det = YOLODetector(model_path)
        self.debug = debug
        self.gpad = None
        self.last_dir = 0
        self.frame_id = 0
        self._running = True
        self._end_reason = ""  # 最近一次 _is_end 匹配的结果原因
        self._coin_turn_log_count = 0  # 金币转向诊断计数
        self._record_mode = record_mode  # 记录模式：不拦截手柄，记录人工操作
        # 跳帧推理缓存
        self._cached_coins: list = []
        self._cached_cars: list = []
        self._cached_bonus: list = []
        self._cached_yolo_debug: list = []
        self._cached_all_raw: list = []
        self._lane_debug: dict | None = None  # 标线检测中间数据（供 debug 可视化）
        # 防碰撞历史
        self._wall_memory = 0  # 标线丢失后的防碰撞记忆：0=无, 1=左墙, -1=右墙
        self._wall_pos_history: list[int] = []  # 单边标线位置历史（防碰撞二阶导用）
        self._wall_side: str | None = None      # 当前追踪的标线侧
        self._dynamic_horizon = None  # 从 YOLO 推断的地平线，首次检测到后锁死当整局
        self._keep_hist: list[int] = []  # 车道保持位置历史
        self._keep_strength: float = 0.0   # 车道保持当前力度 (0~1)
        self._keep_dir: int = 0            # 车道保持当前方向 (-1/0/1)
        self._keep_cooldown: int = 0       # 车道保持冷却帧数
        self._last_dodge_dir: int = 0       # 上次避障方向（防抖迟滞用）
        self._last_dodge_frame: int = 0      # 上次避障帧号
        self._c_burst = 0  # C区突发修正剩余帧数
        self._c_burst_dir = 0
        self._c_coast = 0  # 突发后强制归中滑行剩余帧数
        # 前馈控制：记录上一帧目标位置
        self._prev_aim_cx: float = 0.0  # 上一帧瞄准目标的 cx
        self._prev_aim_cy: float = 0.0  # 上一帧瞄准目标的 cy
        self._prev_aim_frame: int = 0   # 上一帧瞄准帧号
        self._aim_debug: dict = {}      # 前馈调试信息

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
        self._last_rt: int = 0
        self._last_stick: tuple[int, int] = (0, 0)

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
        self._cached_all_raw = []
        self._lane_debug = None
        self._dynamic_horizon = None
        self._wall_pos_history.clear()
        self._wall_side = None
        self._keep_hist.clear()
        self._keep_strength = 0.0
        self._keep_dir = 0
        self._keep_cooldown = 0
        # 重置前馈
        self._prev_aim_cx = 0.0
        self._prev_aim_cy = 0.0
        self._prev_aim_frame = 0

    def _steer(self, direction: int):
        """方向控制。direction=-1/0/1=全量, ±(2000~32767)=比例值"""
        if self.gpad is None:
            return
        if direction == 0:
            x = 0
        elif abs(direction) <= 1:
            x = direction * 32767  # -1/0/1 → full lock
        else:
            x = max(-32768, min(32767, direction))  # 比例值原样传入
        self.gpad.left_joystick(x_value=x, y_value=0)
        self.gpad.right_joystick(x_value=0, y_value=0)
        self.gpad.update()
        self._last_stick = (x, 0)

    def _apply_trigger(self, value: int):
        """设置油门并记录状态"""
        if self.gpad is None:
            return
        self.gpad.right_trigger(value=value)
        self.gpad.update()
        self._last_rt = value

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
            # MAA/OpenCV 默认返回 BGR，统一转为 RGB 供全链路使用
            if arr.shape[2] == 4:
                arr = cv2.cvtColor(arr, cv2.COLOR_BGRA2RGB)
            elif arr.shape[2] == 3:
                arr = cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)
            return arr
        except Exception as e:
            logger.log(f"截图异常: {e}", "ERROR")
            return None

    # ---------- 距离区域划分 ----------

    @property
    def _zone_boundaries(self) -> tuple[int, int, int, int]:
        """返回 (horizon, far_bot, mid_bot, roi_bot)
        horizon 从 YOLO 动态推断（首次锁死），分界线相对地平线偏移固定像素
        """
        horizon = self._dynamic_horizon
        if horizon is None:
            horizon = int(720 * 0.445)  # 默认 44.5%
        return (
            horizon,
            horizon + 14,   # 远/中 = 地平线 +2.0%（720×0.020=14px）
            horizon + 43,   # 中/近 = 地平线 +6.0%（720×0.060=43px，下移1%给更多反应空间）
            self.ROI[3],    # 561
        )

    def _get_zone(self, cy: int, bh: int = 0) -> int:
        """根据对象框底部(y2)判断距离区域：0=远区, 1=中区, 2=近区"""
        y2 = cy + bh // 2  # 框底部 = 中心 + 半高
        _, far_bot, mid_bot, _ = self._zone_boundaries
        if y2 < far_bot:
            return 0
        if y2 < mid_bot:
            return 1
        return 2

    _ZONE_LABELS = ["远区", "中区", "近区"]

    # ---------- 动态地平线推断 ----------

    def _detect_horizon(self, all_raw_dets: list, h: int, w: int) -> int | None:
        """从 YOLO 低置信度小车群推断地平线，首次成功即锁死"""
        if self._dynamic_horizon is not None:
            return self._dynamic_horizon
        if self.frame_id < 40:  # 前40帧等加速后镜头稳定
            return None
        if not all_raw_dets:
            return None
        MAX_AREA = 400  # 20×20px 以上排除（近处大车）
        car_mids = []
        for d in all_raw_dets:
            if d["class_name"] != "car" or d["confidence"] > 0.25:
                continue
            x1, y1, x2, y2 = d["box"]
            area = (x2 - x1) * (y2 - y1)
            if area > MAX_AREA:
                continue  # 排除近处大车（出商店误判）
            cx = (x1 + x2) // 2
            if cx > w * 0.15 and cx < w * 0.85:
                car_mids.append((y1 + y2) // 2)
        if len(car_mids) < 3:
            return None
        car_mids.sort()
        self._dynamic_horizon = car_mids[len(car_mids) // 4]
        logger.log(f"[HORIZON] 动态地平线锁定 y={self._dynamic_horizon}（{len(car_mids)}个远处小车的 y 中值）")
        return self._dynamic_horizon

    # ---------- 透视梯形车道 ----------

    def _lane_boundaries_at_y(self, y: int, h: int, w: int) -> dict:
        """返回 y 深度处的车道分界线 x 坐标（基于透视梯形测量点）"""
        horizon = self._dynamic_horizon or int(h * 0.445)
        center_x = w // 2
        if y <= horizon:
            return {"L2c": center_x, "L12": center_x, "LE": center_x,
                    "R2c": center_x, "R12": center_x, "RE": center_x}

        def bound(x_frac: float, y_frac: float) -> int:
            """从消失点(cx, horizon) 经测量点(x_frac*w, y_frac*h) 线性外推到 y"""
            meas_y = int(y_frac * h)
            if meas_y <= horizon:
                return center_x
            return int(center_x + (x_frac * w - center_x) * (y - horizon) / (meas_y - horizon))

        return {
            "LE": bound(0.00, 0.61),    # 左侧路缘
            "L12": bound(0.00, 0.75),   # 左1/左2 交界
            "L2c": bound(0.22, 1.00),   # 左2/中 交界
            "R2c": w - bound(0.22, 1.00),  # 中/右2 交界（对称）
            "R12": w - bound(0.00, 0.75),  # 右2/右1 交界
            "RE": w - bound(0.00, 0.61),   # 右侧路缘
        }

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

    # ---------- 黄色标线检测 ----------

    def _detect_lane(self, img_rgb: np.ndarray) -> dict | None:
        """检测道路两侧黄色标线（Hough 直线法：找 y>50% 区域最黄最直的线，断裂自动对齐）"""
        h, w = img_rgb.shape[:2]

        # 扫描下半部分：y=50%~80%，肯定在地平线以下，避开树冠/隧道墙干扰
        y1, y2 = int(h * 0.50), int(h * 0.80)
        roi = img_rgb[y1:y2, :]

        hsv = cv2.cvtColor(roi, cv2.COLOR_RGB2HSV)
        # 黄色范围：含阴影下的暗黄色（S/V 下限放宽）
        lower = np.array([20, 80, 80])
        upper = np.array([30, 255, 255])
        mask = cv2.inRange(hsv, lower, upper)

        # 形态学去噪
        kernel = np.ones((3, 3), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

        # Canny 边缘（提高阈值，只保留强边缘）
        edges = cv2.Canny(mask, 100, 200)

        # Hough 直线检测（提高阈值，只保留最突出的直线）
        lines = cv2.HoughLinesP(
            edges, rho=1, theta=np.pi / 180, threshold=60,
            minLineLength=40, maxLineGap=40,
        )

        self._lane_debug = {
            "left": ([], []), "right": ([], []),
            "zone": (0, y1, w, y2), "failed": "无标线",
        }

        if lines is None or len(lines) == 0:
            return None

        # 分类：左标线（角度 ≈150°） vs 右标线（角度 ≈30°）
        roi_w = w
        left_lines, right_lines = [], []
        pts = lines.reshape(-1, 4)
        for x1, y1_, x2, y2_ in pts:
            if x2 == x1:
                continue
            angle = np.degrees(np.arctan2(y2_ - y1_, x2 - x1)) % 180
            length = np.hypot(x2 - x1, y2_ - y1_)
            mid_x = (x1 + x2) / 2
            if 120 <= angle <= 165 and mid_x < roi_w * 0.50:
                left_lines.append((x1, y1_, x2, y2_, length, angle))
            elif 15 <= angle <= 60 and mid_x >= roi_w * 0.50:
                right_lines.append((x1, y1_, x2, y2_, length, angle))

        # ---- 单边选择：两侧中选更可靠的一侧 ----
        def side_score(lines):
            if not lines:
                return 0
            total_len = sum(l[4] for l in lines)
            if len(lines) >= 3:
                angles = [l[5] for l in lines]
                spread = max(angles) - min(angles)
                consistency = max(0, 1 - spread / 45)
            else:
                consistency = 1.0
            return total_len * consistency

        left_score = side_score(left_lines)
        right_score = side_score(right_lines)

        MIN_SCORE = 30
        if left_score < MIN_SCORE and right_score < MIN_SCORE:
            self._lane_debug["failed"] = "两侧标线太弱"
            return None

        if left_score >= right_score and left_score >= MIN_SCORE:
            best_lines = left_lines
            side = "left"
        else:
            best_lines = right_lines
            side = "right"

        # 延展到 ROI 边界 + 取 ROI 中点处 x
        top = sorted(best_lines, key=lambda l: l[4], reverse=True)[:3]
        roi_h = y2 - y1
        xs = []
        for x1, y1_, x2, y2_, length, _ in top:
            inv_slope = (x2 - x1) / (y2_ - y1_) if (y2_ - y1_) != 0 else 1e-6
            x_bottom = x1 + inv_slope * (roi_h - y1_)
            x_top = x1 - inv_slope * y1_
            xs.append((x_top, x_bottom, length))
        total_len = sum(l for _, _, l in xs) or 1
        avg_top = int(sum(xt * l for xt, _, l in xs) / total_len)
        avg_bot = int(sum(xb * l for _, xb, l in xs) / total_len)
        pos_at_mid = int(avg_top + (avg_bot - avg_top) / roi_h * (roi_h // 2))

        # 构建 debug 边缘散点（只选中那侧）
        debug_xs, debug_ys = [], []
        for x1, y1_, x2, y2_, *_ in best_lines:
            debug_xs.extend([x1, x2])
            debug_ys.extend([y1_ + y1, y2_ + y1])
        self._lane_debug = {
            side: (debug_xs, debug_ys),
            "zone": (0, y1, w, y2),
            "failed": None,
        }

        if self.frame_id % 90 == 0:
            logger.log(f"[LANE] 单边={side} pos={pos_at_mid} 线={len(best_lines)}条")
        return {"side": side, "pos": pos_at_mid}

    def _estimate_road_center(self, lane: dict | None, w: int) -> int:
        """从标线检测结果估算道路中线（单边检测 + 向中心修正50px）"""
        if lane is None:
            return w // 2
        side = lane.get("side")
        pos = lane.get("pos", w // 2)
        if side == "left":
            # 左标线 → 中线在右侧，向中心方向偏移50
            return (pos + w) // 2 - 50
        elif side == "right":
            # 右标线 → 中线在左侧，向中心方向偏移50
            return (0 + pos) // 2 + 50
        return w // 2

    # ---------- 工具函数 ----------

    @staticmethod
    def _calc_drift(hist: list) -> tuple[int, int, int]:
        """从位置历史计算漂移 d、加速度 dd、3帧累计 cum3

        Args:
            hist: 位置历史列表，至少 2 个元素

        Returns:
            (d, dd, cum3): 最近1帧变化、加速度、3帧累计变化
        """
        if len(hist) < 2:
            return 0, 0, 0

        d = hist[-1] - hist[-2]  # 最近 1 帧变化
        dd = 0
        if len(hist) >= 3:
            d_prev = hist[-2] - hist[-3]
            dd = d - d_prev  # 加速度：d - d_prev

        cum3 = 0
        if len(hist) >= 4:
            cum3 = hist[-1] - hist[-4]  # 3帧累计

        return d, dd, cum3

    # ---------- 全局路径规划 ----------

    def _wall_avoidance(self, lane: dict, w: int) -> tuple[int, int]:
        """
        单边标线防碰撞（择优选一侧，用 pos 判断墙壁接近度）
        返回 (zone, direction):
          zone=0 → 安全，无动作
          zone=1 → B区警戒：direction 反方向的决策被阻挡
          zone=2 → C区强制：必须往 direction 方向修正
        """
        side = lane["side"]
        pos = lane["pos"]

        # 更新防撞记忆
        if side == "left":
            if pos > 400:
                self._wall_memory = 1
            elif pos < 300:
                self._wall_memory = 0
        elif side == "right":
            if pos < 800:
                self._wall_memory = -1
            elif pos > 900:
                self._wall_memory = 0

        # 切换侧时清空历史
        if side != self._wall_side:
            self._wall_pos_history.clear()
            self._wall_side = side

        # 维护 5 帧历史
        self._wall_pos_history.append(pos)
        if len(self._wall_pos_history) > 5:
            self._wall_pos_history.pop(0)

        # ---- 左墙检查 ----
        if side == "left" and pos > 350 and len(self._wall_pos_history) >= 2:
            d, dd, cum3 = self._calc_drift(self._wall_pos_history)
            if pos > 450 and cum3 > 10:
                logger.log(f"[WALL] 左墙C区 pos={pos} cum3={cum3}，强制右转")
                return (2, 1)
            if dd > 5 and d > 0:
                return (1, 1)

        # ---- 右墙检查 ----
        if side == "right" and pos < 930 and len(self._wall_pos_history) >= 2:
            d, dd, cum3 = self._calc_drift(self._wall_pos_history)
            if pos < 830 and cum3 < -10:
                logger.log(f"[WALL] 右墙C区 pos={pos} cum3={cum3}，强制左转")
                return (2, -1)
            if dd < -5 and d < 0:
                return (1, -1)

        return (0, 0)

    def _lane_keep(self, lane: dict) -> int:
        """闭环车道保持：检测漂移趋势自适应调节力度，返回比例值 -32768~32767 或 0"""
        self._keep_cooldown = max(0, self._keep_cooldown - 1)
        pos = lane["pos"]

        # ── 超过 5 帧没激活或标线换侧，旧历史已失效 → 清空重来 ──
        last = getattr(self, "_keep_last_frame", 0)
        prev_side = getattr(self, "_keep_side", None)
        if self.frame_id - last > 5 or lane["side"] != prev_side:
            self._keep_hist.clear()
            self._keep_strength = 0.0
            self._keep_dir = 0
        self._keep_last_frame = self.frame_id
        self._keep_side = lane["side"]

        self._keep_hist.append(pos)
        if len(self._keep_hist) > 30:
            self._keep_hist.pop(0)

        if len(self._keep_hist) < 6:
            return 0

        d, dd, cum3 = self._calc_drift(self._keep_hist)
        # cum3 用于判断漂移幅度，d 用于判断收敛速度
        # 方向（朝漂移反方向）
        # 标线往右移(cum3>0)→车往左漂→右修(1)；标线往左移(cum3<0)→车往右漂→左修(-1)
        # 左右标线侧逻辑相同，因为两条标线在画面上同向移动
        new_dir = 1 if cum3 > 0 else -1

        # ── 判断逻辑 ──
        # 位置变化率检测：最近 1 帧变化 <5px 视为已停，提前结束
        if self._keep_strength > 0 and abs(d) < 5:
            self._keep_strength = max(0, self._keep_strength - 0.3)
            if self._keep_strength < 0.01:
                self._keep_cooldown = 8
                return 0

        # 只在转向过大时修正：阈值从15提高到30
        elif abs(cum3) >= 30:
            # 漂移超过阈值 → 激活/升级
            if self._keep_strength < 0.01:
                self._keep_strength = 0.5   # 首次激活 50%
                self._keep_dir = new_dir
            elif new_dir != self._keep_dir:
                # 方向反了 → 过冲了，快速收敛→ 升档
                self._keep_strength = min(1.0, self._keep_strength + 0.25)
                self._keep_dir = new_dir
            elif dd > 0:
                # 漂移仍在加速 → 升档
                self._keep_strength = min(1.0, self._keep_strength + 0.25)
            elif dd > -2:
                # 慢速收敛 → 维持
                pass
            else:
                # 快速收敛 → 减档
                self._keep_strength = max(0.5, self._keep_strength - 0.1)
        elif self._keep_strength > 0 and abs(cum3) < 25:
            # 漂移已收敛 → 快速降低力度
            self._keep_strength = max(0, self._keep_strength - 0.3)
            if self._keep_strength < 0.01:
                self._keep_cooldown = 8  # 完全关闭，冷却约 0.5 秒
                return 0
        else:
            # 阈值之间（25~30），保持当前力度不调
            pass

        if self._keep_strength < 0.01:
            self._keep_strength = 0.0
            return 0

        return int(self._keep_dir * self._keep_strength * 32767)

    def _aim_at(self, target: tuple, w: int, h: int, lane: dict | None = None) -> int:
        """前馈瞄准：根据目标大小预测提前停止，减少转向过度"""
        cx, cy, bw, bh = target[0], target[1], target[2], target[3]
        bottom_y = cy + bh // 2  # 框底部中心
        center_x = w // 2
        offset = (cx - center_x) / (w / 2)

        # ── 基础数据 ──
        area = bw * bh
        frame_area = w * h
        area_ratio = area / frame_area
        zone = self._get_zone(bottom_y, 0)
        zone_label = self._ZONE_LABELS[zone]

        # ── 中心区检查（类似 _avoid 的反向逻辑）──
        # 用透视分界线判断目标是否在中心区（L2c~R2c）
        # 如果目标偏离中心区，即使 offset 小也要转向修正
        b = self._lane_boundaries_at_y(bottom_y, h, w)
        quarter = bw * 0.125  # 两侧各裁掉 12.5%（75%宽度，居中对齐）
        left = cx - bw // 2 + quarter
        right = cx + bw // 2 - quarter
        in_center = left < b["R2c"] and right > b["L2c"]  # 目标75%宽度在中心区

        # ── 前馈计算 ──
        # 1. 动态停止区：目标越大（越近），停止区越大
        #    基础 1% + 面积补偿（area_ratio * 30，限幅 0.01~0.11）
        #    area_ratio=0.001 → stop=0.04 (4%)
        #    area_ratio=0.01  → stop=0.11 (11%，限幅)
        stop_zone = 0.01 + min(0.10, area_ratio * 30)

        # 2. 目标移动速度（dx/dy per frame）
        dx = 0.0
        dy = 0.0
        frames_since_last = self.frame_id - self._prev_aim_frame
        if self._prev_aim_frame > 0 and frames_since_last > 0 and frames_since_last < 5:
            dx = (cx - self._prev_aim_cx) / frames_since_last
            dy = (cy - self._prev_aim_cy) / frames_since_last

        # 3. 更新历史
        self._prev_aim_cx = cx
        self._prev_aim_cy = cy
        self._prev_aim_frame = self.frame_id

        # 4. 判断目标是否正在向中心移动
        #    offset > 0 且 dx < 0 = 目标从右向左移向中心
        #    offset < 0 且 dx > 0 = 目标从左向右移向中心
        moving_to_center = (offset > 0 and dx < -0.5) or (offset < 0 and dx > 0.5)

        # 5. 前馈决策
        feedforward_stop = False
        if abs(offset) < stop_zone and moving_to_center:
            feedforward_stop = True

        # 6. 偏离中心区检查：目标不在中心区时，强制转向
        off_center = not in_center

        # ── 计算力度（用于日志）──
        if feedforward_stop:
            strength = 0
            reason = "前馈停止"
        elif off_center:
            # 目标偏离中心区，强制转向修正
            if zone == 2:
                strength = 0.5  # 近区降低力度，防撞
            elif zone == 1:
                strength = 1.0  # 中区满力
            else:
                strength = 0.5  # 远区半力
            reason = "偏离中心"
        elif abs(offset) < 0.06:
            strength = 0
            reason = "死区"
        elif zone == 2:
            strength = 0
            reason = "近区"
        elif zone == 1:
            strength = 1.0
            reason = "中区满力"
        else:
            strength = 0.5
            reason = "远区半力"

        # ── 诊断日志 ──
        logger.log(
            f"[AIM] target=({cx:.0f},{cy:.0f}) size={bw:.0f}×{bh:.0f} "
            f"area={area:.0f} ratio={area_ratio:.4f} "
            f"offset={offset:+.3f} stop={stop_zone:.3f} "
            f"dx={dx:+.1f} moving={moving_to_center} "
            f"in_center={in_center} off_center={off_center} "
            f"zone={zone_label} strength={strength:.2f} "
            f"reason={reason}",
            "DEBUG"
        )

        # 存储前馈信息供 debug 显示
        self._aim_debug = {
            "offset": offset,
            "stop_zone": stop_zone,
            "dx": dx,
            "moving": moving_to_center,
            "area_ratio": area_ratio,
            "ff_reason": reason,
            "in_center": in_center,
        }

        # ── 执行 ──
        if feedforward_stop:
            return 0

        if abs(offset) < 0.06:
            return 0

        sign = 1 if offset > 0 else -1

        # 近区：来不及了
        if zone == 2:
            return 0

        # 中区：中间不追，偏左/右全力转
        if zone == 1:
            return int(sign * 32767)

        # 远区：中间不追，偏左/右 50% 轻柔对准
        return int(sign * 0.50 * 32767)

    def _avoid(self, cars: list, w: int, h: int) -> int:
        """目标落在行驶方向中心区（L2c~R2c）则满躲，否则不管"""
        DANGER_Y = h * 0.30
        threats = [c for c in cars if c[1] > DANGER_Y]
        if not threats:
            return 0

        threat = max(threats, key=lambda c: c[1])
        tx, ty = threat[0], threat[1]
        tw, th = threat[2], threat[3]
        bottom_y = ty + th // 2  # 框底部中心

        # 记录区域，远区用50%力度
        zone = self._get_zone(ty, th)

        # 用透视分界线判断框下边线是否进入行驶方向（下边框 75% 长度，居中对齐）
        b = self._lane_boundaries_at_y(bottom_y, h, w)
        quarter = tw * 0.125  # 两侧各裁掉 12.5%
        left = tx - tw // 2 + quarter
        right = tx + tw // 2 - quarter
        in_path = left < b["R2c"] and right > b["L2c"]
        if not in_path:
            return 0

        # 同深度其他车阻挡检查
        def occupied(x1, x2) -> bool:
            return any(
                x1 < c[0] < x2 and abs(c[1] - ty) < h * 0.15
                for c in threats if c is not threat
            )

        right_ok = not occupied(b["R2c"], b["R12"])
        left_ok = not occupied(b["L12"], b["L2c"])

        # 力度：远区50%，中近区100%
        strength = 16383 if zone == 0 else 32767

        # 根据障碍物在主车道内的位置决定优先方向：偏右→左躲，偏左→右躲
        mid_lane = (b["L2c"] + b["R2c"]) / 2
        if tx > mid_lane:
            # 障碍物偏右，优先左躲
            if left_ok:
                return -strength
            if right_ok:
                return strength
        else:
            # 障碍物偏左，优先右躲
            if right_ok:
                return strength
            if left_ok:
                return -strength
        return 0

    def _decide(self, coins: list, cars: list, bonus_cars: list,
                lane: dict | None, w: int, h: int,
                wall_zone: int = 0, wall_dir: int = 0) -> tuple[int, str, str]:
        """
        全局决策，返回 (direction, reason, detail)
        wall_zone/wall_dir: 由外部预计算的防撞状态（避免重复调用 _wall_avoidance）

        优先级（贪婪模式，撞车无惩罚）：
        1. 金币+奖励车合并决策（面积优先，面积近时选离中线近的）
        2. C区防撞（强制）
        3. 障碍车避让（只在真要撞时才躲）
        4. 无目标
        """
        # ========== 1. 金币+奖励车合并决策 ==========
        # 收集所有奖励类目标（金币+跳板车），比较面积和位置
        reward_targets = []

        # 跳板车
        for b in bonus_cars:
            area = b[2] * b[3]
            reward_targets.append((*b, area, "跳板车"))

        # 金币
        for c in coins:
            area = c[2] * c[3]
            reward_targets.append((*c, area, "金币"))

        if reward_targets:
            # 选择最优目标：面积优先，面积近时选离X轴中线近的
            def target_score(t):
                cx, cy, bw, bh, area, t_type = t[0], t[1], t[2], t[3], t[4], t[5]
                # 面积越大越好（主要权重）
                # 离X轴中线越近越好（次要权重，面积差距小时生效）
                center_dist = abs(cx - w / 2)
                # 综合评分：面积 - 中线距离惩罚
                # 面积权重高，中线距离作为tie-breaker
                return area - center_dist * 0.1

            target = max(reward_targets, key=target_score)
            cx, cy, bw, bh, area, t_type = target[0], target[1], target[2], target[3], target[4], target[5]

            aim = self._aim_at(target, w, h, lane)

            # B区：往墙方向则取消
            if wall_zone == 1 and ((aim < 0 and wall_dir == 1) or (aim > 0 and wall_dir == -1)):
                b_cls = "左" if wall_dir == 1 else "右"
                return 0, "防撞", f"B区 阻挡往{b_cls}（{t_type}被拦）"

            if aim != 0 and self._coin_turn_log_count < 5:
                l_info = f"side={lane['side']} pos={lane['pos']}" if lane else "None"
                logger.log(f"[DECIDE] w={w} h={h} {t_type}=({cx:.0f},{cy:.0f}) "
                           f"aim={aim} lane={l_info}", "DEBUG")
                self._coin_turn_log_count += 1

            d_cls = "直冲" if aim == 0 else ("左转" if aim < 0 else "右转")
            zone = self._ZONE_LABELS[self._get_zone(cy, 0)]
            return aim, t_type, f"{zone} {d_cls} area={area:.0f}"

        # ========== 2. C区防撞（强制） ==========
        if wall_zone == 2:
            d_cls = "左" if wall_dir == -1 else "右"
            return wall_dir, "防撞", f"C区 {d_cls}转 强制"

        # ========== 3. 障碍车避让 ==========
        DANGER_Y = h * 0.35
        near_cars = [c for c in cars if c[1] > DANGER_Y]
        if near_cars:
            aim = self._avoid(near_cars, w, h)
            # 只有障碍物在行驶方向内才占用决策，否则穿透到金币逻辑
            if aim != 0:
                # B 区检查：如果避障方向与墙方向相反，尝试反方向躲避
                if wall_zone == 1 and ((aim < 0 and wall_dir == 1) or (aim > 0 and wall_dir == -1)):
                    # 尝试反方向躲避（如果可行）
                    reverse_aim = -aim
                    # 检查反方向是否可行（重新调用 _avoid 逻辑）
                    # 简化处理：直接使用反方向，让后续逻辑判断是否撞墙
                    self._last_dodge_dir = reverse_aim
                    self._last_dodge_frame = self.frame_id
                    d_cls = "左躲" if reverse_aim < 0 else "右躲"
                    return reverse_aim, "避障", f"{d_cls}（B区反向）"
                self._last_dodge_dir = aim
                self._last_dodge_frame = self.frame_id
                d_cls = "左躲" if aim < 0 else "右躲"
                return aim, "避障", d_cls
            # aim == 0 → 障碍物不在行驶方向，不占用决策，落到后面的金币逻辑

        # ========== 4. 无目标 ==========
        if lane is None and self._wall_memory != 0:
            direction = self._wall_memory
            cls = "右" if direction == 1 else "左"
            if self.frame_id % 10 == 0:
                logger.log(f"[WALL] 标线丢失，记忆回带{cls}转(mem={self._wall_memory})")
            return direction, "回带", f"标线丢失 {cls}带回(mem={self._wall_memory})"
        # 有标线时车道保持
        if self.frame_id % 15 == 0:
            logger.log("[YOLO] 无目标，直行")
        return 0, "直行", "无目标 直行"

    def _run_impl(self, ctrl) -> bool:
        """赛车控制核心逻辑（被 run / run_direct 共用）"""
        if self._record_mode:
            logger.log("📹 记录模式启动（不拦截手柄，记录人工操作数据）")
        else:
            logger.log("赛车控制启动")
        self._running = True
        self.frame_id = 0  # 重试时重置帧计数
        self._lane_debug = None  # 重置标线中间数据
        self._dynamic_horizon = None  # 重置动态地平线
        self._c_burst = 0
        self._c_coast = 0
        if self.debug is not None:
            self.debug.start_session("racing")

        # 记录模式：准备数据文件
        record_file = None
        if self._record_mode:
            log_dir = Path(__file__).parent.parent / "logs"
            log_dir.mkdir(exist_ok=True)
            record_path = log_dir / f"record_{time.strftime('%Y%m%d_%H%M%S')}.csv"
            record_file = open(record_path, "w", encoding="utf-8")
            record_file.write("frame,time_ms,lx,ly,rt,")
            record_file.write("target_cx,target_cy,target_w,target_h,target_area,area_ratio,offset,zone,")
            record_file.write("lane_side,lane_pos,wall_zone,wall_dir,")
            record_file.write("coin_count,car_count,bonus_count,reason,detail\n")
            logger.log(f"记录数据写入: {record_path}")
        else:
            self._create_pad()

        # ── 常量 ──
        YOLO_INTERVAL = 2          # 每 2 游戏帧做一次 YOLO 推理
        SLOW_CHECK = 15            # 每秒（~15fps）检一次商店/结束

        # 起步：按住 RT 加速（游戏内部有倒计时，车不会立即动）
        if not self._record_mode:
            assert self.gpad is not None, "手柄未创建"
            self._apply_trigger(255)

        try:
            while self._running:
                t0 = time.time()
                img = self._cap(ctrl)
                if img is None:
                    time.sleep(0.05)
                    continue

                self.frame_id += 1
                h, w = img.shape[:2]

                # ── 黄色标线检测（每帧，开销极低） ──
                lane = self._detect_lane(img)

                # ── 1Hz 检测：本轮结束（商店弹窗 / 回合1结束画面） ──
                if self.frame_id % SLOW_CHECK == 0 and self._is_end(img):
                    self._steer(0)
                    return True

                # ── 跳帧 YOLO 推理 ──
                if self.frame_id % YOLO_INTERVAL == 0:
                    t_yolo = time.time()
                    coins, cars, bonus_cars, yolo_debug, all_raw = self.det(img, roi=self.ROI)
                    yolo_ms = (time.time() - t_yolo) * 1000
                    if self.frame_id % 30 == 0:
                        logger.log(f"[YOLO] 推理耗时 {yolo_ms:.0f}ms")
                    self._cached_coins = coins
                    self._cached_cars = cars
                    self._cached_bonus = bonus_cars
                    self._cached_yolo_debug = yolo_debug
                    self._cached_all_raw = all_raw
                coins = self._cached_coins
                cars = self._cached_cars
                bonus_cars = self._cached_bonus
                yolo_debug = self._cached_yolo_debug
                all_raw = self._cached_all_raw

                # 动态地平线推断（仅首次成功）
                self._detect_horizon(all_raw, h, w)

                # ── 防碰撞检查（独立于冷却，始终执行） ──
                wall_zone, wall_dir = 0, 0
                if lane is not None:
                    wall_zone, wall_dir = self._wall_avoidance(lane, w)
                elif self._wall_memory != 0:
                    wall_zone = 2
                    wall_dir = self._wall_memory

                direction, reason, detail = self._decide(
                    coins, cars, bonus_cars, lane, w, h,
                    wall_zone, wall_dir)

                # ── 闭环车道保持：仅直行/回带时按漂移趋势自适应调节 ──
                if (direction == 0 and lane is not None
                        and reason in ("直行", "回带")):
                    keep_val = self._lane_keep(lane)
                    if keep_val != 0:
                        direction = keep_val
                        k_cls = "左" if keep_val < 0 else "右"
                        pct = int(self._keep_strength * 100)
                        detail = f"车道保持 {k_cls}修({pct}%)"

                # ── C 区突发修正 + 强制归中（反打思维） ──
                # 突发：短促打满改变车头指向 → 归中滑行让车远离墙 → 重评估
                actual_dir = direction  # 实际执行的转向
                if self._c_burst > 0:
                    # 突发中：持续打 burst_dir
                    actual_dir = self._c_burst_dir
                    self._c_burst -= 1
                    if self._c_burst == 0:
                        self._c_coast = 5  # 突发结束 → 强制归中滑行
                        logger.log("[WALL] 突发结束，归中滑行 5 帧")
                elif self._c_coast > 0:
                    actual_dir = 0
                    self._c_coast -= 1
                elif reason == "防撞" and direction != 0:
                    # 触发新的突发修正（中断归中）
                    self._c_coast = 0
                    self._c_burst = 2
                    self._c_burst_dir = direction
                    actual_dir = direction
                    self._c_burst -= 1
                    cls = "左" if direction == -1 else "右"
                    logger.log(f"[WALL] 突发修正{cls}转×2帧")

                # ── 记录模式：读取物理手柄 + 写入数据 ──
                if self._record_mode:
                    lx, ly, rt = _read_physical_xinput()
                    # 找到当前瞄准的目标（如果有）
                    aim_target = None
                    if bonus_cars:
                        aim_target = max(bonus_cars, key=lambda b: b[1])
                    elif coins:
                        aim_target = max(coins, key=lambda c: c[1])
                    if aim_target:
                        tcx, tcy, tw, th = aim_target[0], aim_target[1], aim_target[2], aim_target[3]
                        t_area = tw * th
                        t_ratio = t_area / (w * h)
                        t_offset = (tcx - w // 2) / (w / 2)
                        t_zone = self._ZONE_LABELS[self._get_zone(tcy, th)]
                    else:
                        tcx, tcy, tw, th, t_area, t_ratio, t_offset, t_zone = 0, 0, 0, 0, 0, 0, 0, "无"
                    elapsed_ms = int((time.time() - t0) * 1000)
                    lane_side = lane["side"] if lane else ""
                    lane_pos = lane["pos"] if lane else 0
                    assert record_file is not None
                    record_file.write(
                        f"{self.frame_id},{elapsed_ms},{lx},{ly},{rt},"
                        f"{tcx:.0f},{tcy:.0f},{tw:.0f},{th:.0f},{t_area:.0f},{t_ratio:.4f},{t_offset:+.3f},{t_zone},"
                        f"{lane_side},{lane_pos},{wall_zone},{wall_dir},"
                        f"{len(coins)},{len(cars)},{len(bonus_cars)},{reason},{detail}\n"
                    )
                    if self.frame_id % 30 == 0:
                        record_file.flush()
                    # 记录模式不执行转向，继续下一帧
                    continue

                # ── 转向控制（直接使用原生数据，无平滑） ──
                steer_val = int(actual_dir * 32767)
                if steer_val != self.last_dir:
                    self._steer(steer_val)
                    self.last_dir = steer_val

                # ── 决策日志（DEBUG 级别，仅 yolo 推理帧） ──
                if self.frame_id % YOLO_INTERVAL == 0:
                    dir_label = "左" if direction < 0 else "右" if direction > 0 else "直"
                    lane_info = f"{lane['side']}@{lane['pos']}" if lane else "无标线"
                    # RAW 统计（诊断过滤原因）
                    raw_cars = [d for d in all_raw if d["class_name"] == "car"] if all_raw else []
                    car_raw_info = f"raw={len(raw_cars)}" + (f"@{max(d['confidence'] for d in raw_cars):.2f}" if raw_cars else "")
                    if cars:
                        nearest = max(cars, key=lambda c: c[1])
                        cz = self._ZONE_LABELS[self._get_zone(nearest[1], nearest[3])]
                        car_info = f"car={len(cars)}({cz},{car_raw_info})"
                        # 框位置：(cx,cy,w×h)，最多显示 4 个
                        car_boxes = ",".join(f"({c[0]},{c[1]},{c[2]}×{c[3]})" for c in cars[:4])
                        car_info += f" [{car_boxes}]"
                    else:
                        car_info = f"car=0({car_raw_info})"
                    # 金币框位置
                    coin_info = f"coin={len(coins)}"
                    if coins:
                        coin_boxes = ",".join(f"({c[0]},{c[1]},{c[2]}×{c[3]})" for c in coins[:3])
                        coin_info += f" [{coin_boxes}]"
                    bonus_info = f"bonus={len(bonus_cars)}"
                    if bonus_cars:
                        bonus_boxes = ",".join(f"({b[0]},{b[1]},{b[2]}×{b[3]})" for b in bonus_cars[:2])
                        bonus_info += f" [{bonus_boxes}]"
                    logger.log(f"[DECIDE] #{self.frame_id} {reason} {detail} | "
                               f"标线={lane_info} | {car_info} | {coin_info} {bonus_info} | "
                               f"dir={dir_label}", "DEBUG")

                # ── 调试帧 ──
                if self.debug is not None and (self.debug.enabled or self.debug.peep_enabled):
                    save_disk = self.debug.enabled
                    dir_char = 'L' if actual_dir == -1 else 'S' if actual_dir == 0 else 'R'
                    racing_info = {
                        "direction": actual_dir,
                        "stick": actual_dir if abs(actual_dir) > 1 else actual_dir * 32767,
                        "reason": reason,
                        "detail": detail,
                        "lane": lane,
                        "n_coins": len(coins),
                        "n_cars": len(cars),
                        "n_bonus": len(bonus_cars),
                        "frame_id": self.frame_id,
                        "zone_lines": self._zone_boundaries,
                        "throttle": 255,
                        "horizon_locked": self._dynamic_horizon is not None,
                        "wall_zone": wall_zone,
                        "keep_strength": self._keep_strength,
                        "aim_debug": self._aim_debug,  # 前馈调试信息
                    }
                    # 把标线检测中间数据（扫描区域、边缘点）合并到 lane 供 debug 可视化
                    lane_vis = self._lane_debug  # debug 数据含 zone/edges/failed
                    if lane:
                        center = self._estimate_road_center(lane, w)
                        lane_vis = {**lane, "center": center, "_debug": self._lane_debug} if self._lane_debug else {**lane, "center": center}
                    self.debug.save_frame(
                        img, detections=yolo_debug, lane=lane_vis,
                        label=f"race_f{self.frame_id}_d{dir_char}",
                        save_to_disk=save_disk,
                        racing_info=racing_info,
                        all_raw_dets=all_raw,
                    )

                elapsed = time.time() - t0
                sleep = max(0, 1 / 15 - elapsed)
                if sleep:
                    time.sleep(sleep)
        finally:
            if self._record_mode and record_file is not None:
                record_file.close()
                logger.log(f"记录文件已保存: {record_path}")
            if not self._record_mode:
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
