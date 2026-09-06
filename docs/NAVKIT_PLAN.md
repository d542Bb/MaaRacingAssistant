# NavKit 基建方案（鉴宝先行）

版本：rev.2（2026-09-05）｜状态：**已落地（S0-S5 全部完成，2026-09-05 验收）**
标记约定：`[现状]` 已存在的实现｜`[提案]` 本方案新增｜`[改造]` 在现有实现上修改

本版相对 rev.1 的变化：移除全部比喻性表述；补 §5 数据流与所有权、§3.3 校验规则表、§10 文件级改动清单、§6 等价性回归规格；§5.4 生效时机与 §12.1 已定决策 D1-D7。

***

## 0. 评审须知：本计划要达成的效果与既定前提

（供第二评审人先读，避免把已定取舍当成疏漏来提。）

### 0.1 一句话目标

把"程序在每一步认什么、认到之后做什么、做完去哪一步"这套判断逻辑，从**一半在 JSON、一半在 Python 常量**的分裂状态，收敛成**一份人写、工具可编辑、运行时可执行、事后可还原**的模型；本阶段只在鉴宝模块落地。

### 0.2 达成后的可感知效果（验收面向"性质"，不面向实现）

| 编号 | 效果                                                                       |
| -- | ------------------------------------------------------------------------ |
| P1 | 打开一个页面即可看到全部判断逻辑：某锚点属于哪个阶段、优先级、点完跳去哪、哪些是全局共用                             |
| P2 | 判断结果是**有走向的树**而非清单；纸上声明的边与代码实现的边不一致时，模块**启动即失败**并精确到哪条边                  |
| P3 | 任何一次改动都有回执：落盘前校验报告 + 全量历史帧逐帧等价回归                                         |
| P4 | 游戏更新后的处置成本分级：入口换位置=换图或拖框；面板整体位移=无需动作（由面板锚点带动）；文案变化=改期望值。均不需改代码或重新发版      |
| P5 | 新模块接入 = 填一份资产清单，不写寻路匹配代码（本阶段建立底座，**该性质要到 racing 重做那一轮才被实证**）             |
| P6 | 错误暴露位置前移：从"竞拍中判错阶段（损失真金）"前移到"启动期一条清单"                                    |
| P7 | 资产与编译产物全部位于程序目录内，不向 C 盘复制；C 盘仅新增 trace（约 10MB 上限）与既有调试帧（沿用用户既有决策，但加保留上限） |

### 0.3 明确不做（不是疏漏）

- 不把策略算法数据化：出价策略、防碰撞三区、彩蛋 HSV/NMS、OCR 后处理仍在代码里。模型里只允许出现指向它们的 `dynamic_narrow: "code:..."` 声明，树上渲染为"进入代码"的终止块。

- 不做可视化条件表达式/脚本节点（否则等于发明第二门编程语言）。

- 不引入任何新依赖：navkit 为纯标准库（`tests/` 与 CI 只装 pytest，不拉 maa/cv2）；控制台的树视图自写 SVG，不引 DAG 图库；服务端仍是标准库 `http.server`；无前端构建链、无 CDN。

- 不做运行时资源云端下发。

- 不追求"每个点击目标都有模板图"（理由见 §4.2 分类判据与 D2）。

### 0.4 为什么本阶段不含 racing（最易被误读的一点）

1. **验证手段决定顺序。** S1 的合入闸门是 §9.1 的逐帧等价回归，它依赖真实历史会话帧。鉴宝有 5 会话 / 3582 帧 / 1.29GB 实测数据；racing 无同等素材。在缺证据的模块上改判定逻辑，等于放弃回归直接上真机。
2. **racing 的活路径太少，改了验不出。** 其 `navigation.py` 727 行中约 470 行无任何调用方，实际活着的只有归位与商店弹窗（详见 racing 重做专项调查）。用它来验证模型表达能力，样本面过窄。
3. **依赖方向。** racing 重做要消费的正是这套底座（模型、唯一匹配、NavGraph 跳转、`MRA_Press` 桥、手柄长时独占契约）。底座未稳先重做 = 在流沙上盖楼。
4. **但 schema 必须为 racing 预留检验。** 因此 S1 的出口条件里包含"用归位/关弹窗/商店三件事反套 v3 模型"（§11 R2），套不进说明设计错，当场改。**这是"只管鉴宝"不等于"只想鉴宝"的具体保障。**
5. racing 重做排期在 S5 之后，单开一轮，不在本文件评审范围内。

### 0.5 不可推翻的既有项目约束（评审时请默认成立）

- 实时玩法不进跳转图：YOLO 对局、逐帧出价必须留在帧循环；跳转图节点是秒级串行、阻塞式。

- 鉴宝的产品决策是"只显示准星意图、由真人按 A"，任何改动不得绕过 `intent_mode`。

- 手柄单 owner 语义：进入对局前必须销毁导航手柄（否则游戏不识设备）；活跃租约期间禁止 `reset_device()`。

- 插件自包含契约：代码/模板/资源随 `plugins/<id>/` 整目录分发。

- GUI 断点契约：`STAGE_ORDER` 的阶段名与顺序不得改变（改名/重排会破坏 sidecar 校验与既有测试）。

- 版本与发布：SemVer、Git tag 唯一信源、0.x 一律 `v0.x.y-dev.N`。

### 0.6 请评审重点看的五处

1. schema v3 是否**欠设计或过设计**：§3.3 的 27 条校验里有没有互相打架的；`arbitration`/`domain` 两个袋是不是该更硬。
2. §5 数据流与线程所有权是否成立：尤其 §5.4 的生效时机（D6）与 §5.5 最后一行的模板缓存失效（R4，当前真实缺陷）。
3. §9.1 回归闸门是否足以挡住 R1（搬纸改坏判定）：比较键、容差、分批粒度、`NAVKIT_SOURCE=v2` 单点回退，有无漏掉的失败模式。
4. §4.4 纸码互查（D1/D2 之外最重的约束）是否会在实践中被绕过，以及绕过后的检测手段。
5. S4a 提前到 S1 之后是否值得（让"看得见"比"改得动"早一步），还是应当并入 S4b 一次交付。

### 0.7 已定取舍，除非有新证据不必重议

D1-D7 全部决策及理由见 §12.1。其中三项最容易被提异议：

- **transitions 上纸并启动期互查**：代价是每个阶段处理器多一行声明、忘声明则启动失败。不这么做的后果是树与代码必然漂移，"可还原"失效。

- **固定件不配模板**：代价是面板内件仍靠坐标。收益是不引入串键与状态污染，且不产生"每次 UI 微调都要重裁十张图"的维护税。

- **跳转 JSON 为编译产物、禁止手改**：代价是多一个 `generated/` 目录与一步 CI。收益是双真源在物理上不可再现（本方案撰写期间我们自己制造过一次，见 F2）。

***

## 1. 目标（可验证条款）

