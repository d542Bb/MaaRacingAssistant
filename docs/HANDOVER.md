# MaaRacingAssistant v0.7.0 — 开发交接文档

## 项目目标
自动完成《巅峰极速》"极速狂飙"活动：
**启动归位**（按B直到设置页面，再按B回主界面）→ **光标导航**（`ButtonDef` 配置驱动，3 步导航进比赛 → 左摇杆移动光标到按钮，按A确认）→ 回合1赛车（YOLO识别 + 黄色标线车道检测 + 手柄控制）→ 回合2放弃 → 循环

## 技术栈
- MAA Framework 5.11.1（UI 流程 + 窗口控制）
- YOLOv8 + ONNX Runtime DirectML（视觉识别，3 类：coin / car / bonus_car）
- vgamepad（虚拟 Xbox 手柄）
- OpenCV（模板匹配：归位识别设置页面 + 导航验证页面切换 + 结束检测）
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
- ✅ **Pipeline 绑定** — `tasks.json` 4 步线性流程：`比赛→结束→放弃→确认`，导航由 Python 主循环驱动
- ✅ **YOLO 模型** — 已训练 mAP50≈0.92，ONNX 已导出 `assets/model/model.onnx`（3 类：coin / car / bonus_car）
- ✅ **Pipeline 日志** — `PipelineLogger(ContextEventSink)` 监听每步识别命中/动作成功状态
- ✅ **RT 加速** — `RacingLoop.run()` 起步按住 `right_trigger(255)`，`finally` 释放
- ✅ **YOLO 决策日志** — `_decide()` 每步打印 bonus_car 对准/障碍避让/金币吃取/直行的中文日志
- ✅ **启动归位（Homing）** — 按B直到识别到设置页面（多尺度模板匹配，阈值0.65），再按B返回主界面
- ✅ **日志分级** — DEBUG/INFO/WARNING/ERROR 四级，GUI 默认只显示 INFO 及以上
- ✅ **可中断睡眠** — `_interruptible_sleep()` 每 0.1 秒检查 `_running`，stop 能立即响应
- ✅ **手柄生命周期管理** — `_create_pad()` / `_destroy_pad()` 对，每次赛车前创建并归零，结束后销毁
- ✅ **光标导航** — `ButtonDef` 配置驱动，模板匹配正反逻辑，双中心面积评分，独立死区摇杆，假光标静止拉黑
- ✅ **第二个按钮（"开始挑战"）** — 测试通过，12px 阈值成功命中并退出活动页
- ✅ **导航三（"寻找对手"）** — `find_opponent_template.jpg` 模板匹配，`template_should_match=False`（消失=成功），25px 阈值 ✅ v0.5.0
- ✅ **PEEP 实时预览** — GUI 独立开关，OpenCV 独立线程实时显示调试帧，不依赖 DEBUG 存盘 ✅ v0.5.0
- ✅ **YOLO 检测可视化** — PEEP 窗口实时显示 YOLO 检测框（金色=coin/红色=car/紫色=bonus_car）+ 置信度 ✅ v0.5.0
- ✅ **模板匹配可视化** — PEEP 窗口实时显示模板匹配位置（青色矩形）+ 置信度 ✅ v0.5.0
- ✅ **黄色标线车道检测** — HSV 滤波（H:15-35, S:80-255, V:80-255）检测道路两侧黄色标线，提供左右边界和车道中心参考 ✅ v0.7.0
- ✅ **全局路径规划** — `_decide()` 重写：边缘紧急修正 > bonus_car > 障碍避让(车道约束) > 金币(链式评分) > 保持道路中心 ✅ v0.7.0
- ✅ **车道约束避让** — 以车道中心为参照，检查左右占道，不再以画面中心为"正" ✅ v0.7.0
- ✅ **YOLO ROI 裁剪** — 只检测 y28%~78% 路面区域（1280×720→1280×360），减少 UI 干扰，坐标自动回映射到全屏 ✅ v0.7.0
- ✅ **导航阈值分辨率自适应** — 所有硬编码像素阈值改为 `min_dim` 百分比：FAR=20%/MID=10%/NEAR=5%/BASE=28%，ALIGN_PX=2.5% ✅ v0.7.0
- ✅ **PEEP/存盘双模式可视化** — `_render_full()` 全量绘制存盘 vs `_render_peep()` 精简绘制预览，独立渲染互不干扰 ✅ v0.7.0
- ✅ **双手柄冲突修复** — controller.py 在 racing 开始前销毁导航手柄 ✅ v0.7.0

