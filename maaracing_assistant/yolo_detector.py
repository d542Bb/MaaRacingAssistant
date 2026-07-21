#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YOLO 目标检测模块：基于 ONNX Runtime 的 YOLOv8 推理封装
"""

import numpy as np
import cv2
import onnxruntime as ort

from maaracing_assistant.logger import logger


class YOLODetector:
    # 按类别的置信度阈值：coin(0)和bonus_car(2)面积小/样本少，降低阈值
    CLASS_CONF = {0: 0.35, 1: 0.50, 2: 0.35}

    def __init__(self, model_path: str, conf: float = 0.5, iou: float = 0.45):
        # ── Session 选项（图优化 + 缓存） ──
        from pathlib import Path
        import os

        cache_dir = Path(__file__).resolve().parent / "__pycache__" / "ort_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)

        sess_opts = ort.SessionOptions()
        sess_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        sess_opts.optimized_model_filepath = str(cache_dir / "model_optimized.onnx")
        sess_opts.add_session_config_entry("session.dml_kernel_cache_path", str(cache_dir))
        sess_opts.add_session_config_entry("session.dml_kernel_cache_enabled", "1")
        sess_opts.intra_op_num_threads = 4
        sess_opts.inter_op_num_threads = 4
        sess_opts.enable_mem_pattern = True
        sess_opts.enable_cpu_mem_arena = True

        try:
            self.session = ort.InferenceSession(
                model_path,
                sess_options=sess_opts,
                providers=["DmlExecutionProvider", "CPUExecutionProvider"]
            )
            logger.log("YOLO 使用 GPU (DirectML)")
        except Exception:
            try:
                self.session = ort.InferenceSession(
                    model_path,
                    sess_options=sess_opts,
                    providers=["CUDAExecutionProvider", "CPUExecutionProvider"]
                )
                logger.log("YOLO 使用 GPU (CUDA)")
            except Exception:
                self.session = ort.InferenceSession(
                    model_path,
                    sess_options=sess_opts,
                    providers=["CPUExecutionProvider"]
                )
                logger.log("YOLO 使用 CPU (GPU 不可用)")

        logger.log(f"ONNX 缓存: {cache_dir}")
        self.input_name = self.session.get_inputs()[0].name
        self.conf = conf
        self.iou = iou
        self.input_size = 640
        self._call_count = 0

    def __call__(self, img_rgb: np.ndarray, roi: tuple | None = None):
        """YOLO 推理

        Args:
            img_rgb: 全屏 RGB 图像
            roi: (x1, y1, x2, y2) 裁剪区域（原始图坐标），None 表示全图
        """
        if roi is not None:
            x1, y1, x2, y2 = roi
            orig = img_rgb[y1:y2, x1:x2].copy()
            ox, oy = x1, y1
        else:
            orig = img_rgb
            ox, oy = 0, 0

        orig_h, orig_w = orig.shape[:2]
        scale = min(self.input_size / orig_h, self.input_size / orig_w)
        nh, nw = int(orig_h * scale), int(orig_w * scale)
        pad_y = (self.input_size - nh) // 2
        pad_x = (self.input_size - nw) // 2

        padded = np.full((self.input_size, self.input_size, 3), 114, dtype=np.uint8)
        padded[pad_y: pad_y + nh, pad_x: pad_x + nw] = cv2.resize(orig, (nw, nh), interpolation=cv2.INTER_LINEAR)
        blob = padded.transpose(2, 0, 1)[None].astype(np.float32) / 255.0

        raw_outputs = self.session.run(None, {self.input_name: blob})
        outputs = raw_outputs[0]
        assert isinstance(outputs, np.ndarray), f"ONNX 返回非数组: {type(outputs)}"
        preds = outputs[0].transpose(1, 0)

        xywh = preds[:, :4]
        cls_conf = preds[:, 4:]
        max_scores = np.max(cls_conf, axis=1)
        max_classes = np.argmax(cls_conf, axis=1)

        # ── 诊断：统计各类别原始置信度分布（每 10 帧一次） ──
        self._call_count += 1
        if self._call_count % 10 == 0:
            for cls_name, cls_id in [("coin", 0), ("car", 1), ("bonus_car", 2)]:
                confs = max_scores[max_classes == cls_id]
                if len(confs) > 0:
                    logger.log(f"[RAW] {cls_name}: {len(confs)}个 pred, "
                               f"max={confs.max():.3f}, mean={confs.mean():.3f}", "DEBUG")
                else:
                    logger.log(f"[RAW] {cls_name}: 无 pred（均低于0.01）", "DEBUG")

        # 按类别使用不同置信度阈值
        per_class_thresholds = np.array([self.CLASS_CONF.get(int(c), self.conf) for c in max_classes])
        mask = max_scores > per_class_thresholds
        if not np.any(mask):
            return [], [], [], []

        boxes = xywh[mask]
        scores_f = max_scores[mask]
        classes = max_classes[mask]

        xyxy = np.zeros_like(boxes)
        xyxy[:, 0] = boxes[:, 0] - boxes[:, 2] / 2
        xyxy[:, 1] = boxes[:, 1] - boxes[:, 3] / 2
        xyxy[:, 2] = boxes[:, 0] + boxes[:, 2] / 2
        xyxy[:, 3] = boxes[:, 1] + boxes[:, 3] / 2

        # NMS 使用较松的 0.35 阈值以避免低置信硬币被误杀
        nms_conf = min(self.CLASS_CONF.values())
        indices = cv2.dnn.NMSBoxes(xyxy.tolist(), scores_f.tolist(), nms_conf, self.iou)
        if len(indices) == 0:
            return [], [], [], []

        coins, cars, bonus_cars = [], [], []
        debug_dets = []
        for i in indices:
            i = int(i)
            cls = int(classes[i])
            x1, y1, x2, y2 = xyxy[i]
            x1, x2 = (x1 - pad_x) / scale + ox, (x2 - pad_x) / scale + ox
            y1, y2 = (y1 - pad_y) / scale + oy, (y2 - pad_y) / scale + oy
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(orig_w + ox, x2), min(orig_h + oy, y2)
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            bw, bh = x2 - x1, y2 - y1
            cls_name = {0: "coin", 1: "car", 2: "bonus_car"}.get(cls, "?")
            debug_dets.append({
                "box": (int(x1), int(y1), int(x2), int(y2)),
                "confidence": float(scores_f[i]),
                "class_name": cls_name,
            })
            if cls == 0:
                coins.append((int(cx), int(cy), int(bw), int(bh)))
            elif cls == 1:
                cars.append((int(cx), int(cy), int(bw), int(bh)))
            else:
                bonus_cars.append((int(cx), int(cy), int(bw), int(bh)))

        return coins, cars, bonus_cars, debug_dets
