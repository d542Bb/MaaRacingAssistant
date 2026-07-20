<p align="center">
  <img src="assets/mra_icon.jpg" width="128" alt="MaaRacingAssistant logo">
</p>

<h1 align="center">MaaRacingAssistant</h1>

<p align="center">
  <em>《巅峰极速》"极速狂飙"活动自动化工具 —— MAA Framework × YOLOv8 × vgamepad</em>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11+-blue?logo=python" alt="Python">
  <img src="https://img.shields.io/badge/MaaFramework-5.11.1-green" alt="MaaFramework">
  <img src="https://img.shields.io/badge/YOLOv8-ONNX-orange" alt="YOLO">
  <img src="https://img.shields.io/badge/license-MIT-blue" alt="License">
  <img src="https://img.shields.io/badge/status-development-yellow" alt="Status">
</p>

---

> [!WARNING]
> ⚠️ **账号风险与合规声明**
>
> 本项目为游戏自动化技术研究与学习项目，仅用于编程技术、计算机视觉领域的交流与教学演示。
>
> 使用本工具操作游戏账号存在违反《巅峰极速》用户协议的风险，可能导致账号处罚、封禁等后果，所有使用后果由使用者自行承担，项目开发团队不承担任何相关责任。
>
> 请严格遵守游戏规则与相关法律法规，禁止将本项目用于商业牟利、非法批量操作等违规用途。

> [!IMPORTANT]
> 🚧 **项目开发状态说明**
>
> 当前项目处于活跃开发阶段，尚未完成全部规划功能。
>
> 现有版本可能存在逻辑缺陷、兼容性问题与不稳定表现，部分模块仍在迭代优化中，不建议作为生产级工具直接使用。如遇运行异常，可提交 Issue 反馈或参考开发文档自行调试。

---

## 目录