| 编号 | 目标       | 验收判据（可执行）                                                                                  |
| -- | -------- | ------------------------------------------------------------------------------------------ |
| G1 | 判定逻辑单一真源 | 一个模块的阶段归属、优先级、互斥、阶段感知清单、跳转链全部来自一份 `assets.json`；对应 Python 常量删除（§2.2 五个）                    |
| G2 | 新模块零寻路代码 | 接入 = 写 `assets.json` + 放模板图；`grep -c "navigate_to\|find_template" plugins/<新模块>/*.py == 0` |
| G3 | 识别口径唯一   | 尺度表/默认阈值在全仓只出现一处定义；`rg "MATCH_SCALES\\s*=" --glob '*.py'` 命中数从 5 降到 1                      |
| G4 | 双真源不可再现  | 跳转 JSON 为编译产物；CI 比对"重新编译 == 磁盘产物"，不一致即失败                                                   |
| G5 | 实际走法可还原  | 任一会话的 `trace.jsonl` 可按帧号回答"该帧为何未点击 X"；设计链与实际链可在同一棵树上叠加显示                                   |
| G6 | 资源不入 C 盘 | 资产与生成物全部位于程序目录内；新增代码对 `user_data_dir()` 的写入仅限 `debug/`（trace 落盘，用户已批准）                     |

非目标：可视化条件表达式/脚本节点；前端构建链与 CDN 依赖；运行时资源云端下发；策略算法（出价、防碰撞、HSV 判定）的数据化。

***

## 2. 现状与缺陷（证据）

### 2.1 已在纸上：`plugins/treasure/resources/config/treasure_rois.json`（v2，34 项）

| 段            | 项数 | 字段                                                  | 备注                       |
| ------------ | -- | --------------------------------------------------- | ------------------------ |
| `stage`      | 13 | `rect`/`templates`/`threshold?`/`comment?`          | 已校准                      |
| `ocr`        | 18 | `rect`（`templates` 恒空）                              | 已校准                      |
| `actions`    | 21 | `rect`/`templates`（仅 3 项非空）                         | 3 个 badge 自陈"rect 为占位粗估" |
| `appraisers` | 2  | `prio`/`rect`/`templates`/`threshold`               | 已校准                      |
| `eggs`       | 1  | `rect`/`templates`/`threshold` + `_count_*_norm` 偏移 | 已校准                      |

`rect` 全部归一化 `[0,1]`、左上原点、`x2/y2` 排他 —— 与 `core/roi_config.py` 的 `NormalizedROI` 契约一致（坐标换算 `x1=floor(x1*W)`、`x2=ceil(x2*W)`）。

### 2.2 只在代码中（待上纸，约 90 行常量）

| 常量                                                       | 位置                                      | 内容                                                                    | 目标字段                                               |
| -------------------------------------------------------- | --------------------------------------- | --------------------------------------------------------------------- | -------------------------------------------------- |
| `_ROI_STAGE`                                             | `plugins/treasure/detector.py` L109-136 | 锚点 → 阶段名、`priority`、`margin`、`round_from_template`、`thresholds` 单模板覆盖 | `anchors.*.order` / `.arbitration` / `transitions` |
| `_GLOBAL_ANCHORS`                                        | `plugins/treasure/module.py` L321-324   | 恒全量参与检测的大厅锚点                                                          | `stages.global_anchors`                            |
| `_STAGE_PERCEPTION`                                      | 同上 L350-397                             | 阶段 → 本帧允许扫的锚点集合（13 阶段）                                                | `stages.definitions[*].anchors`                    |
| `_BID_OCR_KEYS` / `_SETTLE_OCR_KEYS` / `_STAGE_OCR_KEYS` | 同上 L339-409                             | 阶段 → 投递 OCR 的键集合                                                      | `stages.definitions[*].ocr`                        |
| `STAGE_ORDER`                                            | 同上 L454-473                             | 12 阶段顺序（GUI 断点契约）                                                     | `stages.order`                                     |

### 2.3 缺陷登记

| 编号 | 事实                                                                                                                                                                                     | 影响                                             | 处置                                                |
| -- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------- | ------------------------------------------------- |
| F1 | 统一底座 `core/roi_config.py`、`module_config.py`、`render_plan.py`、`debug_io.py` 已交付并有单测（P1a/P2b），生产代码零引用；`detector.py` 自写 `_load_rois`/`_load_roi_templates` 直解 JSON（L61/L143/L159）        | 底座与实现平行演化，早晚不一致                                | S1：`navkit` 复用 `ROIConfig` 而非另造；`detector` 改读编译产物 |
| F2 | 同一段"大厅→前往鉴宝→鉴宝大厅"曾被两处描述：`core/resources/pipeline/hall.json`（`roi:[0.55,0.55,1.00,0.90]`）与 `treasure_rois.json`（`rect:[0.760,0.803,0.896,0.891]`）                                       | 搜索区不一致；该段无任何代码引用（已核实：`鉴宝_` 前缀在全部 `.py` 中 0 命中） | **已处理**（2026-09-05 经授权删除）；防复发机制见 §7、D3            |
| F3 | 尺度表 5 处：`detector.MATCH_SCALES`、`eggs.py`、`module._APPRAISER_MATCH_SCALES`/`_CHECK_MATCH_SCALES`、`tools/debug_studio/server.py`、`core/template_match.DEFAULT_SCALES`；历史上以"四处保持一致"的约定维持 | 一处漏改 → 控制台分数 ≠ 运行时分数                           | S1：唯一来源 `assets.match.scales`，五处改读它               |
| F4 | `actions` 21 项中 18 项无模板，纯 `rect` 中心点击                                                                                                                                                  | **非缺陷**（判据见 §4.2）：面板级锚点在时，面板内相对坐标成立。缺的是担保关系未落纸 | S1.5：登记 `guarded_by`；不新增模板                        |
| F5 | 无结构化决策流水，仅有帧图片                                                                                                                                                                         | 实际走法不可还原（G5 不成立）                               | S2                                                |
| F6 | 控制台帧白名单硬编码四位序号 + raw 的 png/jpg/jpeg/webp（`tools/debug_studio/core/session.py`）；实测 `user_data/debug/navigate` 5 个会话匹到 **0 帧**                                                           | 跨模块会话浏览失效                                      | S4：帧模式下沉为每模块 `kit.frame_pattern`                  |
| F7 | 控制台无帧序列播放（下拉逐帧）                                                                                                                                                                        | 校准效率与归因能力受限                                    | S4 可选项，D5 关联                                      |
| F8 | 控制台的 `match_local` 与运行时 `detector._match_local` 是"同源实现的两份拷贝"                                                                                                                           | 任一侧被单独修改即静默失准                                  | S1：两侧改读 `core/template_match`，编译期共用               |
| F9 | `core/resources/image/` 不存在（`NavGraph` 默认把它列为一级模板目录）                                                                                                                                   | 全局覆盖图机制无法使用                                    | S1 建目录                                            |

素材：`user_data/debug/treasure` = 5 会话 / 3582 raw 帧 / 1294.7 MB（实测），足以支撑 §9 全量逐帧回归。

***

## 3. 架构

### 3.1 组件与职责 `[新增]`

```
maaracing_assistant/core/navkit/
├── __init__.py       # 对外导出
├── assets.py         # 模型数据类 + 加载（纯标准库）
├── validate.py       # 结构/引用/担保/互查 校验器（纯标准库）+ CLI
├── legacy.py         # v2 只读判定与 v2→v3 迁移器（纯标准库）
├── compile_detect.py # assets → DetectionPlan（帧循环用）
├── compile_route.py  # assets → MAA pipeline JSON（跳转图用）
└── trace.py          # FrameTrace 记录器（帧循环调用）
```

依赖方向（硬性）：

