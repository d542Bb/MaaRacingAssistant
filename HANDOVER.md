# MaaRM-Alpha 开发交接文档

## 项目目标
自动完成《巅峰极速》"极速狂飙"活动：
回合1赛车（吃金币+避让）→ 回合2放弃 → 循环

## 技术栈
- MAA Framework 5.11.1（UI 流程 + 窗口控制）
- YOLOv8 + ONNX Runtime（视觉识别，3 类：coin / car / bonus_car）
- vgamepad（虚拟 Xbox 手柄）
- ttkbootstrap（GUI）

## 已确认 API（重要！不要假设）
- `Toolkit.find_desktop_windows()` 返回 DesktopWindow 对象列表
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

## 当前状态
- GUI 正常，UAC 提权正常
- 窗口连接正常
- Pipeline 绑定正常
- **YOLO 模型已训练** ✅ 见 `assets/model/yolov8n_coins_cars.onnx`（3类: coin/car/bonus_car）
- **下一步**：运行 `gui.py` 连接游戏测试

## 文件结构
- `main.py` — 主入口
- `gui.py` — 图形界面
- `train.py` — YOLO 训练
- ~~`capture.py` — 截图采集（已移除）~~
- `tools/` — 工具脚本
- `assets/model/` — 模型文件
- `config/` — 配置文件
- `dataset/` — 数据集

## 对 AI 助手的要求
1. 先沟通对齐，再输出代码
2. 兼容性优先，性能次之
3. 不了解的技术先暂停，调查后再决策
4. 必须确保缩进正确（4 空格）
5. 长代码优先给完整文件
6. 能提问就提问，关键决策必须确认