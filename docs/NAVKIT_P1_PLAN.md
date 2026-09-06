# NavKit P1 · 决策规则数据化方案

> 状态：**草案（待评审）** · 范围：鉴宝（treasure）
> 前置：NAVKIT\_PLAN rev.2（已落地 S0-S5）· 本文档仅覆盖「决策逻辑上纸」这一横向切片

***

## 0. 评审须知（给第二评审人）

### 本方案要达成的效果

1. 把当前硬编码在 Python 里的\*\*「决策规则」与「调参常量」\*\*迁入统一的 JSON 单一真源（treasure\_assets.json 新 `policies` 段），使控制台能在 UI 里预览与修改。
2. **行为零变化**：迁移全程以 3582 帧历史 trace 逐帧等价回归（diff=0）为闸门——不引入新策略、不改任何数值、不提任何性能。
3. 为未来的 `racing` 重做建立「模块 = 识别资产 + 决策规则（JSON） + 算法灰块（代码）」的开发语义。

### 明确不做

- ❌ 不改出价策略算法（BidStrategy 内部定价逻辑留码，见 §2-E）。

- ❌ 不新增/删除任何锚点、阈值、转移（那是 S1 已落地范畴）。

- ❌ 不引入新依赖（纯标准库 + 既有 pytest）。

- ❌ 不做运行时热加载（冷生效，策略级改动重启生效）。

### 为什么这样定界

MAA / MaaFramework 六年演进验证了同一条边界：**流转与场景规则上纸、算法与引擎留码**。MAA 的肉鸽策略、基建算法始终是 C++ 代码；出错率高的恰恰是"把算法塞进 JSON 造图灵完备配置"。我们沿用这条经血泪验证的线，并叠加本项目的杀手锏：**3582 帧 trace 逐帧等价回归**——这是 MAA 生态（包括 MaaPipelineEditor/MaaInspector）都不具备的机械验证手段。

### 评审重点五处

1. §2 上纸范围界定表（每类配置的归属与理由，尤其 E 类为何留码）。
2. §4 policies schema 逐字段（规则表达式是否足够表达现有 `_decide_action` 全部分支）。
3. §5 等价迁移策略（引擎化改造的平滑路径，diff=0 如何达成）。
4. §6 验收闸门（回归口径、excluded 白名单规则）。
5. §7 数据流与线程所有权（谁读、谁写、冷生效语义）。

***

## 1. 背景：为什么现在做

### 1.1 现状病根（代码盘点证据）

鉴宝 `module.py` 中，**「游戏逻辑」被分成了三种形态**，分界线不清晰：

| 形态              | 存量                         | 位置                              | 谁来改         |
| --------------- | -------------------------- | ------------------------------- | ----------- |
| 已数据化（v3 资产）     | anchors/transitions/routes | treasure\_assets.json           | 控制台/手改 JSON |
| 已 GUI 可调（运行时参数） | 场次目标/每日上限/风控/模式            | `get/set_module_config` 通道      | GUI 面板      |
| **硬编码在代码里**     | 决策规则 + 调参常量 + 匹配阈值         | module.py 常量 / `_decide_action` | **只有改代码的人** |

第三条就是本次要啃的：**目前** **`_decide_action`（约 170 行 stage→action 查表）+ 十几个调参常量（冷却/重试/超时帧数）全部固化在 Python 里**，控制台路径树只能看不能改，"游戏更新改了哪里 → 要编译改代码" 的老问题在这部分依然存在。

### 1.2 你设定的终局对齐

"一站式预览并修改逻辑的超级 app" = 识别资产 ✅ 已可看（路径树）+ 决策规则 ❌ 本次 + 调参 ❌ 本次 + 算法灰度块（留码，超蓝图范围）。

***

## 2. 上纸范围界定（核心决策表）

> 判断三问：① 会不会随游戏更新变化？ ② 是"做什么"还是"怎么做"？ ③ 变更是否涉及核心算法？
> 一票否决：涉及算法/引擎 → 留码。