### maaracing_assistant/ 包结构

v0.6.0 已将 `main.py` 拆分为 6 个单一职责模块并入 `maaracing_assistant/` 包目录：

| 模块 | 职责 |
|------|------|
| `maaracing_assistant/logger.py` | Logger 类 + 全局实例，分级日志，零项目依赖 |
| `maaracing_assistant/window_utils.py` | 窗口查找 + XInput 物理手柄检测 |
| `maaracing_assistant/yolo_detector.py` | YOLODetector ONNX 推理封装 |
| `maaracing_assistant/pipeline_logger.py` | PipelineLogger MAA 事件监听 |
| `maaracing_assistant/racing_loop.py` | RacingLoop CustomAction 赛车控制 |
| `maaracing_assistant/controller.py` | MaaRacingAssistantController 总控编排 |
| `maaracing_assistant/navigation.py` | Navigation + ButtonDef 光标导航 |
| `maaracing_assistant/gui.py` | MRAGUI ttkbootstrap 窗口 |
| `maaracing_assistant/debug.py` | NavigationDebugger 调试可视化 |
| `maaracing_assistant/opencv_utf8_patch.py` | OpenCV 中文路径补丁 |
| `run.py` | 快捷入口，一行导入 gui.main() |

### 模板图片

4 张模板图片统一存放在 `assets/resource/image/`，命名格式 `{用途}_template.{ext}`：

| 文件 | 用途 | 匹配阈值 | 状态 |
|------|------|----------|------|
| `settings_page_template.jpg` (~484×300) | 归位：识别设置页面（左上角区域，彩色匹配） | 0.65 | ✅ 正常 |
| `activity_page_template.jpg` (1100×550) | 导航：识别活动页面 / 检测页面已离开（第二个按钮） | 0.70 | ✅ 正常 |
| `find_opponent_template.jpg` (374×195) | 导航三：识别寻找对手页面，按钮消失验证 | 0.55 | ✅ v0.5.0 |
| `store_popup_template.jpg` (159×262) | 商店弹窗检测 + `_is_end` 结束检测 | 0.55 | ✅ v0.6.0 |
| `round1_end_template.jpg` | 回合1结束画面检测（用户截图重命名） | 0.55 | ✅ v0.6.0 |
| `cursor_template.jpg` (168×176) | 导航：旧模板，已废弃改用几何形状识别 | — | ❌ 已废弃 |
| `button_main_template.jpg` (~242×67) | 导航：已废弃，按钮位置改为百分比硬编码 | — | ❌ 已废弃 |

### 启动流程（`MaaRacingAssistantController.start()` v0.6.0+）

```
[初始化]
  1. check_model()    → 检查 ONNX 模型存在
  2. connect()        → 查找窗口 → Win32Controller → Tasker 绑定
  3. _running = True  → 允许 stop 中断

[大厅层]
  4. homing()         → 按B×N → 截图匹配设置页面 → 再按B回主界面（仅首次）
  5. while _running:
     a. 导航一(极速狂飙入口) ×3:
        成功 → 模板出现确认
        失败 → destroy → 等2s → homing() → 重试
        3次全失败 → 整体结束

     b. while _running:    ← 对局层循环
        ─── 关口：导航二(开始挑战) ×6 ───
           成功 → 模板消失确认 → _in_match = True（已进入对局）
           失败且 _in_match=False → destroy → 等2s → 原地重试（首次穿插homing+导航一兜底）
           失败且 _in_match=True  → 直接停止流程（对局中不回退大厅）
           6次全失败(首次) → break(回大厅层)

        ═══ 导航三(寻找对手) ×6 ═══
          先 _wait_for_template(超时15s) → 等待页面出现
          成功 → 模板消失确认
          失败 → destroy → 等2s → 原地重试
          6次全失败 → 停止流程（对局层异常不可恢复）

        ═══ 商店弹窗处理 ═══
          _wait_for_template("store_popup_template", 15s)
          按A关闭 → 验证消失
          光标自动复位 → 直接进入确认上阵导航

        ═══ 确认上阵 ═══
          导航到(82.3%,93.1%) → 按A

        ═══ 比赛(RacingLoop) ═══
          run_direct(self.controller) 直接运行（绕过 MAA CustomAction）
          运行 < 3秒 → 判定异常，最多重试 3 次
          全部异常 → 停止流程
          正常完成 → post_task("回合1结束") → Pipeline OCR 处理后续
          _in_match = False → continue 从导航二开始下一轮
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
_ensure_cursor() → 找不到就4方向搜索，还找不到进盲操

对 _ in range(30):
  截图 → _find_cursor_by_shape(..., last_known_pos=_, last_stick=_last_stick)
  按钮坐标 = 截图尺寸 × btn.pct
  缓存帧跳过? → 光标弹回左上角且面积缩小 → 等100ms跳过本帧
  未找到光标? → 计时, ≥2秒放弃
  找到光标? → 距离 < close_th? → _press_and_verify()
              否则 → _move_cursor_to_target(stop_distance=adaptive)

  _press_and_verify:
    停摇杆 → 按A(0.3秒) → 等待1秒
    截图 → 模板匹配
    template_should_match=True?  → 匹配到=成功 return True
    template_should_match=False? → 模板消失=成功 return True
    模板未变化? → close_th ×0.65 收缩(下限5px) → return None(重试)
    Fallback: 光标面积降>100 → 成功

  假光标静止检测: 候选人用自己的位置跨帧对比
    _prev_frame_positions中有该位置+推杆中 → cnt+=1
    cnt≥3 → continue彻底拉黑(切页面清空)
超时 → return False
finally: 摇杆归零
```

