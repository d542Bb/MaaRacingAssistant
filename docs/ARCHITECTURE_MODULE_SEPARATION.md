# 架构规划：主程序 / 插件模块 分离（Module Separation）

> **状态**：规划定稿（2026-08-22），待分阶段执行。
> **本文档**即 `modules/capabilities.py` 注释所引用的 `ARCHITECTURE_MODULE_SEPARATION.md`（此前为悬空引用）。
> **方向已确认**（2026-08-22 用户决策）：① 运行时自动扫描注册；② 模块专属资源随模块进 `plugins/<id>/resources/`；③ 只被单活动使用的能力下沉进该模块。

---

## 1. 目标

1. 主程序（应用层）与活动模块（插件）在**目录结构**上彻底分离。
2. 一个模块 = 一个自包含目录（代码 + 资源 + manifest），支持「删目录即剥离、丢目录即安装」。
3. 逻辑归属有明确判定标准，模块不再穿透主程序私有 API。
4. 三个 CODE_WIKI 文档与最终目录结构保持一致（随迁移同步）。

---

## 2. 逻辑归属判定（三问）

| 问题 | 答案 | 归属 |
|------|------|------|
| 这条逻辑只被**一个活动**用吗？ | 是 | 模块或其子域 |
| 这条逻辑是「应用怎么跑」还是「活动怎么玩」？ | 应用 | 主程序能力层 |
| 换一个活动还能复用吗？ | 能 | 主程序共享能力 |

**一句话**：应用生命周期 / 跨活动通用能力（截图、手柄、窗口、日志、模板、路径）→ 主程序；
活动状态机、阶段检测、出价、驾驶等「玩法」→ 模块内。

---

## 3. 目标目录结构

```
maaracing_assistant/
├── core/                              # 主程序（应用层）
│   ├── __init__.py
│   ├── sidecar.py                     # JSONL RPC 业务后端（原位置不变，仅归入 core/）
│   ├── controller.py                  # 模块编排 + 共享能力提供
│   ├── registry.py                    # 插件扫描 + MODULE_REGISTRY（扫描填充，签名向后兼容）
│   ├── base.py                        # ActivityContext / ActivityModule 基类
│   ├── capabilities.py                # typed capability 窄接口 + adapter
│   ├── logger.py / pipeline_logger.py / window_utils.py
│   ├── paths.py / opencv_utf8_patch.py / vgamepad_lazy.py / wgcap.py / debug.py
│   └── yolo_detector.py               # 跨活动视觉基础设施
├── plugins/                           # 插件根目录（一个活动 = 一个自包含子目录）
│   ├── racing/                        # 极速狂飙
│   │   ├── manifest.py                # ID/NAME/STAGE_ORDER/REQUIRES + resources 声明
│   │   ├── module.py                  # 原 racing_module.py（状态机主循环）
│   │   ├── loop.py                    # 原 racing_loop.py（驾驶循环）
│   │   ├── navigation.py              # 原 navigation.py（仅 racing 使用 → 下沉）
│   │   ├── renderer.py                # 原 racing_renderer.py
│   │   └── resources/                 # 该模块专属模板图（activity/find_opponent/...）
│   └── treasure/                      # 巅峰鉴宝
│       ├── manifest.py
│       ├── module.py                  # 原 treasure_module.py（状态机主循环 + 编排，减负）
│       ├── detector.py / ocr.py / strategy.py / eggs.py / renderer.py
│       ├── store.py                   # 【新增】从 treasure_module 拆出的落盘/DB 子域
│       └── resources/                 # treasure_rois.json + 全部鉴宝模板图
├── __main__.py                        # 保持入口（import core.sidecar）
└── ...
```

> 平铺顶层遗留的 `racing_loop.py`、`navigation.py` 随 racing 插件化一并下沉；
> `assets/resource/image/treasure/` 随 treasure 迁移；仅跨活动共享的模板留在主程序 `assets/`。

---

## 4. 核心机制

### 4.1 registry 运行时自动扫描（已确认）

- `core/registry.py` 遍历 `plugins/*/manifest.py`，import 后读取元信息填充 `MODULE_REGISTRY`。
- **manifest 契约**（模块自描述）：

```python
# plugins/racing/manifest.py
ID = "racing"
NAME = "极速狂飙"
STAGE_ORDER = ["归位", "导航一(极速狂飙入口)", ...]
REQUIRES = frozenset({"capture", "gamepad"})
REQUIRES_GAMEPAD_EXCLUSIVE = True
# 模块类定位：扫描时按约定 module 名推导，或显式声明
MODULE_CLASS = "module.RacingModule"
RESOURCES_DIR = "resources"
```

- `get_module_info` / `create_module` **签名与返回结构保持不变**，sidecar 零改动。
- 剥离 = 删 `plugins/<id>/` 目录；安装 = 丢一个自包含目录进 `plugins/`。GUI 列表自动随之变化。

