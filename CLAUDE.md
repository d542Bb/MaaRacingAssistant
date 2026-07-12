# MaaRM-Alpha — Claude 项目配置

## 语言要求

**所有输出、思考过程、分析、计划、工具调用说明，必须使用中文（简体）。** 包括内部思考链。

## 操作规范

1. **执行命令后，必须把终端的完整输出（stdout + stderr）完整打印到对话里，不要截断、不要隐藏。**
2. **请求权限时（如 Bash、Edit、Write 等工具），必须用中文解释该指令的目的和可能的结果。** 例如："需要执行 pip install 安装 torch CUDA 版，耗时约 2 分钟，会替换当前的 CPU 版 torch。"
3. 遇到不确定的技术方案，先暂停并提问，确认后再继续。
4. 确保所有 Python 代码缩进为 4 空格。

## 项目概述

自动完成《巅峰极速》"极速狂飙"活动：
回合1赛车（吃金币+避让+撞 bonus_car）→ 回合2放弃 → 循环

## 技术栈

- MAA Framework 5.11.1（UI 流程 + 窗口控制）
- YOLOv8 + ONNX Runtime（视觉识别，3 类：coin / car / bonus_car）
- vgamepad（虚拟 Xbox 手柄）
- ttkbootstrap（GUI）

## 已确认 API（不要假设）

- `Toolkit.find_desktop_windows()` 返回 `DesktopWindow` 对象列表
  - 属性：`hwnd`, `class_name`, `window_name`
- `Toolkit.init_option(path, "")` 第二个参数传空字符串
- `Win32Controller(hWnd=hwnd)` 参数名驼峰 `hWnd`
- `Tasker.bind(resource, controller)` 顺序：resource 在前！
- `Resource.post_bundle(path)` 不是 `post_path`
- `Resource.register_custom_action(name, action)`

## 已知坑点

- 截图需要管理员权限
- ttkbootstrap Window 会覆盖图标，用原生 tk.Tk + ttk.Style
- ttkbootstrap LabelFrame 不支持 padding 参数
- YOLO ONNX 导出时 `simplify=True` 可能产生损坏模型

## 文件结构

```
d:\maazs_racing/
├── main.py              # 主入口：YOLO 推理 + RacingLoop + MaaRMController
├── gui.py               # 图形界面（ttkbootstrap + UAC 提权）
├── CLAUDE.md            # 本文件
├── HANDOVER.md          # 完整交接文档
├── README.md            # 快速开始
├── requirements.txt     # 依赖
├── .gitignore
├── assets/
│   ├── model/
│   │   └── yolov8n_coins_cars.onnx   # YOLO 模型（3 类）
│   ├── resource/
│   │   └── pipeline/
│   │       └── tasks.json            # MAA Pipeline 流程定义
│   └── icon.ico
├── config/
│   └── maa_option.json               # MAA 配置
├── dataset/
│   ├── images/train/   (150 张)
│   ├── images/val/     (38 张)
│   ├── labels/train/   (150 个)
│   └── labels/val/     (38 个)
├── tools/
│   ├── train.py         # YOLO 训练脚本（自动导出 ONNX + 复制到 assets）
│   └── dataset.yaml     # 数据集配置（3 类：coin / car / bonus_car）
└── logs/                # 运行日志（gitignore）
```

## Pipeline 流程

`tasks.json` 定义了 6 步闭环：
```
极速狂飙入口 → 回合1准备 → 回合1比赛(RacingLoop) → 回合1结束 → 回合2放弃 → 确认放弃 → 循环
```

## 决策优先级（RacingLoop._decide）

```
0️⃣ bonus_car（跳板车/油罐车）→ 对准撞上去
1️⃣ 障碍车（car）→ 躲避（3 车道判断）
2️⃣ 金币（coin）→ 吃（选最近的）
3️⃣ 无目标 → 直行
```

## 当前状态

- ✅ GUI — 正常，UAC 提权正常
- ✅ 窗口连接 — 正常
- ✅ Pipeline 绑定 — 正常
- ✅ 数据集 — 188 张标注（150 训练 / 38 验证），3 类
- ✅ YOLO 模型 — 已训练，ONNX 已导出（mAP50 ≈ 0.92）
- 👣 下一步 — 管理员权限运行 `gui.py`，连接游戏进行端到端测试

## 对 AI 助手的要求

1. 先沟通对齐，再输出代码
2. 兼容性优先，性能次之
3. 不了解的技术先暂停，调查后再决策
4. 必须确保缩进正确（4 空格）
5. 长代码优先给完整文件
6. 能提问就提问，关键决策必须确认
7. **所有输出和思考必须使用中文（简体）**
8. **执行命令后完整打印终端输出**
9. **请求权限时必须用中文解释目的和后果**