```
plugins/*  →  core/navkit  →  core/roi_config（坐标换算与三段式访问复用）
tools/navkit（控制台） → core/navkit
core/navkit ✕ 不得 import：cv2、numpy、maa、vgamepad   ← 保证可进 tests/ 且 CI 免装重依赖
```

理由：`tests/` 与 CI 的既有约定是"只装 pytest、不拉 maa/opencv"；模型层若要单测就必须保持纯标准库。像素匹配留在 `core/template_match.py`（消费方在运行时侧）。

### 3.2 与既有运行时的关系

| 既有件                                     | 关系                                                                                         |
| --------------------------------------- | ------------------------------------------------------------------------------------------ |
| `core/roi_config.py`                    | 复用为 DetectionPlan 的内部坐标/访问实现，不另造数据结构                                                       |
| `core/template_match.py`                | 唯一匹配实现；`detector._match_local`、`tools/debug_studio/core/reader.match_local` 改为调用它（消 F3/F8） |
| `core/nav_graph.py`                     | 消费 `compile_route` 产物；节点参数字段不变（已实现），来源由"人写 JSON"改为"生成 JSON"                                |
| `core/clicker.py` / `gamepad_cursor.py` | 不变；点击与光标执行的唯一出口                                                                            |
| `core/debug_io.py` / `render_plan.py`   | S1 后由 `assets.render` 与 `assets.trace` 驱动（treasure 首次真正使用底座）                               |
| `tools/debug_studio`                    | S4 改名 `tools/navkit` 并升级为模型编辑器；`CategoryDefs` 由 `ModuleKit` 取代（§8.4）                       |

### 3.3 校验规则表（`validate.py` 的验收契约）

`E*` = 阻断（落盘/启动失败）；`W*` = 告警（可继续，控制台标黄）。§4 各处引用的编号以本表为准。

| 编号  | 判据                                                                                                                  | 依据                                                   |
| --- | ------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------- |
| E01 | `_schema_ver != 3`                                                                                                  | 版本门禁；v2 走 `legacy.py` 只读                             |
| E02 | `_module` 与所在 `plugins/<id>` 目录不一致                                                                                  | 防错挂模块                                                |
| E03 | `reference_size` 非两个正整数                                                                                             | `NormalizedROI` 契约前提                                 |
| E04 | `match.scales` 为空或含非正数；`match.threshold ∉ (0,1]`                                                                    | 唯一口径不得为空                                             |
| E05 | `anchors.*.kind` 不在 `{template, ocr, point}`                                                                        | §4.2                                                 |
| E06 | `rect` 非 4 元 / 越出 `[0,1]` / 不满足 `x1<x2`、`y1<y2`                                                                     | `NormalizedROI.__post_init__` 同规则，构造期即抛              |
| E07 | `kind=template` 但 `templates` 为空，或含路径分隔符 / `..` / 非 `png\|jpg\|jpeg`                                                | 模板名即资源键，必须可解析                                        |
| E08 | `owner` 既非 `global` 亦非本模块名                                                                                          | §7.3 归属唯一合法集                                         |
| E09 | `page` 未在 `pages` 中定义                                                                                               | 树分组完整性                                               |
| E10 | `kind=point` 缺 `guarded_by`                                                                                         | 无证据目标不得存在（§4.2 第四类）                                  |
| E11 | route 的 `click`/`press` 步骤缺 `confirm`                                                                               | 跳转必须可证伪                                              |
| E12 | 任意引用指向不存在的锚点（`stages.*.anchors/ocr`、`global_anchors`、`guarded_by`、`transitions.on`、`routes.*.steps.target/confirm`） | 引用闭合                                                 |
| E13 | `guarded_by` 指向的锚点 `kind != template`                                                                               | 面板内件不能由另一个面板内件担保                                     |
| E14 | `stages.definitions` 的键不在 `stages.order` 中                                                                          | 防幽灵阶段                                                |
| E15 | `stages.order` 为空或含重复项                                                                                              | `StageTracker` 与 GUI 断点契约                            |
| E16 | `dynamic_narrow.by` 不以 `code:` 开头                                                                                   | 禁止在 JSON 内写伪表达式                                      |
| E17 | 纸上有边、代码未实现（提供 `code_edges` 时）                                                                                       | §4.4 互查                                              |
| E18 | 代码已实现、纸上未声明（提供 `code_edges` 时）                                                                                      | §4.4 互查                                              |
| E19 | `transitions.to` 不在 `order` 且非 `same` / `$round`                                                                    | 迁移目标合法集                                              |
| E20 | 编译后 MAA 节点名冲突（两条 route 生成同名节点）                                                                                      | 节点名命名空间唯一（§6 映射表）                                    |
| W01 | 模板存在但未被任何锚点引用                                                                                                       | 原 `template_status.unassigned`                       |
| W02 | 锚点引用的模板文件不存在                                                                                                        | 原 `template_status.dangling`（racing 现有 4 例，见 F9/§10） |
| W03 | `global_anchors` 为空                                                                                                 | 阶段冻结事故（不变量 I-1）                                      |
| W04 | `owner=global` 但模板图实际只存在于模块目录                                                                                       | 归属与物理位置矛盾                                            |
| W05 | `order` 中某阶段无 `definitions`                                                                                         | 运行时回退全量检测，属既有安全兜底，不得升级为 E                            |
| W06 | 同一 `page` 内 `order` 重复                                                                                              | 检测与展示顺序歧义                                            |
| W07 | 模块目录同名覆盖 `global` 资产但未声明 `_override: true`                                                                          | §6 覆盖必须显式                                            |

***

## 4. 数据模型 schema v3

### 4.1 顶层

| 字段               | 类型         | 必填     | 语义                                             | 生产者    | 消费者                            |
| ---------------- | ---------- | ------ | ---------------------------------------------- | ------ | ------------------------------ |
| `_schema_ver`    | int        | ✓      | 固定 3；`legacy.py` 读到 2 走只读适配                    | 控制台    | `assets.load`                  |
| `_module`        | str        | ✓      | 模块标识，须与 `plugins/<id>` 一致                      | 控制台    | 校验器（跨引用检查）                     |
| `reference_size` | \[int,int] | ✓      | 归一化基准（默认 1280×720），仅作人读参考，换算按实际帧尺寸             | 控制台    | `NormalizedROI.to_pixel`       |
| `match`          | obj        | ✓      | 唯一匹配口径：`scales[]`、`threshold`、`margin_default` | 人工/控制台 | 检测计划、路由计划、控制台回测                |
| `pages`          | map        | ✓      | 页面定义 `{key:{label}}`；树的分组层                     | 人工     | 校验器、控制台视图                      |
| `anchors`        | map        | ✓      | 识别/点击目标（§4.2）                                  | 人工+控制台 | 全部                             |
| `stages`         | obj        | ✓      | 阶段定义（§4.3）                                     | 人工     | 帧循环检测计划                        |
| `transitions`    | list       | ✓      | 阶段迁移声明（§4.4）                                   | 人工     | 校验互查、控制台树                      |
| `routes`         | map        | ✓(可为空) | 跨页面跳转链（§4.5）                                   | 人工     | `compile_route` → MAA pipeline |
| `render`         | obj        | ✗      | 图层计划                                           | 人工     | renderer（racing 阶段二才实装）        |
| `trace`          | obj        | ✗      | 流水开关与保留策略                                      | 人工     | `trace.py`                     |

