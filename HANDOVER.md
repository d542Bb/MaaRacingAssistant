# MaaRacingAssistant v0.3.0 — 开发交接文档

## 项目目标
自动完成《巅峰极速》"极速狂飙"活动：
**启动归位**（按B直到设置页面，再按B回主界面）→ **光标导航**（`ButtonDef` 配置驱动，左摇杆移动光标到按钮，按A确认）→ 回合1赛车（YOLO识别 + 手柄控制）→ 回合2放弃 → 循环

## 技术栈
- MAA Framework 5.11.1（UI 流程 + 窗口控制）
- YOLOv8 + ONNX Runtime（视觉识别，3 类：coin / car / bonus_car）
- vgamepad（虚拟 Xbox 手柄）
- OpenCV（模板匹配：归位识别设置页面 + 导航验证页面切换）
- ttkbootstrap（GUI）

## 已确认 API（重要！不要假设）
- `Toolkit.find_desktop_windows()` 返回 DesktopWindow 对象列表
  - 属性：`hwnd`, `class_name`, `window_name`
- `Toolkit.init_option(path, "")` 第二个参数传空字符串
- `Win32Controller(hWnd=hwnd)` 参数名驼峰 `hWnd`
- `Tasker.bind(resource, controller)` 顺序：resource 在前！
- `Resource.post_bundle(path)` 不是 `post_path`
- `Resource.register_custom_action(name, action)`
- **`Win32Controller.post_screencap()` 返回 `JobWithResult`，需调用 `.wait().get()` 获取图像数据**
- **`XInputGetState(i, buf) == 0` 表示第 i 号物理手柄已连接**（通过 `xinput1_4.dll`/`xinput1_3.dll` 调用）

## 已知坑点
- 截图需要管理员权限
- ttkbootstrap Window 会覆盖图标，用原生 tk.Tk + ttk.Style
- ttkbootstrap LabelFrame 不支持 padding 参数
- YOLO ONNX 导出时 `simplify=True` 可能产生损坏模型
- **`Win32Controller` 直接调用 `post_screencap()` 需用 `.wait().get()` 解包，但 `Context.controller` 会自动解包**
- **vgamepad 驱动层可能残留偏置**：每次 `run()` 时新创建手柄并发送 3 次全零报告清除
- **MAA 截图坐标 vs 游戏实际坐标映射未验证**：截图 1280×720 vs 游戏 1920×1080，模板匹配返回的是截图坐标，但摇杆控制作用于游戏窗口，缩放映射是否正确待确认
- **模板匹配正反逻辑**：`template_should_match=True` 表示匹配到模板 = 成功进入页面；`False` 表示模板消失 = 成功离开页面（第二个按钮用反逻辑）
- **`messagebox.showerror` 不继承父窗口图标**，物理手柄弹窗已改用 `tk.Toplevel + iconbitmap`
- **XInput DLL 加载顺序**：`xinput1_4.dll` → `xinput9_1_0.dll` → `xinput1_3.dll`

## 当前状态

### 功能完成度

- ✅ **GUI** — 正常，UAC 提权正常，窗口可自由拖拽（最小 480×400）
- ✅ **物理手柄检测** — XInput API 遍历 4 端口，检测到手柄时弹自定义对话框阻止运行（带 icon.ico）
- ✅ **窗口连接** — 通过 MAA `find_desktop_windows()` + Win32Controller 正常
- ✅ **Pipeline 绑定** — `tasks.json` 6 步闭环：`入口→回合1准备→比赛→结束→回合2放弃→确认→循环`
- ✅ **YOLO 模型** — 已训练 mAP50≈0.92，ONNX 已导出 `assets/model/yolov8n_coins_cars.onnx`（3 类：coin / car / bonus_car）
- ✅ **Pipeline 日志** — `PipelineLogger(ContextEventSink)` 监听每步识别命中/动作成功状态
- ✅ **RT 加速** — `RacingLoop.run()` 起步按住 `right_trigger(255)`，`finally` 释放
- ✅ **YOLO 决策日志** — `_decide()` 每步打印 bonus_car 对准/障碍避让/金币吃取/直行的中文日志
- ✅ **启动归位（Homing）** — 按B直到识别到设置页面（多尺度模板匹配，阈值0.70），再按B返回主界面
- ✅ **日志分级** — DEBUG/INFO/WARNING/ERROR 四级，GUI 默认只显示 INFO 及以上
- ✅ **可中断睡眠** — `_interruptible_sleep()` 每 0.1 秒检查 `_running`，stop 能立即响应
- ✅ **手柄生命周期管理** — `_create_pad()` / `_destroy_pad()` 对，每次赛车前创建并归零，结束后销毁
- ✅ **光标导航** — `ButtonDef` 配置驱动，模板匹配正反逻辑，独立死区摇杆控制，`_press_and_verify` 统一验证
- ✅ **第二个按钮（"开始挑战"）** — 测试通过，25px 阈值成功命中并退出活动页

