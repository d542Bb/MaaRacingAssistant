# MaaRacingAssistant

<p align="center">
  <img src="assets/mra_icon.jpg" width="128" alt="MaaRacingAssistant logo">
</p>

基于 **MAA Framework** + **YOLOv8** + **vgamepad** 的《巅峰极速》"极速狂飙"活动自动化工具。

自动完成：启动归位 → 光标导航进入活动 → 回合1 YOLO 赛车 → 回合2放弃 → 循环。

## 功能

- 🎮 **虚拟手柄控制** — vgamepad 模拟 Xbox 手柄，支持摇杆移动 + 按键操作
- 👁️ **YOLO 视觉识别** — 实时识别金币/障碍车/跳板车（3类），ONNX Runtime 推理（CUDA 加速）
- 🧭 **光标导航** — 3 步导航进比赛（极速狂飙入口→开始挑战→寻找对手），配置化按钮管理
- 🏎️ **自动赛车** — 吃金币 + 避让障碍车 + 对准跳板车，15FPS 决策循环
- 🔄 **自动循环** — 回合1赛车 → 回合2放弃 → 重复，MAA Pipeline 驱动
- 🖥️ **图形界面** — ttkbootstrap GUI，UAC 提权，物理手柄检测，实时日志，PEEP 实时预览，PEEP 实时预览
- 🛡️ **容错机制** — 导航失败自动重试（归位→重导航），光标丢失恢复，可中断睡眠

## 快速开始

### 环境要求

- Windows 10/11（需要 **管理员权限** 截图）
- Python 3.10+
- 游戏窗口分辨率 **1280×720**

### 安装

```bash
# 1. 克隆仓库
git clone https://github.com/ZRY233/MaaRacingAssistant.git
cd MaaRacingAssistant

# 2. 创建虚拟环境
python -m venv .venv
.venv\Scripts\activate

# 3. 安装依赖
pip install -r requirements.txt

# 4. 运行
python main.py
```

或双击 `gui.py` 启动图形界面（自动申请管理员权限）。

### 启动顺序

1. 打开《巅峰极速》到主界面
2. 运行 `python main.py`（或双击 gui.py）
3. 程序自动连接窗口 → 归位 → 导航 → 进入活动循环

## 项目结构

```
d:\maaracing_assistant/
├── main.py                # 主程序：YOLO + Pipeline + 归位 + 导航 + 日志
├── gui.py                 # 图形界面（ttkbootstrap + UAC 提权）
├── HANDOVER.md            # AI 助手上下文文档（详尽的开发记录）
├── CLAUDE.md              # Claude 项目配置
├── README.md              # 本文件
├── requirements.txt       # 依赖
├── assets/
│   ├── model/
│   │   └── yolov8n_coins_cars.onnx   # YOLO ONNX 模型（3 类）
│   ├── resource/
│   │   ├── image/                     # 模板图片（归位 + 导航）
│   │   └── pipeline/
│   │       └── tasks.json            # MAA Pipeline 流程定义
│   └── icon.ico
├── config/
│   └── maa_option.json               # MAA 配置
├── tools/
│   ├── train.py          # YOLO 训练脚本
│   └── dataset.yaml      # 数据集配置
└── logs/                 # 运行日志（自动生成）
```

## 技术栈

| 组件 | 用途 |
|------|------|
| [MAA Framework](https://github.com/MaaAssistantArknights/MaaFramework) 5.11.1 | UI 流程编排 + 窗口控制 |
| YOLOv8 + ONNX Runtime | 实时视觉识别（3类：coin / car / bonus_car） |
| vgamepad | 虚拟 Xbox 手柄模拟 |
| OpenCV | 模板匹配（归位 + 导航页面验证） |
| ttkbootstrap | GUI 界面 |

## Pipeline 流程

```
回合1比赛(RacingLoop) → 回合1结束 → 回合2准备 → 确认放弃（导航由 Python 主循环驱动）
```

## 决策优先级（赛车中）

```
0️⃣ 跳板车(bonus_car)  → 对准撞上去（加分）
1️⃣ 障碍车(car)        → 躲避（3 车道判断）
2️⃣ 金币(coin)         → 吃（选最近的）
3️⃣ 无目标             → 直行
```

## 开发指南

详细的技术文档、已知坑点、API 速查见 [HANDOVER.md](HANDOVER.md)。

## 致谢

本项目受益于以下 AI 模型的协作开发：

- **DeepSeek V4** - 代码逻辑优化与问题排查
- **Kimi K2.6** - 架构设计实现与接口规范
- **GLM 4.5 Air** - 代码质量审查与改进建议

感谢这些优秀的 AI 模型在开发过程中的技术支持！

## 许可证

MIT