### 4.2 Anchor（`anchors.<id>`）

| 字段            | 类型             | 必填条件                 | 语义                                                                                                    |
| ------------- | -------------- | -------------------- | ----------------------------------------------------------------------------------------------------- |
| `kind`        | enum           | ✓                    | `template`（模板认图）/ `ocr`（读字取值）/ `point`（固定坐标目标）                                                        |
| `owner`       | str            | ✓                    | `global` 或模块名；决定模板图物理目录与覆盖优先级（§7.3）                                                                   |
| `page`        | str            | ✓                    | 所属页面 key                                                                                              |
| `label`       | str            | ✓                    | 中文显示名（控制台与树）                                                                                          |
| `rect`        | \[x1,y1,x2,y2] | ✓                    | 归一化；对 `template` 是搜索区，对 `ocr` 是取值区，对 `point` 是点击区                                                     |
| `templates`   | str\[]         | `kind=template` 必填非空 | 候选图名（含扩展名），多张取最高分                                                                                     |
| `threshold`   | float          | ✗                    | 覆盖 `match.threshold`                                                                                  |
| `scales`      | float\[]       | ✗                    | 覆盖 `match.scales`                                                                                     |
| `order`       | int            | ✗                    | 同页/同阶段内的检测与展示顺序（取代 `_ROI_STAGE.priority`）                                                             |
| `arbitration` | obj            | ✗                    | `{margin: float, round_from_template: bool, template_thresholds: {tpl: float}}`（取代 `_ROI_STAGE` 余下字段） |
| `guarded_by`  | anchor-id      | `kind=point` **必填**  | 证明该目标所属画面此刻存在的锚点；该锚点必须 `kind=template`                                                                |
| `domain`      | obj            | ✗                    | 领域参数袋（如彩蛋 HSV/NMS 偏移），navkit 不解释、只透传给模块                                                               |

分类判据（替代 rev.1 的比喻表述）：

| 类别                           | 判据                                | 允许 `kind`                         | 必备证据                  |
| ---------------------------- | --------------------------------- | --------------------------------- | --------------------- |
| **迁移目标（entry-target）**       | 点击导致页面/阶段变化，且其屏幕位置在不同活动轮换或不同会话间会变 | `template` 优先；仅当长期实测固定才允许 `point` | 跳转 step 的 `confirm`   |
| **面板内件（panel-fixed-target）** | 位于某面板内部，面板整体位移时相对位置不变             | `point`（或可选 `template`）           | `guarded_by`          |
| **取值区（readout）**             | 只读不点（OCR 数字/名字）                   | `ocr`                             | 无                     |
| **无证据目标**                    | 既无 `confirm` 亦无 `guarded_by`      | ——                                | **校验失败（E10）；运行时不得点击** |

判定所需证据由 §9 回归提供：同一锚点在 3582 帧上的位置方差（`template` 命中框中心的标准差 > 15px ⇒ 归为会变位置，必须 `template` 定位）。

### 4.3 Stages

```jsonc
"stages": {
  "order": ["游戏大厅", "...", "结算弹窗"],
  "global_anchors": ["hall_peak_appraise_card", "hall_session_cards"],
  "definitions": {
    "第1回合出价": {
      "page": "bidding",
      "anchors": ["round_big_banner", "smart_bid_btn", "settle_title", "result_banner"],
      "ocr": ["bid_result_amount_box", "bid_player1", "..."],
      "dynamic_narrow": { "by": "code:_active_stage_rois",
                          "note": "拨号盘交互期收窄为仅 smart_bid_btn" }
    }
  }
}
```

| 规则                                 | 依据                                              |
| ---------------------------------- | ----------------------------------------------- |
| `order` 元素唯一且非空                    | GUI 断点与 `StageTracker` 契约                       |
| `definitions` 键必须 ∈ `order`        | 防幽灵阶段（E14）                                      |
| `order` 中的阶段可缺 `definitions`       | **允许**（W05）：与运行时"未登记 → 回退全量检测"的既有安全兜底一致，不得改成硬失败 |
| `global_anchors` 恒并入每帧检测集合         | 不变量 I-1（实测事故：漏并入 → 结算弹窗关闭后回不了鉴宝大厅，阶段永久冻结）       |
| `dynamic_narrow.by` 必须以 `code:` 开头 | 上不了纸的条件逻辑只允许留指针，禁止在 JSON 里写伪表达式（E16）            |

### 4.4 Transitions（决定"树"是否有走向）

```jsonc
{ "stage": "匹配中", "on": "appraiser_title", "to": "选择鉴宝师" }
{ "stage": "选择鉴宝师", "on": "round_big_banner", "to": "$round", "when": "round_from_template" }
{ "stage": "*", "on": "hall_session_cards", "to": "鉴宝大厅(选择场次)" }
```

| 字段      | 取值                                                                                        |
| ------- | ----------------------------------------------------------------------------------------- |
| `stage` | 阶段名 或 `"*"`（任意阶段生效）                                                                       |
| `on`    | 已存在且 `kind=template` 的锚点 id                                                               |
| `to`    | 阶段名 或 `"same"`（原地停留）或 `"$round"`（按 `arbitration.round_from_template` 解析出的回合号实例化 `第N回合出价`） |

**纸与代码互查协议（D1 机制）**：

```python
# [提案] 运行时注册表：阶段处理器声明它实现哪些边
CODE_EDGES: frozenset[tuple[str, str]] = {("匹配中", "appraiser_title"), ...}
validate_assets(assets, code_edges=CODE_EDGES)
→ E17 纸上有、代码无（画了不存在的边）
→ E18 代码有、纸上无（藏了一条边）
```

启动期调用；失败即模块拒绝启动并逐条打印。校验器本身不依赖运行时（`code_edges=None` 时跳过双向检查），保持纯标准库。

### 4.5 Routes

```jsonc
"routes": {
  "hall→鉴宝大厅": {
    "entry": true, "start_stage": "游戏大厅",
    "steps": [
      { "target": "hall_peak_appraise_card", "action": "click",
        "confirm": "goto_appraise_btn", "timeout_ms": 45000, "rate_limit_ms": 600 },
      { "target": "goto_appraise_btn", "action": "click",
        "confirm": "hall_session_cards", "timeout_ms": 45000 }
    ]
  }
}
```

| 字段        | 规则                                                                                               |
| --------- | ------------------------------------------------------------------------------------------------ |
| `target`  | 锚点 id；其 `kind` 必须是 `template` 或 `point`（`point` 时按 `rect` 中心定位，仍须有 `guarded_by` 满足画面证明）          |
| `action`  | `click` / `press`（`{button, until, max_press}`，为"原地按键"预留）/ `do_nothing`                          |
| `confirm` | **`click`/`press`** **步骤必填**（E11）：点击进入下一 step 的判据即"该锚点出现"；末步的 `confirm` 即整个 route 的 `reached` 节点 |
| 末步之后      | 编译为 `action=DoNothing` 且无 `next` 的节点 → 任务自然结束                                                    |

### 4.6 `match` / `render` / `trace`

```jsonc
"match":   { "scales": [0.70,...,1.30], "threshold": 0.75, "margin_default": 0.0 }
"render":  { "layers": ["hit","stage_anchors","ocr","peep"] }
"trace":   { "enabled": true, "keep_sessions": 10 }
```

