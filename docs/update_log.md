# MaaRacingAssistant 修改日志

> 按时间顺序记录每次重大修改。

---

## 2026-07-23

### v0.10.0 转向平滑校准 + 防碰撞优化 + 阴影标线检测 🎯
- **版本号：** `v0.10.0`
- **转向平滑系统：** 指数平滑 `smoothed = smoothed × alpha + target × (1-alpha)`，消除镜头惯性导致的摆动
- **alpha 校准状态机：** baseline→steer→settle 三阶段嵌入主循环，dd 加速度检测转向响应，自动计算 alpha = 0.5^(1/settle)
- **校准四区域策略：** 检测 L/R 标线 + 中线估测 → 决定先往中线打还是先往标线打，保证全程可见标线且不撞墙
- **校准数据验证：** settle 后检查标线位移 ≥15px，不够则重试（最多 2 次，每次转向帧数 +4），全部失败回退 alpha=0.6
- **C 区防碰撞 cum3 位移过滤：** 3 帧累计位移 >10px 才触发 C 区，防止车道 1 正常行驶误触（pos~500 触发旧阈值）
- **HSV 阴影标线检测：** S/V 下限从 150 降至 80，可识别 #7f7200 等阴影下的黄色标线
- **道路中线估测（`_estimate_road_center`）：** 从单侧标线推断中线位置，-50/+50 修正偏向中心
- **Debug 实时值追踪：** `_apply_trigger` / `_steer` 封装手柄操作并自动记录 `_last_rt` / `_last_stick`，debug 帧显示真实油门和摇杆值（不再硬编码）
- **Debug 校准可视化：** 校准帧 `save_to_disk=True`，label 带 frame_id，可查看完整校准过程

## 2026-07-23

### v0.9.0 赛车决策系统重构 + NMS 跨类抑制修复 + 车道保持 🔄
- **版本号：** `v0.9.0`
- **NMS 按类分别处理（`_nms_per_class`）：** 避免 YOLO 跨类 NMS 压掉 bonus_car（car 0.89 压 bonus_car 0.86），索引映射链 `mask_indices[cls_local[nms_idx]]`
- **三区变力度瞄准（`_aim_at`）：** 远区 50% / 中区 100% / 近区 0%，水平死区 ±0.06，替换旧的简单左/中/右三档
- **避障框重叠检测：** 车框左沿<R2c 且右沿>L2c 才触发躲避，不用中心点；`_avoid` 返回 0 时穿透到金币逻辑
- **闭环车道保持（`_lane_keep`）：** 漂移趋势检测（3 帧跨度 diff）+ 自适应力度调节（50%~100%），force_init 切回直行时立即回正
- **车道保持方向修复：** 右标线侧方向符号取反修复（`new_dir = 1 if diff > 0 else -1` 统一左右侧）
- **动态地平线推断（`_detect_horizon`）：** 从 YOLO 低置信度小车群（area<400, conf≤0.25）推测地平线，首次 ≥3 车锁死整局
- **透视车道分界线（`_lane_boundaries_at_y`）：** 梯形透视投影 `bound()` 线性外推，6 条线（LE/L12/L2c/R2c/R12/RE）
- **动态油门（`_calc_throttle`）：** 防撞 120 / 避障 180 / 金币&跳板车 200 / 直行 255
- **标线单边选择：** `_detect_lane` `side_score` 择优选一侧，返回 `{side, pos}` 替代旧 `{left, right, center}`
- **防碰撞重写：** 单边标线 `_wall_pos_history` 替代旧左右双历史，切换侧自动清空
- **标线丢失 C 区延续：** 无标线但有 `_wall_memory` 时直接进 C 区强制修正，不再等待记忆回带
- **Debug 可视化全面升级：** 区域分割线（地平线/远中近）、决策详情、动态油门值、透视车道线；虚线框去重 `_dedup_overlapping` + 实线框重叠隐藏
- **帧日志重写：** 统一 `[DECIDE]` 格式（帧号/决策/详情/标线/车况/金币/方向/油门），每 2 帧输出一次

---

## 2026-07-22