| 类别             | 具体项（盘点实证）                                                          | 归属               | 理由                              |
| -------------- | ------------------------------------------------------------------ | ---------------- | ------------------------------- |
| **B1 阶段→动作查表** | `_decide_action`：游戏大厅→点 purl\_peak、活动页面→点 goto\_appraise、领取分红→点领取… | **上纸（P1）**       | 纯映射，随游戏/布局变化，正是"做什么"            |
| **B2 条件门槛**    | 冷却帧内不产出意图 / 数据齐备才准星指按钮 / 重试超限停手                                    | **上纸（P1）**       | 声明式条件，可表达为 guards               |
| **C 调参常量**     | 冷却/重试/超时帧数（详见 §3 表格）                                               | **上纸（P1）**       | 随实机调优需要改，留码=改代码                 |
| **A 匹配阈值/ROI** | `_APPRAISER_MATCH_THRESHOLD` 等                                     | **归属争议→P1 统一收编** | 部分已在 v3 资产，剩余的并进 tuning 段（见 §4） |
| **D 运行时参数**    | 场次/上限/风控/模式                                                        | **保留 config 通道** | 已经 GUI 可调，勿重复建设                 |
| **E 算法/引擎**    | BidStrategy 定价、OCR、点击执行器、手柄导航                                      | **留码**           | 算法=怎么做，JSON 化只会造图灵完备配置          |
| **F 基建**       | debug 落盘、IO worker、Clicker 协议                                      | **留码**           | 与游戏变化无关的机制层                     |

### 2.1 E 类的反例自检（为什么 BidStrategy 不上纸）

- 定价是**数值计算+启发式**，不是"做什么"；把它写成条件表会让逻辑晦涩、测试失效、且失去 Python 数值能力（正负无穷/多目标优化）。

- MAA 六年未动这条线的实证（肉鸽策略 60+ C++ 文件、基建算法反复重写仍未 JSON 化）。

- 结论：**风险收益比极差，明确划出**。出价策略的调参（already config 化）足以满足"傻瓜式"诉求。

***

## 3. 固化常量全清单（盘点实证，迁移的迁移目标）

### 3.1 匹配阈值 / ROI（并入 tuning 或收编进 assets）

| 常量                           | 当前值                      | 代码位置          |
| ---------------------------- | ------------------------ | ------------- |
| `_APPRAISER_SEARCH_ROI`      | (0.03, 0.18, 0.97, 0.92) | module.py:125 |
| `_APPRAISER_MATCH_THRESHOLD` | 0.72                     | module.py:126 |
| `_CHECK_MATCH_THRESHOLD`     | 0.62                     | module.py:142 |
| `_SESSION_MATCH_THRESHOLD`   | 0.90                     | module.py:155 |
| `_SMART_BID_MATCH_THRESHOLD` | 0.72                     | module.py:335 |

> 注：`appraisers`/`stage` 段的阈值已入 v3 资产；上表是**尚未数据化**的部分，P1 统一收编进 `tuning`，消除"同名阈值两处定义"的隐患（S1 遗留的已知债）。

### 3.2 冷却 / 重试 / 超时（并入 tuning）

| 常量                                    | 当前值 | 语义           |
| ------------------------------------- | --- | ------------ |
| `SESSION_START_CLICK_COOLDOWN_FRAMES` | 3   | 点开始匹配后冷却     |
| `CLICK_COOLDOWN_S`                    | 0.2 | 最小物理点击间隔     |
| `CLICK_RETRY_FRAMES`                  | 10  | 点击后等待切换帧数    |
| `CLICK_RETRY_MAX`                     | 3   | 同一意图最多重试     |
| `SETTLE_SKIP_RETRY_FRAMES`            | 10  | 领取无响应判定帧数    |
| `SETTLE_SKIP_RETRY_MAX`               | 3   | 领取无响应重试上限    |
| `POPUP_CONTINUE_RETRY_FRAMES`         | 3   | 弹窗连点重试帧      |
| `POPUP_CLICK_COOLDOWN_FRAMES`         | 5   | 弹窗点击冷却       |
| `DAILY_HIGH_TIMEOUT_FRAMES`           | 8   | 今日最高读积分超时    |
| `EGG_OCR_TIMEOUT_FRAMES`              | 8   | 彩蛋 OCR 超时兜底  |
| `CLICK_RETRY_FRAMES_BY_KEY`           | 见代码 | 按 key 差异化重试帧 |

### 3.3 上限类（保留 config 通道，不入 JSON 资产）

`DEFAULT_MAX_DAILY_LOOPS=50`、`TARGET_SESSION_OPTIONS`、`STRATEGY_LABEL` —— 已由 `set_module_config` 承担，勿重复。

***

## 4. policies schema（草案，逐字段）

> 位置：`treasure_assets.json` 顶层新增 `policies` 段。编译产物不变（policies 不参与 pipeline 编译，只作为模块运行时决策源）。