***

## 5. 数据流与所有权（审查重点）

### 5.1 装配期

```
plugins/treasure/resources/config/treasure_assets.json   ← 唯一人写文件；唯一写入者＝控制台
  └─[navkit.assets]  load_assets(path) -> Assets
        ├─ .validate(code_edges=None) -> Report            # §3.1 纯标准库
        ├─[compile_detect] compile_detection(assets) -> DetectionPlan
        │     DetectionPlan.stage_order   : tuple[str, ...]
        │     DetectionPlan.global_anchors: tuple[str, ...]
        │     DetectionPlan.active        : Mapping[str, tuple[str, ...]]
        │     DetectionPlan.ocr_keys      : Mapping[str, tuple[str, ...]]
        │     DetectionPlan.spec          : Mapping[str, AnchorSpec]   # kind/templates/threshold/scales/rect/arbitration/guarded_by
        │     └─ 内部持有 core.roi_config.ROIConfig（坐标换算与三段式访问复用）
        └─[compile_route]  compile_routes(assets) -> Mapping[str, dict]  # MAA pipeline JSON 文本
              └─ 写 resources/generated/pipeline/<module>_routes.json（文件头 _generated 标记）
```

生命周期：`Assets` 与 `DetectionPlan` 在 `module.start()` 内构造，随模块实例存活；`module.cleanup()` 置 None。跨阶段不重建（避免中途换表）。

### 5.2 帧循环（`treasure` worker 线程，每帧一次）

```
ctx.capture.screenshot() -> frame: np.ndarray RGB (H,W,3)          [现状 CaptureAdapter]
  ↓ active = plan.active[stage] ∪ plan.global_anchors               # 不变量 I-1
  ↓ TreasureStageDetector.detect(frame_rgb, active_rois) -> (stage, round_no)   [现状签名]
        逐 anchor：rect_px = spec.rect.to_pixel(W,H)
                   template_match.find_any(frame, spec.templates, image_dirs,
                                           spec.threshold, plan.scales, rect_px)
                   -> (box_px, score, hit_template)
                   arbitration：多模板 margin 领先判定；round_from_template 解析回合
  ↓ 返回 (stage, round_no) + 同帧产出 scores{anchor:float} + hit_anchor        [改造：detect 需回传明细]
  ↓ handler(stage) -> Intent | None
        Intent = {key, center_norm(cx,cy), box_norm(w,h), guarded_by, fingerprint}
  ↓ 担保检查：Intent.guarded_by 在本帧 scores 中 >= 阈值，否则拒绝提交（新增硬门）
  ↓ Clicker.submit_click(cx, cy, box=box_norm) -> bool（入队成功）    [现状]
  ↓ Clicker.consume_result() -> {ok, intent, device_lost} | None      [现状，下一帧消费]
  ↓ trace.write(FrameTrace(...))                                      [提案 S2]
```

关键改造点（S1）：`detect()` 现只回传 `(stage, round)`，需改为回传 `DetectResult(stage, round_no, scores, hit_anchor, active_used)`，否则 G5（可还原）在数据源处即断链。这是**唯一必须修改的既有函数签名**，调用方只有 `module.py` 与调试台。

### 5.3 跳转链（框架线程）

```
NavGraph.run(entry, reached)                                     [现状 core/nav_graph.py]
  ├─ click_mode=="gamepad" → ctx.gamepad.acquire() 租约（ExitStack，run 期间持有）
  ├─ tasker.post_task(entry)
  │    每 rate_limit 毫秒框架请求识别：
  │      TemplateRecognizer.analyze(ctx, argv)
  │        argv.custom_recognition_param ← compile_routes 写入的节点参数
  │        frame = NavGraph.frame() = ctx.capture.screenshot()    ← 与 §5.2 同一帧源
  │        box_px = template_match.find_any(...)  | fallback_pct | expect_absent
  │        -> AnalyzeResult(box, detail)
  │    命中后框架执行动作：
  │      ClickAction.run(ctx, argv)
  │        argv.box(px) ÷ NavGraph.frame_size() -> (cx,cy,box_norm)
  │        NavGraph.click(...) -> Clicker.submit_click + consume_result 轮询
  │    走 next 边 → 末节点 DoNothing 且无 next → task 结束
  ├─ job.status.done 轮询（0.2s）；ctx.lifecycle.running==False → tasker.post_stop()
  └─ reached 判定：tasker.get_latest_node(reached).completed      ← run() 返回值
```

线程边界：`analyze`/`run` 全部在框架任务线程；`NavGraph._last_frame` 的写者=识别调用、读者=动作调用，二者同线程，无并发。手柄租约计数 `GamepadAdapter._active` 跨线程访问，不变量：**活跃租约期间禁止** **`reset_device()`**（抛 RuntimeError），因此模块的失败重试必须在 `run()` 返回之后。

### 5.4 编辑回写链与生效时机（D6）

```
控制台 UI → POST /api/assets  {module, document}
  → validate_assets(document) -> Report      非 0 errors → 400，不落盘
  → save_atomic(document, assets_path)       tmp + os.replace（现有机制）
  → compile_detection + compile_routes → 写 generated/*
  → 生效时机：
      (a) 冷生效：下次 module.start() 重新 load_assets            ← 默认，零风险
      (b) 阶段边界热生效：帧循环在"阶段发生变化"的那一帧检查 mtime 并重载  ← 提案
      (c) 任意帧热生效：禁止（会导致同帧检测集合与阈值不一致）
```

建议 (a)+(b)：编辑期默认冷生效，运行时提供"阶段边界重载"开关（默认关）。

### 5.5 所有权与并发矩阵

| 数据                       | 写者（线程）        | 读者（线程）                  | 同步手段                                                               |
| ------------------------ | ------------- | ----------------------- | ------------------------------------------------------------------ |
| `assets.json`            | 控制台进程         | 模块 worker（start / 阶段边界） | 原子替换 + `_schema_ver` + 校验前置                                        |
| `generated/*.json`       | 控制台编译步        | MAA 资源加载（run 时）         | 同上；CI 一致性检查                                                        |
| `DetectionPlan`          | 模块 worker 构造  | 同线程检测器                  | 单线程，无锁                                                             |
| `NavGraph._last_frame`   | 框架任务线程        | 框架任务线程                  | 同线程                                                                |
| `Clicker` 导航状态           | 导航后台线程        | worker `consume_result` | 任务槽 + 结果槽                                                          |
| `GamepadAdapter._active` | worker / 框架线程 | 同                       | 计数 + 抛错不变量                                                         |
| `trace.jsonl`            | 模块 worker 追加  | 控制台只读                   | append-only，按帧号单调                                                  |
| 模板 PNG                   | 控制台裁剪/上传      | 检测器、识别桥                 | 文件存在性 + 加载缓存失效（S1 需给 `template_match._cache` 加目录 mtime 失效，否则改图不生效） |

最后一条是 rev.1 遗漏的真实风险：**模板缓存无失效机制**，控制台换图后运行时不会重读。列入 S1 交付。

***

## 6. 编译与一致性保证