### v0.7.1 HoughLinesP 标线检测 + 三区防碰撞 + 反打修正 🛞
- **版本号：** `__version__ = "0.7.1"`
- **标线检测改为 HoughLinesP：** 从像素扫描改为 Hough 直线检测，y>50% 区域找最黄最直的线，断裂自动延长对齐，HSV H:20-30 S:150-255 V:150-255 严格滤波
- **三区防碰撞替代车道归中：** 移除 `_keep_center`，新增 `_wall_avoidance` 三区系统（A 区安全无干预 / B 区二阶导识别加速贴墙趋势 / C 区硬边界强制修正）
- **反打修正（突发+归中）：** C 区不再持续满打方向，改为"突发修正 2 帧（改变车头指向）→ 强制归中 5 帧（滑行远离墙）→ 重评估"的类人驾驶策略
- **不推断缺失侧标线：** 移除单侧推断代码，`_detect_lane` 只返回真实检测到的标线，防碰撞只信任真实侧
- **标线丢失记忆回带：** 新增 `_wall_memory` 机制，标线丢失但有历史记忆时（无 YOLO 目标）轻柔回带
- **`_aim_at`/`_avoid` 移除边界约束：** 去掉了标线边界约束，防碰撞由独立模块负责，变道吃金币不再受阻
- **Debug 摇杆状态条：** 底部方向文字 `<< LEFT` / `RIGHT >>` 替换为摇杆滑条指示器 + 数值显示
- **debug.py KeyError 修复：** `lane['right']` / `lane['left']` 改为 `.get()` 安全访问
- **CLAUDE.md 更新：** 新增防碰撞参数表，更新决策优先级和坑点

---

## 2026-07-21

### v0.7.0 黄色标线车道检测 + 全局路径规划 + PEEP/存盘双模式可视化 🎉
- **版本号：** `__version__ = "0.7.0"`
- **黄色标线车道检测：** `_detect_lane` HSV 黄色标线检测，提供道路边界和中心参考线
- **全局路径规划重写 `_decide`：** 边缘修正 > bonus_car 对准 > 车道约束避让 > 金币链式评分 > 归中，替代原简单优先级逻辑
- **车道中心替代画面中心：** `_keep_center` / `_avoid` / `_aim_at` 全部以车道中心为参考
- **YOLO ROI 区域裁剪：** `yolo_detector.py` 新增 `roi` 参数，y28%~78% 区域裁剪推理，减少天空/仪表盘干扰
- **导航百分比阈值：** `navigation.py` 硬编码像素阈值改为 `min_dim` 百分比（FAR/MID/NEAR/BASE/ALIGN_PX），适配不同分辨率
- **PEEP/存盘双模式渲染：** `debug.py` 拆分 `_render_full`（全量存盘）和 `_render_peep`（精简预览）两套独立渲染，PEEP 仅显示 YOLO 框/标线/方向指示器
- **双手柄冲突修复：** `controller.py` racing 开始前销毁导航手柄，解决双手柄冲突
- **YOLO11n 模型训练：** 从 yolov8n 升级到 yolo11n，753 张标注图片训练，mAP50=0.771
- **auto_label.py 预标脚本：** 用训练模型自动预标未标注图片，低阈值宁可多标不漏标
- **train.py 路径修复：** 导出路径从相对路径改为绝对路径，避免 `best.pt` 找不到

---

## 2026-07-20

### v0.6.0 DirectML GPU 推理 + 性能优化 + 流程重构 🚀
- **版本号保持 v0.6.0**（未升级版本号）
- **onnxruntime-directml 替代 CPU-only onnxruntime**：YOLO 推理从 ~33ms 降到 ~3.7ms（9×加速），解决 GPU 4060 未被使用的问题。无需安装 CUDA Toolkit，DirectX 12 即可
- **ONNX Session 缓存**：图优化（`ORT_ENABLE_ALL`）+ DirectML 内核缓存 + `model_optimized.onnx` 持久化到 `__pycache__/ort_cache/`
- **跳帧推理**：YOLO 每 3 帧推理一次，中间帧复用缓存结果，GPU 负载降到 1/3
- **`save_frame` 磁盘控制**：新增 `save_to_disk` 参数，PEEP 预览每帧更新（标注渲染仅 ~1-2ms），磁盘 `cv2.imwrite` 每 15 帧一次
- **`_is_end` 统一模板匹配**：去掉不可靠的白色区域检测，改用 `store_popup_template.jpg` + `round1_end_template.jpg` 模板匹配（阈值 0.55），`_is_shop` 逻辑合并进 `_is_end`
- **新增模板 `round1_end_template.jpg`**：用户截取的回合1结束画面
- **`_in_match` 对局标记**：导航二成功后标记已进入对局，此后所有失败不回退大厅，直接停止流程
- **RacingLoop 异常重试**：运行 < 3 秒判定异常，最多重试 3 次，全部异常停止
- **关闭 handle_store_popup 后的光标复位**：直接进入确认上阵导航
- **`requirements.txt` / `pyproject.toml`**：`onnxruntime` → `onnxruntime-directml`
- **删除 `profile_racing.py`**：临时性能剖析脚本已清理