**关键经验：**
- 光标用**双中心面积评分**（常态 310 / 变形 420），覆盖两种形态，`area < 240` 硬过滤假光标
- 假光标静止拉黑：**不依赖 `last_known_pos`**（被选中光标位置），而是跨帧对比候选人自己的位置（`_prev_frame_positions: set[tuple]`）
- **`_press_and_verify` 失败后不清空 `_last_stick`**，保留推杆方向让下帧运动评分/静止惩罚继续生效
- 收缩保底公式 `max(5, int(close_th × 0.65))`，不是 `max(30, -15)`（后者对 25px 反而放大）
- **不要加微轴归零阈值**（如 `abs(dy)<10→ly=0`），否则光标在目标附近±10px内无法做Y方向最终修正
- 独立死区：每个轴独立升到4260（保留方向），不再等比例缩放
- vgamepad Y 轴取反：`uy = -dy / dist`
- 刹车时间自适应：<35px 时 80ms，否则 50ms
- 销毁手柄（`del gpad`）让游戏自动把光标复位到左上角
- **`_get_gpad()` 创建手柄后必须做 3 次 `reset()+update()` 归零握手**清除驱动层偏置，否则首推方向异常
- `_ensure_cursor` 4方向搜索顺序：右上→左上→右下→左下（vgamepad y正=下，y负=上）
- **导航三失败=对局内异常** → 直接停止，不复位不回退（对局内按B无效）
- **比赛完成** → 从导航二开始下一轮（不经过归位+导航一）
- **`_in_match` 标记控制回退行为**：导航二成功前可回大厅，成功后不回
- **DirectML 优先于 CUDA**：`onnxruntime-directml` 不需要 CUDA Toolkit，RTX 4060 推理 ~3.7ms
- **ONNX Session 选项**：`graph_optimization_level=ORT_ENABLE_ALL` + `optimized_model_filepath` 持久化图优化模型到 `__pycache__/ort_cache/`，DirectML 内核缓存同样存该目录
- **`_is_end` 统一模板匹配**：不再用 ROI 白色区域检测，加载 `_end_templates` 列表，所有模板用 `matchTemplate` TM_CCOEFF_NORMED 阈值 0.55，任一匹配即返回 True
- **比赛异常重试**：RacingLoop `run_direct` 运行 < 3 秒判定异常，重试 3 次后停止
- 按钮用 `ButtonDef` 配置类统一管理，新按钮只需加一行定义
- 导航调试用 `debug.py`（NavigationDebugger），多色标注体系
- PEEP 模式用 OpenCV 独立线程实时预览调试帧，不依赖 DEBUG 存盘

### 导航重试机制（`start()` v0.6.0+）