| 性质    | 要求                                                          | 校验方式                                                        |
| ----- | ----------------------------------------------------------- | ----------------------------------------------------------- |
| 确定性   | 同一 `assets.json` 编译两次字节相同（键序固定、无时间戳）                        | CI：`compile → diff`                                         |
| 幂等    | 生成物可被删除后重建                                                  | 自检脚本 `--regen` 对比                                           |
| 可审计   | 生成物头部写 `_generated`、来源 hash（assets 的 sha256 前 8 位）          | 校验器读头比对                                                     |
| 禁止手改  | 生成物目录被编辑 → CI 失败                                            | `python -m maaracing_assistant.core.navkit.compile --check` |
| 覆盖优先级 | `owner:global` 先加载、模块同名覆盖；覆盖必须显式（`_override: true`）否则告警 W07 | 校验器 + 加载器                                                   |

编译映射（v3 → MAA pipeline）：

| v3                        | MAA 节点                                                                                             |
| ------------------------- | -------------------------------------------------------------------------------------------------- |
| step 的 `target`（template） | `recognition: Custom` + `custom_recognition: MRA_Template` + `{templates, threshold, scales, roi}` |
| step 的 `target`（point）    | 同上 + `fallback_pct: [cx, cy]`                                                                      |
| `confirm`（下一 step）        | 当前节点 `next: [<confirm 节点名>]`                                                                       |
| `confirm`（末步）             | 独立 `DoNothing` 终点节点，`custom_recognition_param.expect_absent` 可选                                    |
| `action: click`           | `action: Custom` + `custom_action: MRA_Click` + `{timeout_s, wait_after_ms}`                       |
| `action: press`           | `custom_action: MRA_Press` `[提案，待 core 补桥]`                                                        |
| 节点名                       | `<module>::<route>::<step#>::<target>`（避免跨模块重名）                                                    |

***

## 7. 迁移与回退

### 7.1 迁移器接口 `[提案]`

```python
# core/navkit/legacy.py
def schema_of(doc: dict) -> int                      # 读 _schema_ver
def migrate_v2_to_v3(doc: dict, *, semantic: dict) -> tuple[dict, list[str]]
    # semantic：无法从 v2 推得的部分（order/page/kind/guarded_by），
    #           由 §2.2 的 Python 常量作为唯一输入源，人工复核后固化
    # 返回 (v3 文档, 缺口清单)
def diff_v2_v3(old: dict, new: dict) -> list[str]     # 逐字段比对：rect/threshold/templates 必须逐位相同
```

**缺口清单必须非空可查**：v2 没有 `owner`、`page`、`kind`、`guarded_by`，这些由迁移器按规则推断 + 人工确认，禁止静默造默认。

### 7.2 分批与回退

1. 迁移器先产出 v3 草稿 + 缺口报告，**不落运行时路径**（评审用）。
2. 分 5 批上纸（每批 ≤8 锚点，每批一次提交）：`stage` → `actions` → `ocr` → `appraisers/eggs` → `stages/transitions`。每批过 §9 回归才继续。
3. 回退：`legacy.py` 保留 v2 只读适配一个大版本；`detector` 的读取入口是一行开关（`NAVKIT_SOURCE=v2|v3` 环境变量），单点可回退。
4. 禁止事项：迁移期间不得改任何 rect/threshold（纯搬迁，改动与搬迁分开提交，否则回归失败无法归因）。

### 7.3 归属与物理位置（G1「归属可分」的落地形式）

| `owner`    | 模板图目录                                      | 解析顺序                 | 典型资产               |
| ---------- | ------------------------------------------ | -------------------- | ------------------ |
| `global`   | `core/resources/image/` `[提案，目录待建 = F9]`   | 第 1 位                | 大厅锚点、跨模块共用页面标题、设置页 |
| `<module>` | `plugins/<module>/resources/image/` `[现状]` | 第 2 位，同名可覆盖 `global` | 模块私有按钮/横幅/读数区      |

不变量：

- 解析按 `image_dirs` 顺序**首次命中**（`core/template_match.load_template` 已如此实现 `[现状]`）。

- 模块覆盖 `global` 同名资产必须显式声明 `_override: true`，否则 W07；`owner=global` 但文件只存在于模块目录 → W04。

- 把某资产从全局改为模块私有（或反向）＝ 改 `owner` + 移动文件 + 重编译，**代码零改动**；这就是"自由划分所属"的全部成本。

- 编译产物落模块的 `resources/generated/`，与人工资产目录物理分离，CI 只校验生成物。

***

## 8. 控制台改造

### 8.1 端点（`[现状]` 保留，`[新增]` 标注）

| 方法   | 路径                                                                                              | 请求                            | 响应                                                                        |
| ---- | ----------------------------------------------------------------------------------------------- | ----------------------------- | ------------------------------------------------------------------------- |
| GET  | `/api/list_sessions` `list_images` `image` `rois` `list_templates` `template` `template_status` | 同现状                           | 同现状                                                                       |
| POST | `/api/rois` `template_upload` `crop_to_template` `match_score` `cross_frame_test`               | 同现状                           | 同现状                                                                       |
| GET  | `/api/assets` `[新增]`                                                                            | `?module=`                    | 完整 v3 文档 + 校验报告                                                           |
| POST | `/api/assets` `[新增]`                                                                            | `{module, document}`          | 校验通过→落盘+编译；否则 400 + `Report`                                              |
| GET  | `/api/graph` `[新增]`                                                                             | `?module=`                    | `{nodes[], edges[], orphans[], code_edges_missing[]}`（供树/思维导图渲染）          |
| GET  | `/api/trace` `[新增]`                                                                             | `?module=&session=&from=&to=` | FrameTrace 行数组（按帧号区间）                                                     |
| POST | `/api/compile` `[新增]`                                                                           | `{module}`                    | 重编译并返回 diff（仅 dev 模式）                                                     |
| GET  | `/api/template_status`（扩展）                                                                      | <br />                        | 增加 `unguarded_points[]`、`scales_mismatch[]`、`unreferenced[]`、`dangling[]` |

### 8.2 视图与数据绑定

| 视图   | 输入                           | 渲染要素                                                                                                |
| ---- | ---------------------------- | --------------------------------------------------------------------------------------------------- |
| 结构树  | `/api/graph`                 | 分层 = `stages.order`；泳道 = `pages`；边 = `transitions` + `routes`；`dynamic_narrow` 渲染为"进入代码"终止符；无证据目标标红 |
| 实况叠加 | `/api/trace` + 帧号            | 在**同一棵树**上叠加实际走过的边（次数、每帧分数中位数、掉头点）                                                                  |
| 帧上标定 | `image` + `/api/match_score` | 任锚点在选中帧的框与分数（与运行时同一匹配实现，F8 修后成立）                                                                    |
| 编辑面板 | `/api/assets`                | 归属(owner)/顺序(order)/阈值/尺度/阶段勾选/连线；保存前本地跑一次校验器，即时反馈                                                  |

### 8.3 adapter 契约升级

`[现状] tools/debug_studio/core/categories.py`：

```python
CategoryDefs(categories: tuple[str,...], *, name: str, default_items: dict)
  .validate(data) .fill_defaults(data) .load(path) .save_atomic(data, path)
```

`[提案]` 取代为：

```python
class ModuleKit:                                   # 每模块一个
    name: str
    assets_path: Path
    session_dir: Path
    frame_pattern: re.Pattern                      # 修 F6
    def pages(self) -> dict[str, str]
    def stage_order(self) -> tuple[str, ...]
    def dynamic_nodes(self) -> dict[str, str]      # 树上"进代码"出口
    def code_edges(self) -> frozenset[tuple[str, str]]   # 供 §4.4 互查
    def register_endpoints(self, state) -> None    # 领域端点（OCR/彩蛋）原样保留
```

