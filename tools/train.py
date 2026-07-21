#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YOLO11n 训练脚本：自动训练并导出 ONNX 到 assets/model/
"""
from ultralytics import YOLO
from pathlib import Path
import shutil


def main():
    model = YOLO("yolo11n.pt")

    model.train(
        data="dataset.yaml",
        epochs=100,
        imgsz=640,
        batch=16,
        device=0,
        project=".",
        name="train_output",
        patience=20,
        exist_ok=True,
    )

    best = YOLO("train_output/weights/best.pt")
    best.export(format="onnx", imgsz=640, simplify=True, opset=12)
    onnx_path = "train_output/weights/best.onnx"

    dst = Path(__file__).parent.parent / "assets" / "model" / "model.onnx"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(onnx_path, dst)
    print(f"导出完成: {onnx_path}")
    print(f"已复制到: {dst}")


if __name__ == "__main__":
    main()
