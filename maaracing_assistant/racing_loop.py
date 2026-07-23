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
    # 路面 ROI（裁剪掉顶部分数条和底部仪表盘，让 YOLO 专注路面）
    # 1280×720 下 y=28%~78% → (0, 201, 1280, 561)
    ROI = (0, 201, 1280, 561)

    def __init__(self, model_path: str, debug=None):
        super().__init__()
        self.det = YOLODetector(model_path)
        self.debug = debug
        self.gpad = None
        self.last_dir = 0
        self.frame_id = 0
        self._running = True
        self._end_reason = ""  # 最近一次 _is_end 匹配的结果原因
        self._coin_turn_log_count = 0  # 金币转向诊断计数
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
        self._current_throttle = 255  # 当前油门深度（动态调节）
        self._dynamic_horizon = None  # 从 YOLO 推断的地平线，首次检测到后锁死当整局
        self._keep_hist: list[int] = []  # 车道保持位置历史
        self._keep_strength: float = 0.0   # 车道保持当前力度 (0~1)
        self._keep_dir: int = 0            # 车道保持当前方向 (-1/0/1)
        self._keep_cooldown: int = 0       # 车道保持冷却帧数
        self._prev_reason: str = ""        # 上一帧的决策原因（检测策略切换）
        self._last_dodge_dir: int = 0       # 上次避障方向（防抖迟滞用）
        self._last_dodge_frame: int = 0      # 上次避障帧号
        self._c_burst = 0  # C区突发修正剩余帧数
        self._c_burst_dir = 0
        self._c_coast = 0  # 突发后强制归中滑行剩余帧数
        self._force_init_count = None  # force_init 观察帧计数
        self._steer_smoothed = 0.0  # 转向平滑值（指数平滑，消除镜头延迟抖动）
        self._steer_alpha = 0.6    # 平滑系数（由校准动态设置）

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
        self._current_throttle = 255
        self._dynamic_horizon = None
        self._wall_pos_history.clear()
        self._wall_side = None
        self._keep_hist.clear()
        self._keep_strength = 0.0
        self._keep_dir = 0
        self._keep_cooldown = 0
        self._prev_reason = ""

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

    def _calc_throttle(self, reason: str, direction: int) -> int:
        """根据路况动态调整油门深度（转向收油换机动性，空旷全油冲）"""
        if reason == "防撞" and direction != 0:
            return 120   # C 区紧急修正，减速换最大转向力
        if reason == "避障" and direction != 0:
            return 180   # 避让障碍车，适度减速
        if reason in ("金币", "跳板车") and direction != 0:
            return 200   # 转向吃分，稍收油便于精确定位
        if reason == "冷却":
            return 180   # 镜头延迟期间适度减速，避免跑太远
        return 255       # 直行/空旷路面 = 全油门

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
            d = self._wall_pos_history[-1] - self._wall_pos_history[-2]
            dd = 0
            if len(self._wall_pos_history) >= 3:
                d_prev = self._wall_pos_history[-2] - self._wall_pos_history[-3]
                dd = d - d_prev
            # 累计位移：3帧内移动>10px 才判定为真实接近（过滤噪声）
            cum3 = 0
            if len(self._wall_pos_history) >= 4:
                cum3 = self._wall_pos_history[-1] - self._wall_pos_history[-4]
            if pos > 450 and cum3 > 10:
                logger.log(f"[WALL] 左墙C区 pos={pos} cum3={cum3}，强制右转")
                return (2, 1)
            if dd > 5 and d > 0:
                return (1, 1)

        # ---- 右墙检查 ----
        if side == "right" and pos < 930 and len(self._wall_pos_history) >= 2:
            d = self._wall_pos_history[-1] - self._wall_pos_history[-2]
            dd = 0
            if len(self._wall_pos_history) >= 3:
                d_prev = self._wall_pos_history[-2] - self._wall_pos_history[-3]
                dd = d - d_prev
            # 累计位移：3帧内移动>10px 才判定为真实接近（过滤噪声）
            cum3 = 0
            if len(self._wall_pos_history) >= 4:
                cum3 = self._wall_pos_history[-1] - self._wall_pos_history[-4]
            if pos < 830 and cum3 < -10:
                logger.log(f"[WALL] 右墙C区 pos={pos} cum3={cum3}，强制左转")
                return (2, -1)
            if dd < -5 and d < 0:
                return (1, -1)

        return (0, 0)

    def _lane_keep(self, lane: dict, force_init: bool = False) -> int:
        """闭环车道保持：检测漂移趋势自适应调节力度，返回比例值 -32768~32767 或 0

        force_init=True: 刚切回直行时用标线偏移直接估算力度，跳过历史积累
        """
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

        # ── 强制初始化：从避障/奖励切回直行时，观察两帧再修正 ──
        if force_init:
            self._keep_hist.clear()
            self._keep_strength = 0.0
            self._keep_dir = 0
            self._force_init_count = 0

        self._keep_hist.append(pos)
        if len(self._keep_hist) > 30:
            self._keep_hist.pop(0)

        # force_init 模式：前两帧观察，第三帧开始修正
        if hasattr(self, '_force_init_count') and self._force_init_count is not None:
            self._force_init_count += 1
            if self._force_init_count < 3:
                return 0
            if len(self._keep_hist) >= 2:
                diff = self._keep_hist[-1] - self._keep_hist[-2]
                if abs(diff) >= 8:
                    self._keep_dir = 1 if diff > 0 else -1
                    self._keep_strength = 0.5
                    self._force_init_count = None
                    return int(self._keep_dir * self._keep_strength * 32767)
            self._force_init_count = None

        if len(self._keep_hist) < 6:
            return 0

        diff = self._keep_hist[-1] - self._keep_hist[-4]  # 3帧跨度漂移
        prev_diff = self._keep_hist[-2] - self._keep_hist[-5]
        dd = abs(diff) - abs(prev_diff)  # >0 = 漂移加速, <0 = 减速

        # 方向（朝漂移反方向）
        # 标线往右移(diff>0)→车往左漂→右修(1)；标线往左移(diff<0)→车往右漂→左修(-1)
        # 左右标线侧逻辑相同，因为两条标线在画面上同向移动
        new_dir = 1 if diff > 0 else -1

        # ── 判断逻辑 ──
        if abs(diff) >= 15:
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
        elif self._keep_strength > 0 and abs(diff) < 12:
            # 漂移已收敛 → 快速降低力度
            self._keep_strength = max(0, self._keep_strength - 0.3)
            if self._keep_strength < 0.01:
                self._keep_cooldown = 8  # 完全关闭，冷却约 0.5 秒
                return 0
        else:
            # 阈值之间（8~14），保持当前力度不调
            pass

        if self._keep_strength < 0.01:
            self._keep_strength = 0.0
            return 0

        return int(self._keep_dir * self._keep_strength * 32767)

    def _aim_at(self, target: tuple, w: int, h: int, lane: dict | None = None) -> int:
        """三区变力度瞄准：远50%/中100%/近0%，水平居中时不转"""
        cx, cy, bw, bh = target[0], target[1], target[2], target[3]
        bottom_y = cy + bh // 2  # 框底部中心
        center_x = w // 2
        offset = (cx - center_x) / (w / 2)

        if abs(offset) < 0.06:
            return 0

        sign = 1 if offset > 0 else -1
        zone = self._get_zone(bottom_y, 0)

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

        # 用透视分界线判断框下边线是否进入行驶方向（框左沿 < R2c 且框右沿 > L2c）
        b = self._lane_boundaries_at_y(bottom_y, h, w)
        left = tx - tw // 2
        right = tx + tw // 2
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
        """
        # C区：无条件强制
        if wall_zone == 2:
            d_cls = "左" if wall_dir == -1 else "右"
            return wall_dir, "防撞", f"C区 {d_cls}转 强制"

        # ========== 1. 跳板车 ==========
        if bonus_cars:
            target = max(bonus_cars, key=lambda b: b[1])
            aim = self._aim_at(target, w, h, lane)
            # B区：往墙方向则取消
            if wall_zone == 1 and ((aim < 0 and wall_dir == 1) or (aim > 0 and wall_dir == -1)):
                b_cls = "左" if wall_dir == 1 else "右"
                return 0, "防撞", f"B区 阻挡往{b_cls}（跳板车被拦）"
            d_cls = "直冲" if aim == 0 else ("左转" if aim < 0 else "右转")
            return aim, "跳板车", f"目标({target[0]},{target[1]}) {d_cls}"

        # ========== 2. 障碍车避让 ==========
        DANGER_Y = h * 0.35
        near_cars = [c for c in cars if c[1] > DANGER_Y]
        if near_cars:
            aim = self._avoid(near_cars, w, h)
            # 只有障碍物在行驶方向内才占用决策，否则穿透到金币逻辑
            if aim != 0:
                self._last_dodge_dir = aim
                self._last_dodge_frame = self.frame_id
                if wall_zone == 1 and ((aim < 0 and wall_dir == 1) or (aim > 0 and wall_dir == -1)):
                    b_cls = "左" if wall_dir == 1 else "右"
                    return 0, "防撞", f"B区 阻挡往{b_cls}（避障被拦）"
                d_cls = "左躲" if aim < 0 else "右躲"
                return aim, "避障", d_cls
            # aim == 0 → 障碍物不在行驶方向，不占用决策，落到后面的金币逻辑

        # ========== 3. 金币 ==========
        if coins:
            def coin_value(c):
                cx, cy, bw, bh = c[0], c[1], c[2], c[3]
                nearby = sum(1 for o in coins
                            if abs(o[0] - cx) < w * 0.2
                            and 0 < cy - o[1] < h * 0.3)
                zone = self._get_zone(cy, bh)
                zone_bonus = {0: 0, 1: 100, 2: 200}.get(zone, 0)
                value = cy + nearby * 50 + zone_bonus
                # 车道惩罚：用透视判断金币在不在本车道，不在则扣分
                if lane:
                    b = self._lane_boundaries_at_y(cy, h, w)
                    in_center = cx > b["L2c"] and cx < b["R2c"]
                    in_left = cx <= b["L2c"]
                    in_right = cx >= b["R2c"]
                    ls, lp = lane["side"], lane["pos"]
                    # 左墙方向+金币在左车道 → 扣分
                    if ls == "left" and in_left and lp > 350:
                        value -= (lp - 350) * 2
                    # 右墙方向+金币在右车道 → 扣分
                    if ls == "right" and in_right and lp < 930:
                        value -= (930 - lp) * 2
                return value
            target = max(coins, key=coin_value)
            aim = self._aim_at(target, w, h, lane)
            if aim != 0 and self._coin_turn_log_count < 5:
                l_info = f"side={lane['side']} pos={lane['pos']}" if lane else "None"
                logger.log(f"[DECIDE] w={w} h={h} coin=({target[0]:.0f},{target[1]:.0f}) "
                           f"aim={aim} lane={l_info}", "DEBUG")
                self._coin_turn_log_count += 1
            if aim != 0:
                cls = "左" if aim < 0 else "右"
                logger.log(f"[YOLO] 金币({target[0]:.0f})，{cls}转")
            if wall_zone == 1 and ((aim < 0 and wall_dir == 1) or (aim > 0 and wall_dir == -1)):
                b_cls = "左" if wall_dir == 1 else "右"
                return 0, "防撞", f"B区 阻挡往{b_cls}（金币被拦）"
            zone = self._ZONE_LABELS[self._get_zone(target[1], 0)]
            d_cls = "直行" if aim == 0 else ("左转" if aim < 0 else "右转")
            return aim, "金币", f"{zone} {d_cls}"

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

    def _cal_detect_lane_pos(self, img) -> int | None:
        """校准用：从截图中检测标线位置，返回 x 坐标或 None"""
        h, w = img.shape[:2]
        roi_y1, roi_y2 = int(h * 0.50), int(h * 0.80)
        roi = img[roi_y1:roi_y2, :]
        hsv = cv2.cvtColor(roi, cv2.COLOR_RGB2HSV)
        mask = cv2.inRange(hsv, np.array([20, 80, 80]), np.array([30, 255, 255]))
        kernel = np.ones((3, 3), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        edges = cv2.Canny(mask, 100, 200)
        lines = cv2.HoughLinesP(edges, 1, np.pi / 180, 60, minLineLength=40, maxLineGap=40)
        if lines is None:
            return None
        for x1, y1_, x2, y2_ in lines.reshape(-1, 4):
            if x2 == x1:
                continue
            angle = np.degrees(np.arctan2(y2_ - y1_, x2 - x1)) % 180
            mid_x = (x1 + x2) / 2
            if 120 <= angle <= 165 and mid_x < w * 0.50:
                return int((x1 + x2) / 2)
            elif 15 <= angle <= 60 and mid_x >= w * 0.50:
                return int((x1 + x2) / 2)
        return None

    def _cal_init(self) -> None:
        """初始化校准状态（嵌入主循环，每帧执行一步）"""
        self._cal_phase = "init"
        self._cal_step = 0
        self._cal_positions = []
        self._cal_stable = 0
        self._cal_settles = []
        self._cal_seq = []
        self._cal_seq_idx = 0
        self._cal_steer_frames = 8
        self._cal_retries = 0
        self._cal_pos_start = None

    def _calibrate_step(self, img, ctrl) -> bool:
        """每帧执行一步校准。返回 True=校准中，False=校准完成"""
        STRENGTH = 16383
        MAX_WAIT = 30
        DD_THRESHOLD = 5
        DD_CONFIRM = 2
        MIN_POS_DELTA = 15  # 标线最少移动 15px 才算有效
        MAX_RETRIES = 2

        w = img.shape[1]
        center = w // 2
        pos = self._cal_detect_lane_pos(img)

        if self._cal_phase == "init":
            if pos is None:
                logger.log("[CAL] 未检测到标线，等待...")
                return True
            side = "left" if pos < center else "right"
            left_of_center = pos < center
            if side == "left":
                self._cal_seq = [(1, "右→中线"), (-1, "左→标线")] if left_of_center else [(-1, "左→标线"), (1, "右→中线")]
            else:
                self._cal_seq = [(1, "右→标线"), (-1, "左→中线")] if left_of_center else [(-1, "左→中线"), (1, "右→标线")]
            logger.log(f"[CAL] 检测到{side.upper()}标线 pos={pos}，车在中线{'左' if left_of_center else '右'}侧")
            logger.log(f"[CAL] 转向顺序: {self._cal_seq[0][1]} → {self._cal_seq[1][1]}")
            # 低油门起步
            self._apply_trigger(64)
            self._cal_phase = "baseline"
            self._cal_step = 0
            self._cal_positions = []
            self._cal_dir, _ = self._cal_seq[0]
            self._cal_seq_idx = 0
            return True

        if self._cal_phase == "baseline":
            if pos is not None:
                self._cal_positions.append(pos)
            self._cal_step += 1
            if self._cal_step >= 2:
                # 记录转向前位置，开始转向
                self._cal_pos_start = self._cal_positions[-1] if self._cal_positions else None
                self._steer(self._cal_dir * STRENGTH)
                self._cal_phase = "steer"
                self._cal_step = 0
            return True

        if self._cal_phase == "steer":
            if pos is not None:
                self._cal_positions.append(pos)
            self._cal_step += 1
            # dd 超阈值 → 检测到有效变化，立即停止转向进入 settle（至少打满 3 帧）
            if self._cal_step >= 3 and len(self._cal_positions) >= 3:
                p = self._cal_positions
                dd = (p[-1] - p[-2]) - (p[-2] - p[-3])
                if abs(dd) >= DD_THRESHOLD:
                    logger.log(f"[CAL]   {self._cal_seq[self._cal_seq_idx][1]} lag={self._cal_step}帧")
                    self._steer(0)
                    self._cal_phase = "settle"
                    self._cal_step = 0
                    self._cal_stable = 0
                    return True
            # 超时兜底：打满 steer_frames 仍无变化 → 也进 settle（后续 delta 检查会判失败）
            if self._cal_step >= self._cal_steer_frames:
                self._steer(0)
                self._cal_phase = "settle"
                self._cal_step = 0
                self._cal_stable = 0
            return True

        if self._cal_phase == "settle":
            if pos is not None:
                self._cal_positions.append(pos)
            if len(self._cal_positions) >= 3:
                p = self._cal_positions
                dd = (p[-1] - p[-2]) - (p[-2] - p[-3])
                if abs(dd) < DD_THRESHOLD:
                    self._cal_stable += 1
                else:
                    self._cal_stable = 0
            self._cal_step += 1

            settle_done = self._cal_stable >= DD_CONFIRM or self._cal_step >= MAX_WAIT
            if settle_done:
                settle = self._cal_step
                # 检查标线位移是否足够
                cur_pos = self._cal_positions[-1] if self._cal_positions else None
                delta = abs(cur_pos - self._cal_pos_start) if (cur_pos is not None and self._cal_pos_start is not None) else 0
                if delta < MIN_POS_DELTA:
                    if self._cal_retries < MAX_RETRIES:
                        self._cal_retries += 1
                        self._cal_steer_frames += 4
                        logger.log(f"[CAL] 标线仅移动{delta}px < {MIN_POS_DELTA}px，数据不可信，重试({self._cal_retries}/{MAX_RETRIES}) steer_frames={self._cal_steer_frames}")
                        self._cal_phase = "init"
                        self._cal_step = 0
                        self._cal_positions = []
                        self._cal_stable = 0
                        self._cal_settles = []
                        self._cal_seq = []
                        self._cal_seq_idx = 0
                        self._cal_pos_start = None
                        self._steer(0)
                    else:
                        logger.log(f"[CAL] 重试耗尽，校准失败，使用默认 alpha=0.6")
                        self._steer_alpha = 0.6
                        self._cal_phase = "done"
                        self._steer(0)
                    return True

                logger.log(f"[CAL]   {self._cal_seq[self._cal_seq_idx][1]} settle={settle}帧 位移={delta}px")
                self._cal_settles.append(settle)
                self._cal_seq_idx += 1
                if self._cal_seq_idx < len(self._cal_seq):
                    # 下一个方向
                    self._cal_dir, _ = self._cal_seq[self._cal_seq_idx]
                    self._cal_phase = "baseline"
                    self._cal_step = 0
                    self._cal_positions = []
                else:
                    # 校准完成
                    self._cal_finish()
                    return False
            return True

        return False

    def _cal_finish(self) -> None:
        """校准完成：计算 alpha，恢复油门"""
        self._apply_trigger(255)
        self._steer(0)
        settles = [v for v in self._cal_settles if v < 30]
        avg = sum(settles) / len(settles) if settles else 8.0
        alpha = max(0.5, min(0.9, 0.5 ** (1.0 / avg)))
        self._steer_alpha = alpha
        self._cal_phase = "done"
        logger.log(f"[CAL] settle数据: {settles}，平均={avg:.1f}帧 → alpha={alpha:.2f}")
        logger.log(f"[CAL] === 校准完成 → alpha={alpha:.2f}，主逻辑启动 ===")

    def _run_impl(self, ctrl) -> bool:
        """赛车控制核心逻辑（被 run / run_direct 共用）"""
        logger.log("赛车控制启动")
        self._running = True
        self.frame_id = 0  # 重试时重置帧计数
        self._lane_debug = None  # 重置标线中间数据
        self._dynamic_horizon = None  # 重置动态地平线
        self._c_burst = 0
        self._c_coast = 0
        self._steer_smoothed = 0.0
        self._steer_alpha = 0.6
        if self.debug is not None:
            self.debug.start_session("racing")
        self._create_pad()

        # ── 镜头迟滞校准（嵌入主循环，跳过决策逻辑，共享 debug 绘制） ──
        self._cal_init()

        # ── 常量 ──
        YOLO_INTERVAL = 2          # 每 2 游戏帧做一次 YOLO 推理
        SLOW_CHECK = 15            # 每秒（~15fps）检一次商店/结束

        # 起步：按住 RT 加速（游戏内部有倒计时，车不会立即动）
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

                # ── 校准模式：跳过所有决策逻辑，只跑校准步骤 ──
                if self._cal_phase != "done":
                    cal_done = not self._calibrate_step(img, ctrl)
                    # 校准期间也画 debug（标线+状态）
                    if self.debug is not None:
                        lane = self._detect_lane(img)
                        lane_vis = self._lane_debug
                        if lane:
                            center = self._estimate_road_center(lane, w)
                            lane_vis = {**lane, "center": center, "_debug": self._lane_debug} if self._lane_debug else {**lane, "center": center}
                        self.debug.save_frame(
                            img, lane=lane_vis,
                            label=f"cal_{self._cal_phase}_{self.frame_id}",
                            save_to_disk=True,
                            racing_info={"direction": self._cal_dir, "reason": "校准",
                                         "detail": f"{self._cal_phase} alpha={self._steer_alpha:.2f}",
                                         "frame_id": self.frame_id,
                                         "throttle": self._last_rt,
                                         "n_coins": 0, "n_cars": 0, "n_bonus": 0,
                                         "stick": self._last_stick[0],
                                         "horizon_locked": False},
                        )
                    if cal_done:
                        logger.log(f"[CAL] alpha={self._steer_alpha:.2f}，进入主逻辑")
                    continue

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
                    force = (self._prev_reason not in ("直行", "回带", ""))
                    keep_val = self._lane_keep(lane, force_init=force)
                    if keep_val != 0:
                        direction = keep_val
                        k_cls = "左" if keep_val < 0 else "右"
                        pct = int(self._keep_strength * 100)
                        detail = f"车道保持 {k_cls}修({pct}%)"
                self._prev_reason = reason

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
                # ── 转向平滑：消除镜头延迟导致的急转急回 ──
                # C 区突发期间不平滑（安全机制，需要满力度）
                if self._c_burst > 0:
                    self._steer_smoothed = float(actual_dir * 32767)
                else:
                    target = actual_dir * 32767.0
                    a = self._steer_alpha
                    self._steer_smoothed = self._steer_smoothed * a + target * (1 - a)
                smoothed_dir = int(self._steer_smoothed)
                if smoothed_dir != self.last_dir:
                    self._steer(smoothed_dir)
                    self.last_dir = smoothed_dir

                # ── 油门控制 ──
                throttle = self._calc_throttle(reason, direction)
                if throttle != self._current_throttle:
                    self._apply_trigger(throttle)
                    self._current_throttle = throttle

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
                               f"dir={dir_label} thr={throttle}", "DEBUG")

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
                        "throttle": throttle,
                        "horizon_locked": self._dynamic_horizon is not None,
                        "wall_zone": wall_zone,
                        "keep_strength": self._keep_strength,
                        "steer_alpha": self._steer_alpha,
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