### 8.4 改名

`tools/debug_studio` → `tools/navkit`（83 处 / 20 文件）。执行序：`git mv` → 脚本与 `start.cmd` → `tests/test_debug_studio_*` → `.github/workflows/test.yml` → 三份 CODE\_WIKI → 旧路径留 README 指针。**`docs/update_log.md`** **不回改**（历史记录）。与 D4 绑定，未批则本方案其余部分照做、目录名保留。

***

## 9. 验证策略

### 9.1 等价性回归（S1 合入硬闸门）

```
python tools/navkit/regress_stages.py --sessions all [--new-only]
```

| 项    | 规格                                                                                      |
| ---- | --------------------------------------------------------------------------------------- |
| 输入   | `user_data/debug/treasure/*` 全部 raw 帧（当前 3582 帧 / 5 会话）                                 |
| 对照   | 同一帧分别喂 旧实现（`detector.detect` + Python 常量）与 新实现（读 v3 编译的 DetectionPlan）                  |
| 比较键  | 逐帧 `(stage, round_no)` 必须完全一致；差异打印帧号、双方结果、命中锚点与分数                                       |
| 分数容差 | 绝对差 ≤ 1e-4（阈值/尺度搬迁不得引入漂移）                                                               |
| 退出码  | 0 一致；1 有差异（阻塞合入）；2 数据/环境错误                                                              |
| 附加报告 | 每锚点：命中帧数、分数分位数（P10/P50/P90）、**非所属阶段最高分**（串图风险指标，> 阈值−0.05 则告警）、命中框中心像素标准差（§4.2 位置稳定性判据） |

此 harness 建成后复用于：改 rect、换模板、调尺度表、S3 路由生成，任何影响识别的改动都要跑。

### 9.2 单测（纯标准库，进 `tests/`）

| 文件                              | 覆盖                                                  |
| ------------------------------- | --------------------------------------------------- |
| `tests/test_navkit_validate.py` | §3.3 全部 E/W 规则各一正一反（预计 45–60 断言）；`code_edges` 双向检查  |
| `tests/test_navkit_compile.py`  | 确定性（两次编译字节相同）、节点名唯一、route→pipeline 映射表逐条、末步无 next   |
| `tests/test_navkit_legacy.py`   | v2 判定、迁移缺口清单非空可查、`diff_v2_v3` 对 rect/threshold 逐位比对 |

### 9.3 离线自检与真机清单

`scripts/verify_nav_pipeline.py` `[现状]` 扩展：加载生成物、校验节点名规范、担保闭环检查。
真机验收（每阶段末）：① 鉴宝从大厅自动进到对局并完整跑 3 场；② 中途停止即时生效（租约正确释放、无卡死）；③ `intent` 模式下不产生真实点击；④ 掉回大厅能被全局锚点拉回（不变量 I-1）。

***

## 10. 里程碑（文件级）

| 阶段            | 新增                                                                                                                                                               | 修改                                                                                                                                                                                             | 删除                                                                 | 退出条件                                         | 回滚                      |
| ------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ | -------------------------------------------- | ----------------------- |
| **S0** 规格冻结   | `core/navkit/{__init__,assets,validate,legacy}.py`；`tests/test_navkit_validate.py`、`test_navkit_legacy.py`                                                       | 本文件定稿                                                                                                                                                                                          | —                                                                  | §9.2 全绿；对真实 v2 文件能产出缺口报告                     | 未接线，删文件即可               |
| **S1** 语义上纸   | `core/navkit/compile_detect.py`、`trace.py`（占位）；`plugins/treasure/resources/config/treasure_assets.json`；`tools/navkit/regress_stages.py`；`core/resources/image/` | `detector.py`（读 plan、`detect()` 回传 `DetectResult`、尺度表改单一来源）、`module.py`（删 §2.2 五常量、OCR 清单改读 plan、注册 `CODE_EDGES`）、`eggs.py`、`tools/debug_studio/core/reader.py`、`core/template_match.py`（缓存失效） | `_ROI_STAGE`、`_STAGE_PERCEPTION`、`_GLOBAL_ANCHORS`、`_STAGE_*_KEYS` | §9.1 逐帧一致 + 真机 3 场                           | `NAVKIT_SOURCE=v2` 一行回退 |
| **S1.5** 担保登记 | 校验规则 E10/E11 生效；21 个 `actions` 归类与 `guarded_by`                                                                                                                  | `assets.json`                                                                                                                                                                                  | —                                                                  | 无担保清单为空或每条被显式认可；新增模板图 = 0                    | 纯数据                     |
| **S2** 决策流水   | `trace.jsonl` 写入（`FrameTrace` 字段 §5.2）                                                                                                                           | `module.py` 帧尾追加                                                                                                                                                                               | —                                                                  | 任取一帧可答"为何未点 X"；单会话 ≤1MB；`keep_sessions` 生效   | 开关关闭                    |
| **S3** 路由单真源  | `compile_route.py`、`resources/generated/pipeline/treasure_routes.json`                                                                                           | `NavGraph`（吃生成物）、`hall.json`（仅 racing，注明鉴宝不再手写）、`treasure/module.py`（两段式 `run()` 接线入口跳转）                                                                                                       | 手写的鉴宝跳转定义（F2 类）                                                    | §6 CI 一致性通过；真机大厅→鉴宝大厅自动进                     | 保留生成前快照                 |
| **S4a** 看得见   | `ModuleKit`（`pages/stage_order/dynamic_nodes/code_edges/frame_pattern`）、`GET /api/graph`、控制台只读结构树视图（分层 + 泳道 + 边 + 灰块"进代码"出口 + 无证据目标标红）                           | 改名（D4，与本步同批执行）                                                                                                                                                                                 | `CategoryDefs` 的只读部分                                               | 打开控制台即可看到**完整**判断树（不是半张）；树内容与 §9.1 报告的锚点清单一致 | 纯增量，不改运行时               |
| **S4b** 改得动   | `GET/POST /api/assets`、编辑面板（归属/顺序/阈值/连线）、`/api/trace`、`/api/compile`、回放与实况叠加（D5/F7）                                                                              | `save_atomic` 接 v3 + 保存前本地校验；生效按 D6（冷默认 + 阶段边界热开关）                                                                                                                                             | —                                                                  | 在树上改一处阈值→保存→（下次启动或阶段边界）运行时按新值跑；点一帧→树上亮       | 纯增量，可整体推迟               |
| **S5** 收口     | —                                                                                                                                                                | 三份 CODE\_WIKI 相应章节、AGENTS.md 失效红线（`ButtonDef`/`_press_and_verify`/`_last_stick` 指向已死代码者）                                                                                                       | 缺失的两份被引用 docs 的悬空引用                                                | 文档-代码交叉引用抽查通过                                | —                       |

### 10.1 落地验收记录（2026-09-05）

