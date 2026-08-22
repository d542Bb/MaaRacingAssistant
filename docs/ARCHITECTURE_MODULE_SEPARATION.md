# 架构规划：主程序 / 插件模块 分离（Module Separation）

> **状态**：P0–P5 已落地（2026-08-22 分阶段执行完成，每阶段独立提交）。
> **本文档**即 `core/capabilities.py` 注释所引用的 `ARCHITECTURE_MODULE_SEPARATION.md`（此前为悬空引用）。
> **方向已确认**（2026-08-22 用户决策）：① 运行时自动扫描注册；② 模块专属资源随模块进 `plugins/<id>/resources/`；③ 只被单活动使用的能力下沉进该模块。
> **后置项**：racing 专属模板暂留 `assets/resource/image/`（受 `resource.post_bundle` MAA bundle 机制约束，需实测后迁移）；racing 穿透私有 API 的收口（§4.3）待做。

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
│   │   ├── manifest.py                # ID + MODULE_CLASS（registry 扫描用）
│   │   ├── module.py                  # 原 racing_module.py（状态机主循环）
│   │   ├── loop.py                    # 原 racing_loop.py（驾驶循环）
│   │   ├── navigation.py              # 原 navigation.py（仅 racing 使用 → 下沉）
│   │   ├── renderer.py                # 原 racing_renderer.py
│   │   └── resources/                 # 该模块专属模板图（后置项：暂留 assets/）
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
- **manifest 契约**（模块自描述，NAME/STAGE_ORDER/REQUIRES 从模块类读取，单一来源）：

```python
# plugins/racing/manifest.py
ID = "racing"
MODULE_CLASS = "module.RacingModule"   # plugins/<id>/module.py 中的类
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

| 阶段 | 内容 | 风险 | 状态 |
|------|------|------|------|
| **P0** | 确定性小修：`sidecar.get_status` 死代码；`capture_backend` 语义确认 | 低 | ✅ 已落地（commit c483593） |
| **P1** | 主程序抽 `core/`：纯搬移 + import 修正 | 中 | ✅ 已落地（commit ae3c2df） |
| **P2** | racing 插件化：`racing_*`/navigation → `plugins/racing/`；registry 自动扫描生效 | 中 | ✅ 已落地（commit f3fa6e1） |
| **P3** | treasure 插件化：→ `plugins/treasure/`；拆 `store.py`；移除 modules 包 | 高 | ✅ 已落地（commit a420f2e / d31f2ff） |
| **P4** | 资源迁移：treasure → `plugins/treasure/resources/`；racing 资源**后置** | 中 | ✅ treasure 完成（commit c1f96f3 / fb96ca8）；⚠️ racing 资源因 MAA bundle 待迁 |
| **P5** | 文档同步（三个 CODE_WIKI + README/SELF_CHECK） | 低 | ✅ 已完成 |

> **后置项（未做）**：
> 1. racing 专属模板迁入 `plugins/racing/resources/`（需先实测 `resource.post_bundle` 对目录结构的依赖）。
> 2. §4.3 racing 穿透私有 API 的窄接口收口（`nav._wait_for_template` / `_ensure_cursor` / `racing_loop._end_reason` 公有化）。

---

## 6. 文档同步（含 CODE_WIKI 迁移）

用户明确要求「别忘了迁移各自的 codewiki」。原则：**文档与最终目录结构保持一致**。

- `docs/CODE_WIKI.md`（主文档）：保留架构总览与分层图；§3 目录结构章节更新为 `core/ + plugins/` 结构；§5/§6 类速查与依赖关系更新实际路径。
- `docs/CODE_WIKI_RACING.md`：全部引用路径更新为 `plugins/racing/`；新增一节说明插件化后的模块入口与资源位置。
- `docs/CODE_WIKI_TREASURE.md`：全部引用路径更新为 `plugins/treasure/`；新增 `store.py` 落盘子域说明；`treasure_rois.json` 路径更新。
- `docs/SELF_CHECK.md` / `README.md` / `CONTRIBUTING.md`：涉及模块路径的描述同步。

---

## 7. 受影响面清单（执行核对结果）

| 影响面 | 处理 | 状态 |
|--------|------|------|
| `sidecar.py` import | 改指 `core` / `plugins`，默认模块改 id 引用 | ✅ 已处理 |
| `tools/` 调试脚本（diagnose_treasure / test_stage_detector_replay / extract_treasure_templates / treasure_debug_studio/server.py） | 更新 import 与资源路径 | ✅ 已处理 |
| `apps/mra_shell/MainWindow.xaml.cs` | sidecar 启动命令 `-m maaracing_assistant.core.sidecar` | ✅ 已处理 |
| `tests/`（conftest + test_bid_strategy） | 直导路径改为 `plugins/treasure/` | ✅ 已处理 |
| `scripts/release/assemble.ps1` | 打包路径、runtime-lock | ⏳ 待打包实测（`maaracing_assistant*` 自动覆盖 core/plugins） |
| `requirements.txt` / `pyproject.toml` | `include=["maaracing_assistant*"]` 已覆盖新子包 | ✅ 无需改 |
| `.trae/skills/project-update/SKILL.md` 中的程序路径清单 | 更新模块路径 | ⏳ 待更新（.trae 本地文件） |
| 工作区记忆（`mcp_memory-ws`） | 每个迁移阶段完成后更新模块实体路径 | ✅ 待本阶段收尾写入 |

---

## 8. 验收标准（最终态）

1. 删除 `plugins/treasure/` 整个目录 → app 正常启动、GUI 无鉴宝模块、无 import 残留、pytest 通过。
2. 新活动只需丢入一个自包含 `plugins/<id>/` 目录即可被 GUI 识别（含资源）。
3. 模块代码零穿透主程序私有 API（除注册契约外）。
4. 三个 CODE_WIKI 中所有代码路径与实际目录一致。
5. `assemble.ps1` 打包产物可正常启动（解压即用）。
