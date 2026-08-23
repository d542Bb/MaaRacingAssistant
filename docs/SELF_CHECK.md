# 可行性自检清单（SELF CHECK）

> 从零跑通 MaaRacingAssistant 的**分步验收清单**：每步给你「怎么验、成功是什么样、失败怎么查」。
> 适合新环境部署、跨机器迁移、或运行异常时定位「是环境问题还是功能 bug」。

所有命令默认在**仓库根目录**（含 `README.md` 的那一层）执行。本文与 [README 快速开始](../README.md#快速开始) 配套。

---

## 0. 总览

| 层级 | 检查点 | 一句话目的 |
|------|--------|-----------|
| A. 系统 | 系统版本 / 权限 / 分辨率 | 前置是否满足 |
| B. Python | 版本 + 虚拟环境 + 全依赖 | 解释器与依赖是否就位 |
| C. 资源 | 模型 / 模板 / 配置文件 | 运行必需文件是否齐 |
| D. 逻辑 | 单元测试（pytest） | 核心算法是否通过回归 |
| E. 启动 | 导入冒烟 + GUI 快捷方式 / sidecar | 代码能否真正起来 |
| F. 运行期 | 窗口连接 / 前台 / OCR 心跳 | 跑起来后是否健康 |

**快速结论法**：先只跑 D（最快、最确定），过了基本可排除「纯逻辑缺陷」；再跑 E；最后 F 需真实游戏画面。

---

## A. 系统前置

| 检查 | 命令 | 预期 |
|------|------|------|
| 系统版本 | 右键我的电脑 → 属性 | Windows 10/11 64-bit |
| 管理员权限 | 任务管理器 → 账户 | 本账户在 Administrators 组 |
| 游戏窗口分辨率 | 游戏中查看设置 | 1280×720 |

> 截图基于窗口客户区，分辨率不符会导致坐标换算偏差 → 优先用 1280×720。

---

## B. Python 环境

### B1. Python 版本

```bash
python --version
```

预期：`Python 3.9+`（推荐 3.11）。<3.9 会因类型语法不兼容报错。

### B2. 创建并激活虚拟环境

```bash
python -m venv .venv
.venv\Scripts\activate
```

预期：命令行前缀出现 `(.venv)`，且 `.venv\Scripts\python.exe` 存在。

> **GUI 需要 venv 在 `d:\maaracing_assistant\.venv`**（预编译 shell 硬编码该路径）。仓库不在该路径时，GUI 后端可能连不上，可先用 D/E 自检确认逻辑层可用，详见 README 快速开始第 2 步的警告。

### B3. 安装依赖

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

预期：无 `ERROR`，末尾提示 requirements 已满足。抽查几项：

```bash
pip show maafw vgamepad onnxruntime-directml rapidocr numpy ultralytics opencv-python
```

| 依赖 | 缺失时的现象 |
|------|--------------|
| `maafw` | 截图/窗口连接不可用 |
| `rapidocr` | 鉴宝金额识别失效 |
| `onnxruntime-directml` | 极速狂飙 YOLO 推理不可用 |
| `vgamepad` | 极速狂飙虚拟手柄不可用 |

---

## C. 资源文件

| 文件 | 路径 | 缺失后果 |
|------|------|---------|
| YOLO 模型 | `assets/model/model.onnx` | 极速狂飙不可启动（sidecar 会提示「模型未找到」） |

```bash
dir assets\model\model.onnx
```

预期：文件存在且非 0 字节。

> 鉴宝模块不需要 model.onnx，缺失只影响极速狂飙。训练导出见 [docs/CODE_WIKI.md](docs/CODE_WIKI.md#训练) 或 `tools/training/train.py`。

---

## D. 逻辑回归（最快、最确定）

```bash
.venv\Scripts\python.exe -m pytest tests -q
```

预期：`==== 21 passed, 0 failed ====` 之类（以 `0 failed` 为准）。

- 只测纯逻辑模块（出价策略等），不拉入 maa/opencv 重依赖，秒级完成。
- 失败时若有断言信息，基本可判断是刻意的行为基线变化，请提交 Issue 并附失败用例。

---

## E. 启动

### E1. 导入冒烟（验证 sidecar 可 import）

```bash
.venv\Scripts\python.exe -c "import maaracing_assistant; print('OK', maaracing_assistant.__version__)"
```

预期：`OK <v版本号>`。若报 `ModuleNotFoundError`，说明包未能在仓库根解析（确认 cwd 是仓库根）。

### E2. 启动 GUI（推荐入口）

首次需先编译前台 shell（构建产物 `apps/mra_shell/bin/` 已 gitignore，需本地构建）：

```bash
dotnet build apps\mra_shell\mra_shell.csproj -c Debug
```

编译成功后，双击根目录 **`MaaRacingAssistant.lnk`**，或在命令行：

```bash
start apps\mra_shell\bin\x64\Debug\net8.0-windows10.0.19041.0\win-x64\mra_shell.exe
```

预期：exe 自身 manifest `requireAdministrator` 自动触发 UAC 提权；GUI 左上角显示版本号，后端连接成功后「巅峰鉴宝」模块与阶段列表正常加载。

> 该快捷方式指向**本机**编译产物路径，仅本地使用（`.gitignore` 排除）；仓库迁移后需按上述命令重建。

### E3. 独立调试 sidecar（不经 GUI，可选）

```bash
.venv\Scripts\python.exe -u -m maaracing_assistant.core.sidecar
```

预期：进程保持运行等待 stdin。可另开终端发一行 JSONL 测试吞吐：

```bash
echo {"id":1,"method":"get_initial_state","params":{}}
```

（管道输入后应看到 JSONL response。）

---

## F. 运行期自诊

启动 GUI → 选择「巅峰鉴宝」→ 开始运行，观察：

| 现象 | 说明 / 判断 |
|------|-------------|
| 日志出现「窗口连接失败」 | 游戏未开 / 未前台 / 游戏窗口标题不符 → 确认游戏已到主界面 |
| 日志频繁「前台校验失败」 | 游戏窗口不在前台（安全策略不抢前台）→ 把游戏切到前台 |
| OCR 心跳正常、阶段正常流转 | 截图 + 识别链路健康 |
| 模块中途静默退出 | 参考 [鉴宝文档 CODE_WIKI](../maaracing_assistant/plugins/treasure/CODE_WIKI.md) 遗留问题与坑点 |

> 单帧异常已被主循环兜底（忽略并继续），只有连续多帧系统性问题才会终止——若持续异常，请收集 `Debug` 存盘截图 + 日志后提 Issue。

---

## 常见失败与处理

| 现象 | 可能原因 | 处理 |
|------|---------|------|
| `python -m venv` 报错 | Python 未安装 / 未加入 PATH / 是 Store 别名 | 安装 Python 3.11 并勾选「Add to PATH」 |
| `pip install` 网络失败 | 代理 / 镜像问题 | 换国内镜像 `pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt` |
| pytest 报 `No module named pytest` | 未装 test 依赖 | `pip install "pytest>=7.0"` |
| GUI 版本号一直不出现 / 后端 unavailable | venv 不在 `d:\maaracing_assistant\.venv` | 见 B2 警告 | 
| 极速狂飙点「开始」提示模型未找到 | `assets/model/model.onnx` 缺失 | 见 C |
| 鉴宝一直「等待」、阶段不动 | 前台校验失败或窗口未就绪 | 切游戏到前台；确认分辨率 1280×720 |

---

## 自动化回归（CI 已接入）

本仓库在 GitHub Actions 上对 `push`（master / release）与 `pull_request` 自动跑 D 步单测（Python 3.10 / 3.11 矩阵）；发布流程前置测试 gate——**打 tag 发布前必须单测通过**。本地无需重复配置，C 步的 pytest 命令与 CI 完全一致。