| 项       | 结果                                                                                            |
| ------- | --------------------------------------------------------------------------------------------- |
| 全量测试    | `pytest tests`：310 passed                                                                     |
| 逐帧等价回归  | 5 会话 / 3582 帧，`(stage, round_no)` 差异 = 0，环境错误 = 0（报告：`tools/navkit/out/regress_stages.json`）  |
| 离线自检    | `scripts/verify_nav_pipeline.py` EXIT=0                                                       |
| 路由生成物校验 | `tools/navkit/compile_routes.py --check` 通过                                                   |
| 迁移纯度    | `diff_v2_v3` 差异数 = 0（纯搬迁，rect/threshold 逐位一致；报告：`tools/navkit/out/gaps.txt`，235 条缺口全部人工复核后固化） |

保留边界（有意不并入本轮，属 S1.5 独立数据变更）：`session_master_badge` 等无模板 point 件的 `guarded_by` 尚未人工指定（gaps.txt 已列明），不影响运行时（运行时按 E10 拒绝无担保点击）。racing 重做不在本方案内，S5 之后单开一轮（其入口件模板与 `MRA_Press` 桥在那一轮处理）。

***

## 11. 风险登记

| #  | 风险                      | 影响                       | 概率       | 缓解                                                          | 触发信号                  |
| -- | ----------------------- | ------------------------ | -------- | ----------------------------------------------------------- | --------------------- |
| R1 | 搬纸过程改坏判定                | 真实竞拍误判阶段（资金损失）           | 中        | §9.1 为合入硬闸门；分 5 批；搬迁与调参分开提交                                 | 回归差异表非空               |
| R2 | v3 过度设计                 | racing 重做时套不进，被迫改 schema | 中        | S1 出口做反例检查：归位/弹窗/商店三件事套 v3；套不进即改                            | 需要新增"表达式"字段才能表达       |
| R3 | `transitions` 互查被嫌烦而被绕过 | 树变成假树，G5 失效              | 中高       | 声明放处理器装饰器上，一行；启动失败信息精确到边                                    | 有人给 `code_edges` 加白名单 |
| R4 | 模板缓存无失效                 | 控制台换图后运行时不认新图，误判为"改了没用"  | 高（当前已存在） | S1 必修（按目录 mtime 失效）                                         | 换图后分数不变               |
| R5 | detect() 签名改动牵连         | 调试台/测试需同步改               | 低        | 改动点单一；`DetectResult` 提供 `__iter__` 兼容二元组解包                  | 回归脚本报类型错              |
| R6 | 帧目录 1.29GB 且随会话累积       | 磁盘占用（C 盘）                | 中        | `keep_sessions` 对 debug 帧同样适用（G6 例外条款：帧落 C 盘是用户既定决策，但必须有上限） | 目录 > 阈值               |
| R7 | 阶段边界热重载引入状态不一致          | 偶发难复现的判定抖动               | 低        | D6 默认关；仅在阶段切换那一帧重载并打日志                                      | 同一阶段内 plan 版本变化       |

***

## 12. 待决策

| #      | 议题                                    | 选项                            | 推荐                        | 影响面                               | 不决策的后果                        |
| ------ | ------------------------------------- | ----------------------------- | ------------------------- | --------------------------------- | ----------------------------- |
| D1     | `transitions` 上纸 + 纸码互查               | 上 / 只上 anchors                | **上**                     | 每阶段处理器 +1 行声明；启动期校验               | 树无走向，G5 只兑现"设计图"，"还原"无对照物     |
| D2     | 固定件是否逐键配模板                            | 逐键 / 担保制（§4.2）                | **担保制**                   | 无新增模板；`guarded_by` 字段             | 串键与状态污染风险进入出价热路径              |
| D3     | 跳转 JSON 是否为生成物                        | 生成物+CI 校验 / 手写但引用 anchors     | **生成物**                   | `resources/generated/` 新目录；CI 加一步 | F2 类双真源必然复发                   |
| D4     | `tools/debug_studio` → `tools/navkit` | 改 / 不改                        | **改**                     | 83 处 / 20 文件                      | 语义持续回流到 Python 常量             |
| D5     | trace 常开                              | 常开 / 仅调试模式                    | **常开**                    | 每帧 \~200B，会话 \~0.7MB，留 10 份       | 真机偶发掉头无法归因                    |
| **D6** | 编辑生效时机（§5.4）                          | 冷 / 阶段边界热 / 任意帧热              | **冷为默认 + 阶段边界热（默认关）**     | 帧循环加一处 mtime 检查                   | 编辑后"没生效"会被当成 bug 追查，浪费归因时间    |
| **D7** | `detect()` 回传结构                       | `DetectResult` / 保持二元组 + 旁路缓存 | **`DetectResult`**（带迭代兼容） | 2 个调用点                            | G5 数据源断链，trace 只能记"结果"记不了"依据" |

### 12.1 决策状态（2026-09-05 定）

授权范围：技术与逻辑判断由执行方决断；用户只验收"最终展现的性质"。据此定：

| 编号 | 决定                                                    | 理由（一句话）                                |
| -- | ----------------------------------------------------- | -------------------------------------- |
| D1 | **上** `transitions` + 纸码互查（E17/E18）                   | 没有边的树不是判断树；G5 的"还原"必须有一棵设计树作对照         |
| D2 | **担保制**，固定件不逐键配模板                                     | 位置固定时加识别等于在确定解上加噪声（串键、状态污染、实时性）        |
| D3 | 跳转 JSON 为**编译产物**，禁止手改，CI 校验一致                        | F2 已由我们自己在 2026-09-05 制造过一次，不禁止必复发     |
| D4 | **改名** `tools/navkit`                                 | 目录名会持续把语义赶回 Python；成本 83 处 / 20 文件，一次性 |
| D5 | trace **常开** + `keep_sessions=10`                     | 真机偶发问题不可复现，仅调试模式开等于没有流水                |
| D6 | **冷生效为默认**；阶段边界热生效为可开关能力（默认关）；禁止任意帧热生效                | 同一帧的检测集合与阈值必须来自同一版本                    |
| D7 | `detect()` 改回传 **`DetectResult`**（`__iter__` 兼容二元组解包） | 否则 G5 在数据源处断链；调用点仅 2 处                 |

结构调整：S4 拆为 **S4a 只读结构树（S1 后即可交付）** 与 **S4b 编辑回写 + 归属/排序 + 回放（S3 后）**，让"看得见"提前于"改得动"（详见 §10）。

***

## 13. 术语

| 术语                 | 定义                                         |
| ------------------ | ------------------------------------------ |
| anchor             | 一个识别/点击目标的完整定义（`kind` + 区域 + 素材 + 阈值 + 归属） |
| entry-target       | 点击导致页面或阶段变化、且位置可能随活动轮换而变的锚点                |
| panel-fixed-target | 面板内固定件，位置随面板整体位移，允许坐标定位，需 `guarded_by`     |
| `guarded_by`       | 为坐标型目标提供"该面板此刻在场"证据的模板锚点                   |
| `confirm`          | 一次跳转步进"确实进入了下一步"的证据锚点                      |
| DetectionPlan      | `assets.json` 编译出的帧循环检测计划（阶段裁剪、全局锚点、锚点参数）  |
| code\_edges        | 运行时代码声明已实现的迁移边集合，与 `transitions` 双向互查      |
| trace              | 与 raw 帧同帧号对齐的每帧决策流水（JSONL）                 |
| 冷生效 / 阶段边界热生效      | 下次模块启动生效 / 仅在阶段切换那一帧重载配置                   |