### main.py 结构概览

| 类/模块 | 职责 |
|---------|------|
| `Logger` | 分级日志（DEBUG/INFO/WARNING/ERROR），文件输出 `MRA_*.log`，GUI 只显示 INFO+ |
| `PipelineLogger(ContextEventSink)` | 监听 MAA Pipeline 每步识别/动作成功/失败，打印到日志 |
| `YOLODetector` | ONNX Runtime 推理（优先 CUDA → CPU），NMS 后处理，返回 (coins, cars, bonus_cars) |
| `RacingLoop(CustomAction)` | 赛车控制：RT 加速 + YOLO 决策 + 手柄转向 |
| `ButtonDef` | 按钮配置类：`name`/`pct`/`page_template`/`template_should_match`/`close_threshold` |
| `MaaRacingAssistantController` | 主控：连接/归位/导航/Pipeline 循环/停止 |
| `has_physical_controller()` | 模块级函数，XInput API 检测物理手柄是否已连接 |

### 模板图片

3 张模板图片统一存放在 `assets/resource/image/`，命名格式 `{用途}_template.{ext}`：

| 文件 | 用途 | 匹配阈值 | 状态 |
|------|------|----------|------|
| `settings_page_template.jpg` (~484×300) | 归位：识别设置页面（左上角区域，彩色匹配） | 0.70 | ✅ 正常 |
| `activity_page_template.jpg` (1100×550) | 导航：识别活动页面 / 检测页面已离开（第二个按钮） | 0.70 | ✅ 正常 |
| `cursor_template.jpg` (168×176) | 导航：旧模板，已废弃改用几何形状识别 | — | ❌ 已废弃 |
| `button_main_template.jpg` (~242×67) | 导航：已废弃，按钮位置改为百分比硬编码 | — | ❌ 已废弃 |

### 启动流程（`MaaRacingAssistantController.start()`）

```
1. check_model()          → 检查 ONNX 模型存在
2. connect()              → 查找窗口 → Win32Controller → Tasker 绑定
3. _running = True        → 允许 stop 中断
4. homing()               → 按B×N → 截图匹配设置页面 → 再按B回主界面
5. while _running:        → 外层循环
   a. navigate_to_button(极速狂飙入口)  → 模板出现确认
   b. navigate_to_button(开始挑战)      → 模板消失确认
   c. Pipeline 循环 post_task("极速狂飙入口")
```

### 归位流程（`homing()`）

```
对 i in range(15):
  截图 → match_template 多尺度(0.5×~2.0×)
  匹配成功? → 再按B返回主界面 → 等待2秒 → return True
  否则 → 按B(0.3秒) → interruptible_sleep(1.5秒)
失败 → return False（流程继续，不阻塞）
```

### 导航流程（`navigate_to_button(btn: ButtonDef)`）

```
center_first=True:  推摇杆(12000,-12000) 0.4秒把光标从左上角拉入画面

对 _ in range(30):
  截图 → _find_cursor_by_shape() 几何形状识别
  按钮坐标 = 截图尺寸 × btn.pct
  未找到光标? → 计时, ≥2秒放弃
  找到光标? → _move_cursor_to_target() 独立死区摇杆移动
  距离 < btn.close_threshold? → _press_and_verify()

  _press_and_verify:
    停摇杆 → 按A(0.3秒) → 等待1.5秒
    截图 → 模板匹配
    btn.template_should_match=True?  → 匹配到=页面已切换 → return True
    btn.template_should_match=False? → 模板消失=页面已切换 → return True
    模板未变化? → 缩小阈值(阈值-5,下限10) → 继续

  假光标检测: 同一位置>3帧 → 强制丢失模式 → 等2秒超时
超时 → return False
finally: 摇杆归零(不销毁手柄,保持复用)
```

**关键经验：**
- 光标用几何形状识别（白色圆形 + 圆度≥0.82 + 面积评分中心260），不要用模板匹配
- **不要加微轴归零阈值**（如 `abs(dy)<10→ly=0`），否则光标在目标附近±10px内无法做Y方向最终修正。应直接用独立死区，每个非零轴自动升到4260推到底
- 独立死区：每个轴独自判断是否低于4260，低了就升到4260（保留方向），不再等比例缩放两轴
- vgamepad Y 轴取反：`uy = -dy / dist`
- 销毁手柄（`del gpad`）能让游戏自动把光标复位到左上角，比摇杆归中可靠
- 按钮用 `ButtonDef` 配置类统一管理，新按钮只需加一行定义

