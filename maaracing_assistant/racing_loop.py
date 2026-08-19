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

from maa.custom_action import CustomAction
from maa.context import Context

from maaracing_assistant.vgamepad_lazy import vg
from maaracing_assistant.yolo_detector import YOLODetector
from maaracing_assistant.logger import logger
from maaracing_assistant.wgcap import WgcCapture


class RacingLoop(CustomAction):
    # 路面 ROI（裁剪掉顶部分数条和底部仪表盘，让 YOLO 专注路面）
    # 1280×720 下 y=28%~78% → (0, 201, 1280, 561)
    ROI = (0, 201, 1280, 561)

    def __init__(self, model_path: str, debug=None,
                 capture_backend: str = "wgc_latest", hwnd: int = 0):
        super().__init__()
        self.det = YOLODetector(model_path)
        self.debug = debug
        self.gpad = None
        self.last_dir = 0
        self.frame_id = 0
        self._running = True
        self._end_reason = ""  # 最近一次 _is_end 匹配的结果原因
        self._coin_turn_log_count = 0  # 金币转向诊断计数
        self.capture_backend = capture_backend  # "maa" | "wgc_latest"
        self._wgc_cap: WgcCapture | None = None
        # 路径审计 counter（benchmark 隔离验证用）
        self._maa_cap_count = 0
        self._wgc_cap_count = 0
        # 跳帧推理缓存
        self._cached_coins: list = []
        self._cached_cars: list = []
        self._cached_bonus: list = []
        self._cached_yolo_debug: list = []
        self._cached_all_raw: list = []
        self._lane_debug: dict | None = None  # 标线检测中间数据（供 debug 可视化）
        # 防碰撞历史
        self._hwnd = hwnd              # 游戏窗口句柄（WGC 捕获需要）
        self._use_fast_cap = False    # 已废弃：BitBlt 对 GPU 窗口必黑屏，统一走 MAA FramePool (WGC)
        self._fast_cap_mode = "auto"  # 保留字段，兼容旧代码
        self._target_fps = 15         # 目标帧率（基准测试后自动调优）
        self._wall_memory = 0  # 标线丢失后的防碰撞记忆：0=无, 1=左墙, -1=右墙
        self._wall_pos_history: list[int] = []  # 单边标线位置历史（防碰撞二阶导用）
        self._wall_side: str | None = None      # 当前追踪的标线侧
        self._wall_side_stable: int = 0         # lane side 稳定计数（≥3 才接受切换）
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
        # C区防撞升级：方向偏置 + 同侧冷却 + burst递增
        self._wall_bias: int = 0               # 轻柔持续反向偏置（比例值 -32767~32767）
        self._wall_bias_frames: int = 0        # 偏置剩余帧数
        self._c_cool_last_side: str | None = None  # 上次触发C区的墙侧（left/right）
        self._c_cool_frames: int = 0           # 同侧 C区冷却剩余帧数
        self._c_burst_level: int = 0           # 同侧连续触发等级（1→2帧, 2→4帧, 3→6帧, 4→8帧）
        # 前馈控制：记录上一帧目标位置
        self._prev_aim_cx: float = 0.0  # 上一帧瞄准目标的 cx
        self._prev_aim_cy: float = 0.0  # 上一帧瞄准目标的 cy
        self._prev_aim_frame: int = 0   # 上一帧瞄准帧号
        self._aim_debug: dict = {}      # 前馈调试信息

        # 转向输出平滑：低通滤波 + 转向率限制，防止过冲（但要够跟手不要卡）
        self._steer_filtered: float = 0.0  # 滤波后的摇杆值（浮点 -32767~32767）
        # 每帧最大变化量 MAX_STEP：22000 约等于 1.5 帧完成满舵行程，既不瞬间跳又跟得上
        self._steer_max_step: int = 22000
        # 低通滤波 α：0.80 偏重跟手（决策值马上就有80%权重过去），配合 MAX_STEP 防过冲
        self._steer_alpha: float = 0.80

        # 加载结束检测模板（任一匹配即认为本轮结束）
        from pathlib import Path
        self._end_templates: list[tuple[np.ndarray, str, float]] = []  # (gray, name, threshold)
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
        self._cleanup_wgc()
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
        # 重置防撞升级状态
        self._wall_bias = 0
        self._wall_bias_frames = 0
        self._c_cool_last_side = None
        self._c_cool_frames = 0
        self._c_burst_level = 0
        self._last_dodge_dir = 0
        self._last_dodge_frame = 0
        # 重置转向平滑
        self._steer_filtered = 0.0

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

    def _cap_fast(self, capture) -> np.ndarray | None:
        """已废弃：BitBlt/GDI 对 GPU 渲染的游戏窗口必黑屏，保留签名兼容调用方。
        现在统一返回 None，让上层走 MAA FramePool (WGC)。
        """
        return None

    # ---- WGC 后端 ----

    def _init_wgc(self):
        """初始化 WGC 常驻捕获并等待首帧（幂等）。"""
        if self._wgc_cap is not None:
            return
        if self._hwnd == 0:
            raise RuntimeError("WGC 初始化需要有效的 hwnd")
        cap = WgcCapture(self._hwnd)
        cap.start()
        # 等待首帧，最多 2 秒
        for _ in range(40):
            frame, *_ = cap.get_latest()
            if frame is not None:
                break
            time.sleep(0.05)
        else:
            cap.stop()
            raise RuntimeError("WGC 启动后 2 秒未收到首帧")
        self._wgc_cap = cap
        logger.log(f"WGC 后端已就绪（hwnd={self._hwnd}）")

    def _cleanup_wgc(self):
        """清理 WGC 捕获（幂等）。"""
        cap = self._wgc_cap
        self._wgc_cap = None
        if cap is not None:
            try:
                cap.stop()
            except Exception:
                pass

    def _cap(self, capture):
        """截图：MAA FramePool (WGC) 或 WGC 常驻后端，根据 capture_backend 选择。

        capture 为 CaptureCapability（经 capability 接口访问截图，不再接收完整 controller）。
        """
        # ── WGC 常驻后端 ──
        if self.capture_backend == "wgc_latest":
            self._wgc_cap_count += 1
            if self._wgc_cap_count == 1:
                logger.log(">>> REAL WGC LATEST PATH <<<", "WARNING")
            if self._wgc_cap is None:
                raise RuntimeError("WGC 未初始化，请先调用 _init_wgc()")
            t0 = time.perf_counter_ns()
            result = self._wgc_cap.get_latest()
            t1 = time.perf_counter_ns()
            if result is None or result[0] is None:
                raise RuntimeError("WGC get_latest() 返回空帧")
            bgra, fid, capture_ts, frame_age = result
            # 底部锚定 16:9：从底部向上裁，保证车头/路面坐标系不变
            h, w = bgra.shape[:2]
            target_h = int(w * 9 / 16)
            if h > target_h:
                bgra = bgra[-target_h:, :]
            rgb = cv2.cvtColor(bgra, cv2.COLOR_BGRA2RGB)
            t2 = time.perf_counter_ns()
            # 记录细分耗时（μs），供 benchmark 采集
            self._wgc_get_latest_us = (t1 - t0) / 1000
            self._wgc_convert_us = (t2 - t1) / 1000
            self._wgc_total_us = (t2 - t0) / 1000
            self._wgc_frame_age = frame_age
            self._wgc_frame_id = fid
            return rgb

        # ── MAA FramePool 后端（经 capability.screenshot()，内部已做 BGR→RGB 与 ctypes 兜底）──
        self._maa_cap_count += 1
        if self._maa_cap_count == 1:
            logger.log(">>> REAL MAA CAP PATH <<<", "WARNING")
        if self._use_fast_cap:
            arr = self._cap_fast(capture)
            if arr is not None:
                return arr
            self._use_fast_cap = False
            logger.log("快速截图失效，降级到 MAA 截图", "WARNING")
        try:
            t_post = time.perf_counter_ns()
            arr = capture.screenshot()
            t_after_wait = time.perf_counter_ns()
            self._maa_post_wait_us = (t_after_wait - t_post) / 1000
            if arr is None or arr.size == 0 or arr.ndim < 3:
                logger.log(f"图像格式异常: shape={arr.shape if arr is not None else None}", "WARNING")
                return None
            # capability.screenshot() 已返回 RGB；此处仅做 4 通道归一化以兼容旧路径
            if arr.shape[2] == 4:
                arr = cv2.cvtColor(arr, cv2.COLOR_BGRA2RGB)
            # 帧签名（快速校验新鲜度）
            self._maa_frame_signature = hash(arr[:1].tobytes())
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
            logger.log(f"[LANE] 单边={side} pos={pos_at_mid} 线={len(top)}条")
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

        # ---- Bug #5：静态越界兜底（在稳定锁之前，用 raw side/pos 直接判断物理越界） ----
        # 无论稳定锁处于什么状态，只要当前 lane 已物理越界就强制修正
        # 左墙：pos > 380 → 已贴左墙边缘，必须强制右转
        if side == "left" and pos > 380:
            return (2, 1)
        # 右墙：pos < 850 → 已贴右墙边缘，必须强制左转
        if side == "right" and pos < 850:
            return (2, -1)

        # ---- Lane side 稳定锁（Bug #4：至少连续 3 帧才接受切换） ----
        # 首次初始化（_wall_side is None）跳过稳定锁，直接接受
        if self._wall_side is None:
            self._wall_side = side
            self._wall_side_stable = 0
        elif side == self._wall_side:
            self._wall_side_stable = min(3, self._wall_side_stable + 1)
        else:
            self._wall_side_stable = max(-3, self._wall_side_stable - 1)

        # 稳定 3 帧后才接受切换，并镜像映射历史（Bug #1：不清空）
        if side != self._wall_side and self._wall_side_stable <= -3:
            # 镜像映射：left pos ↔ right pos 用 w - p 对称变换
            # 左墙 pos 越大越靠右，右墙 pos 越小越靠左，所以 w - p 是精确逆映射
            self._wall_pos_history = [w - p for p in self._wall_pos_history]
            self._wall_side = side
            self._wall_side_stable = 0
            if self._c_cool_last_side != side:
                self._c_burst_level = 0

        # 冷却帧自然衰减（每帧 -1）
        if self._c_cool_frames > 0:
            self._c_cool_frames -= 1

        # 维护 5 帧历史（映射到稳定 side 的坐标系）
        if side != self._wall_side:
            mapped_pos = w - pos  # 原始 side 未稳定 → 映射到稳定 side 坐标系
        else:
            mapped_pos = pos
        self._wall_pos_history.append(mapped_pos)
        if len(self._wall_pos_history) > 5:
            self._wall_pos_history.pop(0)

        # 以下墙检查使用 self._wall_side（稳定后的 side），确保历史与 side 一致
        # 所有阈值判断使用 mapped_pos（映射到稳定 wall_side 坐标系），避免过渡期坐标错位
        # ---- 左墙检查 ----
        if self._wall_side == "left" and mapped_pos > 350 and len(self._wall_pos_history) >= 2:
            d, dd, cum3 = self._calc_drift(self._wall_pos_history)
            if mapped_pos > 450 and cum3 > 10:
                # 同侧冷却命中：降级为 B区阻挡，不触发 burst
                if (self._c_cool_last_side == "left"
                        and self._c_cool_frames > 0):
                    logger.log(
                        f"[WALL] 左墙C区 pos={pos} 冷却中({self._c_cool_frames})→降级B区"
                    )
                    return (1, 1)
                # 通过冷却 → 触发 C区 + 递增 burst 等级
                self._c_cool_last_side = "left"
                self._c_cool_frames = 20  # 20帧同侧冷却
                self._c_burst_level = min(4, self._c_burst_level + 1)
                burst_frames = self._c_burst_level * 2  # 1→2, 2→4, 3→6, 4→8
                logger.log(
                    f"[WALL] 左墙C区 pos={pos} cum3={cum3}，"
                    f"强制右转 (burst_lv={self._c_burst_level}, {burst_frames}帧)"
                )
                return (2, 1)
            if dd > 5 and d > 0:
                return (1, 1)

        # ---- 右墙检查 ----
        if self._wall_side == "right" and mapped_pos < 930 and len(self._wall_pos_history) >= 2:
            d, dd, cum3 = self._calc_drift(self._wall_pos_history)
            if mapped_pos < 830 and cum3 < -10:
                # 同侧冷却命中：降级为 B区阻挡
                if (self._c_cool_last_side == "right"
                        and self._c_cool_frames > 0):
                    logger.log(
                        f"[WALL] 右墙C区 pos={pos} 冷却中({self._c_cool_frames})→降级B区"
                    )
                    return (1, -1)
                # 通过冷却 → 触发 C区 + 递增 burst 等级
                self._c_cool_last_side = "right"
                self._c_cool_frames = 20
                self._c_burst_level = min(4, self._c_burst_level + 1)
                burst_frames = self._c_burst_level * 2
                logger.log(
                    f"[WALL] 右墙C区 pos={pos} cum3={cum3}，"
                    f"强制左转 (burst_lv={self._c_burst_level}, {burst_frames}帧)"
                )
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
        # 方向（朝漂移反方向）—— 左右标线语义相反！
        # 左标线右移(cum3>0)→车左漂→右修(1)；左标线左移(cum3<0)→车右漂→左修(-1)
        # 右标线右移(cum3>0)→车贴右墙→左修(-1)；右标线左移(cum3<0)→车左漂→右修(1)
        # （Bug #6 修正：右标线 cum3 方向与安全修正方向相反）
        if lane["side"] == "left":
            new_dir = 1 if cum3 > 0 else -1
        else:
            new_dir = -1 if cum3 > 0 else 1

        # ── 判断逻辑 ──
        # 位置变化率检测：最近 1 帧变化 <5px 视为已停，提前结束
        if self._keep_strength > 0 and abs(d) < 5:
            self._keep_strength = max(0, self._keep_strength - 0.2)
            if self._keep_strength < 0.01:
                self._keep_cooldown = 8
                return 0

        # 阈值放松：15px 就起步（原20太保守，等漂到20再动已经滞后）
        # 起步强度提高：按 abs(cum3) 连续起步，15px≈0.2，30px≈0.4，50px≈0.70（上限）
        elif abs(cum3) >= 15:
            # 漂移超过阈值 → 激活/升级
            if self._keep_strength < 0.01:
                self._keep_strength = min(0.70, 0.10 + abs(cum3) / 150)
                self._keep_dir = new_dir
            elif new_dir != self._keep_dir:
                # 方向反了 = 上一帧修正已经过冲 → 轻降档（-0.16 不再猛砍 -0.22），保持有效力度
                self._keep_strength = max(0.08, self._keep_strength - 0.16)
                self._keep_dir = new_dir
            elif dd > 0:
                # 漂移仍在加速 → 升档（0.08→0.10，拉得更快）
                self._keep_strength = min(1.0, self._keep_strength + 0.10)
            elif dd > -2:
                # 慢速收敛 → 维持
                pass
            else:
                # 快速收敛 → 减档（-0.08→-0.06，慢一点减防止放松太早）
                self._keep_strength = max(0.1, self._keep_strength - 0.06)
        elif self._keep_strength > 0 and abs(cum3) < 16:
            # 漂移已收敛 → 降低力度（关闭阈值 18→16，保持更久）
            self._keep_strength = max(0, self._keep_strength - 0.18)
            if self._keep_strength < 0.01:
                self._keep_cooldown = 8  # 完全关闭，冷却约 0.5 秒
                return 0
        else:
            # 阈值之间（16~15），保持当前力度不调
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
        #    基础 0.5% + 面积补偿（area_ratio * 30，限幅 0~1.5%）
        #    area_ratio=0.001 → stop=0.005 + 0.030(限0.015) = 0.020（上限 2%）
        #    area_ratio=0.01  → stop=0.005 + 0.30(限0.015)  = 0.020
        # 修复：原上限3%把近区大跳板车的 2~3% offset 全吞了→判死区→完全不转向→没吃到
        stop_zone = 0.005 + min(0.015, area_ratio * 30)

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

        # 5. 偏离中心区检查：目标不在中心区时，强制转向
        off_center = not in_center

        # ── 计算连续力度（日志 + 实际返回共用一套逻辑） ──
        # off_center 优先：已经偏离中心区 → 死区收缩到 1%，不允许"偏到车道外还直行"
        if off_center:
            effective_stop = 0.01
        else:
            effective_stop = stop_zone

        if abs(offset) < effective_stop:
            # 动态死区：offset 落在 effective_stop 内视为已到位
            strength = 0.0
            reason = "死区"
        else:
            # 连续线性映射：超过 effective_stop 的部分按比例分配到 0~1
            usable_range = max(0.01, 1.0 - effective_stop)
            raw = (abs(offset) - effective_stop) / usable_range
            raw = max(0.0, min(1.0, raw))
            # 用 sqrt 做缓动：小偏离轻柔、大偏离快速拉起
            raw = raw ** 0.5

            # 区域系数：金币/跳板车瞄准语义——目标越近越应该积极抢角度！
            # 原Bug：近区×0.35 是"防撞微调"语义用过来的，结果近区反而最不积极→错过跳板车
            # 修复：远区×0.70（提前对准但别太猛）/ 中区×1.00（正常）/ 近区×1.15（马上到窗口，抓紧角度）
            if zone == 0:
                zone_mul = 0.70
            elif zone == 1:
                zone_mul = 1.00
            else:
                zone_mul = 1.15

            # off_center（偏离中心区）分区梯度保底
            # 原保底太保守：近区40%=只有1/4舵→错过角度
            # 修复：远区30% / 中区50% / 近区70%，保证有偏就有明显动作
            if off_center:
                if zone == 0:
                    oc_floor = 0.30
                elif zone == 1:
                    oc_floor = 0.50
                else:
                    oc_floor = 0.70
                raw = max(raw, oc_floor)
                reason = "偏离中心"
            else:
                reason = f"{zone_label}连续"

            strength = raw * zone_mul

            # A1：moving_to_center（目标正在往中心走）—— 与近区回摆合并单一分支，禁止双重相乘！
            # 原Bug：not_in_center×0.75 之后预见性模块 zone==2+moving 又 ×0.85，两者叠加 0.6375→力度 0.80→0.51
            # 修复：
            #   - 如果同时是近区回摆场景（zone==2 and moving）：单次×0.85，reason="+mv(近区)"
            #   - 否则普通 moving：in_center 收 45%，not_in_center 收 75%，reason="+mv"
            if moving_to_center:
                if zone == 2:
                    strength *= 0.85
                    if reason == "死区":
                        reason = "前馈衰减(mv近区)"
                    else:
                        reason = reason + "+mv(近区)"
                else:
                    if in_center:
                        strength *= 0.45
                    else:
                        strength *= 0.75
                    if reason == "死区":
                        reason = "前馈衰减(mv+cen)"
                    else:
                        reason = reason + "+mv"


        # 限幅（安全）
        strength = max(0.0, min(1.0, strength))

        # ── 预见性提前收敛（延后+轻收，不要过早放掉） ──
        # 原Bug：ETA<12 太早 + progress*0.88 压太狠→离目标10帧就开始放→到目标跟前已经转不动→没吃到
        # 修复：ETA<7 才启动收力；最多收 30%，不到跟前不放弃
        # 注意：近区回摆（zone==2 and moving_to_center）已与上方 moving 分支合并单一衰减，不要在此重复乘
        ff_reason_extra = ""
        if strength > 0.01 and dy > 0.5:  # dy>0 = 目标正朝我们靠近（y增大=像素往下=距离拉近）
            # ETA 估算：还有多少帧目标底部会抵达 ROI 底边（车头前方判定线）
            dist_to_bottom = (self.ROI[3] if hasattr(self, 'ROI') else h * 0.78) - bottom_y
            eta_frames = dist_to_bottom / max(0.5, dy)  # 避免除 0

            # 新收力曲线（最多收 30%，最多压到 0.70，不再把 1 帧前压到 0.50 以下）：
            #   ETA > 7 帧：完全不收（早着呢，先拉到角度再说）
            #   ETA ≈ 5 帧：开始轻微收（progress=0.4 → eta_k=0.88）
            #   ETA ≈ 3 帧：力度压到约 76%（progress=0.8 → eta_k=0.76）
            #   ETA ≤ 2 帧：力度压到约 70%（最后2帧才明显松，不要提前放干净）
            if eta_frames < 7:
                progress = max(0.0, min(1.0, (7.0 - eta_frames) / 5.0))  # 0→1 收力进度
                # 最多收掉 30%
                eta_k = 1.0 - progress * 0.30
                old_s = strength
                strength *= eta_k
                ff_reason_extra = f"+提前收敛(ETA={eta_frames:.0f}f {old_s:.2f}→{strength:.2f})"

        # 再次限幅，确保经过预见性模块后仍合法
        strength = max(0.0, min(1.0, strength))

        # ── 诊断日志 ──
        if ff_reason_extra:
            reason = reason + ff_reason_extra
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
            "dy": dy,
            "moving": moving_to_center,
            "area_ratio": area_ratio,
            "ff_reason": reason,
            "in_center": in_center,
            "ff_extra": ff_reason_extra,
        }

        # ── 执行 ──
        if strength <= 0.001:
            return 0

        sign = 1 if offset > 0 else -1
        return int(sign * strength * 32767)

    def _avoid(self, cars: list, w: int, h: int,
               wall_zone: int = 0, wall_dir: int = 0) -> int:
        """目标落在行驶方向中心区（L2c~R2c）则满躲，否则不管
        wall_zone/wall_dir: 传入墙状态用于方向锁安全检查，防止锁方向时推着往墙上撞
        """
        DANGER_Y = h * 0.30
        DODGE_LOCK_FRAMES = 6  # 避障方向锁：保持至少 6 帧（~240ms），防止抖动横跳
        threats = [c for c in cars if c[1] > DANGER_Y]

        # ── 方向锁优先判断（若上次避障仍在锁定期内） ──
        lock_frames = self.frame_id - self._last_dodge_frame
        if (self._last_dodge_dir != 0
                and self._last_dodge_frame > 0
                and lock_frames < DODGE_LOCK_FRAMES
                and threats):
            threat = max(threats, key=lambda c: c[1])
            tx, ty, tw, th = threat[0], threat[1], threat[2], threat[3]
            bottom_y = ty + th // 2
            b = self._lane_boundaries_at_y(bottom_y, h, w)

            def occupied(x1, x2) -> bool:
                return any(
                    x1 < c[0] < x2 and abs(c[1] - ty) < h * 0.15
                    for c in threats if c is not threat
                )
            # 检查旧方向的车道是否仍然安全
            if self._last_dodge_dir < 0:
                old_side_ok = not occupied(b["L12"], b["L2c"])
                # B1：左躲 + 墙 B/C 区需要右修（左墙）= 往墙上撞 → 强制破锁
                if wall_zone >= 1 and wall_dir == 1:
                    old_side_ok = False
            else:
                old_side_ok = not occupied(b["R2c"], b["R12"])
                # B1：右躲 + 墙 B/C 区需要左修（右墙）= 往墙上撞 → 强制破锁
                if wall_zone >= 1 and wall_dir == -1:
                    old_side_ok = False

            zone = self._get_zone(ty, th)
            # 力度三档渐进：远区25%（小警告，先带方向）/ 中区60% / 近区100%
            # （之前远区就50%太猛，连续13帧左躲满打直接抽到旁边车道）
            avoid_strengths = {0: 8191, 1: 19660, 2: 32767}
            strength = avoid_strengths[zone]

            if old_side_ok:
                # 旧方向仍然安全 → 锁方向，不抖动
                self._last_dodge_frame = self.frame_id  # 刷新锁定计时
                # 但方向锁只锁左右，允许力度随 zone 变化（越近越猛）
                return (1 if self._last_dodge_dir > 0 else -1) * strength
            # 旧方向不安全 → 跳出锁，重新决策

        if not threats:
            self._last_dodge_dir = 0
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
            self._last_dodge_dir = 0
            return 0

        # 同深度其他车阻挡检查
        def occupied(x1, x2) -> bool:
            return any(
                x1 < c[0] < x2 and abs(c[1] - ty) < h * 0.15
                for c in threats if c is not threat
            )

        right_ok = not occupied(b["R2c"], b["R12"])
        left_ok = not occupied(b["L12"], b["L2c"])

        # 力度三档渐进：远区25% / 中区60% / 近区100%
        avoid_strengths = {0: 8191, 1: 19660, 2: 32767}
        strength = avoid_strengths[zone]

        # 根据障碍物在主车道内的位置决定优先方向：偏右→左躲，偏左→右躲
        mid_lane = (b["L2c"] + b["R2c"]) / 2
        chosen = 0
        if tx > mid_lane:
            # 障碍物偏右，优先左躲
            if left_ok:
                chosen = -strength
            elif right_ok:
                chosen = strength
        else:
            # 障碍物偏左，优先右躲
            if right_ok:
                chosen = strength
            elif left_ok:
                chosen = -strength

        if chosen != 0:
            self._last_dodge_dir = chosen
            self._last_dodge_frame = self.frame_id

        return chosen

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

            # C1-1：B 区反向（AIM 输出方向与墙安全方向相反）→ 顺着墙安全方向轻推
            # （Bug #3：原来限幅 25% 仍往墙上修，改为 wall_dir * 4096 轻推救回方向）
            if wall_zone == 1 and ((aim < 0 and wall_dir == 1) or (aim > 0 and wall_dir == -1)):
                b_cls = "左" if wall_dir == 1 else "右"
                aim = wall_dir * 4096  # 顺着墙安全方向轻推
                detail = f"B区 轻修{b_cls}限幅25%"

            # C1-2：预警区衰减（lane 存在且墙位已经到 B 区阈值 65% 深度时）
            # 推墙同向的 AIM/避障输出 ×0.45，给后面 B 区缓冲，不一直满力推到 C 区才救
            if wall_zone == 0 and lane is not None:
                # lane["pos"] 是当前标线距离同侧边的像素位置
                # 左标线 side=left：lane["pos"] 小 → 接近左墙
                # 右标线 side=right：lane["pos"] 小 → 接近右墙（pos 单位与左墙一致！）
                # Bug #2 修正：右墙阈值从 >(w-168) 改为 <930，对齐 _wall_avoidance B 区阈值
                warn_left = (lane["side"] == "left") and (lane["pos"] < 312)
                warn_right = (lane["side"] == "right") and (lane["pos"] < 930)
                if (warn_left and aim < 0) or (warn_right and aim > 0):
                    # 正在往预警墙方向推 → 0.45 衰减
                    aim = int(aim * 0.45)

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
            aim = self._avoid(near_cars, w, h, wall_zone, wall_dir)
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

                # C1-2：避障同样应用预警区衰减（lane 存在且到 B 区阈值 65% 深度）
                if wall_zone == 0 and lane is not None:
                    warn_left = (lane["side"] == "left") and (lane["pos"] < 312)
                    warn_right = (lane["side"] == "right") and (lane["pos"] < 930)
                    if (warn_left and aim < 0) or (warn_right and aim > 0):
                        # 正在往预警墙方向推（而且还在避障输出 → 更危险）→ 0.45 衰减
                        aim = int(aim * 0.45)

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

    def _smoke_test(self, capture) -> None:
        """轻量基准测试（50 帧），快速确定锁帧目标，替代完整 benchmark。"""
        logger.log("⏱  轻量基准测试（50 帧）...", "DEBUG")
        n_frames = 50
        caps: list[float] = []
        yolos: list[float] = []
        yolo_frame_totals: list[float] = []
        non_yolo_frame_totals: list[float] = []
        yolo_count = 0
        ok = 0

        for i in range(n_frames):
            t0 = time.perf_counter()
            img = self._cap(capture)
            if img is None:
                continue
            ok += 1
            caps.append((time.perf_counter() - t0) * 1000)

            if i % 2 == 0:
                t_y0 = time.perf_counter()
                self.det(img, roi=self.ROI)
                t_y1 = time.perf_counter()
                yolo_lat = (t_y1 - t_y0) * 1000
                yolos.append(yolo_lat)
                yolo_count += 1

            t_total = (time.perf_counter() - t0) * 1000
            if i % 2 == 0:
                yolo_frame_totals.append(t_total)
            else:
                non_yolo_frame_totals.append(t_total)

        # ── 报告 ──
        def pct(vals, p):
            if not vals:
                return 0
            return sorted(vals)[max(0, min(len(vals) - 1, int(len(vals) * p / 100)))]

        caps_s = sorted(caps)
        yolos_s = sorted(yolos)
        yolo_ft = sorted(yolo_frame_totals)
        non_yolo_ft = sorted(non_yolo_frame_totals)

        logger.log(f"  截图: P50={pct(caps_s,50):.1f}ms  P95={pct(caps_s,95):.1f}ms  ({ok}/{n_frames}帧有效)")
        if yolos_s:
            logger.log(f"  YOLO: P50={pct(yolos_s,50):.1f}ms  P95={pct(yolos_s,95):.1f}ms  ({yolo_count}次)")

        # 自动调优（同 _benchmark_latency 逻辑）
        if yolo_ft:
            trimmed = yolo_ft[:-1] if len(yolo_ft) >= 5 else yolo_ft
            p90_idx = max(0, min(len(trimmed) - 1, int(len(trimmed) * 0.90)))
            yolo_p90_trim = trimmed[p90_idx]
            tuned_fps = int(950 / max(1, yolo_p90_trim))
            tuned_fps = max(15, min(30, tuned_fps))
            self._target_fps = tuned_fps
            logger.log(f"  YOLO帧: P50={pct(yolo_ft,50):.1f}ms  P90(trim)={yolo_p90_trim:.1f}ms")
            logger.log(f"  自动调优: {self._target_fps} FPS")
        else:
            self._target_fps = 15

        if non_yolo_ft:
            logger.log(f"  非YOLO帧: P50={pct(non_yolo_ft,50):.1f}ms  ({len(non_yolo_ft)}帧)")

        # WGC 后端额外检查
        if self.capture_backend == "wgc_latest" and self._wgc_cap is not None:
            metrics = self._wgc_cap.get_metrics()
            if metrics:
                logger.log(
                    f"  WGC: callback={metrics['callback_count']}  "
                    f"interval P50={metrics['callback_interval_p50']:.1f}ms",
                    "DEBUG",
                )
            self._wgc_cap.reset_metrics()

    def _benchmark_latency(self, capture) -> None:
        """全链路延迟诊断（1000 帧），含后端隔离审计、YOLO warm-up、新鲜度验证。"""
        # ── 重置审计计数器 ──
        self._maa_cap_count = 0
        self._wgc_cap_count = 0
        if self._wgc_cap is not None:
            self._wgc_cap.reset_metrics()

        # ── YOLO warm-up（3 次 dummy 推理，消除 CUDA kernel 初始化 / 模型 warm-up 影响）──
        import numpy as np
        warmup_img = np.zeros((480, 640, 3), dtype=np.uint8)
        for w in range(3):
            self.det(warmup_img, roi=self.ROI)
        logger.log(f"YOLO warm-up 完成（3 次 dummy 推理）", "DEBUG")

        n_frames = 1000
        caps: list[float] = []
        lanes: list[float] = []
        yolos: list[float] = []
        decides: list[float] = []
        totals: list[float] = []
        yolo_frame_totals: list[float] = []
        non_yolo_frame_totals: list[float] = []
        yolo_count = 0
        yolo_times_list: list[float] = []

        # WGC 后端细分数据收集
        wgc_get_latencies: list[float] = []
        wgc_convert_latencies: list[float] = []
        wgc_frame_ages: list[float] = []
        wgc_frame_ids: list[int] = []

        # MAA 后端细分数据收集
        maa_post_wait: list[float] = []
        maa_get: list[float] = []
        maa_signatures: list[int] = []
        maa_sig_changes = 0
        maa_prev_sig = None

        logger.log(
            f"⏱  延迟基准测试：采集 {n_frames} 帧（含 {n_frames // 2} 次 YOLO）"
            f"  backend={self.capture_backend}  wgc_init={self._wgc_cap is not None}",
            "WARNING",
        )

        temp_cached_coins = []
        temp_cached_cars = []
        temp_cached_bonus = []
        temp_cached_yolo_debug = []
        temp_cached_all_raw = []

        for i in range(n_frames):
            t0 = time.perf_counter()
            img = self._cap(capture)
            t1 = time.perf_counter()
            caps.append((t1 - t0) * 1000)
            if img is None:
                time.sleep(0.05)
                continue

            h, w = img.shape[:2]

            # ── 后端专用数据采集 ──
            if self.capture_backend == "wgc_latest":
                wgc_get_latencies.append(self._wgc_get_latest_us)
                wgc_convert_latencies.append(self._wgc_convert_us)
                wgc_frame_ages.append(self._wgc_frame_age)
                wgc_frame_ids.append(self._wgc_frame_id)
            elif self.capture_backend == "maa":
                maa_post_wait.append(self._maa_post_wait_us)
                maa_get.append(self._maa_get_us)
                sig = getattr(self, "_maa_frame_signature", None)
                if sig is not None:
                    maa_signatures.append(sig)
                    if maa_prev_sig is not None and sig != maa_prev_sig:
                        maa_sig_changes += 1
                    maa_prev_sig = sig

            t_lane0 = time.perf_counter()
            lane = self._detect_lane(img)
            t_lane1 = time.perf_counter()
            lanes.append((t_lane1 - t_lane0) * 1000)

            if i % 2 == 0:
                t_yolo0 = time.perf_counter()
                coins, cars, bonus_cars, yolo_debug, all_raw = self.det(img, roi=self.ROI)
                t_yolo1 = time.perf_counter()
                yolo_lat = (t_yolo1 - t_yolo0) * 1000
                yolos.append(yolo_lat)
                yolo_times_list.append(yolo_lat)
                yolo_count += 1
                # 按序号打印 YOLO latency（前 5 次 + 每 50 次）
                if yolo_count <= 5 or yolo_count % 50 == 0:
                    logger.log(f"   YOLO #{yolo_count}: {yolo_lat:.1f}ms")
                temp_cached_coins = coins
                temp_cached_cars = cars
                temp_cached_bonus = bonus_cars
                temp_cached_yolo_debug = yolo_debug
                temp_cached_all_raw = all_raw

            t_dec0 = time.perf_counter()
            wall_zone, wall_dir = 0, 0
            if lane is not None:
                wall_zone, wall_dir = self._wall_avoidance(lane, w)
            elif self._wall_memory != 0:
                wall_zone, wall_dir = 1, self._wall_memory
            self._decide(temp_cached_coins, temp_cached_cars, temp_cached_bonus,
                         lane, w, h, wall_zone, wall_dir)
            t_dec1 = time.perf_counter()
            decides.append((t_dec1 - t_dec0) * 1000)

            t_total = (time.perf_counter() - t0) * 1000
            totals.append(t_total)
            if i % 2 == 0:
                yolo_frame_totals.append(t_total)
            else:
                non_yolo_frame_totals.append(t_total)

        # ── 报告输出 ──
        def pct(sorted_vals, p):
            if not sorted_vals:
                return 0
            return sorted_vals[max(0, min(len(sorted_vals) - 1, int(len(sorted_vals) * p / 100)))]

        caps_s = sorted(caps)
        lanes_s = sorted(lanes)
        yolos_s = sorted(yolos)
        decides_s = sorted(decides)
        totals_s = sorted(totals)

        if not totals_s:
            logger.log("━" * 50)
            logger.log("⏱  全链路延迟诊断报告（1000 帧采样）")
            logger.log("   ⚠ 所有截图均失败，无法统计延迟指标")
            return

        logger.log("━" * 50)
        logger.log("⏱  全链路延迟诊断报告（1000 帧采样）")
        logger.log(f"   截图: P50={pct(caps_s,50):.1f}ms  P95={pct(caps_s,95):.1f}ms  "
                    f"P99={pct(caps_s,99):.1f}ms  max={caps_s[-1]:.1f}ms")
        logger.log(f"   标线: P50={pct(lanes_s,50):.1f}ms  P95={pct(lanes_s,95):.1f}ms  "
                    f"P99={pct(lanes_s,99):.1f}ms")
        if yolos_s:
            logger.log(f"   YOLO: P50={pct(yolos_s,50):.1f}ms  P95={pct(yolos_s,95):.1f}ms  "
                        f"P99={pct(yolos_s,99):.1f}ms  ({yolo_count}次)")
        logger.log(f"   决策: P50={pct(decides_s,50):.1f}ms  P95={pct(decides_s,95):.1f}ms  "
                    f"P99={pct(decides_s,99):.1f}ms")
        logger.log(f"   总帧: P50={pct(totals_s,50):.1f}ms  P95={pct(totals_s,95):.1f}ms  "
                    f"P99={pct(totals_s,99):.1f}ms  max={totals_s[-1]:.1f}ms")

        avg_total = sum(totals) / len(totals)
        logger.log(f"   平均帧耗时: {avg_total:.2f}ms  "
                    f"P50帧耗时: {pct(totals_s,50):.1f}ms  "
                    f"P95帧耗时: {pct(totals_s,95):.1f}ms")

        cap_med = pct(caps_s, 50)
        yolo_med = pct(yolos_s, 50) if yolos_s else 0
        if avg_total > 100:
            logger.log(f"   ⚠ 平均帧耗时({avg_total:.1f}ms)偏高。"
                        f"截图P50={cap_med:.0f}ms + YOLO P50={yolo_med:.0f}ms")
        elif cap_med > 25:
            logger.log(f"   ⚠ 截图P50={cap_med:.0f}ms偏高，可能影响转向反应")
        elif yolo_med > 35:
            logger.log(f"   ⚠ YOLO P50={yolo_med:.0f}ms偏高，目标检测滞后可能导致碰撞")
        else:
            logger.log(f"   ✓ 延迟正常：截图+检测+YOLO+决策均在健康范围内")

        # ── 自动调优（同原有逻辑）──
        yolo_sorted = sorted(yolo_frame_totals) if yolo_frame_totals else []
        non_yolo_sorted = sorted(non_yolo_frame_totals) if non_yolo_frame_totals else []
        if yolo_sorted:
            trimmed = yolo_sorted[:-1] if len(yolo_sorted) >= 5 else yolo_sorted
            p90_idx = max(0, min(len(trimmed) - 1, int(len(trimmed) * 0.90)))
            yolo_p90_trim = trimmed[p90_idx]
            yolo_p50 = yolo_sorted[len(yolo_sorted) // 2]
            yolo_p95_raw = yolo_sorted[max(0, min(len(yolo_sorted) - 1, int(len(yolo_sorted) * 0.95)))]
            tuned_fps = int(950 / max(1, yolo_p90_trim))
            tuned_fps = max(15, min(30, tuned_fps))
            logger.log(
                f"   YOLO帧: P50={yolo_p50:.1f}ms  剔除1帧后P90={yolo_p90_trim:.1f}ms  "
                f"原始P95={yolo_p95_raw:.1f}ms  ({len(yolo_sorted)}帧, trimmed={len(trimmed)})"
            )
            if yolo_p95_raw > yolo_p90_trim * 1.8:
                logger.log(
                    f"   ⚠ YOLO 存在离群值（P95/P90={yolo_p95_raw/yolo_p90_trim:.1f}×），"
                    f"已剔除慢帧按稳健 P90 调优"
                )
        else:
            tuned_fps = 30
            yolo_p90_trim = 0

        if non_yolo_sorted:
            non_yolo_p50 = non_yolo_sorted[len(non_yolo_sorted) // 2]
            logger.log(f"   非YOLO帧: P50={non_yolo_p50:.1f}ms  ({len(non_yolo_sorted)}帧)")

        old_fps = self._target_fps
        self._target_fps = tuned_fps
        logger.log(
            f"   自动调优: {old_fps}→{tuned_fps} FPS "
            f"(剔除1帧后YOLO P90={yolo_p90_trim:.1f}ms + 5%裕量)"
        )
        yolo_interval = max(1, round(tuned_fps / 10))
        logger.log(f"   YOLO 间隔: 每 {yolo_interval} 帧一次 (≈{tuned_fps / max(1, yolo_interval):.0f} Hz)")

        # ── WGC 后端特有指标 ──
        if self.capture_backend == "wgc_latest" and self._wgc_cap is not None:
            wgc_metrics = self._wgc_cap.get_metrics()
            if wgc_metrics:
                logger.log("   ── WGC 后端指标 ──")
                logger.log(
                    f"   callback interval: "
                    f"P50={wgc_metrics['callback_interval_p50']:.3f}ms  "
                    f"P95={wgc_metrics['callback_interval_p95']:.3f}ms  "
                    f"P99={wgc_metrics['callback_interval_p99']:.3f}ms  "
                    f"count={wgc_metrics['callback_count']}"
                )
            if wgc_get_latencies:
                gl_arr = np.array(wgc_get_latencies)
                cv_arr = np.array(wgc_convert_latencies)
                caps_wgc = np.array(caps)
                logger.log(
                    f"   get_latest: P50={np.median(gl_arr):.3f}μs  "
                    f"P95={np.percentile(gl_arr, 95):.3f}μs  "
                    f"P99={np.percentile(gl_arr, 99):.3f}μs"
                )
                logger.log(
                    f"   color convert: P50={np.median(cv_arr):.3f}μs  "
                    f"P95={np.percentile(cv_arr, 95):.3f}μs  "
                    f"P99={np.percentile(cv_arr, 99):.3f}μs"
                )
                logger.log(
                    f"   _cap total: P50={np.median(caps_wgc):.3f}ms  "
                    f"P95={np.percentile(caps_wgc, 95):.3f}ms  "
                    f"P99={np.percentile(caps_wgc, 99):.3f}ms"
                )
            if wgc_frame_ages:
                fa_arr = np.array(wgc_frame_ages)
                logger.log(
                    f"   frame age: P50={np.median(fa_arr):.3f}ms  "
                    f"P95={np.percentile(fa_arr, 95):.3f}ms  "
                    f"P99={np.percentile(fa_arr, 99):.3f}ms  "
                    f"max={fa_arr.max():.3f}ms"
                )
            if wgc_frame_ids:
                unique = len(set(wgc_frame_ids))
                total = len(wgc_frame_ids)
                dup = total - unique
                logger.log(
                    f"   duplicate ratio (WGC frame_id): {dup}/{total} "
                    f"({dup / max(total, 1) * 100:.1f}%)  unique frame_ids={unique}"
                )
            self._wgc_cap.reset_metrics()

        # ── MAA 后端特有指标 ──
        if self.capture_backend == "maa":
            logger.log("   ── MAA 后端指标 ──")
            if maa_post_wait:
                pw_arr = np.array(maa_post_wait)
                logger.log(
                    f"   post_screencap.wait: P50={np.median(pw_arr):.3f}μs  "
                    f"P95={np.percentile(pw_arr, 95):.3f}μs  "
                    f"P99={np.percentile(pw_arr, 99):.3f}μs"
                )
            if maa_get:
                g_arr = np.array(maa_get)
                logger.log(
                    f"   job.get: P50={np.median(g_arr):.3f}μs  "
                    f"P95={np.percentile(g_arr, 95):.3f}μs  "
                    f"P99={np.percentile(g_arr, 99):.3f}μs"
                )
            if maa_signatures:
                total_sig = len(maa_signatures)
                logger.log(
                    f"   frame signature changes: {maa_sig_changes}/{total_sig - 1} "
                    f"({maa_sig_changes / max(1, total_sig - 1) * 100:.1f}%)  "
                    f"total frames={total_sig}"
                )

        # ── 后端隔离审计 ──
        logger.log("   ── 后端审计 ──")
        logger.log(f"   backend requested: {self.capture_backend}")
        logger.log(f"   MAA path calls: {self._maa_cap_count}")
        logger.log(f"   WGC path calls: {self._wgc_cap_count}")
        logger.log(f"   WGC initialized: {self._wgc_cap is not None}")

        logger.log("━" * 50)

    def _run_impl(self, capture) -> bool:
        """赛车控制核心逻辑（被 run / run_direct 共用）"""
        logger.log("赛车控制启动")
        self._running = True
        self.frame_id = 0  # 重试时重置帧计数
        self._lane_debug = None  # 重置标线中间数据
        self._dynamic_horizon = None  # 重置动态地平线
        self._c_burst = 0
        self._c_coast = 0
        if self.debug is not None:
            self.debug.start_session("racing")

        self._create_pad()

        # ── WGC 后端初始化（需在 benchmark 前就绪） ──
        if self.capture_backend == "wgc_latest":
            self._init_wgc()

        # ── 动态常量（基准测试后 _target_fps 已调优） ──
        YOLO_INTERVAL = max(1, round(self._target_fps / 10))  # YOLO ≈ 10 Hz 更新
        SLOW_CHECK = max(1, self._target_fps)                  # 每秒检一次商店/结束

        # 起步：按住 RT 加速（游戏内部有倒计时，车不会立即动）
        assert self.gpad is not None, "手柄未创建"
        self._apply_trigger(230)  # 90% 油门

        # ── 启动前快速验证 ──
        self._smoke_test(capture)
        backend_label = "WGC Latest" if self.capture_backend == "wgc_latest" else "MAA FramePool"
        logger.log(
            f"🎯 性能甜点: {self._target_fps} FPS（最低延迟模式） | "
            f"截图={backend_label} | "
            f"YOLO=每{YOLO_INTERVAL}帧~{self._target_fps / YOLO_INTERVAL:.0f}Hz | "
            f"结束检测=每{SLOW_CHECK}帧"
        )

        try:
            while self._running:
                t0 = time.time()
                img = self._cap(capture)
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
                    # wall_memory（标线丢失）：降级为 B 区柔和阻挡，不触发 C 区 burst
                    # （如果给 wall_zone=2 会每帧走 _decide 的防撞强制路径 → 连续触发 8+ 次 burst 应激）
                    wall_zone = 1
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

                # ── wall_bias 轻柔方向偏置衰减 ──
                if self._wall_bias_frames > 0:
                    self._wall_bias_frames -= 1
                    if self._wall_bias_frames <= 0:
                        self._wall_bias = 0

                # ── C 区突发修正 + 轻柔归中（反打思维 + 方向偏置保底） ──
                # 突发：短促打满改变车头指向 → coast 阶段带轻柔反向偏置 → 远离墙后重评估
                actual_dir = direction  # 实际执行的转向
                if self._c_burst > 0:
                    # 突发中：持续打 burst_dir（满力 = direction × 32767）
                    burst_val = self._c_burst_dir
                    if abs(burst_val) <= 1:
                        burst_val = int(burst_val * 32767)
                    actual_dir = burst_val
                    self._c_burst -= 1
                    if self._c_burst == 0:
                        # coast 帧数量 = 与 burst 帧相同（对称），之前最少 5 帧兜底
                        self._c_coast = max(5, self._c_burst_level * 2)
                        logger.log(
                            f"[WALL] 突发结束，带偏置 coast {self._c_coast} 帧"
                            f" (bias={self._wall_bias})"
                        )
                elif self._c_coast > 0:
                    # coast 阶段：AIM/避障决策优先
                    # B2-1：direction 非零时，若 direction 与 wall_bias 方向相反 → 丢弃 wall_bias
                    #         （防止上次 burst 的反向 bias 残留继续推上墙，与当前决策打架）
                    if direction == 0:
                        actual_dir = self._wall_bias
                    elif (direction * self._wall_bias) < 0:
                        # 异号：决策层和 bias 打架 → 只用决策层
                        actual_dir = direction
                    else:
                        # 同号：可以叠加，但最多不超过 direction×1.25（不加太猛）
                        actual_dir = direction
                    self._c_coast -= 1
                elif (reason == "防撞" and direction != 0
                      and lane is not None
                      and self._wall_bias_frames < 10):
                    # 触发新的突发修正（中断归中）
                    # 三重保护防重复触发：① lane 必须存在（wall_memory 场景柔和回带不发 burst）
                    #                     ② _c_burst/_c_coast 不在进行中（上面两个 elif 已经拦了）
                    #                     ③ wall_bias 基本衰减完（<10帧≈400ms）
                    self._c_coast = 0
                    # burst 帧数 = 同侧等级 × 2（_wall_avoidance 已设置 _c_burst_level）
                    self._c_burst = max(2, self._c_burst_level * 2)
                    self._c_burst_dir = direction
                    burst_val = direction
                    if abs(burst_val) <= 1:
                        burst_val = int(burst_val * 32767)
                    actual_dir = burst_val
                    self._c_burst -= 1
                    # 20 帧 15% 轻柔反向偏置（救回来后自然偏向安全侧，不与 AIM/避障打架）
                    # 之前 50 帧 25% 太长太猛 → 后续跳板车决策和 bias 方向相反时互相抽风
                    self._wall_bias = int(-direction * 0.15 * 32767)
                    self._wall_bias_frames = 20
                    cls = "左" if direction == -1 else "右"
                    logger.log(
                        f"[WALL] 突发修正{cls}转×{self._c_burst + 1}帧"
                        f" (lv={self._c_burst_level}, bias={self._wall_bias}×20帧)"
                    )

                # ── wall_bias 保底：无决策时叠加轻柔方向带 ──
                # （防止 burst/coast 结束后就纯直行 → 立刻又撞回同侧墙）
                if (actual_dir == 0
                        and self._wall_bias != 0
                        and self._wall_bias_frames > 0):
                    actual_dir = self._wall_bias
                    if reason == "直行":
                        detail = f"防撞偏置(bias={int(self._wall_bias/32767*100)}%)"

                # ── 转向输出平滑 + 转向率限制（解决过冲核心机制） ──
                # raw_steer：决策层想要的目标摇杆值
                # abs(actual_dir) <= 1 说明是 ±1 全量，需×32767；否则已是比例值
                if abs(actual_dir) <= 1:
                    raw_steer = int(actual_dir * 32767)
                else:
                    raw_steer = int(actual_dir)
                raw_steer = max(-32767, min(32767, raw_steer))

                # 判断是否紧急场景（C 区 burst 进行中）：紧急场景跳过平滑，立即响应
                is_emergency = self._c_burst > 0
                if is_emergency:
                    # 紧急修正：直接用 raw 值，不要平滑拖后腿
                    filtered = float(raw_steer)
                else:
                    # 1) 一阶低通滤波：filtered[t] = α·target + (1-α)·filtered[t-1]
                    f = self._steer_alpha * raw_steer + (1.0 - self._steer_alpha) * self._steer_filtered
                    # 2) 转向率限制：每帧变化不超过 _steer_max_step（防止物理瞬间打满）
                    delta = f - self._steer_filtered
                    if abs(delta) > self._steer_max_step:
                        delta = self._steer_max_step if delta > 0 else -self._steer_max_step
                    filtered = self._steer_filtered + delta

                steer_val = int(round(filtered))
                steer_val = max(-32767, min(32767, steer_val))
                self._steer_filtered = float(steer_val)

                # 只有摇杆值变化超过阈值才发送（减少 USB HID 报告刷屏，游戏有输入死区）
                if abs(steer_val - self.last_dir) >= 256:
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
                sleep = max(0, 1.0 / self._target_fps - elapsed)
                if sleep:
                    time.sleep(sleep)
        finally:
            self._cleanup_wgc()
            self._destroy_pad()
            self.last_dir = 0
            logger.log("赛车控制停止")
        return False

    def run(self, context: Context, argv: dict) -> bool:  # type: ignore[override]
        """MAA Pipeline CustomAction 入口（保留兼容）"""
        # Context.controller 由 MAA 运行时动态注入，类型检查器不可见
        from maaracing_assistant.modules.capabilities import PostScreencapCapture
        return self._run_impl(PostScreencapCapture(getattr(context, "controller")))

    def run_direct(self, capture) -> bool:
        """绕过 MAA Pipeline 直接运行赛车控制。

        capture 为 CaptureCapability（截图能力），不再接收完整 controller。
        """
        return self._run_impl(capture)
