# MaaRacingAssistant — Claude 项目配置

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
- **`XInputGetState(i, buf) == 0` 表示第 i 号物理手柄已连接**（通过 `xinput1_4.dll`/`xinput1_3.dll` 调用）
- **`ButtonDef` 配置类**：`name`, `pct`(百分比坐标), `page_template`, `template_should_match`, `close_threshold`

## 已知坑点

- 截图需要管理员权限
- ttkbootstrap Window 会覆盖图标，用原生 tk.Tk + ttk.Style
- ttkbootstrap LabelFrame 不支持 padding 参数
- YOLO ONNX 导出时 `simplify=True` 可能产生损坏模型
- **光标识别面积评分中心是 260（真光标面积）**，不是 1200，否则误识别成其他圆形 UI（`_find_cursor_by_shape`）
- **双中心面积评分**：常态~310 / 变形选中态~530，`max(1-abs(area-310)/300, 1-abs(area-420)/300)`，同时覆盖两种形态
- **面积硬过滤 `area < 240`** 排除假光标~206-221，真光标最低~301
- **游戏摇杆死区约 13%**，摇杆最低幅度必须 > 4260（`MAX_AXIS * min_speed > 4260`）
- **销毁手柄（`del gpad`）游戏会自动把光标复位到左上角**，比摇杆归中更可靠
- **不要加微轴归零阈值**——`abs(dx) < N → lx = 0` 会阻止光标在目标附近的 ±N px 死区内做最终修正，应直接用独立死区让每个非零轴升到 4260 推到底
- **假光标静止拉黑用 `_prev_frame_positions: set[tuple]`**，候选人自己的位置跨帧对比，不依赖 `last_known_pos`（被选中光标位置）。推摇杆时不动的候选人累计静止计数，≥3 帧 `continue` 拉黑
- **`_press_and_verify` 失败后不要清空 `_last_stick`**——保留推杆方向供下帧运动评分（假光标静止惩罚依赖 `last_stick ≠ (0,0)`）
- **收缩保底公式 `max(5, int(close_th × 0.65))`**，不是 `max(30, -15)`——后者对 25px 阈值会从 25 放大到 30
- **stop_distance 自适应 `max(8, close_th × 0.55)`**，不是硬编码 25px——收缩后光标才能推到足够近
- **微调档位 < 35px：25ms 脉冲 + 80ms 刹车**——死区最低 4260 时 40ms 仍过冲，25ms ~1.5 帧才收敛
- **模板匹配正反逻辑**：`template_should_match=True` 表示匹配到模板 = 成功进入页面；`False` 表示模板消失 = 成功离开页面
- **`messagebox.showerror` 不继承父窗口图标**，需要自行 `tk.Toplevel + iconbitmap`
- **防碰撞三区体系**：A 区(L<350, R>850)无干预，B 区(350<L≤450, 750≤R<850)仅 ddL>5/ddR<-5 阻挡往墙方向决策，C 区(L>450/R<750)强制突发修正
- **C 区反打思维**：不持续满打方向，改为"2 帧突发改变车头指向 → 5 帧强制归中滑行 → 重评估"，防止从右墙一路冲到左墙
- **标线不推断对侧**：`_detect_lane` 只返回真实检出的标线 key（无 `left` 或 `right` key），调用方必须用 `.get()` 安全访问
- **防撞记忆 `_wall_memory`**：标线存在时记录是否靠近墙壁(L>400/R<800)，标线丢失 + 无 YOLO 目标时按记忆方向轻柔回带
- **`_aim_at` / `_avoid` 不再有标线边界约束**：防碰撞由独立模块负责，让金币/避障可以自由变道
- **NMS per-class 索引映射链 `mask_indices[cls_local[nms_idx]]`**：`_nms_per_class` 中 NMS 返回的是类别内下标，需经 cls_local→mask_indices 两级映射回原始数组下标
- **`_avoid` 框重叠检测 `left<R2c and right>L2c`**：不用中心点，用车框左右沿判断是否进入中心区。框进区就躲，不进就穿透到金币
- **避障穿透**：`_avoid` 返回 0（不在行驶方向）时 `_decide` 不返回避障，落到金币/跳板车逻辑，避免"远处有车但不挡路却一直不转向"的僵局
- **车道保持方向统一**：左右标线侧 `new_dir = 1 if diff > 0 else -1`（diff>0=标线右移→右修）。右标线侧不能取反，否则会往墙方向修
- **地平线推断**：`_detect_horizon` 用 YOLO 低置信度(≤0.25)小面积(area<400)小车推测，首次≥3 个车取 1/4 分位锁死当整局。前 40 帧不推断（等镜头稳定）
- **`_lane_boundaries_at_y` 透视投影**：`bound(x_frac, y_frac)` 从消失点(cx, horizon)经测量点线性外推。L2c/R2c 测量点在 (0.22, 1.00) 即 22%×1280=281px 偏差/屏幕底部测量
- **标线单边选择**：`_detect_lane` 用 `side_score`（总长度×角度一致性）择优选一侧，只返回 `{side, pos}`，不再返回 `{left, right, center}`