```
[大厅层] 归位(仅首次)
  │
  导航一(极速狂飙入口) ×3:
    失败 → _destroy → 等2s → homing() → 重试
    3次全失败 → break(整体结束)
  │
  [对局层循环]
    │
    导航二(开始挑战) ×6:       ← 关口：_in_match 控制回退行为
      成功 → _in_match = True
      失败且 _in_match=False → 原地重试，穿插 homing+导航一兜底
      失败且 _in_match=True  → 直接停止（首次以外不回退大厅）
      6次全失败(首次) → break(回大厅)
    │
    导航三(寻找对手) ×6:       ← 对局内
      失败 → _destroy → 等2s → 原地重试
      6次全失败 → 停止流程
    │
    商店弹窗 → 确认上阵 → 比赛
    │
    比赛(RacingLoop):          ← 直接 run_direct，<3秒重试×3
      异常/短运行 → 最多重试3次→全部失败→停止
      正常完成 → post_task("回合1结束") → _in_match=False → 从导航二开始下一轮
```

**关键设计原则：**
- **导航二成功前（_in_match=False）** → 可回退大厅从导航一重试
- **导航二成功后（_in_match=True）** → 任何失败不回退大厅，直接停止（对局内按B无效）
- **比赛异常重试** → 运行 <3秒算异常，最多 3 次，全部异常停止

### 基础工具方法

| 方法 | 说明 |
|------|------|
| `_screencap()` | 截图 RGB ndarray（MAA → 失败回退 ctypes GDI） |
| `_screencap_ctypes()` | Win32 GDI 备用截图方案 |
| `_press_button(gpad, button, duration)` | 按下→保持→释放，默认 0.3 秒 |
| `_interruptible_sleep(seconds)` | 每 0.1 秒检查 `_running` 的可中断 sleep |
| `_load_template(name)` | 加载模板（优先 png → jpg），返回 RGB ndarray |
| `_find_template(img, template, threshold, scales)` | 多尺度 `TM_CCOEFF_NORMED`，返回 (位置, 置信度, 缩放) |
| `_move_cursor_to_target(cursor_pos, target_pos, gpad, stop_distance)` | 左摇杆移动光标（四档距离自适应 + 自适应刹车 + 独立死区） |
| `_stop_stick(gpad)` | 摇杆归零（3 次全零报告） |
| `_ensure_cursor(gpad)` | 当前帧无光标时 4 方向搜索 |
| `_blind_move(gpad, last_pos, target, elapsed)` | 光标丢失时盲推一次 |
| `_press_and_verify(gpad, cursor_area, dist_button, btn)` | 按A + 模板验证 + 面积变化兜底，返回 True/None/False |
| `_dist(p1, p2)` | 静态欧几里得距离计算 |
| `_find_cursor_by_shape(img, last_known_pos, last_stick)` | 双中心面积评分 + 假光标静止拉黑 + 运动一致性评分 |
| `_wait_for_template(template_name, timeout, interval)` | 轮询等待模板出现，超时返回 False |
| `NavigationDebugger(proj_dir)` | PEEP 实时预览 / debug 截图标注，支持 template_rects + detections |
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
├── run.py                               # 快捷入口：python run.py
├── pyproject.toml                       # 现代 Python 项目配置（pip install -e . 支持）
├── maaracing_assistant/                 # 应用包（13 个源文件）
│   ├── __init__.py                      # 版本号 "0.6.0"
│   ├── __main__.py                      # python -m maaracing_assistant 入口
│   ├── controller.py                    # MaaRacingAssistantController（总控编排）
│   ├── navigation.py                    # Navigation + ButtonDef（光标导航）
│   ├── racing_loop.py                   # RacingLoop CustomAction（YOLO 赛车控制）
│   ├── yolo_detector.py                 # YOLODetector（ONNX 推理）
│   ├── logger.py                        # Logger（文件+内存日志）
│   ├── pipeline_logger.py               # PipelineLogger（MAA 事件）
│   ├── window_utils.py                  # 窗口查找 + XInput 物理手柄检测
│   ├── gui.py                           # MRAGUI（ttkbootstrap 窗口）
│   ├── debug.py                         # NavigationDebugger（调试可视化）
│   └── opencv_utf8_patch.py             # OpenCV 中文路径补丁
├── docs/                                # 文档集中
│   ├── HANDOVER.md                       # 本文件
│   └── update_log.md                    # 修改历史
├── README.md                            # 快速开始
├── CLAUDE.md                            # AI 助手项目配置
├── requirements.txt                     # 依赖
├── .gitignore
├── assets/
│   ├── model/
│   │   └── model.onnx                 # YOLO ONNX 模型（3 类：coin/car/bonus_car）
│   ├── resource/
│   │   ├── image/                     # 模板图片
│   │   │   ├── settings_page_template.jpg   # 归位：设置页面 ✅
│   │   │   ├── activity_page_template.jpg   # 导航：活动页面 ✅
│   │   │   ├── find_opponent_template.jpg   # 导航三：寻找对手 ✅ v0.5.0
│   │   │   ├── cursor_template.jpg          # ❌ 已废弃
│   │   │   └── button_main_template.jpg     # ❌ 已废弃
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
│   ├── dataset.yaml     # 数据集配置（3 类：coin / car / bonus_car）
│   ├── yolo11n.pt       # 预训练权重 YOLO11n（首次训练自动下载）
│   └── yolo26n.pt       # 预训练权重 YOLO26n
├── logs/                # 运行日志 MRA_*.log（gitignore）
└── debug/               # 调试输出（gitignore）