```jsonc
{
  "_module": "treasure",
  "anchors": { /* 既有 v3 */ },
  "stages": { /* 既有 v3 */ },
  "transitions": [ /* 既有 v3 */ ],
  "routes": { /* 既有 v3 */ },

  "policies": {
    "_schema_ver": 1,

    // B1：阶段 → 动作（查表）
    "on_stage": [
      {
        "stage": "游戏大厅",
        "action": { "key": "hall_peak_appraise_card", "hint": "进入巅峰鉴宝活动页" }
      },
      {
        "stage": "活动页面",
        "action": { "key": "goto_appraise_btn", "hint": "前往鉴宝" }
      },
      {
        "stage": "鉴宝大厅(选择场次)",
        "action": { "key": "session_last_decision", "src": "deferred" },
        "fallback": { "key": "session_waiting", "hint": "等待识别场次按钮..." }
      }
      // ... 选择鉴宝师 / 出价局各轮 / 领取分红 / 结算弹窗
    ],

    // B2：条件门槛（相对 `_decide_action` 里的 if 分支）
    "guards": [
      { "id": "popup_cooldown_arm", "when": "stage==结算弹窗", "then": "clear_cooldown" },
      { "id": "settle_ready", "when": "settle_my_income>=0", "then": "allow_click_settle" },
      { "id": "settle_first_entry", "when": "not settle_collect_clicked_once", "then": "allow_click_settle" },
      { "id": "settle_skip_timeout", "when": "frame-settle_skip_since>=RETRY_FRAMES", "then": "retry_arm" }
      // ...
    ],

    // C：调参常量（含 §3.1 阈值、§3.2 冷却重试）
    "tuning": {
      "click_cooldown_s": 0.2,
      "click_retry_frames": 10,
      "click_retry_max": 3,
      "settle_skip_retry_frames": 10,
      "settle_skip_retry_max": 3,
      "popup_continue_retry_frames": 3,
      "popup_click_cooldown_frames": 5,
      "daily_high_timeout_frames": 8,
      "egg_ocr_timeout_frames": 8,
      "session_start_click_cooldown_frames": 3,
      "match_thresholds": {
        "appraiser_search_roi": [0.03, 0.18, 0.97, 0.92],
        "appraiser_match_threshold": 0.72,
        "check_match_threshold": 0.62,
        "session_match_threshold": 0.90,
        "smart_bid_match_threshold": 0.72
      }
    }
  }
}
```

### 4.1 字段语义约束

- `on_stage[].stage` 必须命中 `stages.order`（校验错误 P01）。

- `on_stage[].action.key` 必须命中锚点/特殊保留 key（`session_last_decision` 等 `src=deferred` 保留 key 白名单）（P02）。

- `guards[].when` 使用受限表达式：仅允许 `==`/`>=`/`frame-` 前缀差分/布尔组合，禁止任意代码（P03）。

- `tuning` 数值须为正数、阈值须在 \[0,1]（P04）；`tuning` 全部可有默认值，缺省时回落代码常量（P05，可删则删，防双源漂移）。

### 4.2 与既有 v3 的关系

- `policies` 是**新增段**，向后兼容：老 JSON 无 `policies` → 引擎回退到代码常量（保证过渡期零故障）。

- 编译校验新增规则码：`P01`-`P05`（并入 `validate.py` 的 E/W 体系，作为阻断「P 系列」）。

***

## 5. 等价迁移策略（diff=0 如何达成）

### 5.1 三步法（每步独立合入、独立回归）

1. **抽取纯函数**：把 `_decide_action` 重构成 `decide(state: DecisionState) -> Decision`（纯输入/纯输出，剥离 pygame/MAA/自旋锁依赖），用现有 trace 重放验证不改行为（**阶段一闸门：模块内单测 + 313 现有测试全绿**）。
2. **引擎化**：实现 `PolicyEngine`（读 policies JSON → 编译 `PolicyPlan`），在 `decide()` 里按"有 policies 数据 → 引擎求值，无 → 旧逻辑"双轨并跑（**阶段二闸门：3582 帧双轨逐帧 diff=0**）。
3. **收敛迁移**：逐规则把旧 if 分支删除、由 policies 数据替代；旧逻辑清空后 `decide()` 只留引擎入口（**阶段三闸门：3582 帧 diff 仍 =0 + 删除全部死代码**）。

### 5.2 回归口径（与 S1 同规格强化）

- **素材**：5 会话 / 3582 帧 / 1.29GB 历史 trace（含 frame/stage/round\_no/scores/hit\_anchor/intent）。

- **指标**：每帧 `(stage, decision.key)` 与基线逐位比对，累计差异必须 =0；容差场景仅允许**白名单**（见 5.3）。

- **工具**：`tools/navkit/regress_decisions.py --baseline <v2> --new <policies>`（新增，仿 `regress_stages`）。

### 5.3 白名单规则（严谨性红线）