## 文件结构

```
d:\maaracing_assistant/
├── run.py                               # 快捷入口：python run.py
├── pyproject.toml                       # 项目配置（pip install -e . 支持）
├── maaracing_assistant/                 # 应用包（源码）
│   ├── __init__.py                      # 版本号
│   ├── __main__.py                      # python -m maaracing_assistant 入口
│   ├── controller.py                    # MaaRacingAssistantController（总控编排）
│   ├── navigation.py                    # Navigation + ButtonDef（光标导航）
│   ├── racing_loop.py                   # RacingLoop（YOLO 赛车控制）
│   ├── yolo_detector.py                 # YOLODetector（ONNX 推理）
│   ├── logger.py                        # Logger（文件+内存日志）
│   ├── pipeline_logger.py               # PipelineLogger（MAA 事件）
│   ├── window_utils.py                  # 窗口查找 + XInput 检测
│   ├── gui.py                           # MRAGUI（ttkbootstrap 窗口）
│   ├── debug.py                         # NavigationDebugger（调试可视化）
│   └── opencv_utf8_patch.py             # OpenCV 中文路径补丁
├── docs/                                # 文档
│   ├── HANDOVER.md                      # 完整交接文档
│   └── update_log.md                    # 更新日志
├── CLAUDE.md                            # 本文件
├── README.md                            # 快速开始
├── requirements.txt                     # 依赖
├── .gitignore
├── assets/
│   ├── model/
│   │   └── model.onnx                 # YOLO 模型（3 类，由 train.py 生成）
│   ├── resource/
│   │   ├── image/
│   │   │   ├── settings_page_template.jpg   # 归位：设置页面
│   │   │   └── activity_page_template.jpg   # 导航：活动页面
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
0️⃣ 防碰撞 C 区（左墙 pos>450 / 右墙 pos<830）→ 突发修正 2 帧 + 归中 5 帧
1️⃣ bonus_car（跳板车/油罐车）→ _aim_at 三区力度瞄准（远50%/中100%/近0%）
2️⃣ 障碍车（car）→ 框与中心区(L2c~R2c)重叠才躲，非重叠穿透到金币
3️⃣ 金币（coin）→ 链式评分（深度+密度+区域奖励+车道惩罚），三区力度瞄准
4️⃣ 无目标 → 标线丢失有记忆时回带，否则车道保持（闭环自适应力度）
```

## 当前状态