## Pipeline 流程

`tasks.json` 定义了 4 步线性流程（导航由 Python 主循环驱动）：
```
回合1比赛(RacingLoop) → 回合1结束 → 回合2准备 → 确认放弃
```

## 决策优先级（`RacingLoop._decide()`）

```
0️⃣ 边缘紧急修正 → 车道偏离 >20% 或 >80% 时立即回中（最高优先级）
1️⃣ bonus_car（跳板车/油罐车）→ 对准撞上去（以车道中心为参考）
2️⃣ 障碍车（car）→ 车道内避让（检查左右占道，DANGER_Y=h*0.35）
3️⃣ 金币（coin）→ 链式评分：cy + 附近同伴数×50，选"最有价值"金币
4️⃣ 无目标 → 保持道路中心（有标线时），否则直行
```

## 关键参数速查

| 参数 | 当前值 | 位置 | 状态 |
|------|--------|------|------|
| 归位阈值 | 0.70（彩色多尺度匹配，0.8~1.2×） | `_match_settings_page()` | ✅ |
| 归位搜索区域 | 左上角 50%×50% | `_match_settings_page()` | ✅ |
| 光标灰度阈值 | 185 | `_find_cursor_by_shape()` | ✅ |
| HSV 饱和度过滤 | S < 30 保留（光标灰白 S≈0，彩色 UI 挖掉） | `_find_cursor_by_shape()` | ✅ |
| 光标面积硬过滤 | area < 240 直接排除 | `_find_cursor_by_shape()` | ✅ v0.4.0 |
| 双中心面积评分 | 常态 310 / 变形 420，各用 `1-abs(area-X)/300` 取 max | `_find_cursor_by_shape()` | ✅ v0.4.0 |
| 置信度阈值 | best_score < 0.70 → return None | `_find_cursor_by_shape()` | ✅ v0.4.0 |
| 按钮1(极速狂飙入口)位置 | 88.0%, 72.0% | `BTN_极速狂飙入口.pct` | ✅ |
| 按钮2(开始挑战)位置 | 85.5%, 89.8% | `BTN_开始挑战.pct` | ✅ |
| 按钮1 对齐阈值 | 50 px | `BTN_极速狂飙入口.close_threshold` | ✅ |
| 按钮2 对齐阈值 | 12 px（原 25px） | `BTN_开始挑战.close_threshold` | ✅ v0.4.0 |
| 收缩保底公式 | `max(5, int(close_th × 0.65))`（原 `max(30, -15)` 对 25px 反放大） | `_press_and_verify()` | ✅ v0.4.0 |
| 按钮1 模板验证 | 匹配 = 成功（template_should_match=True） | `BTN_极速狂飙入口` | ✅ |
| 按钮2 模板验证 | 消失 = 成功（template_should_match=False） | `BTN_开始挑战` | ✅ |
| 导航超时 | 30 帧 | `navigate_to_button()` | ✅ |
| 光标丢失超时 | 2 秒 | `navigate_to_button()` | ✅ |
| 假光标静止拉黑 | 推摇杆时同位置跨帧 ≥3 帧 → `continue` 拉黑 | `_find_cursor_by_shape()` | ✅ v0.4.0 |
| 黑名单清空时机 | 每次 `navigate_to_button()` 开始时 | `navigate_to_button()` | ✅ v0.4.0 |
| stop_distance | `max(8, close_th × 0.55)`（原硬编码 25px） | `navigate_to_button()` | ✅ v0.4.0 |
| 摇杆最大幅值 | 8000 | `_move_cursor_to_target(MAX_AXIS=8000)` | ✅ |
| 摇杆死区 | 4260（13%） | `_move_cursor_to_target()` | ✅ |
| 死区策略 | 独立死区（每轴<4260→升到4260，不缩放） | `_move_cursor_to_target()` | ✅ |
| 远距推送 | >min_dim*0.20: speed=0.7~1.0, hold=0.2s | `_move_cursor_to_target()` | ✅ v0.7.0 |
| 中距推送 | >min_dim*0.10: speed=0.55~0.75, hold=0.1s | `_move_cursor_to_target()` | ✅ v0.7.0 |
| 中近推送 | >min_dim*0.05: speed=0.45, hold=0.08s | `_move_cursor_to_target()` | ✅ v0.7.0 |
| 微调推送 | <min_dim*0.05: speed=0.28(被死区抬到4260), hold=0.025s | `_move_cursor_to_target()` | ✅ v0.7.0 |
| 刹车时间 | <min_dim*0.05 时 80ms，否则 50ms | `_move_cursor_to_target()` | ✅ v0.7.0 |
| 运动一致性评分 | `alignment × 0.15`，Y 取反 `sy = -ly/stick_len` | `_find_cursor_by_shape()` | ✅ v0.4.0 |
| 导航一重试 | 3次（destroy→2s→homing→retry） | `start()` | ✅ |
| 导航二重试 | 3次（destroy→2s→continue外层循环） | `start()` | ✅ |
| 归位最大按B次数 | 15 | `homing()` | ✅ |
| 按B持续时间 | 0.3 s | `_press_button(duration=0.3)` | ✅ |
| _ensure_cursor | 4方向搜索（不再有 center_first 归中推） | `navigate_to_button()` | ✅ v0.4.0 |
| YOLO 置信度 | 0.50 | `YOLODetector(conf=0.5)` | ✅ |
| YOLO NMS IoU | 0.45 | `YOLODetector(iou=0.45)` | ✅ |
| 赛车帧率 | 15 FPS（YOLO 每 3 帧推理一次） | `RacingLoop._run_impl()` | ✅ v0.6.0 |
| YOLO 推理后端 | DirectML（fallback CUDA → CPU） | `YOLODetector.__init__()` | ✅ v0.6.0 |
| ONNX 图优化 | ORT_ENABLE_ALL + 模型缓存到 __pycache__/ort_cache/ | `YOLODetector.__init__()` | ✅ v0.6.0 |
| _is_end 检测 | 模板匹配 `_end_templates` 列表（阈值 0.55） | `RacingLoop._is_end()` | ✅ v0.6.0 |
| 比赛异常重试 | <3 秒异常，最多 3 次，全部失败停止 | `start()` | ✅ v0.6.0 |
| _in_match 标记 | 导航二成功=True，完整一局结束=False | `start()` | ✅ v0.6.0 |
| debug 磁盘写盘 | 每 15 帧一次（PEEP 每帧更新） | `save_frame(save_to_disk=)` | ✅ v0.6.0 |
| 导航三(寻找对手)位置 | 80.4%, 75.3% | `BTN_寻找对手.pct` | ✅ v0.5.0 |
| 导航三对齐阈值 | 25 px | `BTN_寻找对手.close_threshold` | ✅ v0.5.0 |
| 导航三模板验证 | 消失 = 成功 (template_should_match=False) | `BTN_寻找对手` | ✅ v0.5.0 |
| 导航三重试 | 3次 (_wait_for_template 超时15s→destroy→retry) | `start()` | ✅ v0.5.0 |
| 模板匹配 scales (find_opponent) | 0.5, 0.7, 0.9, 1.0, 1.2, 1.5, 1.8 | `_check_page_by_template()` | ✅ v0.5.0 |
| 模板匹配阈值 (find_opponent) | 0.55 | `_check_page_by_template()` | ✅ v0.5.0 |
| PEEP 模式 | GUI 独立开关，OpenCV 线程实时预览调试帧 | `debug.py` / `gui.py` | ✅ v0.5.0 |
| YOLO 调试返回 | 每帧返回 debug_dets (框坐标+置信度+类名) | `YOLODetector.__call__()` | ✅ v0.5.0 |
| 导航三(寻找对手)位置 | 80.4%, 75.3% | `BTN_寻找对手.pct` | ✅ v0.5.0 |
| 导航三对齐阈值 | 25 px | `BTN_寻找对手.close_threshold` | ✅ v0.5.0 |
| 导航三模板验证 | 消失 = 成功 (template_should_match=False) | `BTN_寻找对手` | ✅ v0.5.0 |
| 导航三重试 | 3次 (_wait_for_template 超时15s→destroy→retry) | `start()` | ✅ v0.5.0 |
| 模板匹配 scales (find_opponent) | 0.5, 0.7, 0.9, 1.0, 1.2, 1.5, 1.8 | `_check_page_by_template()` | ✅ v0.5.0 |
| 模板匹配阈值 (find_opponent) | 0.55 | `_check_page_by_template()` | ✅ v0.5.0 |
| PEEP 模式 | GUI 独立开关，OpenCV 线程实时预览调试帧 | `debug.py` / `gui.py` | ✅ v0.5.0 |
| YOLO 调试返回 | 每帧返回 debug_dets (框坐标+置信度+类名) | `YOLODetector.__call__()` | ✅ v0.5.0 |