### 4.2 资源随模块（已确认）

- 模块通过 `ActivityContext` 提供的路径解析 helper 引用自身资源（相对 `plugins/<id>/resources/`），不写死主程序绝对路径。
- 迁移时**只搬文件、不改资源内容**；`treasure_rois.json` 等 JSON 的 rect/阈值字段原样保留。
- 通用模板（如归位 settings_page、store_popup）若后续多活动复用，再上提到主程序 `assets/`。

### 4.3 capabilities 收口（消除穿透）

现状 racing 模块直接调私有接口，需补窄接口后改写为 capability 调用：

| 现状穿透调用 | 收口方案 |
|---|---|
| `nav._wait_for_template(...)` | Navigation 提供公开 `wait_for_page(name, timeout)` |
| `nav._ensure_cursor(...)` | 同上公开化 |
| `racing_loop._end_reason` | RacingLoop 提供公开只读属性 `end_reason`（去掉下划线访问） |

---

## 5. 迁移步骤（分阶段，低风险 → 高风险）

| 阶段 | 内容 | 风险 | 验收 |
|------|------|------|------|
| **P0** | 确定性小修：`sidecar.get_status` 死代码；`capture_backend` 接线或摘除无效开关 | 低 | py_compile + pytest 通过 |
| **P1** | 主程序抽 `core/`：纯搬移 + import 修正（sidecar/controller/base/capabilities/registry/logger/window_utils/paths/debug 等） | 中 | `python -m maaracing_assistant` 可启动、GUI 三 Tab 正常 |
| **P2** | racing 插件化：`racing_module/racing_loop/navigation/racing_renderer` → `plugins/racing/`；registry 自动扫描生效 | 中 | 删 racing 目录后 app 可启动且无残留 import |
| **P3** | treasure 插件化：`treasure_*`/`bid_strategy` → `plugins/treasure/`；拆 `store.py`（落盘/DB 子域），`module.py` 减负 | 高 | 删 treasure 目录后 app 可启动；pytest（bid_strategy）通过 |
| **P4** | 资源迁移：`assets/resource/image/treasure/` → `plugins/treasure/resources/`；racing 专属模板 → `plugins/racing/resources/`；路径解析改造 | 中 | 模块自引用资源路径全部可解析 |
| **P5** | 文档同步（含 CODE_WIKI 迁移，见 §6） | 低 | 文档路径与实际一致 |

> 每阶段独立可合并 / 可回滚；不要求一次完成。

---

## 6. 文档同步（含 CODE_WIKI 迁移）

用户明确要求「别忘了迁移各自的 codewiki」。原则：**文档与最终目录结构保持一致**。

- `docs/CODE_WIKI.md`（主文档）：保留架构总览与分层图；§3 目录结构章节更新为 `core/ + plugins/` 结构；§5/§6 类速查与依赖关系更新实际路径。
- `docs/CODE_WIKI_RACING.md`：全部引用路径更新为 `plugins/racing/`；新增一节说明插件化后的模块入口与资源位置。
- `docs/CODE_WIKI_TREASURE.md`：全部引用路径更新为 `plugins/treasure/`；新增 `store.py` 落盘子域说明；`treasure_rois.json` 路径更新。
- `docs/SELF_CHECK.md` / `README.md` / `CONTRIBUTING.md`：涉及模块路径的描述同步。

---

## 7. 受影响面清单（执行前逐项核对）

| 影响面 | 处理 |
|--------|------|
| `sidecar.py` import（`from maaracing_assistant.modules...` / `controller`） | 改指 `core` / `plugins` |
| `tools/` 调试脚本（diagnose_treasure / test_stage_detector_replay / extract_treasure_templates / treasure_debug_studio/server.py） | 更新 import 与资源路径 |
| `scripts/release/assemble.ps1` | 打包路径、runtime-lock 是否含新增目录 |
| `requirements.txt` / `pyproject.toml` | `packages` 配置改为含 `core`/`plugins` |
| `.trae/skills/project-update/SKILL.md` 中的程序路径清单 | 更新模块路径 |
| 工作区记忆（`mcp_memory-ws`） | 每个迁移阶段完成后更新模块实体路径 |

---

## 8. 验收标准（最终态）

1. 删除 `plugins/treasure/` 整个目录 → app 正常启动、GUI 无鉴宝模块、无 import 残留、pytest 通过。
2. 新活动只需丢入一个自包含 `plugins/<id>/` 目录即可被 GUI 识别（含资源）。
3. 模块代码零穿透主程序私有 API（除注册契约外）。
4. 三个 CODE_WIKI 中所有代码路径与实际目录一致。
5. `assemble.ps1` 打包产物可正常启动（解压即用）。