### 导航重试机制（`start()`）

```
外层 while _running:
  1. homing() → B键回到主界面
  2. 导航一(极速狂飙入口):  重试×3
     失败 → _destroy_gpad() → 等2秒 → homing() → 重试
  3. 导航二(开始挑战):      重试×3
     失败 → _destroy_gpad() → 等2秒 → continue 外层(从头开始)
  4. Pipeline 循环 → 结束 → 回到外层继续
```

导航二失败不单独归位，而是跳回外层循环重新归位+导航一+导航二，确保每次完整重来。

### 基础工具方法

| 方法 | 说明 |
|------|------|
| `_screencap()` | 截图 RGB ndarray（MAA → 失败回退 ctypes GDI） |
| `_screencap_ctypes()` | Win32 GDI 备用截图方案 |
| `_press_button(gpad, button, duration)` | 按下→保持→释放，默认 0.3 秒 |
| `_interruptible_sleep(seconds)` | 每 0.1 秒检查 `_running` 的可中断 sleep |
| `_load_template(name)` | 加载模板（优先 png → jpg），返回 RGB ndarray |
| `_find_template(img, template, threshold, scales)` | 多尺度 `TM_CCOEFF_NORMED`，返回 (位置, 置信度, 缩放) |
| `_move_cursor_to_target(cursor_pos, target_pos, gpad)` | 左摇杆移动光标到目标点（带距离衰减） |
| `_stop_stick(gpad)` | 摇杆归零（3 次全零报告） |
| `_ensure_cursor(gpad, frame, debug_img)` | 当前帧无光标时 4 方向搜索 |
| `_blind_move(gpad, last_pos, target, elapsed)` | 光标丢失时盲推一次 |
| `_press_and_verify(gpad, cursor_area, dist_button, btn)` | 按A + 模板验证，返回 True/None/False |
| `_dist(p1, p2)` | 静态欧几里得距离计算 |
| `has_physical_controller()` | XInput API 遍历 4 端口，任一连接返回 True |

### 日志分级

| 级别 | 用途 | 示例 |
|------|------|------|
| DEBUG | 详细调试信息 | 模板匹配各尺度结果、保存调试图路径、第N次按B、摇杆方向值 |
| INFO | 关键业务日志 | 归位完成、返回主界面、开始循环、本轮完成、导航完成 |
| WARNING | 警告但流程继续 | 截图失败、归位超时、模板不存在、按钮未找到 |
| ERROR | 错误需要关注 | 模板加载失败、连接失败、Pipeline异常 |

### gui.py 改进

| 改进 | 说明 |
|------|------|
| 窗口可拖拽 | `resizable(True, True)` 替代原有的 `False, False` |
| 安全最小尺寸 | `minsize(480, 400)` 防止窗口缩到 UI 不可用 |
| 日志过滤 | `logger.get_lines(min_level)` 按级别过滤，GUI 默认 INFO+ |
| 物理手柄检测 | `has_physical_controller()` 检测到物理手柄时弹出 `tk.Toplevel` 阻止运行（带 ico） |
| 弹窗图标修正 | 用 `dlg.iconbitmap(str(icon_path))` 确保自定义对话框继承应用图标 |

## 未完成任务

暂无。

## 文件结构

```
d:\maaracing_assistant/
├── main.py              # 主入口：YOLO + Pipeline + Homing + 导航 + 日志
├── gui.py               # 图形界面（ttkbootstrap + UAC 提权）
├── HANDOVER.md          # 本文件 — AI 助手上下文文档
├── update_log.md        # 修改历史记录
├── README.md            # 快速开始
├── requirements.txt     # 依赖
├── .gitignore
├── assets/
│   ├── model/
│   │   └── yolov8n_coins_cars.onnx   # YOLO ONNX 模型（3 类：coin/car/bonus_car）
│   ├── resource/
│   │   ├── image/                     # 模板图片
│   │   │   ├── settings_page_template.jpg   # 归位：设置页面 ✅
│   │   │   ├── activity_page_template.jpg   # 导航：活动页面 ✅
│   │   │   ├── cursor_template.jpg          # 导航：光标 ❌ 已废弃
│   │   │   └── button_main_template.jpg     # 导航：已废弃（位置硬编码）
│   │   └── pipeline/
│   │       └── tasks.json            # MAA Pipeline 流程定义
│   └── icon.ico
├── config/
│   └── maa_option.json               # MAA 配置（save_on_error 开启）
├── dataset/
│   ├── images/train/   (150 张)
│   ├── images/val/     (38 张)
│   ├── labels/train/   (150 个)
│   └── labels/val/     (38 个)
├── tools/
│   ├── train.py         # YOLO 训练脚本（自动导出 ONNX + 复制到 assets）
│   ├── dataset.yaml     # 数据集配置（3 类：coin / car / bonus_car）
│   ├── yolov8n.pt       # 预训练权重 YOLOv8n
│   └── yolo26n.pt       # 预训练权重 YOLO26n
├── logs/                # 运行日志 MRA_*.log（gitignore）
├── debug/               # 调试输出（gitignore）
│   ├── homing_debug.png            # 归位首帧调试截图
│   └── on_error/                   # MAA save_on_error 保存的失败截图
└── MEMORY.md
```

