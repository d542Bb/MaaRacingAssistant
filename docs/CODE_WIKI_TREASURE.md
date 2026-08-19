# MaaRacingAssistant — Code Wiki · 鉴宝域

> 《巅峰极速》"巅峰鉴宝"活动 —— **出价 / 估值 / OCR 全自动模块（treasure_*）** 专属文档。
> 聚焦鉴宝核心：12 阶段状态机 / 准星意图 / 出价策略（bid_strategy）/ 异步 OCR / ROI 三段分类。
>
> 配套文档：
> - 主文档：[CODE_WIKI.md](CODE_WIKI.md)（架构 / 导航引擎 / 配置 / 调试 / GUI）
> - 赛车域：[CODE_WIKI_RACING.md](CODE_WIKI_RACING.md)

---

## 目录

1. [treasure_module 巅峰鉴宝模块](#1-treasure_module-巅峰鉴宝模块)
2. [bid_strategy 出价策略](#2-bid_strategy-出价策略)
3. [treasure_detector 阶段检测器](#3-treasure_detector-阶段检测器)
4. [treasure_ocr 金额识别](#4-treasure_ocr-金额识别)
5. [treasure_renderer HUD 渲染](#5-treasure_renderer-hud-渲染)
6. [treasure_debug_studio ROI 校准调试台](#6-treasure_debug_studio-roi-校准调试台)
7. [鉴宝类速查](#7-鉴宝类速查)
8. [鉴宝模板清单](#8-鉴宝模板清单)
9. [鉴宝坑点](#9-鉴宝坑点)

---

## 1. treasure_module 巅峰鉴宝模块

[treasure_module.py](file:///d:/maaracing_assistant/maaracing_assistant/modules/treasure_module.py)（v0.13.0 主战场）

**职责**：
- 活动模块实现（`ActivityModule` 子类，`ID="treasure"`），12 阶段状态机
- **准星意图模式**：当前只算「程序想点击的位置」，不执行真实点击
- 鉴宝师选择自动化 / 场次选择自动化（模板匹配 + 静态按钮中心）
- 异步 OCR worker（latest-only 丢帧 + 关键 ROI 优先通道）
- 估值算法：全 5 回合系统报价最大值 `sysmax_13`（H=智能出价填入的输入框值，只取每回合第一次）×1.35(求稳)/1.4(激进) = 真实估值区间

**阶段链路（`STAGE_ORDER`，与 `treasure_detector._ROI_STAGE` 同步）**：
```
游戏大厅 → 活动页面 → 鉴宝大厅(选择场次) → 匹配中 → 选择鉴宝师
→ 第1~5回合出价 → 中标结算 → 领取分红
```

**准星意图链路**：`_match_appraisers`/`_match_selected_check`（匹配）→ `_run_appraiser_choice`/`_run_session_choice`（算意图写 `_appr_last_decision`/`_session_last_decision`）→ `_decide_action`（阶段驱动决策）→ `_resolve_action_target`（补归一化 center）→ `_treasure_kwargs` → `debug.save_frame`（渲染准星）

**鉴宝师选择**（`选择鉴宝师` 阶段）：
- `_match_appraisers`：全屏搜索区 `_APPRAISER_SEARCH_ROI=(0.03,0.18,0.97,0.92)` 内多尺度匹配（0.70~1.30×13 档），顺位 P1 卡洛琳 → P2 章太郎
- `_match_selected_check`：`stage.appraiser_selected_check` 横向长条 rect 扫黄色√，对勾中心 X ≈ 目标卡片命中框右边界（容差 0.09）→ 已选中 → 准星指 `confirm_red_btn`
- 目标均未识别到 → 兜底：对勾命中（已有卡被选中，大概率是刚点的中间卡）→ 准星指 `confirm_red_btn`；否则准星指屏幕中心 (0.5, 0.5)（凑合点中间卡）—— 避免「点中间卡→对勾出现→仍指中间卡」死循环

**场次选择**（`鉴宝大厅(选择场次)` 阶段）：
- `_match_session_panel`：模板匹配「开始匹配」按钮 `session_start_match_btn`（stage 段）判定"详情卡已切到目标场次"
- 命中 → 准星指 `session_start_match_btn`（静态中心）；未命中 → 准星指 GUI 目标场次 badge（`session_intern_badge`/`session_expert_badge`/`session_master_badge`，静态中心）
- 按钮中心来自 `_load_action_centers`（同时扫 JSON 的 stage+actions 两段）

**回合出价**（`第N回合出价` 阶段，`_run_bidding_choice`）：
- 状态机：S0 转场期（`round_elapsed < SWITCH_CONFIRM_FRAMES`）→ 不出准星；S1 等待出价 → 不出准星；S2 出价亮起 → 准星指 `bid_main_red_btn`；S3 面板已开 → H 未读点 `smart_bid_btn`（智能出价）、H 已读进 `_run_bidding_execute`（策略决策 → 输入子状态机 → 确认出价）；提交后 S4 wait_result（等公开报价，OCR 读 4 槽构建快照）
- 面板已开判定：`stage.smart_bid_btn` 模板匹配（`bid_smart_btn.png`，面板内「智能出价」按钮，只有面板打开才出现 = 强信号）
- 主按钮状态（等待出价/出价）走 **OCR 文字**（`ocr.bid_main_btn_label`）——按钮明暗态模板匹配不稳（见 Experience 1112416），用 OCR 文字「等待出价」→「出价」切换判 S1/S2，比模板稳
- `_load_action_centers` 同时扫 stage+actions，`smart_bid_btn`（stage）与 `bid_main_red_btn`（actions）自动进 center 表

**OCR worker（异步，`_ocr_worker_loop`）**：
- 两段式：先识别 `OCR_CRITICAL_KEYS=('bid_result_amount_box',)` 关键 ROI 立即发布（保 H 不丢），再全量 18 ROI
- **投递时机**：出价阶段仅面板已开（S3，识别到智能出价按钮）才投递——H 就是输入框当前值（智能出价填入），面板未开（S1/S2）输入框区域是别的 UI，投递既浪费又误判
- 时效老化：`age = consume_time - captured_ts`，超 `OCR_MAX_AGE_MS=800` 丢弃
- 结果槽 `_ocr_result` 完整 dict 替换，不原地修改

**关键配置**：`FRAME_INTERVAL_MS=300`（主循环 ~3.3Hz）、`OCR_ZERO_ALLOWED_KEYS=('settle_my_income','settle_profit')`（0 值合法）

---

## 2. bid_strategy 出价策略

[bid_strategy.py](file:///d:/maaracing_assistant/maaracing_assistant/modules/bid_strategy.py)（v0.3.5 编码基线，设计文档 `docs/treasure_bid_strategy.md`）

- 数据结构：`RoundSnapshot`（上一轮完整公开快照，策略唯一对手信息源）/ `BidContext`（决策输入）/ `BidDecision`（决策输出）/ `LureState`（逼价基线）
- 纯函数：`trigger_bid(k, opp_max)`（k≈1.0 → `opp_max+TICK`；k>1.0 → `ceil(k×opp_max)`）/ `sanitize_bid`（只验证域，非法返回 None → 降级 decision，绝不 clamp 后保留原 decision）
- `BidStrategy.decide`：R1~R2 observe（出 H）；R3 风格分流（`_pick_lure_target` 找最高且激进者 r>1.5 建基线，无则 normal=V̂÷1.3）；R4 `_try_lure`（退却换目标→跟随+1000，上限 min(余额, 1.3×V̂, 1.1×opp_max-1)）→ 失败转 win（ceil(1.1×opp_max)）→ 再失败 target_second；R5 及附加回合清空 lure → win（opp_max+TICK）→ target_second → observe
- 第二名策略 `_try_target_second`：第二高独立价 `second_unique` + 开区间夹层（+TICK，价差不足 +1）；三对手全并列 → `opp_max-TICK`；夹层不存在 → None（降级 observe）
- **phase 门控**（`_bid_phase`：wait_first/wait_next/bidding/wait_result）：面板「关→开」上升沿只在等待相位有效才建新 bidding epoch，防模板抖动制造假 epoch；提交后 wait_result，OCR 4 槽全读成功才构建快照并放行 wait_next
- **输入子状态机**（`_run_bidding_execute`，画面驱动）：输入框当前值 B（OCR `bid_result_amount_box` 实时读）对比目标价 T——B==T 点 `bid_confirm_red_btn`；B==0 或前缀不匹配点 `bid_numpad_clear`；前缀匹配输下一位 `bid_numpad_{d}`。不依赖「我点过了」内部标记，用户任何遗漏/改价都能自动纠正
- **附加回合**：`_extract_round_from_stage` 正则提取任意「第N回合」，`set_stage` clamp 到 5（附加回合数据统一写进第5回合槽），用原始数字判断回合切换以正确重置转场期
- `_bid_input_latest` 无条件更新：OCR 读到无数字（已清空/占位）→ 0，避免输入子状态机反复点✖死循环

---

## 3. treasure_detector 阶段检测器

[treasure_detector.py](file:///d:/maaracing_assistant/maaracing_assistant/modules/treasure_detector.py)

**职责**：
- 按优先级扫描 `_ROI_STAGE` 映射的 stage ROI（`priority` 决定顺序）
- 同 ROI 多模板聚合匹配（TM_CCOEFF_NORMED，默认阈值 0.75）
- 匹配强度弱告警节流（同 ROI 每 30s 一次）
- 回合识别：roundN_banner 模板 → 文件名解析回合号；横幅未命中时 OCR 兜底读「第N回合」小字

**核心接口**：`detect(frame_rgb) -> (stage, round_no)`

**自定义阈值**：`result_banner=0.900`、`is_matching_btn=0.900`（`treasure_rois.json` stage 段 `threshold` 字段）

---

## 4. treasure_ocr 金额识别

[treasure_ocr.py](file:///d:/maaracing_assistant/maaracing_assistant/modules/treasure_ocr.py)

**职责**：
- RapidOCR（rapidocr_onnxruntime）薄封装，懒加载引擎、失败降级
- `recognize_amounts(frame, min_amounts=...)`：对 ocr 段 ROI 逐区识别 → 金额解析
- 金额提取加固：千分位逗号优先、重复逗号合并、`MIN_AMOUNT=10000` 过滤、7 位噪点前缀处理
- **CPU 亲和性**：`PIN_P_CORE_AFFINITY=[0..7]` 绑定 P-core（本机 Intel Alder Lake 8P+4E，E-core 推理慢 ~2.15 倍，详见 OCR_LATENCY_SPIKE_ANALYSIS.md）
- `USE_CLS=False` 关闭方向分类

---

## 5. treasure_renderer HUD 渲染

[treasure_renderer.py](file:///d:/maaracing_assistant/maaracing_assistant/modules/treasure_renderer.py)

**职责**：复用调试渲染器，绘制鉴宝专属 HUD：
- 阶段/回合号、系统报价 H、估值区间、我方出价、排名
- 5 回合 H 历史折线图、玩家出价表
- **准星渲染**：`treasure_action`（程序想点击的位置，`_resolve_action_target` 输出）画黄色准星 + 目标说明
- 底部 12 阶段进度条、OCR 性能指标（total/failures/dur_ms/age_ms）

---

## 6. treasure_debug_studio ROI 校准调试台

[tools/treasure_debug_studio](file:///d:/maaracing_assistant/tools/treasure_debug_studio)

**职责**：可视化校准 `treasure_rois.json` 的 ROI（Flask 后端 + 静态前端，独立启动）：
- 三段分类 tab：`stage`（模板阶段检测）/ `actions`（纯 rect 按钮）/ `ocr`（识别区）/ `unassigned`（未分配模板）
- ROI 拖拽/缩放/新建/删除、rect 归一化编辑、模板上传/裁剪/多选
- 匹配分数实时预览（TM_CCOEFF_NORMED）+ 跨帧测试（直方图/达标率）
- 显示控制：框显示模式（all/selected/none）+ 命中位置高亮（showHit）
- 截图来源：`debug/treasure/<ts>/raw/`（支持 png/jpg/webp）

**配置**：`assets/resource/image/treasure/treasure_rois.json`，`reference_size=[1280,720]` 归一化坐标，三段结构（stage/actions/ocr）

---

## 7. 鉴宝类速查

### treasure_module.TreasureModule

| 方法 | 说明 |
|------|------|
| `start(start_from)` | 启动：连接窗口 → 装渲染器 → 初始化 OCR/检测器/模板 → 主循环 `_tick_once`（~3.3Hz） |
| `_tick_once()` | 每帧：截图 → 阶段检测同步 → 鉴宝师/场次意图 → OCR 投递 → 变化检测 → save_frame |
| `_match_appraisers(frame)` | 多尺度顺位匹配鉴宝师（P1→P2），返回 `[(prio,key,score,cxn,cyn,rx2)]` |
| `_match_selected_check(frame)` | 对勾扫描区匹配黄色√，返回 `(score,cxn,cyn)` |
| `_run_appraiser_choice(frame)` | 选择鉴宝师阶段：匹配+选中判定 → 写 `_appr_last_decision` 意图 |
| `_match_session_panel(frame)` | 详情卡标题匹配（状态判定用） |
| `_run_session_choice(frame)` | 鉴宝大厅阶段：标题判定 → 静态按钮中心意图写 `_session_last_decision` |
| `_decide_action()` | 阶段驱动决策（返回 `{"key","hint"}`），全部 12 阶段准星覆盖 |
| `_resolve_action_target()` | 决策 → 补归一化 center（动态匹配/静态按钮/兜底中心） |
| `_treasure_kwargs()` | 统一构造 save_frame/DebugState 字段（含 `treasure_action` 准星） |
| `_ocr_push/pop_latest/publish_result` | 异步 OCR worker 投递/取帧/发布（latest-only + 两段式） |
| `set_h / set_our_bid / set_rank` | 状态注入（系统报价/我方出价/排名），H 只取每回合第一次 |

### treasure_detector.TreasureStageDetector

| 方法 | 说明 |
|------|------|
| `detect(frame_rgb)` | 返回 `(stage, round_no)`：按 `_ROI_STAGE` priority 扫描 + 多模板聚合匹配 |
| `_round_from_template(name)` | roundN_banner 文件名 → 回合号 |
| `_round_no_from_text(text)` | OCR 文本提取回合号（1~5 之外视为噪声） |
| `_round_label_rect()` | 回合小字 OCR 区 rect（优先 ocr.round_label_area） |

### treasure_ocr.TreasureOcr

| 方法 | 说明 |
|------|------|
| `recognize_amounts(frame, min_amounts=...)` | ocr 段 ROI 逐区识别 → `{key: amount}` |
| `_extract_amount(...)` | 金额解析（千分位/逗号合并/MIN_AMOUNT 过滤） |
| `_get_engine()` | RapidOCR 懒加载（失败降级，绑定 P-core） |

### treasure_renderer.TreasureDebugRenderer

| 方法 | 说明 |
|------|------|
| `render_full(img_bgr, state)` | 全量 HUD + 准星绘制（save_frame 存盘用） |
| `_draw_appraiser_peep(...)` | 选择鉴宝师阶段准星（目标头像/确认按钮/中心兜底） |

---

## 8. 鉴宝模板清单

配置源 `treasure_rois.json`（三段：stage / actions / ocr），匹配阈值：stage 段默认 0.75，鉴宝师/场次 0.72：

| ROI 键 | 模板文件 | 阶段/用途 | 阈值 |
|--------|----------|----------|------|
| `settle_title` | settle_final_price_title.png | 结算页标题 | 0.75 |
| `result_banner` | result_auction_fail/win_banner.png | 中标结算横幅（自定义 0.90） | **0.90** |
| `smart_bid_btn` | bid_smart_btn.png | 智能出价按钮 | 0.75 |
| `round_big_banner` | round1~5_banner.png | 回合大横幅（文件名解析回合号） | 0.75 |
| `appraiser_title` | select_appraiser_title.png | 选择鉴宝师页标题 | 0.75 |
| `hall_peak_appraise_card` | hall_peak_appraise_card.png | 游戏大厅「巅峰鉴宝」入口卡片 | 0.75 |
| `goto_appraise_btn` | act_goto_appraise_btn.png | 活动页「前往鉴宝」按钮 | 0.75 |
| `hall_session_cards` | hall_session_cards.png | 鉴宝大厅场次卡片区 | 0.75 |
| `is_matching_btn` | is_matching_btn.png | 匹配中按钮（自定义 0.90） | **0.90** |
| `session_start_match_btn` | session_start_match_btn.png | 「开始匹配」按钮（详情卡出现判定，自定义 0.90） | **0.90** |
| `appraiser_selected_check` | appraiser_selected_check.png | 已选中黄色√（对勾判定） | 0.72 |
| —（actions 段） | — | session_master_badge / session_start_match_btn / confirm_red_btn 等纯 rect 中心按钮，**不挂模板** | — |
| —（鉴宝师模板） | appraiser_p1_caroline.png / appraiser_p2_shotaro.png | 选择鉴宝师顺位匹配（全屏多尺度） | 0.72 |

> 注：`hall_session_cards` 曾名 `hall_start_match_btn`；`hall_peak_appraise_card` 曾名 `hall_participation_card`（v0.13.0-dev.3/4 语义化改名）。已删除 `round_label_*.png`（回合小字改 OCR）。

---

## 9. 鉴宝坑点

| 坑点 | 说明 |
|------|------|
| 准星意图模式 | 当前全部逻辑只算「程序想点击的位置」，经 `_decide_action → _resolve_action_target → _treasure_kwargs → debug.save_frame` 渲染 PEEP 准星，**不执行真实点击**（已删除 `_click_norm`） |
| 模板 ROI 分类语义 | `treasure_rois.json` 分三段：**stage = 模板匹配做阶段/状态判定**（如 `session_start_match_btn` 判详情卡出现、`is_matching_btn` 判匹配中）；**actions = 纯 rect 中心点击按钮**（准星直接用中心，不挂模板，如 `session_master_badge`/`session_expert_badge`/`session_intern_badge`/`session_start_match_btn`/`confirm_red_btn`）；**ocr = RapidOCR 识别区**。按钮位置固定就别放 stage 段挂模板 |
| 鉴宝师/场次多尺度匹配 | 0.70~1.30× 共 13 档（步长 0.05），缩小时 `INTER_AREA`/放大 `INTER_CUBIC`；中心/右边界用**缩放后模板尺寸**计算（不是原始尺寸）；阈值 0.72 |
| 「已选中」对勾判定 | `stage.appraiser_selected_check` 是横向长条 rect（覆盖三卡右上角对勾高度带），扫描黄色√；判定对勾中心 X ≈ 目标卡片命中框右边界（容差 0.09） |
| 鉴宝师搜索区 | `_APPRAISER_SEARCH_ROI=(0.03,0.18,0.97,0.92)` 全屏范围（三卡位置/尺寸不固定），顺位 P1 卡洛琳→P2 章太郎，均未命中→准星指屏幕中心 |
| 回合出价状态机 | `_run_bidding_choice`：S0 转场期/S1 等待/S2 点主出价按钮/S3 面板内智能出价→确认出价；「等待/出价」用 OCR 文字判（`ocr.bid_main_btn_label`），面板是否打开用 `stage.smart_bid_btn` 模板判；等待状态 `key=None` → `_resolve_action_target` 返回 None 不出准星 |
| 出价按钮明暗 | 主出价按钮「等待出价/出价」明暗态**不要用模板匹配**（禁用态透明渐变 + 亮度变化 → 置信度跳变，见 Experience 1112416），改用 OCR 文字判状态 |
| `_load_selected_check` 解包 | `_, fname = _SELECTED_CHECK_DEF` 是**二元组**；按三元组解包会报 `not enough values to unpack (expected 3, got 2)` |
| 调试台黑屏 | 截图正则 `_RAW_RE` 必须覆盖 `png|jpg|jpeg|webp`（原始存盘是 JPG，只认 png 会全黑） |
| 调试台框交互 | 框显示开关 `showRois` 需同步 `hitTest()`（none→全部不响应；selected→仅选中项响应），否则隐藏的框仍可被点中/拖动 |
| 回合小字 | 已由模板像素差改为 OCR 识别（`round_label_area` 迁入 ocr 段），`round_label_*.png` 模板已删除 |
| pyright 类型噪音 | `tuple(float(n) for n in list)` 会被推断为 `tuple[float,...]`，赋给 `tuple[float,float,float,float]` 报错 → 用显式 4 元构造 `(float(r[0]), float(r[1]), float(r[2]), float(r[3]))`；多尺度 `best` 元组是 **6 元**（score,scale_idx,x,y,th,tw） |
| 主循环单帧异常兜底 | `start()` 主循环**必须**对 `_tick_once()` 做 per-frame `try/except`，否则单帧未捕获异常会直接杀死整个主循环/模块线程（2026-08-19 核实原实现是 `try/finally` 无 `except`）。正确做法：单帧异常跳过继续并 `WARNING`，仅当连续 `_MAIN_CRASH_RETRY_MAX=30` 帧（≈9s）仍异常才上抛走 `finally` 清理终止，防"静默空转"掩盖真 bug |

## 10. 遗留问题清单（v1.0 发布前核查）

| 项 | 类别 | 结论 / 处理 |
|----|------|------------|
| 主循环无 per-frame 异常兜底 | 稳定性 bug | ✅ 已修复：`start()` 对 `_tick_once` 包 try/except，单帧异常跳过，连续 30 帧才终止（见坑点表）。`py_compile` 通过 |
| 阶段切换类点击卡死 | 稳定性 | ✅ 已核实无需改：`_maybe_retry_stage_click` 用 `CLICK_RETRY_MAX=3` 封顶，达上限 WARNING 停止，换 key/切阶段归零，有界无死循环 |
| 结算后弹窗连点/跳过 | 稳定性 | ✅ 已修复（既有）：`POPUP_CLICK_COOLDOWN_FRAMES=5` 冷却 + `POPUP_LOOPBACK_STABLE_FRAMES=3` 连续稳定帧确认，详见坑点表弹窗链相关条目 |
