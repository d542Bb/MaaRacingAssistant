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
        self._L_history: list[int] = []
        self._R_history: list[int] = []
        self._wall_memory = 0  # 标线丢失后的防碰撞记忆：0=无, 1=左墙, -1=右墙
        self._c_burst = 0  # C区突发修正剩余帧数
        self._c_burst_dir = 0
        self._c_coast = 0  # 突发后强制归中滑行剩余帧数

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
        self._cached_all_raw = []
        self._lane_debug = None

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
            # MAA/OpenCV 默认返回 BGR，统一转为 RGB 供全链路使用
            if arr.shape[2] == 4:
                arr = cv2.cvtColor(arr, cv2.COLOR_BGRA2RGB)
            elif arr.shape[2] == 3:
                arr = cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)
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

    # ---------- 黄色标线检测 ----------

    def _detect_lane(self, img_rgb: np.ndarray) -> dict | None:
        """检测道路两侧黄色标线（Hough 直线法：找 y>50% 区域最黄最直的线，断裂自动对齐）"""
        h, w = img_rgb.shape[:2]

        # 扫描下半部分：y=50%~80%，肯定在地平线以下，避开树冠/隧道墙干扰
        y1, y2 = int(h * 0.50), int(h * 0.80)
        roi = img_rgb[y1:y2, :]

        hsv = cv2.cvtColor(roi, cv2.COLOR_RGB2HSV)
        # 严格黄色范围：只取高饱和高亮度的纯黄色，排除路面反射/阴影
        lower = np.array([20, 150, 150])
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
        pts = lines.reshape(-1, 4)  # 兼容 (N,1,4) 和 (N,4) 格式
        for x1, y1_, x2, y2_ in pts:
            if x2 == x1:
                continue
            angle = np.degrees(np.arctan2(y2_ - y1_, x2 - x1)) % 180
            length = np.hypot(x2 - x1, y2_ - y1_)
            mid_x = (x1 + x2) / 2  # 线段中点 x 坐标
            # 左标线：角度 120°~165° + 中点偏左
            if 120 <= angle <= 165 and mid_x < roi_w * 0.50:
                left_lines.append((x1, y1_, x2, y2_, length, angle))
            # 右标线：角度 15°~60° + 中点偏右
            elif 15 <= angle <= 60 and mid_x >= roi_w * 0.50:
                right_lines.append((x1, y1_, x2, y2_, length, angle))

        def pick_best(lines_sorted, prefer_left: bool):
            """从候选线中选最长的，返回 (x_at_y1, x_at_y2) 延展到 ROI 边界"""
            if not lines_sorted:
                return None
            # 按长度排序，取前 3 条最长的
            top = sorted(lines_sorted, key=lambda l: l[4], reverse=True)[:3]
            # 对每条线，延展到 y=0 和 y=roi_h（ROI 顶部和底部）
            roi_h = y2 - y1
            xs = []
            for x1, y1_, x2, y2_, length, angle in top:
                slope = (y2_ - y1_) / (x2 - x1) if (x2 - x1) != 0 else 1e-6
                inv_slope = (x2 - x1) / (y2_ - y1_) if (y2_ - y1_) != 0 else 1e-6
                # 延展到 ROI 底部 (y = roi_h) 和顶部 (y = 0)
                x_bottom = x1 + inv_slope * (roi_h - y1_)
                x_top = x1 - inv_slope * y1_
                xs.append((x_top, x_bottom, length))
            # 取平均（加权长度）
            total_len = sum(l for _, _, l in xs) or 1
            avg_top = int(sum(xt * l for xt, _, l in xs) / total_len)
            avg_bot = int(sum(xb * l for _, xb, l in xs) / total_len)
            return (avg_top, avg_bot)

        left_xy = pick_best(left_lines, prefer_left=True)
        right_xy = pick_best(right_lines, prefer_left=False)

        # 构建 debug 边缘散点
        debug_left_xs = []
        debug_left_ys = []
        debug_right_xs = []
        debug_right_ys = []
        for x1, y1_, x2, y2_, *_ in left_lines:
            debug_left_xs.extend([x1, x2])
            debug_left_ys.extend([y1_ + y1, y2_ + y1])
        for x1, y1_, x2, y2_, *_ in right_lines:
            debug_right_xs.extend([x1, x2])
            debug_right_ys.extend([y1_ + y1, y2_ + y1])
        self._lane_debug = {
            "left": (debug_left_xs, debug_left_ys),
            "right": (debug_right_xs, debug_right_ys),
            "zone": (0, y1, w, y2),
            "failed": None,
        }

        # 需要至少一侧有标线
        if left_xy is None and right_xy is None:
            self._lane_debug["failed"] = "两侧无标线"
            return None

        # 取 ROI 中部（y=roi_h/2）处的左/右 x
        # 不推断缺失侧——防碰撞只信任真实检出的标线
        result = {}
        if left_xy is not None:
            lt, lb = left_xy
            left_at_mid = int(lt + (lb - lt) / (y2 - y1) * (y2 - y1) // 2) if (y2 - y1) else 0
            result["left"] = left_at_mid
        if right_xy is not None:
            rt, rb = right_xy
            right_at_mid = int(rt + (rb - rt) / (y2 - y1) * (y2 - y1) // 2) if (y2 - y1) else 0
            result["right"] = right_at_mid
        if not result:
            self._lane_debug["failed"] = "两侧无标线"
            return None
        result["center"] = (result.get("left", 0) + result.get("right", w)) // 2

        if self.frame_id % 90 == 0:
            n_left = len(left_lines)
            n_right = len(right_lines)
            L_str = result.get("left", "?")
            R_str = result.get("right", "?")
            logger.log(f"[LANE] 检测成功: L={L_str} R={R_str} C={result['center']} "
                       f"左线={n_left}条 右线={n_right}条")
        return result

    # ---------- 全局路径规划 ----------

    def _wall_avoidance(self, lane: dict, w: int) -> tuple[int, int]:
        """
        三区防碰撞 + 二阶导监控
        只信任真实检出的标线（不在 lane 中的 key 视为缺省）
        返回 (zone, direction):
          zone=0 → 安全，无动作
          zone=1 → B区警戒：direction 反方向的决策被阻挡
          zone=2 → C区强制：必须往 direction 方向修正
        """
        L = lane.get("left", -1)
        R = lane.get("right", -1)

        # 更新防撞记忆（标线丢失时用于推测位置）
        near_left = L > 0 and L > 400
        near_right = R > 0 and R < 800
        if near_left:
            self._wall_memory = 1  # 近左墙
        elif near_right:
            self._wall_memory = -1  # 近右墙
        elif L > 0 and L < 300 or R > 0 and R > 900:
            self._wall_memory = 0  # 远离墙壁，清空记忆

        # 更新防撞记忆
        if L > 0 and L > 400 or R > 0 and R < 800:
            self._wall_memory = 1 if (L > 400) else -1  # 近左墙→记忆左, 近右墙→记忆右
        elif L > 0 and L < 300 or R > 0 and R > 900:
            self._wall_memory = 0  # 远离墙壁，清空记忆

        # 维护 5 帧历史（只存储有效值）
        if L > 0:
            self._L_history.append(L)
            if len(self._L_history) > 5:
                self._L_history.pop(0)
        else:
            # 缺失侧：清空历史，避免旧值 + 新值混算 ddL
            self._L_history.clear()
        if R > 0:
            self._R_history.append(R)
            if len(self._R_history) > 5:
                self._R_history.pop(0)
        else:
            self._R_history.clear()

        # ---- 左墙 ----
        if L > 350 and len(self._L_history) >= 2:
            dL = self._L_history[-1] - self._L_history[-2]
            ddL = 0
            if len(self._L_history) >= 3:
                dL_prev = self._L_history[-2] - self._L_history[-3]
                ddL = dL - dL_prev

            if L > 450:
                logger.log(f"[WALL] 左墙C区 L={L}，强制右转")
                return (2, 1)
            if L > 350 and ddL > 5 and dL > 0:
                return (1, 1)  # 阻挡往左(aim=-1)

        # ---- 右墙 ----
        if R > 0 and R < 850 and len(self._R_history) >= 2:
            dR = self._R_history[-1] - self._R_history[-2]
            ddR = 0
            if len(self._R_history) >= 3:
                dR_prev = self._R_history[-2] - self._R_history[-3]
                ddR = dR - dR_prev

            if R < 750:
                logger.log(f"[WALL] 右墙C区 R={R}，强制左转")
                return (2, -1)
            if R < 850 and ddR < -5 and dR < 0:
                return (1, -1)

        return (0, 0)

    def _aim_at(self, target: tuple, w: int) -> int:
        """对准目标，以车的位置（屏幕中心）为参考"""
        cx = target[0]
        car_x = w // 2
        deadzone = w * 0.06

        if cx < car_x - deadzone:
            return -1
        elif cx > car_x + deadzone:
            return 1
        return 0

    def _avoid(self, cars: list, w: int, h: int) -> int:
        """避让障碍车（不做边界约束，交给 _wall_avoidance）"""
        DANGER_Y = h * 0.30
        center_x = w // 2

        # 过滤出危险区内的车
        threats = [c for c in cars if c[1] > DANGER_Y]
        if not threats:
            return 0

        # 选最近的威胁
        threat = max(threats, key=lambda c: c[1])
        tx, ty = threat[0], threat[1]

        LANE_W = w * 0.12
        THREAT_RANGE = LANE_W * 1.8

        if abs(tx - center_x) > THREAT_RANGE:
            return 0

        def in_lane(cx, cy):
            return cy > DANGER_Y and abs(cx - center_x) < LANE_W * 2.2

        left_occ = any(c[0] < center_x and in_lane(c[0], c[1]) for c in cars)
        right_occ = any(c[0] > center_x and in_lane(c[0], c[1]) for c in cars)

        if tx < center_x - LANE_W * 0.3:
            if not right_occ:
                return 1
            if not left_occ:
                return -1
            return 0
        elif tx > center_x + LANE_W * 0.3:
            if not left_occ:
                return -1
            if not right_occ:
                return 1
            return 0
        else:
            if not left_occ and right_occ:
                return -1
            if not right_occ and left_occ:
                return 1
            return 0

    def _decide(self, coins: list, cars: list, bonus_cars: list,
                lane: dict | None, w: int, h: int) -> tuple[int, str]:
        """
        全局决策：
          1. 防碰撞（C区强制 > B区阻挡）
          2. 跳板车 / 避障 / 金币（B区阻挡往墙方向）
          3. 无目标直行
        """
        # ========== 0. 防碰撞检查 ==========
        wall_zone, wall_dir = 0, 0
        if lane is not None:
            wall_zone, wall_dir = self._wall_avoidance(lane, w)

        # C区：无条件强制
        if wall_zone == 2:
            return wall_dir, "防撞"

        # ========== 1. 跳板车 ==========
        if bonus_cars:
            target = max(bonus_cars, key=lambda b: b[1])
            aim = self._aim_at(target, w)
            if aim != 0:
                cls = "左" if aim == -1 else "右"
                logger.log(f"[YOLO] bonus_car({target[0]:.0f},{target[1]:.0f})，{cls}转对准")
            else:
                logger.log(f"[YOLO] bonus_car({target[0]:.0f},{target[1]:.0f})，直冲")
            # B区：往墙方向则取消
            if wall_zone == 1 and ((aim == -1 and wall_dir == 1) or (aim == 1 and wall_dir == -1)):
                return 0, "防撞"
            return aim, "跳板车"

        # ========== 2. 障碍车避让（不做边界约束，交给 wall_avoidance） ==========
        DANGER_Y = h * 0.35
        near_cars = [c for c in cars if c[1] > DANGER_Y]
        if near_cars:
            aim = self._avoid(near_cars, w, h)
            if wall_zone == 1 and ((aim == -1 and wall_dir == 1) or (aim == 1 and wall_dir == -1)):
                return 0, "防撞"
            return aim, "避障"

        # ========== 3. 金币 ==========
        if coins:
            def coin_value(c):
                cx, cy = c[0], c[1]
                nearby = sum(1 for o in coins
                            if abs(o[0] - cx) < w * 0.2
                            and 0 < cy - o[1] < h * 0.3)
                return cy + nearby * 50
            target = max(coins, key=coin_value)
            aim = self._aim_at(target, w)
            if aim != 0 and self._coin_turn_log_count < 5:
                l_info = f"L={lane.get('left', '?')} R={lane.get('right', '?')}" if lane else "None"
                logger.log(f"[DECIDE] w={w} h={h} coin=({target[0]:.0f},{target[1]:.0f}) "
                           f"aim={aim} lane={l_info}", "DEBUG")
                self._coin_turn_log_count += 1
            if aim != 0:
                cls = "左" if aim == -1 else "右"
                logger.log(f"[YOLO] 金币({target[0]:.0f})，{cls}转")
            if wall_zone == 1 and ((aim == -1 and wall_dir == 1) or (aim == 1 and wall_dir == -1)):
                return 0, "防撞"
            return aim, "金币"

        # ========== 4. 无目标 ==========
        # 标线丢失但有记忆：从墙边带出来
        if lane is None and self._wall_memory != 0:
            direction = self._wall_memory  # 记忆左墙(1)→右转(1)，右墙(-1)→左转(-1)
            cls = "右" if direction == 1 else "左"
            if self.frame_id % 10 == 0:
                logger.log(f"[WALL] 标线丢失，记忆回带{cls}转(mem={self._wall_memory})")
            return direction, "回带"
        if self.frame_id % 15 == 0:
            logger.log("[YOLO] 无目标，直行")
        return 0, "直行"

    def _run_impl(self, ctrl) -> bool:
        """赛车控制核心逻辑（被 run / run_direct 共用）"""
        logger.log("赛车控制启动")
        self._running = True
        self.frame_id = 0  # 重试时重置帧计数
        self._lane_debug = None  # 重置标线中间数据
        self._c_burst = 0
        self._c_coast = 0
        if self.debug is not None:
            self.debug.start_session("racing")
        self._create_pad()

        # ── 常量 ──
        YOLO_INTERVAL = 2          # 每 2 游戏帧做一次 YOLO 推理
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

                direction, reason = self._decide(coins, cars, bonus_cars, lane, w, h)

                if self.frame_id % LABEL_LOG == 5:
                    logger.log(f"[YOLO] 帧#{self.frame_id} 金币={len(coins)} 障碍车={len(cars)} 跳板车={len(bonus_cars)} 方向={'左' if direction<0 else '右' if direction>0 else '直'}")

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
                    # 触发新的突发修正
                    self._c_burst = 2
                    self._c_burst_dir = direction
                    actual_dir = direction
                    self._c_burst -= 1
                    cls = "左" if direction == -1 else "右"
                    logger.log(f"[WALL] 突发修正{cls}转×2帧")
                if actual_dir != self.last_dir:
                    self._steer(actual_dir)
                    self.last_dir = actual_dir

                # ── 调试帧 ──
                if self.debug is not None and (self.debug.enabled or self.debug.peep_enabled):
                    save_disk = self.debug.enabled
                    dir_char = 'L' if actual_dir == -1 else 'S' if actual_dir == 0 else 'R'
                    racing_info = {
                        "direction": actual_dir,
                        "stick": actual_dir * 32767,
                        "reason": reason,
                        "lane": lane,
                        "n_coins": len(coins),
                        "n_cars": len(cars),
                        "n_bonus": len(bonus_cars),
                        "frame_id": self.frame_id,
                    }
                    # 把标线检测中间数据（扫描区域、边缘点）合并到 lane 供 debug 可视化
                    lane_vis = self._lane_debug  # debug 数据含 zone/edges/failed
                    if lane:
                        lane_vis = {**lane, "_debug": self._lane_debug} if self._lane_debug else lane
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
