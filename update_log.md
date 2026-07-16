# MaaRacingAssistant 修改日志

> 按时间顺序记录每次重大修改。

---

## 2026-07-17

### v0.4.0 光标识别重构+假光标拉黑+debug可视化 🎉
- **版本号：** `__version__ = "0.4.0"`
- **双中心面积评分：** `_find_cursor_by_shape` 改用双中心评分（常态 310 / 变形 420），同时覆盖两种光标形态，不再依赖单一面积中心
- **面积硬过滤：** `area < 240` 直接排除假光标（~206-221），不再进入候选池
- **运动 Y 轴校正：** vgamepad ly 正=上 vs 屏幕 Y 正=下，点积改用 `sy = -ly/stick_len` 修正
- **假光标静止拉黑：** 跨帧位置对比（`_prev_frame_positions: set[tuple]`），推摇杆时不动的候选累计静止帧，`cnt ≥ 3` 直接 `continue` 拉黑，切页面清空
- **`_last_stick` 保留：** `_press_and_verify` 失败后不再清空 `_last_stick`，保留推杆方向供下帧静止惩罚/运动评分用（修复原 bug：清空后运动评分块整个跳过，假光标不扣分）
- **close_threshold 12px：** 第二个按钮阈值 25→12，收缩公式 `max(30, -15)` → `max(5, ×0.65)`
- **自适应 stop_distance：** `max(8, close_th × 0.55)` 替代硬编码 25px，确保收缩后光标能推到足够近
- **微调移动档位：** < 35px 增加 25ms 脉冲微调档（原 120ms 65% 在死区 4260 下一推就飞）+ 刹车自适应（<35px 时 80ms 刹车替代 50ms）
- **debug.py 创建：** `NavigationDebugger` 四色标注（红=选中光/绿=入围/黑=拉黑/蓝=按钮），每帧保存到 `debug/navigate/`
- **GUI debug 开关：** 主界面 Checkbutton 控制每帧截图，同步到 controller.debug.enabled
- **假光标减速/刹车/评分参数依据 1080p 重新校准**（原基于 1440p）

---

## 2026-07-14

### v0.3.0 导航重构+物理手柄检测+第二个按钮通过 🎉
- **版本号：** `__version__ = "0.3.0"`
- **导航重构：** `ButtonDef` 配置类统一管理按钮（`name`/`pct`/`page_template`/`template_should_match`/`close_threshold`），新增按钮只需一行定义
- **模板匹配正反逻辑：** `template_should_match=True` 匹配到模板=成功，`False` 模板消失=成功，同时支持"进入页面"和"离开页面"两种场景
- **代码瘦身：** 提取 `_press_and_verify`/`_stop_stick`/`_ensure_cursor`/`_blind_move` 等方法，`navigate_to_button` 从 ~220 行精简到 ~80 行
- **物理手柄检测：** `has_physical_controller()` 通过 XInput API 遍历 4 端口，GUI 检测到手柄时弹自定义对话框阻止运行（带 icon.ico）
- **弹窗图标修复：** `messagebox.showerror` → 自定义 `tk.Toplevel + iconbitmap`，正确继承应用图标
- **第二个按钮测试通过：** "开始挑战" 25px 阈值成功命中，模板消失验证通过
- **新增模板：** `activity_page_template.jpg` (1100×550) 活动页面模板
- **清理：** 删除 `diagnose_coords.py` 调试文件
- **文档更新：** HANDOVER.md 全面反映重构后架构，CLAUDE.md 更新状态

### 光标导航首次打通 🎉
- **问题：** 彩色模板匹配归位正常（0.706），但光标导航卡在最后 ~50px 到不了按钮
- **根因：** 摇杆幅度低于游戏死区（4192 < 4260 阈值）+ 面积评分中心 1200 误识别为 470 面积的假光标
- **修复：**
  1. **光标面积评分中心 1200→260**，470 面积的假光标被扣到零分，不再误识别（`_find_cursor_by_shape`）
  2. **摇杆最低速度 0.5→0.6**，保证幅度 4800 > 4260 游戏死区，光标能推到最后（`_move_cursor_to_target`）
  3. **光标丢失 ≥2 秒 → 放弃导航**，利用 `finally` 销毁手柄触发游戏自动复位光标（`navigate_to_button`）
- **版本号：** 添加 `__version__ = "0.2.0"`

### 更新 HANDOVER.md 标明未完成状态
- 标记光标导航为 ❌ 未完成
- 新增"未完成任务"章节，详细说明光标追踪导航的问题
- 更新模板表格，标注各模板状态
- 更新参数表，加入状态列
- 添加 MAA 截图坐标映射未验证的已知坑点

### 导航盲推尝试
- 按钮位置改为百分比硬编码 (89.8%, 75.1%)，不再用模板匹配
- 光标匹配阈值 0.70→0.60，启用灰度匹配
- 摇杆幅值 32767→8000 防过冲
- 归中推摇杆值 20000→6000
- **结果：光标模板假阳性，导航仍未通过**

---

## 2026-07-13

### 启动归位 + 光标追踪导航（大重构）
- **问题：** stop 后多跑一轮、B 键无反应、阈值太高、模板误匹配
- **修复：** `_press_button(duration=0.3)`、`_interruptible_sleep()`、阈值 0.55
- **新增：** `_move_cursor_to_target()`、`navigate_to_button()`、光标归中
- **新增：** `_load_template()`、`_find_template()`（多尺度 + ROI + 灰度匹配）
- **新增：** `_screencap_ctypes()` 备用截图
- 规范化图片命名：`settings_page_template.jpg`、`cursor_template.jpg`、`button_main_template.jpg`

### 日志分级 + 文件名变更
- 新增日志级别：DEBUG / INFO / WARNING / ERROR
- GUI 仅显示 INFO+
- 文件名 `maazs_*` → `MRA_*`
- `Logger.get_lines(min_level)` 实现级别过滤

---

## 2026-07-12

### Pipeline 日志 + RT 加速 + YOLO 决策日志
- **PipelineLogger：** `ContextEventSink` 监听每步识别/动作成功状态
- **RT 加速：** `RacingLoop.run()` 起步 `right_trigger(255)`
- **YOLO 决策日志：** `_decide()` 打印每种决策的中文日志

### 虚拟手柄生命周期管理
- `__init__` 不再创建手柄，改为 `_create_pad()` / `_destroy_pad()` 对
- 每次 `run()` 新创建 + 3 次归零握手清理驱动偏置
- `_steer()` 增加右摇杆归中 + 空指针保护

### GUI 窗口可拖拽
- `resizable(True, True)` + `minsize(480, 400)`

### Pipeline 优雅中断
- `MaaRacingAssistantController.stop()` 增加 `tasker.post_stop()`

### 项目重命名
- `MaaRM-Alpha` → `MaaRacingAssistant`

---

## 2026-07-11 及之前（初始构建）

### 项目初始化
- MAA Framework 5.11.1 集成
- YOLOv8 + ONNX Runtime 视觉识别
- vgamepad 虚拟手柄控制
- ttkbootstrap GUI
- 数据集 188 张标注（3 类：coin / car / bonus_car）
- YOLO 训练 mAP50≈0.92
- Pipeline 6 步闭环：`入口→回合1准备→比赛→结束→回合2放弃→确认→循环`
