# MaaRacingAssistant — Code Wiki（主文档）

> 《巅峰极速》"极速狂飙"活动自动化工具 —— 完整代码架构文档
>
> **文档导航（Code Wiki 已按功能域拆分，共 3 份）**：
> - **本文件（主文档）**：架构总览 / 目录结构 / 主程核心模块 / 依赖 / 运行流程 / 配置常量 / 开发调试 / 主程坑点 / GUI 选型
> - [CODE_WIKI_RACING.md](CODE_WIKI_RACING.md)：**赛车域**（RacingLoop 决策算法 / RacingModule / 赛车参数 / 赛车坑点）
> - [CODE_WIKI_TREASURE.md](CODE_WIKI_TREASURE.md)：**鉴宝域**（treasure_* 全模块 / 出价策略 / 鉴宝模板 / 鉴宝坑点）

---

## 目录

1. [项目概述](#1-项目概述)
2. [整体架构](#2-整体架构)
3. [目录结构详解](#3-目录结构详解)
4. [核心模块说明](#4-核心模块说明)
5. [关键类与函数索引](#5-关键类与函数索引)
6. [模块依赖关系](#6-模块依赖关系)
7. [运行流程详解](#7-运行流程详解)
8. [关键配置与常量](#8-关键配置与常量)
9. [开发与调试](#9-开发与调试)
10. [已知坑点与注意事项](#10-已知坑点与注意事项)
11. [GUI 宿主选型](#11-gui-宿主选型winui-3-定案)
附录：类速查表

---

## 1. 项目概述

### 1.1 项目定位

MaaRacingAssistant 是一款基于**计算机视觉**与**虚拟手柄控制**的游戏自动化工具，专门用于《巅峰极速》游戏的"极速狂飙"活动全自动循环刷分。

### 1.2 核心技术栈

| 层级 | 技术组件 | 用途 |
|------|----------|------|
| 流程编排 | MAA Framework 5.11.1 | UI 流程编排 + 窗口控制 + 截图 |
| 视觉识别 | YOLOv8 + ONNX Runtime (DirectML) | 实时目标检测（金币/障碍车/跳板车） |
| 手柄模拟 | vgamepad 0.1.x | Xbox 360 虚拟手柄，摇杆精确控制 |
| 图像处理 | OpenCV 4.x | 模板匹配、Hough 直线检测、可视化 |
| GUI 框架 | WinUI 3 (Windows App SDK 1.8) + WebView2 | 原生窗口 + HTML 三 Tab 前端 |
| 系统交互 | XInput API (Win32) | 物理手柄检测，避免冲突 |

### 1.3 核心工作流

```
启动归位 → 光标导航进入活动 → 回合1 YOLO 自动驾驶吃金币 → 回合2放弃 → 循环
```

---

## 2. 整体架构

### 2.1 分层架构图

```
┌─────────────────────────────────────────────────────────────────┐
│               GUI 层 (apps/mra_shell/)                          │
│        WinUI 3 窗口 + HTML 前端 + sidecar 进程托管                │
├─────────────────────────────────────────────────────────────────┤
│                      主控层 (controller.py)                     │
│          MAA 框架绑定 + 阶段编排 + 导航调度 + 手柄生命周期        │
├─────────────────────────────────────────────────────────────────┤
│  ┌─────────────────────┐  ┌──────────────────────────────────┐  │
│  │  导航引擎            │  │  赛车自动驾驶循环                 │  │
│  │  (navigation.py)    │  │  (racing_loop.py)                │  │
│  │  - 模板匹配         │  │  - YOLO 推理调度                 │  │
│  │  - 光标识别追踪     │  │  - 标线检测 (Hough)              │  │
│  │  - 摇杆导航控制     │  │  - 防撞三区体系                  │  │
│  │  - 归位/弹窗处理    │  │  - 贪婪决策算法                  │  │
│  │  - 假光标过滤       │  │  - 前馈瞄准 + 车道保持           │  │
│  └─────────────────────┘  └──────────────────────────────────┘  │
├─────────────────────────────────────────────────────────────────┤
│  ┌─────────────────────┐  ┌──────────────────────────────────┐  │
│  │  YOLO 检测器        │  │  调试可视化                      │  │
│  │  (yolo_detector.py) │  │  (debug.py)                      │  │
│  │  - ONNX Runtime     │  │  - PEEP 实时预览窗口             │  │
│  │  - DirectML GPU     │  │  - 每帧截图标注存盘              │  │
│  │  - per-class NMS    │  │  - 导航/赛车双模式渲染           │  │
│  └─────────────────────┘  └──────────────────────────────────┘  │
├─────────────────────────────────────────────────────────────────┤
│                      基础设施层                                  │
│  ┌────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────┐  │
│  │ 日志系统    │ │ Pipeline日志 │ │ 窗口/手柄检测 │ │ UTF8补丁  │  │
│  │ (logger.py)│ │(pipeline_    │ │(window_utils)│ │(opencv_  │  │
│  │            │ │  logger.py)  │ │              │ │ utf8_    │  │
│  │            │ │              │ │              │ │ patch.py)│  │
│  └────────────┘ └──────────────┘ └──────────────┘ └──────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 阶段流程

项目采用**大厅层/对局层**双层循环结构，防止异常回退：

```
┌──────────────────────────────────────────────────┐
│                  大厅层                            │
│  ① 归位 ──→ ② 导航一(极速狂飙入口)               │
│                          │                       │
│                     ┌────┘  (失败回大厅重试)      │
│                     ▼                            │
│                  对局层                            │
│  ③ 导航二(开始挑战) → 标记进入对局               │
│      │                                           │
│      ▼                                           │
│  ④ 导航三(寻找对手) → ⑤ 商店弹窗 → ⑥ 确认上阵    │
│                     │                            │
│                     ▼                            │
│  ⑦ RacingLoop 自动驾驶 ←──┐ (异常重试≤3次)       │
│                     │      │                      │
│                     ▼      │                      │
│  ⑧ 结束处理 ───────────────┘                      │
│      │                                           │
│      └──→ 回到③（对局层循环）                    │
└──────────────────────────────────────────────────┘
```

> 流程编排已模块化：`modules/racing_module.py`（RacingModule）承载上述流程，`controller.py` 保留主控调度与活动分发，详见 [赛车文档 §2](CODE_WIKI_RACING.md)。

---

## 3. 目录结构详解

```
├── MaaRacingAssistant.lnk                    # 本机 GUI 启动快捷方式（指向编译产物，不提交）
├── pyproject.toml                            # setuptools-scm 包配置
├── requirements.txt                          # Python 依赖清单
├── AGENTS.md                                 # AI 助手项目配置
├── README.md                                 # 用户说明文档
│
├── maaracing_assistant/                      # 📦 核心应用包
│   ├── __init__.py                           # 版本号导出（setuptools-scm 自动生成）
│   ├── __main__.py                           # python -m 入口
│   ├── controller.py                         # 主控编排器（MAA 集成 + 阶段调度）
│   ├── navigation.py                         # 光标导航引擎（ButtonDef + 模板匹配 + 摇杆控制）
│   ├── racing_loop.py                        # 赛车自动驾驶循环（YOLO + 决策 + 手柄）
│   ├── yolo_detector.py                      # YOLOv8 ONNX 推理封装（DirectML/GPU）
│   ├── sidecar.py                           # JSONL RPC 业务后端（供 mra_shell.exe 托管）
│   ├── modules/                             # 活动模块（racing_module / treasure_module）
│   ├── debug.py                              # 调试可视化（PEEP 实时预览 + 截图标注）
│   ├── logger.py                             # 文件+内存双写日志系统
│   ├── pipeline_logger.py                    # MAA Pipeline 事件监听日志
│   ├── window_utils.py                       # 窗口查找 + XInput 物理手柄检测
│   └── opencv_utf8_patch.py                  # OpenCV 中文路径 monkey-patch
│
├── assets/                                   # 资源文件
│   ├── model/
│   │   └── model.onnx                        # YOLOv8 模型（3类：coin/car/bonus_car）
│   ├── resource/
│   │   ├── image/                            # 模板匹配图片
│   │   │   ├── settings_page_template.jpg    # 设置页面（归位用）
│   │   │   ├── activity_page_template.jpg    # 活动页面
│   │   │   ├── find_opponent_template.jpg    # 寻找对手页面
│   │   │   ├── round1_end_template.jpg       # 回合1结束画面
│   │   │   ├── store_popup_template.jpg      # 商店弹窗
│   │   │   └── treasure/                     # 鉴宝模板（详见鉴宝文档 §8）
│   │   └── pipeline/
│   │       └── tasks.json                    # MAA Pipeline 任务定义（备用）
│   ├── icon.ico                              # 应用图标
│   └── mra_icon.png                          # README 展示图标
│
├── config/
│   └── maa_option.json                       # MAA 框架配置
│
├── apps/
│   └── mra_shell/                            # 🖥️ 正式 GUI（WinUI 3 shell + WebView2）
│       ├── MainWindow.xaml(.cs)              # 窗口 + sidecar 生命周期 + 消息转发
│       ├── PythonSidecar.cs                  # JSONL transport 契约实现
│       ├── App.xaml(.cs)                     # 应用入口（DISABLE_XAML_GENERATED_MAIN）
│       └── frontend/                         # HTML 前端（三 Tab：控制/调试/关于）
│           ├── index.html                    # 页面结构 + 元素 id
│           ├── style.css                     # 纯 CSS 样式（无 CDN）
│           └── app.js                        # mra.call RPC + 页面交互逻辑
│
├── archive/                                  # 归档（旧 ttkbootstrap GUI、历史 spike）
│   └── legacy_gui/                           # 已废弃：gui/（ttkbootstrap）、gui_webview/
│
├── tools/                                    # 开发工具脚本（按用途分组）
│   ├── mouse_overlay.py                      # 独立 Overlay 工具（屏幕十字准星）
│   ├── treasure_debug_studio/                # 鉴宝 ROI 可视化校准调试台
│   ├── training/                             # 模型训练与数据准备
│   │   ├── train.py                          # YOLO 训练 + ONNX 导出脚本
│   │   ├── dataset.yaml                      # 数据集类别配置
│   │   └── auto_label.py                     # 自动标注工具
│   └── debug/                                # 诊断 / 回放 / 模板开发
│       ├── diagnose_treasure.py              # 鉴宝调试诊断
│       ├── test_stage_detector_replay.py     # 阶段检测回放验证
│       └── extract_treasure_templates.py     # 模板裁剪提取
│
├── dataset/                                  # YOLO 训练数据集（188张）
│   ├── images/train/ (150张)
│   ├── images/val/ (38张)
│   ├── labels/train/ (150个)
│   └── labels/val/ (38个)
│
├── docs/
│   ├── update_log.md                         # 版本更新日志
│   ├── CODE_WIKI.md                          # 本文档（主文档）
│   ├── CODE_WIKI_RACING.md                   # Code Wiki · 赛车域
│   └── CODE_WIKI_TREASURE.md                 # Code Wiki · 鉴宝域
│
├── logs/                                     # 运行日志（自动生成，gitignore）
└── debug/                                    # 调试截图（自动生成，gitignore）
```

---

## 4. 核心模块说明

> 本文档覆盖**主程核心模块**。按功能域拆分：
> - **赛车域**（racing_loop / racing_module / racing_renderer）→ [CODE_WIKI_RACING.md](CODE_WIKI_RACING.md)
> - **鉴宝域**（treasure_module / treasure_detector / treasure_ocr / treasure_renderer / bid_strategy）→ [CODE_WIKI_TREASURE.md](CODE_WIKI_TREASURE.md)

### 4.1 [controller.py](file:///d:/maaracing_assistant/maaracing_assistant/controller.py) — 主控编排器

**职责**：
- MAA Framework 初始化与绑定（Tasker / Resource / Win32Controller）
- 7阶段流程编排与断点续跑
- 虚拟手柄生命周期管理（懒创建/复位/销毁）
- 双模式截图（MAA PostScreencap + ctypes BitBlt 备用）
- 大厅层/对局层双层循环隔离
- RacingLoop 异常重试机制（<3秒判定异常，最多3次）

**核心类**：`MaaRacingAssistantController`

**关键属性**：
| 属性 | 类型 | 说明 |
|------|------|------|
| `STAGE_ORDER` | list[str] | 7个阶段名称（GUI断点选择用） |
| `tasker` | Tasker | MAA 任务执行器 |
| `resource` | Resource | MAA 资源包 |
| `controller` | Win32Controller | MAA 窗口控制器 |
| `racing_loop` | RacingLoop | 自动驾驶实例 |
| `nav` | Navigation | 光标导航引擎 |
| `_gpad` | VX360Gamepad | 虚拟手柄（懒创建，复用不销毁） |
| `_in_match` | bool | 是否已进入对局（防止异常回退大厅） |
| `debug` | NavigationDebugger | 调试器实例 |

---

### 4.2 [navigation.py](file:///d:/maaracing_assistant/maaracing_assistant/navigation.py) — 光标导航引擎

**职责**：
- 多尺度彩色模板匹配（0.5x–1.8x）
- 白色圆形光标识别与追踪（几何形状+面积评分+静止拉黑）
- 左摇杆精确移动控制（独立死区 + 自适应速度 + 微调脉冲）
- 归位（Homing）：持续按B直到识别设置页面
- 商店弹窗自动关闭
- 盲操兜底（光标丢失时按估算方向推杆）

**核心类**：

#### `ButtonDef`（按钮配置类）
纯配置数据类，一行定义一个导航按钮：

| 属性 | 类型 | 说明 |
|------|------|------|
| `name` | str | 按钮名称（日志显示） |
| `pct` | tuple(float,float) | 屏幕百分比位置 (x%, y%)，基于1280×720 |
| `page_template` | str | 验证模板文件名（空=无验证） |
| `template_should_match` | bool | True=匹配到模板算成功；False=模板消失算成功 |
| `close_threshold` | int | 光标到按钮的距离阈值（像素，按A条件） |

**预定义按钮**（在 controller.start() 中）：
- `BTN_极速狂飙入口`: (0.880, 0.720), activity_page_template, True, 50px
- `BTN_开始挑战`: (0.855, 0.898), activity_page_template, False, 12px
- `BTN_寻找对手`: (0.804, 0.753), find_opponent_template, False, 25px
- `BTN_确认上阵`: (0.823, 0.931), 无模板, True, 25px

#### `Navigation`（导航控制类）

**光标识别算法** (`_find_cursor_by_shape`)：
1. 灰度阈值 >185 + S通道<30 过滤非白色高亮
2. 轮廓检测 → 面积过滤（240–550px²）→ 圆度过滤（边缘0.65/中间0.82）
3. **双中心面积评分**：常态~310px² / 选中态~420px²，取最高分
4. **假光标静止拉黑**：推摇杆时连续≥3帧不动的候选排除
5. **运动一致性评分**：与摇杆方向对齐的候选加分
6. 最终评分 < 0.70 判定为无光标

**摇杆移动算法** (`_move_cursor_to_target`)：
- 距离分级：远(>20%)→0.2s满速 / 中(>10%)→0.1s中速 / 近(>5%)→0.08s低速 / 近距微调→25ms脉冲
- **独立死区**：非零轴值<4260（MAX_AXIS的53%）时自动抬升到死区阈值
- 刹车时间：近距80ms / 远距50ms
- stop_distance 自适应：`max(8, close_th × 0.55)`

**按A验证逻辑** (`_press_and_verify`)：
- 无验证模板 → 直接成功
- 有模板 → 匹配验证
- 验证失败 → 收缩阈值 `max(5, close_th × 0.65)` 重试
- 备选判据：光标面积下降>100 或 光标消失

---

### 4.3 [yolo_detector.py](file:///d:/maaracing_assistant/maaracing_assistant/yolo_detector.py) — YOLO 检测器

**职责**：
- ONNX Runtime 会话初始化（DirectML优先 → CUDA → CPU）
- 图优化 + DirectML 内核缓存
- 640×640 letterbox 预处理
- YOLOv8 输出解析（xywh → xyxy）
- **per-class NMS**：按类别分别做非极大值抑制，避免跨类压制（如car压掉bonus_car）
- 双阈值输出：正式检测（高置信度，供决策用）+ 全量低阈值检测（供debug可视化）

**核心类**：`YOLODetector`

**类别映射**：
| 类别ID | 名称 | 说明 | 置信度阈值 |
|--------|------|------|-----------|
| 0 | coin | 金币 | 0.35 |
| 1 | car | 障碍车 | 0.35 |
| 2 | bonus_car | 跳板车（奖励车） | 0.35 |

**性能指标**（参考 RTX 4060）：~3.7ms/帧，跳帧后GPU负载降至1/3

---

### 4.4 [mra_shell](file:///d:/maaracing_assistant/apps/mra_shell) — GUI 宿主（WinUI 3 + HTML 前端）

> v0.13.0 起 GUI 定案为 WinUI 3 shell + WebView2 HTML 前端（详见 §11）。旧 ttkbootstrap GUI（`gui.py` MRAGUI）已归档至 `archive/legacy_gui/`，以下历史记录仅供参考。

**进程模型**：
- `mra_shell.exe`（C# WinUI 3）：唯一 GUI，只做窗口 + sidecar 进程生命周期 + 消息转发
- `sidecar.py`（Python）：JSONL RPC 业务后端（stdin=request / stdout=response / stderr=日志）
- 前端 HTML 通过 `window.chrome.webview.postMessage` → C# → Python 通信，封装为 `mra.call(method, params)`

**前端文件**（[frontend/](file:///d:/maaracing_assistant/apps/mra_shell/frontend)）：
| 文件 | 职责 |
|------|------|
| `index.html` | 三 Tab 页面结构（控制面板/调试/关于），所有 UI 元素 id 在此定义 |
| `style.css` | 纯 CSS 设计 token + 组件样式（无 CDN，WebView2 离线可用） |
| `app.js` | 通信层 + Tab 切换 + 日志轮询 + 调试页开关/截图方式交互 |

**窗口细节**：自定义标题栏 52px（进入 drag rect，右侧留 140px 给系统按钮）、最小尺寸 1000×700、系统按钮失焦配色、icon.ico。

**启动流程**：
1. 双击根目录 `MaaRacingAssistant.lnk`（定位 `mra_shell.exe`，exe manifest 自动 UAC 提权）
2. shell 启动 Python `sidecar.py`，建立 JSONL 双向管道
3. WebView2 加载 `frontend/index.html`，前端 `mra.call` 初始化状态
4. 用户操作 → 前端 RPC → sidecar → 模块执行

#### 4.4.1 旧 ttkbootstrap GUI 历史记录（已归档，仅存档）

> 原 `gui.py` MRAGUI（ttkbootstrap）的改进历史，代码已移至 `archive/legacy_gui/gui/`。

| 改进项 | 说明 | 核心实现 |
|--------|------|----------|
| 窗口可拖拽可调大小 | 原 `resizable(False, False)` → 改为 `resizable(True, True)` | `gui.py MRAGUI.__init__()` toplevel / root 配置 |
| 安全最小尺寸保护 | `minsize(480, 400)`，防止窗口缩小到 UI 元素互相重叠不可点 | 同上 init 阶段设置 |
| 日志按级别过滤 | `logger.get_lines(min_level=...)` 拉取；GUI 默认只显示 INFO/WARNING/ERROR 三级；DEBUG/TRACE 仅文件 + 显式勾选 DEBUG 存盘开关时显示 | `gui.py _poll_logs()`；配合 §4.6.1 日志分级约定 |
| 物理手柄检测阻止运行 | 非记录模式下调用 `has_physical_controller()` → 返回 True 时阻止 "开始" 并弹对话框提醒拔手柄 | `gui.py _on_start_clicked()` 前置检查；§9.6 / §4.7 |
| 弹窗图标修正（不继承父窗口）| `messagebox.showerror` 默认丢 root.ico → 改用 `tk.Toplevel` + 手动 `dlg.iconbitmap(icon_path)` 设置独立应用图标；agents.md / §10 均有记录 | 物理手柄弹窗 / 模型缺失弹窗 / 连接失败弹窗 |

---

### 4.5 [debug.py](file:///d:/maaracing_assistant/maaracing_assistant/debug.py) — 调试可视化

**职责**：
- 两套渲染模式：全量存盘（enabled）/ 精简PEEP预览（peep_enabled）
- PEEP独立线程OpenCV窗口（~30fps刷新，锁保护最新帧）
- 导航模式标注：光标(红)/候选(绿)/拉黑(紫)/过滤(黑)/按钮(蓝)/模板(青)
- 赛车模式标注：YOLO框(金/红/紫)/透视车道线/远中近分区/HUD状态栏
- 同类别重叠框去重（避免虚线框堆叠）
- 文字带黑色阴影描边（保证任何背景可读性）

**核心类**：`NavigationDebugger`

**颜色约定**：
| 颜色 | BGR值 | 含义 |
|------|-------|------|
| 🔴 红 | (0,0,220) | 选中的光标 / 障碍车car |
| 🟡 金 | (0,215,255) | 金币coin / 按钮目标 |
| 🟣 紫 | (220,0,220) | 跳板车bonus_car / 拉黑候选 |
| 🟢 绿 | (0,200,0) | 入围光标候选 / 车道中线 |
| 🔵 青 | (255,255,0) | 模板匹配框 / 标线 / 距离分区线 |
| ⚫ 黑 | (0,0,0) | 被硬过滤的轮廓 |
| 🟧 橙 | (0,140,255) | 左标线边缘散点 |
| 🔵 蓝 | (255,140,0) | 右标线边缘散点 |

**赛车HUD内容**：
- 左上：帧号 + raw/filt检测统计 + 各类数量
- 底部：摇杆位置条（←/→，彩色点）+ 数值
- 底部居中：决策原因（彩色）+ 详细说明
- 底部摇杆上方：±stop_zone 死区宽度条（半透明绿填充 + 边界细线 + 中心线）
- 右上：前馈调试信息（off/stop/dx/dy/moving/in_center/reason）
- 右上第四行：ff_extra 预见性衰减原因说明（[提前收敛…]/[近区回摆…]/[无预见性衰减]）
- 画面中部：CENTER_L / CENTER_R 半透明红竖线（中心区边界，L2c/R2c 标签）

**v0.12.0 新增 HUD 字段**：
- `dy`: 目标纵向接近速度（px/帧），右上第二行
- `ff_extra`: 预见性衰减原因（提前收敛ETA/近区回摆），右上第四行
- 中心区竖线：`_draw_racing_zones` 中追加，与透视车道线共享 overlay
- 死区条：`_draw_racing_hud` 底部，半宽 = stop_zone × w/2，α=0.30

---

### 4.6 [logger.py](file:///d:/maaracing_assistant/maaracing_assistant/logger.py) — 日志系统

**职责**：
- 内存+文件双写日志
- 5个日志级别：TRACE < DEBUG < INFO < WARNING < ERROR
- GUI 默认只显示 INFO 及以上
- 按时间戳命名日志文件（`MRA_YYYYMMDD_HHMMSS.log`）
- 级别过滤提取（get_lines）

**全局单例**：`logger = Logger(logs_dir)`

#### 4.6.1 日志分级速查

| 级别 | 用途 | 典型示例 |
|------|------|----------|
| TRACE | 超细节开发追踪 | 中间变量、帧级内部状态、循环内计数器步进 |
| DEBUG | 详细调试信息 | 模板匹配各尺度置信度结果、保存调试图路径、第 N 次按 B、摇杆方向值（lx,ly）、死区判定细节 |
| INFO | 关键业务里程碑 | 归位完成、返回主界面、开始循环、本轮完成、导航按钮点击成功、RacingLoop启动/结束、决策最终输出（金币/避让/直行） |
| WARNING | 警告但流程继续 | 截图快速方式失败降级MAA、归位超时、模板不存在、按钮未找到光标丢失、基准测试发现YOLO离群值（P95/P90>1.8×）|
| ERROR | 错误需关注 | 模板加载失败、连接窗口失败、Pipeline异常、模型文件不存在、手柄创建失败、连续重试耗尽 |

> **约定**：所有可继续运行的降级/兜底必须打 WARNING（不能静默）。不能恢复的故障打 ERROR 并配合 stop。

---

### 4.7 [window_utils.py](file:///d:/maaracing_assistant/maaracing_assistant/window_utils.py) — 窗口与手柄检测

**职责**：
- `find_game_hwnd()`: 查找游戏窗口（优先UnrealWindow类名 → 标题关键词 → PID）
- `has_physical_controller()`: XInput API 检测物理手柄（xinput1_4/1_3/9_1_0）
- `hwnd_from_pid()`: EnumWindows 回调按PID找窗口句柄

**窗口查找关键词**："巅峰极速"、"g112"、"Racing Master"

**物理手柄检测**：遍历XInput端口0-3，`XInputGetState(i, buf) == 0` 表示已连接

---

### 4.8 [pipeline_logger.py](file:///d:/maaracing_assistant/maaracing_assistant/pipeline_logger.py) — MAA Pipeline日志

**职责**：继承 `ContextEventSink`，监听Pipeline节点识别/动作事件，输出中文友好日志。

---

### 4.9 [opencv_utf8_patch.py](file:///d:/maaracing_assistant/maaracing_assistant/opencv_utf8_patch.py) — 中文路径补丁

**职责**：Monkey-patch `cv2.imread`/`cv2.imwrite`，支持中文Windows路径。ASCII路径走原生API，中文路径用 `np.frombuffer`/`cv2.imencode`+Python文件IO绕过。程序启动时import一次即全局生效。

---

### 4.10 [wgcap.py](file:///d:/maaracing_assistant/maaracing_assistant/wgcap.py) — WGC 持久化后台截图

**职责**：
- Windows Graphics Capture (WGC) 持久化后台截图，替代 MAA FramePool 的同步截图
- 零拷贝帧访问：WGC → D3D11 CopyResource → Map → memoryview → ndarray
- 独立捕获线程，回调驱动帧更新，业务线程无等待获取最新帧
- 客户区裁剪（排除标题栏/边框），支持窗口后台/遮挡/失焦时持续捕获
- 帧元数据追踪：frame_id, capture_ts_ns, source_timespan, frame_age

**核心类**：`WgcCapture`

**关键指标**（参考 RTX 4060）：
- `get_latest()` P50 ≈ 3μs（仅引用交换，无拷贝）
- 颜色转换（BGR→RGB）P50 ≈ 0.33ms
- 完整 `_cap()` P50 ≈ 0.5ms
- callback interval P50 ≈ 14ms（~70Hz 游戏帧率）
- 帧缓存重复率 ~48.5%（正常现象：consumer 比 producer 快）

**架构决策**：
- 生产默认后端 = `wgc_latest`，MAA FramePool = fallback 兼容
- 通过 `RacingLoop.capture_backend` 切换（`"wgc_latest"` / `"maa"`）
- 线程安全：锁内仅交换 Python 引用和整数，NumPy 操作在锁外
- 帧所有权：NativeMappedFrame → bytes → ndarray，回调结束后 ndarray 地址复用但仍安全

---

## 5. 关键类与函数索引

> 赛车域（RacingLoop）类速查见 [CODE_WIKI_RACING.md §1](CODE_WIKI_RACING.md)；鉴宝域（treasure_*）类速查见 [CODE_WIKI_TREASURE.md §7](CODE_WIKI_TREASURE.md)。

### 5.1 controller.MaaRacingAssistantController

| 方法 | 说明 |
|------|------|
| `__init__()` | 初始化项目路径、导航引擎、调试器 |
| `connect(record_mode)` | 连接游戏窗口、初始化MAA、注册RacingLoop自定义动作 |
| `start(start_from, record_mode)` | 主循环入口，支持断点续跑和记录模式 |
| `stop()` | 停止运行、中断Pipeline、销毁手柄 |
| `set_debug_mode(enabled)` | 切换DEBUG存盘模式 |
| `check_model()` | 检查YOLO模型文件是否存在 |
| `_get_gpad()` | 懒创建并返回虚拟手柄（复用，不销毁） |
| `_reset_gpad()` | 摇杆归零+按钮释放（不销毁） |
| `_destroy_gpad()` | 销毁虚拟手柄实例 |
| `_screencap()` | MAA截图（BGR→RGB），失败时自动降级到ctypes截图 |
| `_screencap_ctypes()` | Win32 GDI BitBlt截图备用方案 |
| `_interruptible_sleep(s)` | 可中断睡眠（每0.1s检查_running） |

### 5.2 navigation.ButtonDef

配置类，见4.2节。

### 5.3 navigation.Navigation

| 方法 | 说明 |
|------|------|
| `__init__(proj, debug, ctrl)` | 初始化，持有父控制器引用 |
| `homing()` | 归位：按B直到识别设置页面，再按B返回主界面 |
| `navigate_to_button(btn)` | 光标导航到按钮并按A，验证成功返回True |
| `handle_store_popup()` | 等待商店弹窗出现，按A关闭 |
| `_find_cursor_by_shape(img)` | 几何形状识别光标，返回(pos, circularity, area) |
| `_move_cursor_to_target(cursor, target, gpad)` | 摇杆移动光标到目标位置，返回是否到达 |
| `_press_and_verify(gpad, area, dist, btn)` | 按A并验证页面切换，自动收缩阈值 |
| `_ensure_cursor(gpad)` | 光标丢失时四方向推杆搜索 |
| `_blind_move(gpad, last, target, elapsed)` | 光标丢失时盲操 |
| `_load_template(name)` | 加载模板图片（png优先，jpg备选） |
| `_find_template(img, tpl, ...)` | 多尺度模板匹配，返回(pos, conf, scale) |
| `_check_page_by_template(name)` | 检测页面是否匹配指定模板 |
| `_wait_for_template(name, timeout)` | 等待模板出现（轮询） |
| `_match_settings_page(img, tpl)` | ROI灰度匹配检测设置页面（归位用） |
| `_stop_stick(gpad)` | 摇杆归零 |
| `_press_button(gpad, btn, dur)` | 按下并释放按钮 |

### 5.4 yolo_detector.YOLODetector

| 方法 | 说明 |
|------|------|
| `__init__(model_path, conf, iou)` | 初始化ONNX会话（DirectML/CUDA/CPU降级） |
| `__call__(img_rgb, roi)` | 推理入口：返回(coins, cars, bonus, debug_dets, all_raw_dets) |
| `_nms_per_class(xyxy, scores, classes, mask, ...)` | 按类别分别做NMS，返回原始下标 |
| `_to_dets(xyxy, scores, classes, ... indices)` | 索引转结构化检测结果dict |
| `CLASS_CONF` | 类属性：各类别置信度阈值字典 |

### 5.5 mra_shell（WinUI 3 shell + sidecar）

| 类/文件 | 职责 |
|------|------|
| `MainWindow.xaml.cs` | 窗口生命周期、WebView2 加载前端、AppWindowTitleBar drag rects |
| `PythonSidecar.cs` | JSONL transport：stdin 串行写 + 唯一 stdout reader + pending 匹配 + 超时/Kill 树 |
| `App.xaml.cs` | 应用入口（DISABLE_XAML_GENERATED_MAIN），UAC 提权环境变量注入 |
| `sidecar.py` | Python 侧 RPC handler（get_initial_state / start / stop / set_peep ...） |
| `frontend/app.js` | 前端逻辑：`mra.call()` 通信 + 三 Tab 切换 + 日志/状态轮询 |
| `frontend/index.html` | 页面结构，所有 UI 元素 id（改 UI 先改这里） |
| `frontend/style.css` | 设计 token + 组件样式 |

### 5.6 debug.NavigationDebugger

| 方法 | 说明 |
|------|------|
| `__init__(proj_dir)` | 初始化 |
| `enable_peep()` / `disable_peep()` | 开关PEEP实时预览窗口 |
| `start_session(label)` | 开始一次调试会话（创建存盘子目录） |
| `save_frame(img, **kwargs)` | 统一入口：存盘全量绘制 + PEEP精简绘制 |
| `_render_full(img, **kw)` | 全量标注绘制（存盘用） |
| `_render_peep(img, **kw)` | 精简绘制（PEEP用） |

### 5.7 基础工具方法速查（Controller / Navigation / RacingLoop 共享）

跨模块高频工具函数，分散在 Navigation / RacingLoop 中，本表统一索引：

| 方法 | 所属模块 | 说明 | 关键参数/坑点 |
|------|----------|------|---------------|
| `_screencap()` | RacingLoop / Navigation | 截图 RGB ndarray（优先 MAA → 失败回退 ctypes GDI）| 返回 BGR/RGB 需核对，推荐走封装好的 `_cap` / `_screenshot` |
| `_screencap_ctypes()` | Navigation | Win32 GDI 备用截图方案（绕过 MAA 管线回退用）| 窗口句柄和 DPI 需提前确认；非首选 |
| `_cap(ctrl)` / `_cap_fast(ctrl)` | RacingLoop | **v0.11.1** 截图：默认快速 BitBlt（~3-7ms），失败降级 MAA | 见 [赛车文档 §5.2](CODE_WIKI_RACING.md) 兜底链路；每步失败 WARNING 日志 |
| `_press_button(gpad, button, duration)` | Navigation | 按下 → 保持 → 释放（button=XInput enum） | duration 默认 0.3 s；racing 用 `_apply_trigger`/`_steer` 另封装 |
| `_interruptible_sleep(seconds)` | Navigation / RacingLoop / Controller | 每 0.1 s 轮询检查 `_running` 的可中断 sleep | stop 能 0.1 s 级响应；**不要用 `time.sleep(>0.2)`** |
| `_load_template(name)` | Controller / Navigation | 加载模板图片，优先 png → jpg 回退 | 返回 RGB ndarray，不存在返回 None 或 WARNING |
| `_find_template(img, template, threshold, scales)` | Navigation | 多尺度 `TM_CCOEFF_NORMED` 模板匹配 | 返回 `(x,y, confidence, scale)`；scales 详见 §8.4.1 各模板 |
| `_move_cursor_to_target(cursor_pos, target_pos, gpad, stop_distance)` | Navigation | 左摇杆移动光标（四档距离自适应 + 自适应刹车 + 独立死区 4260）| 阈值 FAR/MID/NEAR/BASE 见 §8.2 导航参数；vgamepad Y 轴取反 |
| `_stop_stick(gpad)` | Navigation | 摇杆归零（必须 3 次全零报告）| 不做 3 次 → 驱动层偏置导致首推方向异常 |
| `_ensure_cursor(gpad)` | Navigation | 当前帧无光标时 4 方向搜索（右上→左上→右下→左下）| vgamepad y正=下，y负=上 |
| `_blind_move(gpad, last_pos, target, elapsed)` | Navigation | 光标丢失时盲推一次（兜底不回死循环）| 低优先级，只做 1 次 |
| `_press_and_verify(gpad, cursor_area, dist_button, btn)` | Navigation | 按 A → 模板验证正反 → close_th×0.65 收缩兜底 → 返回 True/None/False | 失败后**不清空 `_last_stick`**；收缩保底下限 `max(5, close_th × 0.65)` |
| `_dist(p1, p2)` | RacingLoop / Navigation | 静态欧几里得距离 | `((x1-x2)²+(y1-y2)²)^0.5` |
| `_find_cursor_by_shape(img, last_known_pos, last_stick)` | Navigation | 双中心面积评分 + 假光标静止拉黑 + 运动一致性评分 | 关键坑点 §10.3；评分见 §8.2 "光标双中心面积评分"行 |
| `_wait_for_template(template_name, timeout, interval)` | Controller | 轮询等待模板出现 / 消失，超时返回 False | interval 默认 0.5s；导航三用 `_wait_for_template("find_opponent", 15s)` |
| `NavigationDebugger(proj_dir)` | debug.py | PEEP 实时预览 / debug 截图标注，支持 template_rects + detections | §5.6；§9.3 调试模式说明 |
| `has_physical_controller()` | window_utils.py | XInput API 遍历 4 端口，任一连接返回 True | DLL 回退 xinput1_4 → xinput9_1_0 → xinput1_3；§10.4 坑点 |

---

## 6. 模块依赖关系

> 赛车域（RacingLoop 决策/截图链路）详见 [CODE_WIKI_RACING.md](CODE_WIKI_RACING.md)。

### 6.1 导入关系图

```
sidecar.py（JSONL RPC handler）
  ├── controller.MaaRacingAssistantController
  ├── logger.logger
  └── window_utils.has_physical_controller

mra_shell（C#，不导入 Python）
  └── PythonSidecar（stdin/stdout JSONL 通信）
        └── 子进程：python -m maaracing_assistant（sidecar.py）

controller.py
  ├── navigation.ButtonDef, Navigation
  ├── racing_loop.RacingLoop
  ├── pipeline_logger.PipelineLogger
  ├── debug.NavigationDebugger
  ├── window_utils.find_game_hwnd
  ├── logger.logger
  └── maa.* (Tasker, Resource, Win32Controller, ...)

navigation.py
  └── logger.logger
  (TYPE_CHECKING: controller.MaaRacingAssistantController)

racing_loop.py
  ├── yolo_detector.YOLODetector
  ├── logger.logger
  └── maa.custom_action.CustomAction, maa.context.Context

yolo_detector.py
  └── logger.logger

debug.py
  └── (无内部依赖，纯OpenCV/numpy)

pipeline_logger.py
  └── logger.logger
  └── maa.context.ContextEventSink, maa.event_sink.NotificationType

window_utils.py
  ├── maa.toolkit.Toolkit
  └── logger.logger

logger.py
  └── (无内部依赖)

opencv_utf8_patch.py
  └── cv2 (monkey-patch)
```

### 6.2 运行时对象持有关系

```
sidecar.py（mra_shell 托管）
  └── controller: MaaRacingAssistantController
        ├── nav: Navigation (持有 ctrl 反向引用)
        ├── racing_loop: RacingLoop
        │     └── det: YOLODetector
        ├── debug: NavigationDebugger (共享给 nav 和 racing_loop)
        ├── tasker: Tasker
        │     └── context_sink: PipelineLogger
        ├── resource: Resource
        └── controller: Win32Controller
```

> **注意**：Navigation 通过 `self.ctrl` 反向引用 Controller，调用其 `_screencap()`/`_get_gpad()`/`_running`，形成双向引用。这是为了让导航引擎能复用Controller的截图和手柄管理，避免重复创建。

---

## 7. 运行流程详解

### 7.1 启动流程

```
双击根目录 MaaRacingAssistant.lnk（定位 mra_shell.exe，exe manifest 自动 UAC 提权）
  → WinUI 3 shell 创建窗口（AppWindowTitleBar）并展示 HTML 前端
  → shell 拉起 Python sidecar（python -m maaracing_assistant）
    → sidecar 初始化 Controller，等待 stdin JSONL RPC
  → 前端通过 mra.call(method, params) 与 sidecar 通信
```

独立调试 sidecar（不经 GUI）：`python -m maaracing_assistant`（等待 stdin JSONL RPC）。

### 7.2 用户点击"开始"后流程

```
前端按钮 #btn-start（app.js）
  → mra.call('start', {start_from}) → C# → sidecar.py 的 start handler
  → 检查模型存在
  → 检查物理手柄 → 有则拒绝启动
  → 读取断点选择（start_from）
  → controller.start(start_from)
```

### 7.3 controller.start() 主流程

```
1. connect(record_mode)
   ├─ find_game_hwnd() → Win32Controller(hWnd=hwnd)
   ├─ controller.post_connection().wait()
   ├─ Tasker() + Resource()
   ├─ RacingLoop(model_path, debug, record_mode)
   ├─ resource.register_custom_action("RacingLoop", racing_loop)
   ├─ resource.post_bundle(assets/resource).wait()
   ├─ tasker.bind(resource, controller)
   └─ tasker.add_context_sink(PipelineLogger())

2. 解析断点 skip_until

3. 记录模式 → racing_loop.run_direct() → 返回

4. 归位（skip_until<=0时）
   └─ nav.homing() → 按B直到匹配settings模板 → 再按B返回

5. 大厅层循环（while _running）
   └─ 导航一（极速狂飙入口），最多3次重试
      ├─ 失败 → 销毁手柄 → sleep(2) → 重新归位 → 重试
      └─ 成功 → 进入对局层循环

6. 对局层循环（while _running）
   ├─ 导航二（开始挑战），最多6次重试
   │  ├─ 连续3次失败且首次进入 → 回大厅重新导航一
   │  └─ 失败 → _running=False 或 回大厅
   ├─ 标记 _in_match = True
   ├─ 导航三（寻找对手），最多6次重试
   │  └─ 先等待find_opponent模板出现15秒
   ├─ 商店弹窗处理 handle_store_popup()
   ├─ 确认上阵（_ensure_cursor + navigate_to_button）
   ├─ 销毁导航手柄！（避免RacingLoop创建第二个手柄冲突）
   ├─ 比赛（racing_loop.run_direct），最多3次重试
   │  ├─ <3秒判定异常 → 重试
   │  ├─ 结束原因="商店弹窗" → handle_store_popup()
   │  └─ 成功 → sleep(2)
   ├─ skip_until=0（断点只首轮生效）
   ├─ _in_match=False
   └─ continue（回到导航二，循环对局）
```

> 该流程已由 [RacingModule（赛车文档 §2）](CODE_WIKI_RACING.md) 承载（MAA 对象模块自有）；`RacingLoop._run_impl()` 帧级核心循环见 [赛车文档 §6](CODE_WIKI_RACING.md)。

---

## 8. 关键配置与常量

### 8.1 图像与分辨率

| 常量 | 值 | 说明 |
|------|-----|------|
| 游戏分辨率 | 1280×720 | 所有坐标基于此 |
| RacingLoop.ROI | (0, 201, 1280, 561) | 路面裁剪区（y28%~78%） |
| YOLO输入尺寸 | 640×640 | letterbox填充114灰 |
| 默认地平线 | 720×0.445 ≈ 320px | 动态推断前默认值 |

### 8.2 导航参数

| 常量 | 值 | 说明 |
|------|-----|------|
| DEADZONE | 4260 | 摇杆死区阈值（MAX_AXIS=8000的53%） |
| MAX_AXIS | 8000 | 导航摇杆最大值（非赛车的32767） |
| 光标面积硬过滤 | <240排除 | 过滤假光标~206-221px² |
| 光标常态面积 | ~310px² | 双中心评分第一峰 |
| 光标选中面积 | ~420px² | 双中心评分第二峰 |
| 静止拉黑阈值 | ≥3帧 | 推摇杆不动的候选排除 |
| 光标识别最低分 | 0.70 | 低于此值判定无光标 |
| 微调脉冲时间 | 25ms | <35px时短推 |
| 微调刹车时间 | 80ms | 微调后刹车 |
| 收缩保底公式 | max(5, close_th×0.65) | 按A失败后阈值收缩 |
| stop_distance | max(8, close_th×0.55) | 自适应停止距离 |
| 盲操超时 | 2秒 | 光标丢失盲操超时 |
| 缓存帧跳距 | >250px | 识别到左上角跳帧时跳过 |

> 赛车控制参数（帧率/油门/力度/车道保持/基准调优）见 [赛车文档 §5](CODE_WIKI_RACING.md)。

### 8.3 模板匹配参数

| 场景 | 阈值 | 缩放范围 |
|------|------|----------|
| 设置页面（归位） | 0.65 | 0.8–1.2，ROI左上50%，彩色 |
| 页面验证（导航） | 0.55 | 0.5–1.8，全图彩色 |
| 结束检测（商店） | 0.90 | 单尺度，灰度 |
| 结束检测（回合1） | 0.55 | 单尺度，灰度 |

#### 8.3.1 模板图片清单（assets/resource/image/）

命名格式：`{用途}_template.{ext}`。按钮定义和阈值在 ButtonDef 中同步维护，本表为快速检索快照。

| 文件 | 尺寸 | 用途 | 匹配阈值 | 状态 | 引用/对应场景 |
|------|------|------|----------|------|--------------|
| `settings_page_template.jpg` | ~484×300 | 归位：识别设置页面（左上角 ROI 50%，彩色多尺度）| 0.65 | ✅ 正常 | §7.3 homing()；§8.3 "设置页面（归位）"行 |
| `activity_page_template.jpg` | 1100×550 | 导航：识别活动页面 / 检测页面已离开（导航二"开始挑战"消失验证正反逻辑）| 0.70 | ✅ 正常 | §7.3 导航二；ButtonDef；§8.3 "页面验证（导航）"行 |
| `find_opponent_template.jpg` | 374×195 | 导航三：识别"寻找对手"页面，按钮消失=成功验证（`template_should_match=False`）| 0.55 | ✅ v0.5.0 | §7.3 导航三；ButtonDef；§8.3 "页面验证（导航）"行；scales=0.5/0.7/0.9/1.0/1.2/1.5/1.8 |
| `store_popup_template.jpg` | 159×262 | 商店弹窗检测 + `RacingLoop._is_end()` 结束画面检测（任一模板命中即返回 True）| 0.55 | ✅ v0.6.0 | §7.3 商店弹窗步骤；[赛车文档 §4](CODE_WIKI_RACING.md) RacingLoop._is_end；§8.3 "结束检测（商店）"行（阈值 0.90 是新版加强）|
| `round1_end_template.jpg` | — | 回合 1 结束画面检测；`_is_end` 模板列表中的主模板（用户截图重命名）| 0.55 | ✅ v0.6.0 | [赛车文档 §6](CODE_WIKI_RACING.md) RacingLoop 结束检测；§8.3 "结束检测（回合1）"行 |
| `cursor_template.jpg` | 168×176 | 旧光标模板匹配 → 已废弃，现改为 §10.3 "双中心面积评分"几何形状识别（灰白 S<30 + 面积 310/420 + area<240 硬过滤）| — | ❌ 已废弃 | §10.3 光标导航坑点；§4.2 Navigation._find_cursor_by_shape |
| `button_main_template.jpg` | ~242×67 | 旧按钮位置模板 → 已废弃，现改为 ButtonDef.pct 百分比硬编码 + 页面模板验证 | — | ❌ 已废弃 | §4.2 navigation.py；§5.2 ButtonDef 定义 |

> 鉴宝模板清单（`assets/resource/image/treasure/`）见 [鉴宝文档 §8](CODE_WIKI_TREASURE.md)。

### 8.4 版本管理

- 版本号由 `setuptools-scm` 从 Git Tag 自动生成
- 格式：`vX.Y.Z`（SemVer 2.0.0）；0.x 阶段全部为 pre-release（`v0.x.y-dev.N`）
- 入口：`git tag vX.Y.Z && git push origin vX.Y.Z` 触发CI Release

**双轨版本机制（v0.13.0-dev.5 起，`maaracing_assistant/__init__.py`）**：
- 打包/安装产物（无 `.git`）：读 `_version.py` 构建快照（setuptools-scm 构建时写入）→ 版本固化，**旧版本不会被仓库后续新 tag 带歪**
- 源码直接运行（目录下有 `.git`）：忽略可能过期的 `_version.py`，启动时 `git describe --tags --long` 按**当前 checkout** 动态推导（checkout 到旧 tag 就显示旧版本号）
- 兜底：`"0.0.0.dev"`
- **sidecar 必须读包级 `__version__`**（`from maaracing_assistant import __version__`），**不能读 `_version.__version__`**——后者是 `_version.py` 文件里的构建时硬编码，与 `__init__.py` 动态推导的包级 `__version__` 不是同一个对象（v0.13.0-dev.5 真实踩过：sidecar 一直返回过期版本号）

---

## 9. 开发与调试

### 9.1 安装开发环境

```bash
git clone https://github.com/d542Bb/MaaRacingAssistant.git
cd MaaRacingAssistant
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 9.2 运行

```bash
python -m maaracing_assistant  # 独立调试 sidecar（等待 stdin JSONL RPC）
```

### 9.3 调试模式

1. **DEBUG 存盘模式**：GUI勾选"DEBUG 每帧截图"，每帧全量标注保存到 `debug/navigate/<按钮名>_<时间戳>/` 目录
2. **PEEP 实时预览**：GUI勾选"PEEP 实时预览"，弹出OpenCV窗口实时显示精简标注画面（~30fps）
3. **断点调试**：GUI断点列表双击选择起始阶段，跳过前面的导航步骤
4. **📹记录模式**：不拦截手柄，记录人工操作+画面数据到 `logs/record_*.csv`，用于分析人工驾驶策略

### 9.4 YOLO模型训练

```bash
python tools/training/train.py  # 训练+自动导出ONNX+复制到assets/model/
```

数据集位于 `dataset/`，类别配置见 `tools/training/dataset.yaml`（3类：coin/car/bonus_car）

### 9.5 日志位置

- 运行日志：`logs/MRA_YYYYMMDD_HHMMSS.log`（含DEBUG级别全量日志）
- GUI日志：只显示 INFO 及以上
- 调试截图：`debug/navigate/` 和 `debug/diagnose/`（DEBUG模式开启时）
- 记录数据：`logs/record_YYYYMMDD_HHMMSS.csv`（记录模式开启时）

### 9.6 物理手柄检测

- 非记录模式下启动时检测，有物理手柄连接则弹窗阻止
- 检测方法：XInputGetState遍历端口0-3
- 记录模式需要物理手柄，不拦截操作只采集数据

---

## 10. 已知坑点与注意事项

> 赛车控制坑点见 [赛车文档 §7](CODE_WIKI_RACING.md)；鉴宝坑点见 [鉴宝文档 §9](CODE_WIKI_TREASURE.md)。

### 10.1 系统层

| 坑点 | 说明 | 解决方案 |
|------|------|----------|
| 截图需要管理员权限 | PrintWindow/BitBlt需要提升权限 | mra_shell.exe manifest 自动 UAC 提权（一次，child 继承） |
| ~~ttkbootstrap 相关坑~~（已归档） | 旧 GUI 遗留 | 代码已移至 `archive/legacy_gui/`，不再适用 |
| cv2不支持中文路径 | imread/imwrite在中文路径下失败 | opencv_utf8_patch.py monkey-patch |
| messagebox不继承图标 | tk.messagebox弹窗无图标 | 用tk.Toplevel自行创建+iconbitmap |
| YOLO ONNX 导出 | `onnx.export(simplify=True)` 可能产生损坏模型（推理结果错乱） | 导出时关闭 simplify，或导出后校验精度 |

### 10.2 MAA Framework API

| API | 正确用法 | 常见错误 |
|-----|----------|----------|
| Toolkit.init_option | `init_option(path, "")` 第二个参数传空字符串 | 不要传None |
| Win32Controller | `Win32Controller(hWnd=hwnd)` 参数名驼峰hWnd | 不要写成hwnd= |
| Tasker.bind | `bind(resource, controller)` resource在前 | 参数顺序反了会崩溃 |
| Resource.post_bundle | `post_bundle(path)` | 不是post_path |
| Resource.register_custom_action | `register_custom_action(name, action)` | action需继承CustomAction |
| MAA PostScreencap返回 | BGR格式（OpenCV默认） | 需要手动cvtColor转RGB |

### 10.3 光标导航

| 坑点 | 说明 |
|------|------|
| 光标面积评分中心 | 真光标面积310（常态）/420（选中态），不是1200 |
| 双中心面积评分公式 | `max(1-abs(area-310)/300, 1-abs(area-420)/300)` |
| 面积硬过滤 | area<240必须排除，假光标~206-221 |
| 游戏摇杆死区 | ~13%（约4260/32767），非零轴必须抬升到死区以上 |
| 销毁手柄复位光标 | `del gpad` 游戏自动把光标复位到左上角，比摇杆归中可靠 |
| 不要加微轴归零阈值 | abs(dx)<N→lx=0会阻止目标附近±Npx死区的最终修正 |
| 假光标静止拉黑 | 用_prev_frame_positions集合跨帧对比，不依赖last_known_pos |
| _press_and_verify失败 | 不要清空_last_stick，保留下一帧运动评分依赖 |
| 收缩保底公式 | `max(5, int(close_th×0.65))` 不是max(30,-15) |
| stop_distance自适应 | `max(8, close_th×0.55)` 不是硬编码25px |
| 微调脉冲 | <35px用25ms+80ms刹车，40ms仍过冲 |
| 模板匹配正反逻辑 | True=匹配到算成功；False=模板消失算成功 |

### 10.4 物理手柄XInput

- `XInputGetState(i, buf) == 0` 表示第i号物理手柄已连接
- 尝试加载顺序：xinput1_4.dll → xinput1_3.dll → xinput9_1_0.dll
- 非记录模式必须断开所有物理手柄，否则虚拟手柄被游戏忽略或冲突

---

## 11. GUI 宿主选型（WinUI 3 定案）

> 2026-08 定案。目标：HTML/WebView2 前端 + 原生 Windows 窗口行为（DWM 动画/系统按钮/Snap），Python 保持唯一业务后端（sidecar 模式）。**已落地**：正式 GUI 为 `apps/mra_shell/`（WinUI 3 shell + HTML 前端），旧 ttkbootstrap GUI（`gui/`、`gui_webview/`）与历史 spike 已归档至 `archive/`。

### 11.1 选型历程（三个 Spike 实测结论）

| 候选 | 结论 | 根因 |
|------|------|------|
| pywebview frameless | 出局 | `FormBorderStyle.None` 无 WS_CAPTION → 无 DWM 动画；官方无 custom titlebar（master 分支新增 `drag_region.py` 示例但动画无解） |
| Tauri v2 (2.11.5) | 出局 | `titleBarStyle` 是 **macOS-only**：builder 标 `#[cfg(target_os="macos")]`、`set_title_bar_style` 注释「macOS only」、tao Windows 无实现、wry 无 WCO 代码 |
| WPF + WindowChrome | 出局 | WebView2 **airspace**：WebView2 是独立 HWND 铺满客户区，把 WindowChrome 的拖动区（CaptionHeight）/模拟 caption buttons（UseAeroCaptionButtons）/resize 边缘（ResizeBorderThickness）全部遮挡、鼠标被 WebView2 吞掉 |
| **WinUI 3 + AppWindowTitleBar** | **定案** | drag rects / caption buttons 是**系统级 NC 处理**，不被 WebView2 遮挡；HTML 铺顶 + 系统按钮 overlay + DWM 动画 + 拖动/双击/圆角全部通过 |

### 11.2 锁定版本与依赖

- Windows App SDK **1.8.260710003**（NuGet；meta 包，依赖拆成 9 个子包，restore 自动拉）
- WebView2 SDK **1.0.4129.50**（仅 WPF spike 用到；WinUI 3 的 `Microsoft.UI.Xaml.Controls.WebView2` 随 WindowsAppSDK 提供）
- .NET SDK 8.0.123（本机已装 8/9/10）
- WinUI 3 未打包应用：`WindowsPackageType=None` + `WindowsAppSDKSelfContained=true`（免装 Windows App Runtime）
- spike 原型：`archive/`（已归档，`prototypes/` 已迁移至 `apps/`）

### 11.3 NuGet 网络坑（本机）

| 坑点 | 说明 | 解法 |
|------|------|------|
| nuget.org 被网络阻断 | dotnet 报 SSL EOF；curl 能 302 到 CDN | 用 **Azure CN 镜像** `https://nuget.azure.cn/v3/index.json`（restore 可用） |
| `dotnet restore --source <源名>` 当路径 | 源名被解析为相对目录 | 用**项目级 `NuGet.Config`**（`<clear/>` + 指定源），restore 不带 --source |
| 本地包源 | curl 手动下载 nupkg 到目录 | `dotnet nuget add source <dir>` |

### 11.4 WinUI 3 关键 API（AppWindowTitleBar）

| API | 用途 | 注意 |
|-----|------|------|
| `AppWindow.TitleBar.ExtendsContentIntoTitleBar = true` | 内容延伸到标题栏，保留系统 caption buttons | 关闭系统标题栏视觉，按钮仍在右上 overlay |
| `AppWindow.TitleBar.SetDragRectangles(RectInt32[])` | HTML 拖拽区 | **物理像素**；窗口尺寸变化（`AppWindow.Changed` + `args.DidSizeChange`）需重设 |
| `AppWindow.TitleBar.PreferredHeightOption` | 系统按钮高度 | `Standard` / `Tall` |
| `Window.AppWindow` | 获取 AppWindow | Windows App SDK 1.4+ |

- **drag region 交互区挖孔（v0.13.0-dev.5）**：顶部 52px 整条设为 drag rect 时，双击 tab/品牌按钮区会触发最大化（按钮被「标题栏」行为吃掉）。方案：前端 `reportDragExcludes()` 测量 `.brand`+`.tabs` 合并矩形 → `postMessage({type:'drag-exclude', rect})` → C# 收到后存 DIP 矩形，`UpdateDragRects()` 按 DPI 换算挖孔（左段+右段+按钮下方段三段），坐标基准 = 窗口左上角（HTML 延伸进标题栏后 DOM (0,0) 即窗口左上角）
- **单实例互斥（v0.13.0-dev.5，`Program.cs`）**：`AcquireSingleInstance()` 用命名 Mutex（`Global\MRA_SingleInstance`，权限异常降级会话级）检测已有实例 → `MessageBoxW` 询问「启动新进程（taskkill /T 连 sidecar 杀旧进程）或取消保留旧进程」；旧进程被强杀后接管 Mutex 需捕获 `AbandonedMutexException`

- 系统按钮颜色跟随系统主题（native 正常表现，非 bug）
- Snap Layout hover 在 AppWindowTitleBar 下未出现（用户确认不在乎；疑似系统 SnapAssist 设置，未深究）

### 11.5 WinUI 3 构建坑

| 坑点 | 解决 |
|------|------|
| 手写 `Program.cs` 与 XAML 自动生成 Main 冲突（CS0101） | csproj 加 `DefineConstants=$(DefineConstants);DISABLE_XAML_GENERATED_MAIN` |
| `Application.Start` 回调参数用 `_` 与丢弃赋值冲突（CS0029） | 参数命名 `p` |
| 未打包应用入口样板 | `WinRT.ComWrappersSupport.InitializeComWrappers()` + DispatcherQueueSynchronizationContext |

### 11.6 后续步骤（sidecar 架构）

- 进程模型：`MRA.exe`（C# WinUI 3 shell）+ `maaracing_backend.exe`（PyInstaller sidecar）
- IPC：stdin/stdout **JSONL**（stdin=request / stdout=response+event / stderr=日志），消息带 `type` 字段；C# 侧单一 reader task + `pending` map + timeout
- 管理员权限：只放最外层 exe manifest，一次 UAC，child 继承
- Rust 三原则平移为 C#：**C# 只做窗口/启停 Python/转发消息**，Controller 业务不进入 shell

### 11.7 sidecar transport 契约测试（Step 2 完成）

> `archive/sidecar_spike/`（已归档）：`PythonSidecar.cs` + `fake_sidecar.py` + `Program.cs`。**11/11 通过**（2026-08），正式 shell 的 transport 直接复用。

**契约要点（C# PythonSidecar）**：

| 项 | 实现 |
|----|------|
| 唯一 stdout reader | 一个常驻 `ReaderLoopAsync`，按 `response.id` 匹配 `ConcurrentDictionary<ulong, TCS>` |
| stdin 串行 | `SemaphoreSlim` 写锁 |
| 超时 | `Task.WaitAsync(timeout)`，超时清理 pending，只影响单请求 |
| backend 断开 | reader EOF → 所有 pending 立即抛 `BackendDisconnectedException` |
| malformed stdout | 忽略并记 stderr，不 crash 整个 IPC |
| stderr drain | 独立 task 持续读，防 OS pipe 填满卡死 Python |
| shutdown | `ShutdownAsync(grace)`：发 shutdown → 等进程自退 → 超时 `Kill(entireProcessTree:true)`；返回退出码，不 Dispose |
| 防孤儿 | `Dispose()` 对存活进程 KillTree |

**契约测试坑点（必记）**：

| 坑点 | 说明 |
|------|------|
| `JsonDocument` 生命周期 | reader 中 `using var doc` 循环末释放，`SetResult` 必须传 `root.Clone()`（深拷贝），否则调用方访问即 ObjectDisposedException |
| Python worker 线程退出 | `sys.exit()` 在非主线程只抛 SystemExit 不退出进程，必须 `os._exit(n)` |
| Dispose 后访问 Process | `_process.Dispose()` 后访问属性抛「No process is associated」；验证进程存活用 `ProcessId` + `Process.GetProcessById(pid)` 捕获 `ArgumentException` |

> 正式 sidecar：[sidecar.py](file:///d:/maaracing_assistant/maaracing_assistant/sidecar.py)（Step 4 完成，已命令行验证）。入口强制 `sys.stdout = _StdoutGuard`（一切误写转 stderr）。**坑**：handler 线程必须非 daemon——stdin EOF 后主线程退出会杀 daemon，导致 shutdown 等响应丢失。

---

## 附录：类速查表

> 主程类速查见下表；RacingLoop 见 [赛车文档 §1](CODE_WIKI_RACING.md)，鉴宝类（TreasureModule / TreasureStageDetector / TreasureOcr / TreasureDebugRenderer）见 [鉴宝文档 §7](CODE_WIKI_TREASURE.md)。

| 类名 | 文件 | 核心职责 |
|------|------|----------|
| `MaaRacingAssistantController` | controller.py | 主控编排、MAA绑定、阶段调度 |
| `ButtonDef` | navigation.py | 导航按钮配置数据类 |
| `Navigation` | navigation.py | 光标识别追踪、模板匹配、摇杆导航 |
| `RacingLoop` | racing_loop.py → [赛车文档](CODE_WIKI_RACING.md) | 自动驾驶YOLO循环、决策、手柄控制 |
| `YOLODetector` | racing_loop.py | ONNX推理、per-class NMS |
| `RacingModule` | modules/racing_module.py → [赛车文档 §2](CODE_WIKI_RACING.md) | 极速狂飙活动流程（导航+比赛） |
| `TreasureModule` | modules/treasure_module.py → [鉴宝文档 §1](CODE_WIKI_TREASURE.md) | 巅峰鉴宝活动模块（12阶段状态机） |
| `MRAGUI` | ~~gui.py~~ → `archive/legacy_gui/` | 旧 ttkbootstrap 图形界面（已归档） |
| `Sidecar` | sidecar.py | JSONL RPC 业务后端（mra_shell 托管） |
| `NavigationDebugger` | debug.py | PEEP预览、截图标注存盘 |
| `Logger` | logger.py | 内存+文件双写日志 |
| `PipelineLogger` | pipeline_logger.py | MAA Pipeline事件日志 |