- [概述](#概述)
- [功能特性](#功能特性)
- [环境要求](#环境要求)
- [快速开始](#快速开始)
- [使用说明](#使用说明)
- [项目结构](#项目结构)
- [技术栈](#技术栈)
- [工作流程](#工作流程)
- [开发指南](#开发指南)
- [许可证](#许可证)

---

## 概述

**MaaRacingAssistant** 是一款基于计算机视觉与虚拟手柄控制的游戏自动化工具，采用 **MAA Framework** 进行流程编排，**YOLOv8** 实现实时目标检测，**vgamepad** 模拟 Xbox 手柄操作，实现《巅峰极速》"极速狂飙"活动的全自动循环。

**核心链路：** 启动归位 → 光标导航进入活动 → 回合1 YOLO 自动驾驶 → 回合2放弃 → 循环

---

## 功能特性

### 🎮 虚拟手柄控制
- vgamepad 模拟 Xbox 360 手柄，支持摇杆精确移动 + 按键操作
- 独立死区算法 + 自适应微调，支持按钮级别精度的光标导航
- 物理手柄检测机制：自动检测真实手柄，阻止冲突操作
- 自动生命周期管理：创建/销毁/复位，避免驱动偏置残留

### 👁️ 视觉识别系统
- **YOLOv8 目标检测** — 实时识别 3 类目标（金币 / 障碍车 / 跳板车）
- **GPU 加速推理** — onnxruntime-directml，DirectX 12 后端，~3.7ms/帧（RTX 4060）
- **跳帧优化** — 每 3 帧推理一次，中间帧复用缓存，GPU 负载降至 1/3
- **模板匹配导航** — 多尺度彩色模板匹配（范围 0.5–1.8×），支持页面验证与状态检测

### 🧭 光标追踪导航
- 3 步递进导航：极速狂飙入口 → 开始挑战 → 寻找对手
- 配置化按钮管理：ButtonDef 一行定义一个按钮（坐标 + 模板 + 验证逻辑）
- 假光标过滤：面积硬过滤 + 双中心面积评分 + 静止拉黑（≥3 帧不动排除）
- 自适应停止：距离感知的收缩速度 + 微调脉冲（< 35px 时 25ms）

### 🏎️ 自动驾驶决策
- **4 级决策优先级：** 跳板车 → 障碍车避让 → 吃金币 → 直行
- 3 车道避让算法：判断障碍物横向偏移 + 相邻车道占用
- 实时日志输出：每帧显示检测结果与决策

### 🖥️ 图形界面
- ttkbootstrap 现代化 GUI，窗口可拖拽缩放
- UAC 管理员权限自动提权
- 物理手柄检测弹窗阻止运行
- 实时日志显示（分级过滤）
- PEEP 实时预览窗口：独立线程显示调试帧 + YOLO 检测框 + 模板匹配定位
- 调试模式开关：每帧截图存盘，用于问题分析

### 🛡️ 容错机制
- 导航失败自动重试（归位 → 重新导航）
- RacingLoop 异常检测：< 3 秒判定异常，最多 3 次重试
- 对局层/大厅层隔离：进入对局后不回退到大厅
- 可中断睡眠：停止信号 100ms 内响应

---

## 环境要求

| 项目 | 要求 |
|------|------|
| 操作系统 | Windows 10/11 **64-bit** |
| 权限 | **管理员权限**（窗口截图必需） |
| Python | 3.10+（推荐 3.11） |
| 游戏窗口分辨率 | 1280×720 |
| GPU（可选） | NVIDIA / AMD 独立显卡（DirectML 加速） |

---

## 快速开始

### 1. 克隆仓库

```bash
git clone https://github.com/ZRY233/MaaRacingAssistant.git
cd MaaRacingAssistant
```

### 2. 创建虚拟环境

```bash
python -m venv .venv
.venv\Scripts\activate
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

### 4. 启动

```bash
python run.py
```

或双击 `run.py` 直接启动图形界面（自动申请管理员权限）。

---

## 使用说明

### 启动顺序

1. 打开《巅峰极速》到游戏主界面（窗口分辨率 **1280×720**）
2. 以 **管理员身份** 运行 `python run.py`
3. 在 GUI 中选择起始阶段（支持断点模式），点击「开始运行」
4. 程序自动执行：连接窗口 → 归位 → 导航 → 活动循环

### 断点模式

GUI 支持从指定阶段开始执行，便于调试：

| 阶段 | 说明 |
|------|------|
| 归位 | 从游戏主界面模板定位开始 |
| 导航一 | 极速狂飙活动入口按钮 |
| 导航二 | 开始挑战按钮（成功标记进入对局） |
| 导航三 | 寻找对手按钮 |
| 商店弹窗处理 | 关闭赛前商店弹窗 |
| 确认上阵 | 确认赛车阵容 |
| 比赛 | 直接进入 RacingLoop 自动驾驶 |

### 调试功能

- **PEEP 实时预览**：开启独立线程显示每帧调试画面（YOLO 检测框 + 模板匹配定位）
- **DEBUG 存盘模式**：每帧标注截图保存到 `debug/navigate/`，用于分析光标识别和 YOLO 检测问题

---

## 项目结构

```
d:\maaracing_assistant/
├── run.py                               # 快捷入口
├── pyproject.toml                       # 项目配置（pip install -e .）
├── requirements.txt                     # 依赖清单
├── CLAUDE.md                            # AI 助手项目配置
├── README.md                            # 本文件
│
├── maaracing_assistant/                 # 📦 应用包
│   ├── __init__.py                      # 版本号
│   ├── __main__.py                      # 模块入口（python -m）
│   ├── gui.py                           # 图形界面（ttkbootstrap）
│   ├── controller.py                    # 总控编排（MAA 框架集成 + 导航调度）
│   ├── racing_loop.py                   # 自动驾驶循环（YOLO + 手柄控制）
│   ├── navigation.py                    # 光标导航引擎（ButtonDef + 模板匹配）
│   ├── yolo_detector.py                 # YOLO ONNX 推理封装
│   ├── logger.py                        # 文件 + 内存日志系统
│   ├── pipeline_logger.py               # MAA Pipeline 事件监听
│   ├── window_utils.py                  # 窗口查找 + XInput 手柄检测
│   ├── debug.py                         # 调试可视化 + PEEP 实时预览
│   └── opencv_utf8_patch.py             # OpenCV 中文路径补丁
│
├── assets/
│   ├── model/
│   │   └── yolov8n_coins_cars.onnx      # YOLO 模型（3 类）
│   ├── resource/
│   │   ├── image/                       # 模板图片（归位 + 导航 + 结束检测）
│   │   └── pipeline/
│   │       └── tasks.json               # MAA Pipeline 定义
│   └── icon.ico                         # 应用图标
│
├── config/
│   └── maa_option.json                  # MAA 配置
│
├── dataset/                             # YOLO 训练数据集
│   ├── images/train/   (150 张)
│   ├── images/val/     (38 张)
│   ├── labels/train/   (150 个)
│   └── labels/val/     (38 个)
│
├── tools/
│   ├── train.py                         # YOLO 训练脚本
│   └── dataset.yaml                     # 数据集配置
│
├── docs/
│   ├── HANDOVER.md                      # 完整技术交接文档
│   └── update_log.md                    # 版本更新日志
│
└── logs/                                # 运行日志（自动生成）
```

---

## 技术栈

| 分类 | 组件 | 用途 |
|------|------|------|
| **流程编排** | [MAA Framework](https://github.com/MaaAssistantArknights/MaaFramework) 5.11.1 | UI 流程编排 + 窗口截图 + Pipeline 驱动 |
| **视觉识别** | YOLOv8 + ONNX Runtime (DirectML) | 实时目标检测，3 类（coin / car / bonus_car） |
| **虚拟手柄** | [vgamepad](https://github.com/yannbouteiller/vgamepad) 0.1.1 | Xbox 360 手柄模拟，摇杆 + 按键控制 |
| **图像处理** | OpenCV 4.x | 模板匹配、图像预处理、可视化标注 |
| **物理手柄检测** | XInput API (`xinput1_4.dll`) | 检测物理手柄存在，避免操作冲突 |
| **GUI 框架** | ttkbootstrap 1.x | 现代化主题窗口，拖拽缩放 + 日志实时显示 |

---

## 工作流程

### 全局循环

```
┌──────────────────────────────────────────────────┐
│                  大厅层                            │
│  ① 归位 ──→ ② 导航一 ──→ ③ 导航二（开始挑战）     │
│                          │                       │
│                     ┌────┘                       │
│                     ▼                            │
│                  对局层                            │
│  ④ 导航三（寻找对手）→ ⑤ 商店弹窗 → ⑥ 确认上阵     │
│                     │                            │
│                     ▼                            │
│  ⑦ RacingLoop 自动驾驶 ←──┐                      │
│                     │      │                      │
│                     ▼      │                      │
│  ⑧ 结束处理（弹窗/结算）──┘                      │
└──────────────────────────────────────────────────┘
```

### 赛车决策优先级

| 优先级 | 目标 | 行为 |
|--------|------|------|
| 0️⃣ | 跳板车（bonus_car） | 对准直冲（加分） |
| 1️⃣ | 障碍车（car） | 3 车道避让算法躲避 |
| 2️⃣ | 金币（coin） | 选取最近金币方向 |
| 3️⃣ | 无目标 | 直行 |

---

## 开发指南

- 完整的技术文档、已知坑点、API 速查、决策记录见 [docs/HANDOVER.md](docs/HANDOVER.md)
- AI 助手项目配置见 [CLAUDE.md](CLAUDE.md)
- 版本历史见 [docs/update_log.md](docs/update_log.md)

---

## 许可证

[MIT](LICENSE) © ZRY