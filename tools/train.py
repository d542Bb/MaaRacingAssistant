from ultralytics import YOLO


def main():
    model = YOLO("yolov8n.pt")

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
    print("导出完成: train_output/weights/best.onnx")
    print("请复制到 assets/model/yolov8n_coins_cars.onnx")


if __name__ == "__main__":
    main()