## Pipeline 流程

`tasks.json` 定义了 6 步闭环：
```
极速狂飙入口 → 回合1准备 → 回合1比赛(RacingLoop) → 回合1结束 → 回合2放弃 → 确认放弃 → 循环
```

## 决策优先级（`RacingLoop._decide()`）

```
0️⃣ bonus_car（跳板车/油罐车）→ 对准撞上去
1️⃣ 障碍车（car）→ 躲避（3 车道判断，检查两侧是否被占）
2️⃣ 金币（coin）→ 吃（选最近的）
3️⃣ 无目标 → 直行
```

## 关键参数速查

| 参数 | 当前值 | 位置 | 状态 |
|------|--------|------|------|
| 归位阈值 | 0.70（彩色匹配） | `_match_settings_page()` | ✅ |
| 归位搜索区域 | 左上角 50%×50% | `_match_settings_page()` | ✅ |
| 光标识别方式 | 几何形状（圆度≥0.82 + 面积评分中心 260） | `_find_cursor_by_shape()` | ✅ |
| 按钮1(极速狂飙入口)位置 | 88.0%, 72.0% | `BTN_极速狂飙入口.pct` | ✅ |
| 按钮2(开始挑战)位置 | 85.5%, 89.8% | `BTN_开始挑战.pct` | ✅ |
| 按钮1 对齐阈值 | 50 px（最短边/2） | `BTN_极速狂飙入口.close_threshold` | ✅ |
| 按钮2 对齐阈值 | 25 px（最短边/2） | `BTN_开始挑战.close_threshold` | ✅ |
| 按钮1 模板验证 | 匹配 = 成功（template_should_match=True） | `BTN_极速狂飙入口` | ✅ |
| 按钮2 模板验证 | 消失 = 成功（template_should_match=False） | `BTN_开始挑战` | ✅ |
| 微调停止距离 | 25 px | `_move_cursor_to_target(stop_distance=25)` | ✅ |
| 导航超时 | 30 帧 | `navigate_to_button()` | ✅ |
| 光标丢失超时 | 2 秒 | `navigate_to_button()` | ✅ |
| 假光标卡死阈值 | 同位置≥3 帧 → 强制丢失 | `navigate_to_button()` | ✅ |
| 摇杆最大幅值 | 8000 | `_move_cursor_to_target(MAX_AXIS=8000)` | ✅ |
| 摇杆死区 | 4260（13%） | `_move_cursor_to_target()` | ✅ |
| 死区策略 | 独立死区（每轴<4260→升到4260，不缩放） | `_move_cursor_to_target()` | ✅ |
| 远距推送 | >150px: speed=0.7~1.0, hold=0.2s | `_move_cursor_to_target()` | ✅ |
| 中距推送 | >70px: speed=0.55~0.75, hold=0.1s | `_move_cursor_to_target()` | ✅ |
| 近距推送 | <70px: speed=0.5, hold=0.06s | `_move_cursor_to_target()` | ✅ |
| 刹车时间 | 50ms（推完摇杆归零） | `_move_cursor_to_target()` | ✅ |
| 导航一重试 | 3次（destroy→2s→homing→retry） | `start()` | ✅ |
| 导航二重试 | 3次（destroy→2s→continue外层循环） | `start()` | ✅ |
| 归位最大按B次数 | 15 | `homing()` | ✅ |
| 按B持续时间 | 0.3 s | `_press_button(duration=0.3)` | ✅ |
| 归中推摇杆值 | 12000（首次导航前） | `navigate_to_button(center_first)` | ✅ |
| YOLO 置信度 | 0.50 | `YOLODetector(conf=0.5)` | ✅ |
| YOLO NMS IoU | 0.45 | `YOLODetector(iou=0.45)` | ✅ |
| 赛车帧率 | 15 FPS | `RacingLoop.run()` 循环 | ✅ |

## 对 AI 助手的要求
1. 先沟通对齐，再输出代码
2. 兼容性优先，性能次之
3. 不了解的技术先暂停，调查后再决策
4. 必须确保缩进正确（4 空格）
5. 长代码优先给完整文件
6. 能提问就提问，关键决策必须确认
7. **MAA `Win32Controller.post_screencap()` 需用 `.wait().get()` 解包**