- ✅ GUI — 正常，UAC 提权正常，物理手柄检测弹窗带图标
- ✅ 窗口连接 — 正常
- ✅ Pipeline 绑定 — 正常
- ✅ 数据集 — 188 张标注（150 训练 / 38 验证），3 类
- ✅ YOLO 模型 — 已训练，ONNX 已导出（mAP50 ≈ 0.92）
- ✅ 启动归位（Homing）— 彩色多尺度模板匹配，正常
- ✅ 光标导航（Navigate）— 双中心面积评分 + 假光标静止拉黑 + 独立死区摇杆 + 自适应 stop_distance
- ✅ 第二个按钮（"开始挑战"）— 测试通过，12px 阈值成功命中
- ✅ 假光标静止拉黑 — 区域式检测 ±5px，光标丢失时延续拉黑状态
- ✅ 微调移动 — < 35px 25ms 脉冲 + 80ms 自适应刹车
- ✅ Debug 可视化 — `debug.py` NavigationDebugger 四色每帧截图标注（红/绿/紫/黑/蓝）
- ✅ 假光标静止拉黑 — 区域式检测 ±5px，光标丢失时延续拉黑状态
- ✅ 物理手柄检测 — XInput API，GUI 弹窗阻止运行
- ✅ GitHub PR 工作流 — master 分支启用保护，必须通过 PR 提交代码
- ✅ 版本号 — 通过 Git Tag `vX.Y.Z` 管理，`setuptools-scm` 自动推导（详见"版本管理约定"）
- ✅ 包结构重构 — 源码归入 `maaracing_assistant/` 包目录，`main.py` 拆分为 6 个单一职责模块，零循环导入
- ✅ HoughLinesP 标线检测 — y50%~80% 区域 Hough 直线法检测黄色标线，断裂自动延长对齐
- ✅ 三区防碰撞 — `_wall_avoidance`：A 区安全/B 区 ddL/ddR 加速监控/C 区硬边界，替代旧 `_keep_center`
- ✅ 反打修正（突发+归中）— C 区 2 帧满打 + 5 帧强制归中滑行，防来回弹墙
- ✅ 标线不推断对侧 — 只信任真实检出的标线，防碰撞只读真实侧
- ✅ 标线丢失记忆回带 — `_wall_memory` 机制，无目标时轻柔回带
- ✅ Debug 摇杆状态条 — 底部滑条指示器代替方向文字
- ✅ NMS 按类分别处理 — `_nms_per_class` 避免 car 压掉 bonus_car 的跨类抑制
- ✅ 三区变力度瞄准 — `_aim_at` 远区50%/中区100%/近区0%，水平死区±0.06
- ✅ 避障框重叠判断 — 框左沿<R2c 且框右沿>L2c 才触发，非中心区目标穿透到金币
- ✅ 避障穿透逻辑 — `_avoid` 返回 0 时不占用决策，不阻塞金币/跳板车逻辑
- ✅ 闭环车道保持 — `_lane_keep` 漂移趋势自适应力度（50%~100%），左右标线侧方向统一
- ✅ 动态地平线 — 从 YOLO 低置信度小车推断地平线并锁死整局，用于远/中/近区划分
- ✅ 动态油门 — `_calc_throttle` 防撞120/避障180/金币200/直行255
- ✅ 透视车道分界线 — `_lane_boundaries_at_y` 梯形透视投影，给出任意 y 深度处的 6 条车道线
- ✅ 侧区金币扣分 — 紧贴墙壁侧的金币权重扣分，鼓励往安全侧转向

## 版本管理约定

### 规则

1. **严格遵循 SemVer 2.0.0**：版本号格式 `X.Y.Z`（主版本.次版本.修订号）
2. **Git Tag 是唯一信源**：版本号由 Git Tag `vX.Y.Z` 驱动，禁止手动修改源码中的版本号
3. **`setuptools-scm` 自动管理**：`pyproject.toml` 不写死版本号，`__version__` 从自动生成的 `_version.py` 读取
4. **Tag 推送触发 CI/CD**：推送 `v*` tag 到 GitHub 后，Actions 自动校验格式 + 创建 Release

### 发布操作流程

```bash
# 开发在 feature 分支 → PR 合并到 master
# 合并后在本地 master 打 tag 并推送
git checkout master
git pull origin master
git tag v1.2.3
git push origin v1.2.3      # 触发 GitHub Actions 创建 Release
```

### 版本号递增规则（SemVer）

| 变动类型 | 递进位段 | 示例 |
|---------|---------|------|
| 不兼容修改 | 主版本号 | 0.7.1 → 1.0.0 |
| 向下兼容的新功能 | 次版本号 | 0.7.1 → 0.8.0 |
| 向下兼容的 Bug 修复 | 修订号 | 0.7.1 → 0.7.2 |

### 验证方式

包安装后：
```python
from maaracing_assistant import __version__
print(__version__)  # 与最近的 git tag 一致
```

### 对应文件

- `maaracing_assistant/_version.py` — 自动生成，已加入 `.gitignore`
- `maaracing_assistant/__init__.py` — 从 `_version.py` 导入
- `pyproject.toml` — `[tool.setuptools-scm]` 配置段
- `.github/workflows/release.yml` — Release CI/CD 工作流

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
10. **能直接改就不要先读几十行代码确认**——改前读了代码的话，改后不需要再读一遍验证
11. **任何不必要的 thinking 和文件读取都在烧 token 烧钱**——确定要动的地方直接动手，别绕路
12. **简单问题直接答，无需先思考**——"把 XX 改成 YY"这种明确的需求直接改，不用先想半天然后再动手
13. **思考聚焦思路，不贴代码**——思考时只写"改哪几个文件、怎么改、为什么"，不在 thinking 里拼完整的函数代码。想清楚方案后简要告知用户，直接动手改
