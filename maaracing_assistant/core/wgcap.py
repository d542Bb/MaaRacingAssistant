#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
常驻 WGC (Windows Graphics Capture) 捕获模块。

零拷贝架构：WGC → D3D11 CopyResource → Map → memoryview → ndarray
每帧独立 staging texture，NativeMappedFrame 持有所有权，
ndarray 通过 base 链阻止 Unmap，因此 latest_frame 是安全的 immutable snapshot。

设计原则：
- 回调只做引用交换 + 记录元数据，不做 NumPy 操作
- get_latest() 永远不触发截图、永远不等下一帧
- 锁内只交换 Python 引用和整数
"""

import time
import threading

import numpy as np
from windows_capture import WindowsCapture, Frame, InternalCaptureControl

from maaracing_assistant.core.logger import logger


class WgcCapture:
    """常驻 WGC 捕获器。

    用法：
        cap = WgcCapture(hwnd)
        cap.start()
        frame, fid, ts_ns, age_ms = cap.get_latest()
        cap.stop()
    """

    def __init__(self, hwnd: int):
        self._hwnd = hwnd
        self._capture: WindowsCapture | None = None
        self._lock = threading.Lock()

        # 最新帧状态（锁保护）
        self._latest_frame: np.ndarray | None = None
        self._frame_id = 0
        self._capture_ts_ns = 0          # callback 到达时间，perf_counter_ns
        self._source_timespan = 0        # WGC frame 的 SystemRelativeTime（原始值）

        # 运行状态
        self._started = False
        self._stopped = False

        # metrics（仅统计，不保存 ndarray）
        self._callback_intervals: list[float] = []
        self._last_callback_ts_ns = 0

    # ---- 公开接口 ----

    def start(self):
        """启动后台 WGC 捕获线程。"""
        if self._started:
            return
        self._started = True
        self._stopped = False

        capture = WindowsCapture(
            window_hwnd=self._hwnd,
            cursor_capture=False,
            draw_border=False,
            minimum_update_interval=0,
        )
        self._capture = capture

        @capture.event
        def on_frame_arrived(frame: Frame, control: InternalCaptureControl):
            self._on_frame(frame)

        @capture.event
        def on_closed():
            logger.log("WGC 捕获窗口已关闭", "WARNING")
            self._stopped = True

        capture.start_free_threaded()
        logger.log("WGC 常驻捕获已启动", "DEBUG")

    def stop(self):
        """停止捕获。"""
        self._stopped = True
        if self._capture is not None:
            try:
                self._capture = None
            except Exception:
                pass
        logger.log("WGC 常驻捕获已停止", "DEBUG")

    def get_latest(self):
        """获取最新帧及其元数据。

        Returns:
            (frame, frame_id, capture_ts_ns, frame_age_ms) 或
            (None, 0, 0, 0)

            - frame: BGRA ndarray (H×W×4)，零拷贝 view，非 C-contiguous
            - frame_id: 单调递增帧序号
            - capture_ts_ns: callback 到达时间（time.perf_counter_ns）
            - frame_age_ms: 该帧在 cache 中已停留的时间（ms）
        """
        now_ns = time.perf_counter_ns()

        with self._lock:
            frame = self._latest_frame
            fid = self._frame_id
            ts_ns = self._capture_ts_ns

        if frame is None:
            return None, 0, 0, 0.0

        return frame, fid, ts_ns, (now_ns - ts_ns) / 1_000_000

    @property
    def frame_count(self) -> int:
        with self._lock:
            return self._frame_id

    @property
    def is_running(self) -> bool:
        return self._started and not self._stopped

    @property
    def source_timespan(self) -> int:
        """WGC frame 的原始 SystemRelativeTime，可用于分析 native pipeline 延迟。"""
        with self._lock:
            return self._source_timespan

    # ---- metrics ----

    def get_metrics(self):
        """返回回调间隔统计（ms），用于 benchmark。"""
        intervals = self._callback_intervals
        if not intervals:
            return {}
        arr = np.array(intervals)
        return {
            "callback_interval_p50": float(np.median(arr)),
            "callback_interval_p95": float(np.percentile(arr, 95)),
            "callback_interval_p99": float(np.percentile(arr, 99)),
            "callback_count": len(intervals),
        }

    def reset_metrics(self):
        """重置统计。"""
        self._callback_intervals.clear()

    # ---- 内部回调 ----

    def _on_frame(self, frame: Frame):
        if self._stopped:
            return

        try:
            img = frame.frame_buffer  # BGRA ndarray，零拷贝
            if img is None or img.size == 0:
                return

            now_ns = time.perf_counter_ns()

            with self._lock:
                self._latest_frame = img
                self._frame_id += 1
                self._capture_ts_ns = now_ns
                self._source_timespan = frame.timespan

            # metrics：回调间隔
            if self._last_callback_ts_ns != 0:
                interval_ms = (now_ns - self._last_callback_ts_ns) / 1_000_000
                self._callback_intervals.append(interval_ms)
            self._last_callback_ts_ns = now_ns

        except Exception as e:
            logger.log(f"WGC 帧处理异常: {e}", "ERROR")