---

## 2026-07-19

### v0.6.0 包结构重构 🏗️
- **版本号：** `__version__ = "0.6.0"`
- **创建包目录：** 将根目录全部源码移入 `maaracing_assistant/` 包目录
- **main.py 拆分：** 880 行上帝文件拆分为 6 个单一职责模块（`logger.py` / `window_utils.py` / `yolo_detector.py` / `pipeline_logger.py` / `racing_loop.py` / `controller.py`）
- **根目录精简：** 7 个 .py 文件减为 1 个（`run.py` 快捷入口）
- **pyproject.toml：** 添加 setuptools 项目配置，支持 `pip install -e .`
- **新增 `__main__.py`：** 支持 `python -m maaracing_assistant`
- **导入链验证：** 全部 9 个模块通过导入检查，零循环导入
- **环境清理：** 删除 milo 环境，maazs 重命名为 maaracing_assistant

---

## 2026-07-17

### v0.5.0 导航三+PEEP实时预览+YOLO可视化 🎉
- **版本号：** `__version__ = "0.5.0"`
- **导航三（寻找对手按钮）：** `find_opponent_template.jpg` (374×195) 模板匹配，等待页面加载（超时15s）→ 光标导航到按钮 → 模板消失验证。重试×3，失败回外层循环从头开始
- **Pipeline 重构：** 移除 OCR 预任务（极速狂飙入口/回合1准备），Python 主循环驱动全部导航，Pipeline 只做 RacingLoop + 结束/放弃
- **PEEP 实时预览模式：** GUI 独立开关 "PEEP 实时预览"，OpenCV 独立线程 (~30fps) 实时显示调试帧，不依赖 DEBUG 存盘
- **YOLO 检测可视化：** `YOLODetector.__call__()` 新增第4返回值 `debug_dets`（框坐标+置信度+类名），PEEP 窗口每帧显示金色/红色/紫色检测框
- **模板匹配可视化：** `_check_page_by_template()` 每帧传 template_rects（青色矩形+置信度）到 PEEP 窗口
- **归位可视化：** `homing()` 直接调用 `_find_template`，每帧显示模板匹配位置
- **扩充 scales 范围：** `_check_page_by_template` 的模板匹配 scales 从 [0.8~1.2] 扩展到 [0.5~1.8]，阈值降到 0.55
- **`_wait_for_template()` 新增：** 通用轮询等待模板出现方法，可配超时和间隔
- **PEEP 不依赖 DEBUG：** 即使不勾选"每帧截图"，PEEP 也能独立工作



### v0.5.0 导航三+PEEP实时预览+YOLO可视化 🎉
- **版本号：** `__version__ = "0.5.0"`
- **导航三（寻找对手按钮）：** `find_opponent_template.jpg` (374×195) 模板匹配，等待页面加载（超时15s）→ 光标导航到按钮 → 模板消失验证。重试×3，失败回外层循环从头开始
- **Pipeline 重构：** 移除 OCR 预任务（极速狂飙入口/回合1准备），Python 主循环驱动全部导航，Pipeline 只做 RacingLoop + 结束/放弃
- **PEEP 实时预览模式：** GUI 独立开关 "PEEP 实时预览"，OpenCV 独立线程 (~30fps) 实时显示调试帧，不依赖 DEBUG 存盘
- **YOLO 检测可视化：** `YOLODetector.__call__()` 新增第4返回值 `debug_dets`（框坐标+置信度+类名），PEEP 窗口每帧显示金色/红色/紫色检测框
- **模板匹配可视化：** `_check_page_by_template()` 每帧传 template_rects（青色矩形+置信度）到 PEEP 窗口
- **归位可视化：** `homing()` 直接调用 `_find_template`，每帧显示模板匹配位置
- **扩充 scales 范围：** `_check_page_by_template` 的模板匹配 scales 从 [0.8~1.2] 扩展到 [0.5~1.8]，阈值降到 0.55
- **`_wait_for_template()` 新增：** 通用轮询等待模板出现方法，可配超时和间隔
- **PEEP 不依赖 DEBUG：** 即使不勾选"每帧截图"，PEEP 也能独立工作



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