### v0.7.0 新增参数

| 参数 | 当前值 | 位置 | 状态 |
|------|--------|------|------|
| 黄色标线 HSV 范围 | H:15-35, S:80-255, V:80-255 | `RacingLoop._detect_lane()` | ✅ v0.7.0 |
| 标线检测区域 | y55%~80% 水平条 | `RacingLoop._detect_lane()` | ✅ v0.7.0 |
| 标线宽度校验 | 车道宽 30%~85% 画面宽 | `RacingLoop._detect_lane()` | ✅ v0.7.0 |
| 标线最小黄色像素 | ≥20 像素（左右各≥5） | `RacingLoop._detect_lane()` | ✅ v0.7.0 |
| 车道归中阈值 | 偏离 >20% 或 >80% | `RacingLoop._keep_center()` | ✅ v0.7.0 |
| 障碍车危险区 | DANGER_Y = h*0.35（中下部） | `RacingLoop._decide()` | ✅ v0.7.0 |
| 车道宽度基准 | LANE_W = w*0.12 | `RacingLoop._avoid()` | ✅ v0.7.0 |
| 威胁横向范围 | THREAT_RANGE = LANE_W*1.8 | `RacingLoop._avoid()` | ✅ v0.7.0 |
| 金币链式评分 | cy + 附近同伴数×50（附近<w*0.2 且 <h*0.3） | `RacingLoop._decide()` | ✅ v0.7.0 |
| 对准死区 | w*0.06（以车道中心为参考） | `RacingLoop._aim_at()` | ✅ v0.7.0 |
| YOLO ROI | (0, 201, 1280, 561) = y28%~78% | `RacingLoop.ROI` | ✅ v0.7.0 |
| 导航 FAR 阈值 | min_dim*0.20 (~144px @720p) | `_move_cursor_to_target()` | ✅ v0.7.0 |
| 导航 MID 阈值 | min_dim*0.10 (~72px @720p) | `_move_cursor_to_target()` | ✅ v0.7.0 |
| 导航 NEAR 阈值 | min_dim*0.05 (~36px @720p) | `_move_cursor_to_target()` | ✅ v0.7.0 |
| 导航 BASE 归一化 | min_dim*0.28 | `_move_cursor_to_target()` | ✅ v0.7.0 |
| 方向对齐 ALIGN_PX | max(12, min_dim*0.025) (~18px @720p) | `_move_cursor_to_target()` | ✅ v0.7.0 |
| PEEP 精简渲染 | YOLO框(无置信度)/标线/方向大字/统计，不画候选/模板 | `_render_peep()` | ✅ v0.7.0 |
| 存盘全量渲染 | 黑色过滤/绿紫候选/红色光标+评分/青色模板/YOLO+置信度/标线+坐标 | `_render_full()` | ✅ v0.7.0 |

## 对 AI 助手的要求
1. 先沟通对齐，再输出代码
2. 兼容性优先，性能次之
3. 不了解的技术先暂停，调查后再决策
4. 必须确保缩进正确（4 空格）
5. 长代码优先给完整文件
6. 能提问就提问，关键决策必须确认
7. **MAA `Win32Controller.post_screencap()` 需用 `.wait().get()` 解包**