- 差异产生在**不参与决策的字段**（如 hint 文案措辞微调）→ 允许，须在报告注明。

- 差异产生在**同一决策 key 但 hint 顺序变化** → 允许，注明。

- 差异产生在**决策 key 本身变化** → **拒绝**，必须回到代码侧修正迁移。

- 白名单不设数量上限但**每项必须人工签核**，无"自动通过"。

***

## 6. 验收闸门（合并 master 的硬条件）

| # | 项目                          | 判定                                   |
| - | --------------------------- | ------------------------------------ |
| 1 | 全量测试（.venv Python 3.11）     | 313 现有 + 新增全部 passed                 |
| 2 | 3582 帧等价回归                  | diff（stage, decision.key）= 0（或白名单签核） |
| 3 | `compile_routes.py --check` | 通过（policies 不参与产物，但校验链生效）            |
| 4 | `validate.py` 新增 P 系列规则     | 0 阻断                                 |
| 5 | 死代码清理                       | 迁移完成后 `_decide_action` 旧分支全部删除       |
| 6 | 性能                          | 引擎化后主循环单帧增量耗时 < 5%（帧率不劣化）            |
| 7 | 冷生效语义                       | 重启后 policies 生效，运行中改 JSON 不热载（文档写明）  |

***

## 7. 数据流与线程所有权

```
[treasure_assets.json 含 policies]          [module.py 运行时]
          │ 读（冷启动一次）                        │
          ▼                                        ▼
   core/navkit/compile_policy.py ──→ PolicyPlan ──→ PolicyEngine.decide(state)
    （解析+校验 P01-P05）              （内存不可变）        │
          │                                              ▼
          └───────────── 写（仅 UI 原子落盘，见下）   Decision{key,hint,...}
                                                          │ 走既有 Clicker 分派
```

- **读**：模块启动时 `Assets.from_document` 同一入口加载 policies（冷生效）。主循环内**只读** `PolicyPlan`，不加锁。

- **写**：控制台「策略」视图 → `POST /api/assets`（既有原子落盘链路 tmp+replace+校验）→ 重启生效。运行中改文件不热载（文档明示，防双轨错位）。

- **所有权**：JSON = 唯一真源（连同 anchors/transitions）；代码 = 只实现引擎，不存规则副本。**消除"同名阈值两处定义"的 S1 遗留债**。

***

## 8. 分期与工作量

| 阶段       | 内容                                       | 闸门                 |
| -------- | ---------------------------------------- | ------------------ |
| P1a      | 纯函数抽取 + trace 重放基线                       | 313 测试全绿           |
| P1b      | policies schema + 编译器 + 校验规则 P01-P05     | `-check` 通过 + 新增单测 |
| P1c      | 双轨并跑 + 3582 帧等价回归                        | **diff=0**         |
| P1d      | 收敛删除死代码                                  | diff=0 + 测试全绿      |
| P2（后续轮次） | 控制台「策略」编辑视图（复用 React Flow + assets 原子落盘） | UI 验收              |
| P3（后续轮次） | 编排可视化（routes/弹窗流程）                       | UI 验收              |
| P4（后续轮次） | racing 按新语义重做                            | 独立轮次               |

> 注：P1 为纯后端切片，前端改造（P2）不在本次范围，但 schema 与 API 契约按 P2 可消费设计。

***

## 9. 术语

- **决策规则**：`_decide_action` 的 stage→action 映射与条件门槛（B1/B2）。

- **调参常量**：冷却/重试/超时帧数、匹配阈值（C/A），统称 tuning。

- **冷生效**：策略/常量改动需重启模块生效（不热载）。

- **等价回归**：同素材逐帧 `(stage, decision.key)` 与基线 bit 级一致的机械校验。

***

## 附录：调研结论（MAA 演进避坑映射）

| MAA 经验               | 踩过的坑        | 我们的对策                                       |
| -------------------- | ----------- | ------------------------------------------- |
| 字段废弃显式报错+迁移脚本        | 静默忽略导致陈旧配置  | policies 新字段/废弃走 P 系列校验报错                   |
| AI 写 pipeline 需界面上下文 | 幻觉拼凑        | 我们的 AGENTS.md 立规：改 policies 必须以 trace/截图上下文 |
| 复杂状态机维护成本高 → 删功能     | 状态机膨胀       | P1 只等价迁移，不为上纸造状态机                           |
| 策略永远留码（肉鸽/基建）        | 算法 JSON 化灾难 | §2-E 边界，BidStrategy 不上纸                     |
| 可视化编辑器是生态终点          | 工具割裂        | 我们已用 React Flow 走同方向，且叠加 trace 回放优势         |

