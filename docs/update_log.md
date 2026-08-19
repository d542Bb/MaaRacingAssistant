# MaaRacingAssistant 修改日志

> 按时间顺序记录每次重大修改。

## 2026-08-20

### v0.15.0-dev.6 README 发布物料就绪 + 开发文档防上传 📦
- **版本号：** `v0.15.0-dev.6`（预发布；0.x 系列 tag 全部为 pre-release）
- **发布物料就绪（面向 v1.0.0 鉴宝单模块主打）：**
  - README 演示小节：引用 `assets/demo/mra_preview.mp4`（`<video>` 播放控件），去掉"素材录制中"占位
  - README 开发状态明确：巅峰鉴宝 = v1.0.0 主打（已闭环 + CI 单测回归）；极速狂飙 = 开发中，不纳入 v1.0.0
  - `assets/demo/README.md` 同步为实际素材清单（mp4 + 3 张截图已实拍入库）
  - `docs/PRESENTATION.md` 删除内部「发布衔接」节，头部改社区向表述
- **开发文档防上传：** `.trae/` 下 13 个历史遗留已跟踪文件（4 组 spec + 1 个 skill）`git rm --cached` 移出索引（本地保留），配合 `.gitignore` 的 `.trae/` 规则确保不再上传

---

## 2026-08-19

### v0.15.0-dev.5 内置 vgamepad wheel + 网络连通性自检（根治 CI sdist 卡死） 🐍
- **版本号：** `v0.15.0-dev.5`（预发布；0.x 系列 tag 全部为 pre-release）
- **确诊并根治 vgamepad 卡死：** `vgamepad=0.1.0` 的 sdist 在 CI windows runner 持续卡在 `Preparing metadata (pyproject.toml)`，两次复现（`dev.4` 原 job + 重跑 job），确认与网络无关
  - 方案：本地生成 `vgamepad-0.1.0-py3-none-any.whl` 入库 `scripts/release/wheels/`（纯 Python wheel，极小）；`assemble.ps1` 的 pip 加 `--find-links` 优先用本地 wheel，CI 不再触发 sdist 构建
- **`release.yml` 新增网络连通性自检：** 组装前对 PyPI / files.pythonhosted / 清华 / 阿里多源做 `HEAD` 连通测试，提前暴露网络问题（不阻断，仅诊断）
- **`assemble.ps1` 下载超时：** embedded 下载 `-TimeoutSec 120`、pip 加 `--timeout 60 --retries 2`、去除 `-q`（逐包输出定位）

---

## 2026-08-19

### v0.15.0-dev.4 CI 构建去静默 + pip 缓存兜底（定位卡点） 🔍
- **版本号：** `v0.15.0-dev.4`（预发布，针对 CI 组装仍静默无输出的定位优化；0.x 系列 tag 全部为 pre-release）
- `assemble.ps1` 的 `pip install` 去掉 `-q`，改用 `--progress-bar off`——逐包实时输出，能直接看到卡在哪个依赖；保留 `--timeout 60 --retries 2` 防无限挂
- `release.yml` 的 `setup-python` 加 `cache: 'pip'`，缓存 pip 下载缓存，作为 `build/runtime-cache` 之外的第二层兜底（首次即便失败，已下载的 wheel 也不会重下）

---

## 2026-08-19

### v0.15.0-dev.3 CI 构建缓存 + 下载超时（修复卡死） 🕐
- **版本号：** `v0.15.0-dev.3`（预发布，修复 v0.15.0-dev.2 首次全量可卡死后重发；0.x 系列 tag 全部为 pre-release）
- **修复 win-build 首次全量构建可无限挂起：**
  - `assemble.ps1` 给 embedded Python 下载加 `-TimeoutSec 120`，`pip install` 加 `--timeout 60 --retries 2`——首次在 CI 全新 runner 全量下载大依赖（torch 等）时，网络卡死不再无限挂起，超时即报错退出
- **CI 增加 runtime 缓存：** `release.yml` 以 `requirements-runtime-lock.txt` hash 缓存 `build/runtime-cache`，避免每次重新下载几百 MB runtime 依赖（与 assemble 本地缓存机制自然衔接）

---

## 2026-08-19

### v0.15.0-dev.2 构建失败修复 + 本地打包缓存提速 🔧
- **版本号：** `v0.15.0-dev.2`（预发布，修复 v0.15.0-dev.1 构建失败后重发；0.x 系列 tag 全部为 pre-release）
- **修复 CI「构建解压即用 Windows 包」失败：**
  - `scripts/release/assemble.ps1` 修复 `-OutRoot` 指向的 `build/release` 目录不存在时 `Resolve-Path` 抛 `Cannot find path ... because it does not exist` 报错 —— 先在 Resolve 前 `New-Item -Force` 创建输出目录
  - 修复 WinUI3 GUI 编译错误：禁用右键菜单改用 WebView2 正确初始化事件 `CoreWebView2Initialized`（原误用 WinForms/WPF 的 `CoreWebView2InitializationCompleted`，导致 `CS1061` + XAML 编译器连锁 `WMC9999`；该错误此前被 OutRoot 提前失败掩盖，未在 CI 暴露）
- **本地构建提速（复用缓存，仅本机）：**
  - runtime 缓存 `build/runtime-cache`：embedded Python + pip 依赖复用，跳过重复下载/安装
  - GUI 编译缓存 `build/publish-cache` + `-SkipPublish`：未改 C# 时复用已编译产物，跳过 dotnet publish
  - 两套缓存均以源指纹（源码 / lock hash）自动校验，跨分支或改代码后自动失效重建，杜绝误用旧产物
  - `.gitignore` 忽略本地打包产物（`scripts/release/MaaRacingAssistant-*`）与缓存目录（`build/runtime-cache`、`build/publish-cache`）

---

## 2026-08-19

### v0.15.0-dev.1 净机适配 + GUI 打磨 + 解压即用发布流水线 📦
- **版本号：** `v0.15.0-dev.1`（预发布，0.x 系列 tag 全部为 pre-release）
- **净机适配（新机开箱即用）：**
  - vgamepad 懒加载（`maaracing_assistant/vgamepad_lazy.py`）：无 ViGEmBus 驱动时不再 import 阶段崩溃，controller 暴露 `gamepad_available()` 供 GUI 判断
  - GUI 缺少 ViGEmBus 驱动时弹引导框（含下载入口，sidecar 新增 `open_vigembus_download`）
  - 发布包自带 embedded Python3.11 runtime + 预装依赖，解压即用
  - 依赖收敛：运行时去掉 `ultralytics`（推理改走 onnxruntime-directml），换入 `windows-capture`；训练依赖拆到可选 `[train]` extra，避免把 torch 拽进发布包
- **解压即用发布流水线：** CI 新增 `win-build` job —— dotnet publish GUI + `scripts/release/assemble.ps1` 组装 `MaaRacingAssistant-<ver>-win-x64.zip`(runtime+publish+白名单+自检) + sha256，并补传至同一 Release
- **GUI 重构与打磨：**
  - tab 切换新增左右滑动动画（仅相邻 tab 播放）+ 底部滑块（宽度 85%、水平居中、点击当前 tab 不重播）
  - 主控 tab 左右栏套用数据 tab 的列变换逻辑；运行日志卡高度撑满窗口（四周留白）
  - 数据 tab 实时预览放大重做：16:9 等比 + 四周留白 + 居中 + 随窗自动缩放 + 隐藏背景卡片；放大态只留「还原」按钮
  - 关于 tab：文案对齐现状、logo 换为 `assets/icon.ico`（去除红色底框）、底部「项目主页 / 报告问题 / 使用文档」三按钮（sidecar `open_external_url` 走默认浏览器）
  - GUI 全局禁用右键默认菜单（WebView2 `AreDefaultContextMenusEnabled=false`）
- **仓库与规范：** 移除 `tools/analysis/` 下一次性实验脚本（OCR/回放/性能分析等，3149 行）；新增 `LICENSE`、`THIRD_PARTY_LICENSES.md`；`tools/training/train.py` 对应训练 extra 调整

---

## 2026-08-19

### v0.14.0-dev.3 鉴宝策略 V3 意愿缓冲 + 彩蛋竞态修复 + GUI 今日看板与内嵌预览 🎲
- **版本号：** `v0.14.0-dev.3`（预发布，基于 v0.14.0-dev.2；0.x 系列 tag 全部为 pre-release）
- **鉴宝出价策略 V3（对手加价意愿 + 兜底修复）：**
  - 对手最高价史（`opp_high_history`，逐回合、排除我方槽位）→ 加价意愿系数（涨幅 ≤0 → ×0.3 / <30% → ×0.6 / ≥30% → ×1.0）动态收缩预测缓冲，防止过度抬价
  - 修复全局兜底上限 bug：`max(risk_cap, V̂×0.15)` → `V̂+risk_cap`（原实现把第二层卡钳死在 risk_cap，对手价一高区间即走空弃权，见 log 20260818_000240 R3）
  - PASS 弃权改为「嘲讽出价 250」：不再静默死等，走输入确认链路，既送出报价不浪费回合又维持严格兜底
  - 玩家掉线报价 0 也正常落盘（-1 哨兵区分未读），修复 4 槽快照凑不齐导致整场锁死
  - debug 图 HUD 显示与决策同口径的策略估值 V̂（VAL_COEF×sysmax），图和决策不再脱节
- **彩蛋识别竞态修复：**
  - 彩蛋蛋卡逐帧飞入动画、完整帧仅 1 帧 → 原「首个非空结果即读完」会被不完整帧锁死、漏记黄蛋
  - 改为 `_egg_reading` 门控（进入弹窗持续投递识别，不依赖单帧 title 命中）+ 历史最优累积（更多蛋覆盖更少蛋，绝不降级）+ 连续稳定确认才判定读完；超时兜底落盘用已累积最优值
- **games 表策略模式落盘：** 新增 `strategy_mode` 列（profit=赚钱 / egg=赚蛋，以策略实例实际 mode 为准），旧库自动迁移
- **GUI 重构（用户端体验）：**
  - 主控 tab「断点选择」改名「当前阶段」，去掉双击跳转，▶ 指示器由 selectStage 统一管理随当前阶段移动（修复不跟随 bug）
  - 日志 MAA 风格区块化：锚点分段（进入阶段/会话/场次）+ 折叠聚合 + 级别色点，次要细节折叠、含错误自动展开并加红/黄告警徽章
  - 数据页新增「今日看板」card：读 treasure.db 今日统计（凌晨 5 点日界），场次/胜/负/我方利润/收入/最高单场 + 今日蛋总数（红黄蓝分色），3s 轮询
  - PEEP 实时预览内嵌 16:9：debug 改 headless（不再独立 OpenCV 弹窗），sidecar 新增 `get_peep_frame`（JPEG base64），数据页内嵌轮询 ~10fps；右栏固定、左栏自由变换
  - GUI 进入默认选中鉴宝模块
- **文档与社区规范：**
  - CODE_WIKI 按功能域拆分为主文档 + CODE_WIKI_RACING（赛车域）+ CODE_WIKI_TREASURE（鉴宝域）
  - 新增 CODE_OF_CONDUCT.md、CONTRIBUTING.md、Issue 模板（bug/feature）、PR 模板
  - README / pyproject 定位更新：巅峰鉴宝为主打模块（全链路自动化闭环），极速狂飙为开发中状态

---

## 2026-08-16

### v0.14.0-dev.2 鉴宝出价策略 V2 + 结算彩蛋识别 + GUI 重构 🎲
- **版本号：** `v0.14.0-dev.2`（预发布，基于 v0.14.0-dev.1；0.x 系列 tag 全部为 pre-release）
- **鉴宝出价策略 V2（核心）：**
  - 双层动态缓冲：基础缓冲（价格分桶）× 利润强度缩放（clamp(强度/15%, 0.5, 1.5)），替代固定 +1000 步长；兜底上限 risk_cap（GUI 可调，默认 5 万）防意外接盘
  - 决策分流：对手烧钱（对手最高出价 > 预估实价）→ 卡第二吃 15% 分红；冷静 → 贴底捡漏；估值系数校准 1.38 → 1.28（逐场实测 median=1.265）
  - 策略双模式：赚钱（吃分红/捡漏）/ 赚蛋（搏拍中彩蛋，买入上限 = 估值 + 兜底），GUI 小字提示含赚蛋免责声明
  - 余额三态：OCR 未读到（哨兵 -1，视为充足）/ 真实 0（pass）/ 正常；钱不够不卡死、自动钳制单局可承受亏损
  - PASS 死循环防护：决策 pass 时不编辑输入框、不点确认（DECISION_PASS 分支）；单测重写覆盖观察/捡漏/卡第二/pass/模式切换/余额三态
- **结算与彩蛋识别：**
  - 新增「结算弹窗」阶段：今日最高积分上涨 / 奖励结算彩蛋合并识别（daily_high_banner / egg_reward_title），等级提升无 ROI 盲点跳过
  - 新增 treasure_eggs.py 彩蛋结算识别 + egg.png / egg_reward_title / daily_high_banner 等资源
  - banner_result()：中标结算阶段判定「中标/未中标」写入落盘；结构化落盘 data/treasure/treasure.db（games + daily_summary）
  - 调试渲染按 OCR「（我）」槽位高亮我方出价，不再硬编码「玩家3」；场次选择判定改「开始匹配」按钮模板（支持实习/专家/大师场 badge 动态定位）
- **GUI 重构：**
  - 顶部导航「控制面板/调试/关于」→「主控/数据/设置/关于」；调试页拆分为「数据」（性能监控/当前检测/实时预览）与「设置」（调试选项/截图方式/快捷工具）
  - 数据/设置页卡片按模块渲染：MODULE_PAGE_DEFS 注册表，treasure/racing 各一套独立 id 前缀 DOM，切换模块自动刷新，便于后续模块差异化
  - 鉴宝配置项：策略模式下拉（赚钱/赚蛋）+ 兜底上限输入框 + 策略小字提示动态切换
  - 紧急停止未运行不报错（避免满屏 ERROR）；运行中仅锁定可选项
- **文档与仓库净化：** CODE_WIKI 同步场次选择/结算弹窗机制并吸收策略分析结论（多轮临时分析报告不入库、结论沉淀进 CODE_WIKI）；`.claude/`、`docs/trae-dsh-config-share.md` 忽略、`.trae/` 已跟踪文件移出索引（harness 个性化配置与记忆不入库）

---

## 2026-08-15

### v0.14.0-dev.1 模块化架构改造（能力接口 + 资源所有权）🏗️
- **版本号：** `v0.14.0-dev.1`（预发布，基于 v0.13.0；0.x 系列 tag 全部为 pre-release）
- **模块化架构基线（借鉴 DSH/Cordis 思想，克制落地）：**
  - 引入 typed capability（capture / gamepad / lifecycle / debug_renderer），模块经 `ActivityContext` 窄接口接触宿主，斩断对 controller 私有接口的反向引用
  - `GamepadCapability` 租约语义：`acquire()` 归零归还 + `reset_device()` 断开重建，业务层不再手动 destroy（每个手柄实例任一时刻唯一 owner）
  - `ActivityContext` 引入 `ExitStack` 接管 renderer 生命周期，删除 token 机制，模块退出（含异常）自动释放不泄漏
  - `REQUIRES` 能力声明 + 启动前 fail-fast 校验（`ModuleDependencyError`）
  - `ctx.bind_tasker` 收口 MAA 集成，删除公开 `controller` 暴露（`ModuleIntegrationError`）
- **README 重写：** 定位改为「模块化游戏自动化平台」（目标扩展到全部重复劳作活动），精简结构 + 完善合规声明（严禁代练 / 外挂 / 影响服务器排名）
- **其他说明：** 本次为内部架构重构，无新增用户玩法功能，未充分验证，发 dev 版

---

## 2026-08-15

### v0.13.0 巅峰鉴宝每日循环上限 + 出价策略配置（正式版）🏷️
- **版本号：** `v0.13.0`（正式版，基于 v0.13.0-dev.5；0.x 系列首个正式版 tag）
- **每日循环上限「刷到第几场」：**
  - 控制面板活动卡片新增「刷到第几场」输入框（0-50，0=不指定），每日以凌晨 5 点为日界
  - 识别场次选择页「日已参与 X/50 场」计数：鉴宝大厅阶段同步单 ROI 识别 + 状态机 done_count 双保险，单调更新 + 交叉追平
  - 达到上限后拦截「开始匹配」，自动停止本日循环
- **出价策略 GUI 下拉：** 单一策略「最大利润（刷单日计分）」，`BidStrategy` 回退单一逻辑，后续策略按需求扩展
- **运行中配置锁定：** 模块运行期间 GUI 可选项调灰禁改，sidecar 仅写缓存不热更新
- **鉴宝师偏好 JSON 化：** appraiser_p1/p2（偏好卡洛琳/章太郎）配置迁入 treasure_rois.json（prio/rect/templates/threshold），调试台新增「偏好鉴宝师」分类（`_` 前缀元数据不参与匹配校验）
- **多循环健壮性修复：**
  - 新场次残留数据污染 → 回合状态重置 `_reset_round_state`（H 价/玩家出价/竞拍状态机/策略基线）
  - 页面切换 / 面板打开不重试 → 阶段点击超时重试（CLICK_RETRY）
  - 选择鉴宝师转场误兜底 → 5 帧转场缓冲（APPRAISER_SETTLE_FRAMES）
  - 鉴宝大厅每日计数 OCR 不投递（异步被阶段门控丢弃）→ 同步单 ROI 识别修复
- **其他：** 场次计数 ROI 框经调试台校准，OCR 输出正常（如「24/50场」→ 24）

---

## 2026-08-14

### v0.13.0-dev.5 鉴宝全链路自动化准星 + 软件健壮性增强 🎯
- **版本号：** `v0.13.0-dev.5`（预发布，基于 v0.13.0-dev.4；0.x 系列 tag 全部为 pre-release）
- **鉴宝师选择自动化：** 进入「选择鉴宝师」阶段延迟 3 帧识别，全屏多尺度匹配（0.70~1.30×13 档）按顺位抉择 P1 卡洛琳 → P2 章太郎，均未识别到 → 准星指屏幕中心
  - 「已选中」对勾判定：`stage.appraiser_selected_check` 横向长条 rect 扫描黄色 √，对勾中心 X ≈ 卡片右边界即判定已选中 → 准星指确认按钮
- **全链路准星意图（不真实点击）：** 游戏大厅 → 活动页 → 鉴宝大厅(选择场次) → 选择鉴宝师 → 回合出价 → 领取分红 各阶段经 `_decide_action → _resolve_action_target` 渲染 PEEP 准星，显示程序「想点击的位置」
- **场次选择自动化：** 详情卡标题（session_master_panel_title）模板判定 → 命中点「开始匹配」/ 未命中点「鉴宝大师场」标签；`session_master_badge`、`session_start_match_btn` 迁入 actions 段（静态 rect 中心）
- **调试台（treasure_debug_studio）增强：**
  - 修复黑屏：截图正则放宽支持 jpg/jpeg/webp
  - 匹配命中显示：黄色高亮框 + 中心十字 + 分数（showHit 开关）
  - 框显示开关：all / selected / none；隐藏的框不再响应点击/拖动（hitTest 过滤）
- **软件健壮性：**
  - 单实例互斥：多开弹窗询问「启动新进程（关闭旧进程）/ 取消保留旧进程」
  - 窗口以最小安全尺寸启动（1000×700 DIP）
  - 标题栏交互区挖孔：双击 tab/品牌区不再触发最大化（drag region 动态排除）
- **版本号双轨机制：** 打包产物读 setuptools-scm 构建快照（旧版本不会被新 tag 带歪）；源码运行按当前 checkout 动态 git describe 推导；sidecar 改用包级 `__version__`
- **回合小字 OCR 化：** 删除 `round_label_*.png` 模板，`round_label_area` 迁入 ocr 段
- **编译级清理：** pyright 核心包 48→0、调试台/诊断工具清零（best 元组标注、Optional 断言、类型收窄等）

---

## 2026-08-13

### v0.13.0-dev.4 项目文件结构与命名规范化 🧹
- **版本号：** `v0.13.0-dev.4`（预发布，基于 v0.13.0-dev.3；0.x 系列 tag 全部为 pre-release）
- **文件结构与命名规范化：** 按用途对工具脚本分类，采用 ASCII snake_case 命名，消除历史命名不规范
  - `tools/` 按 `training/`、`analysis/`、`debug/` 分组，清理混杂脚本（如 `analyze_record.py` → `analyze_records_input.py` 等）
  - `AGENTS.md` 统一大写（git mv 经临时名规避大小写不敏感），保证 trae 自动接入上下文
  - `mra_shell` 由 `prototypes/` 迁移至 `apps/`，同步更新 `.sln`、`start.bat`、`MainWindow.xaml.cs` 前端路径
  - 修复 `apps/mra_shell/NuGet.Config` 失效的本地源引用
- **文档同步：** `README.md`、`docs/CODE_WIKI.md` 对齐项目结构；新增 `docs/structure_plan.md` 记录规范化方案
- **project-update skill 优化：** 0.x 阶段版本号规则优化与 release 版本校验修复

---

## 2026-08-13

### v0.13.0-dev.3 巅峰鉴宝全链路阶段检测 + RapidOCR 金额识别 🏷️
- **版本号：** `v0.13.0-dev.3`（预发布，基于 v0.13.0-dev.2；0.x 系列 tag 全部为 pre-release）
- **鉴宝全链路阶段检测（新增游戏大厅→活动页面断点）：**
  - 阶段链路完整化：游戏大厅(participation_card) → 活动页面(goto_appraise_btn) → 鉴宝大厅(hall_session_cards) → 选择鉴宝师 → 回合出价 → 中标结算 → 领取分红
  - `treasure_detector._ROI_STAGE` 新增 3 个前置阶段映射，`STAGE_ORDER` 同步扩充
  - 删除废弃的 `hall_car_show_card` 模板与引用
- **赢局结算横幅阈值放宽：** `result_auction_win_banner` 单独阈值降至 0.60（横幅带彩条特效，匹配分偏低），避免漏检
- **调试台（treasure_debug_studio）分类动态化：**
  - 分类 tab 由硬编码数组改为从 JSON 动态生成（CAT_KEYS 白名单：stage/round_labels/actions/ocr）
  - OCR 分类区域隐藏模板控件，仅保留矩形编辑
- **OCR 识别区扩充（treasure_rois.json）：**
  - 新增 `bid_player1~4`（本轮各玩家出价）、`player_name1~4`（玩家名，含「（我）」标记→名次）
  - `bid_result_amount_box`（弹窗中心金额）→ 注入 `set_h` 系统报价
- **OCR 金额提取加固（treasure_ocr.py）：** 千分位逗号格式优先、重复逗号合并、MIN_AMOUNT=10000 过滤、7 位噪点前缀处理
- **系统报价→估值链路：** 前 3 回合系统报价最大值 `sysmax_13` ×1.35/1.4 = 真实估值区间，HUD 新增「系统报价 / 估值区间」行
- **OCR 重构（RapidOCR）：** `tools/_analyze_treasure_game.py::DigitOCR` 由模板匹配整体 fallback 为 RapidOCR 薄封装，`read_number` 接口不变；删除 38 个模板 OCR 临时脚本与 6 个模板目录；requirements 新增 `rapidocr_onnxruntime>=1.4.4`
- **启动入口统一：** 删除 `run.py`，改 `python -m maaracing_assistant`；新增 `start.bat` 相对定位 `mra_shell.exe`；README / CODE_WIKI 同步

---

## 2026-08-12

### v0.13.0-dev.2 GUI 迁移：WinUI 3 shell + JSONL sidecar + HTML 三 Tab 🖥️
- **版本号：** `v0.13.0-dev.2`（预发布，基于 v0.13.0-dev.1）
- **GUI 宿主定案 WinUI 3：** Tauri v2 / WPF WindowChrome 实测判负（WebView2 airspace 遮挡、Windows 无 Overlay 实现），WinUI 3 `AppWindowTitleBar` 全能力实测通过
- **进程模型：** `mra_shell.exe`（唯一 GUI，只做窗口 + sidecar 生命周期 + 消息转发）+ `sidecar.py`（JSONL RPC 业务后端，stdin=request / stdout=response / stderr=日志）
- **契约测试 11/11：** PythonSidecar 进程生命周期（并发按 id 匹配 / 超时 / crash 全 disconnected / grace shutdown / 不孤儿）
- **HTML 三 Tab 前端：** 控制面板（模块/断点/日志）、调试（PEEP/性能监控/截图方式）、关于，纯 CSS 无 CDN（WebView2 离线可用）
- **窗口细节：** 自定义标题栏 52px（描边不被系统按钮遮挡）、最小尺寸 1000×700（WM_GETMINMAXINFO）、系统按钮失焦配色、icon.ico 应用图标
- **乱码修复：** UAC 提权后 `PYTHONUTF8` 环境变量不继承 → shell 侧强制写入，Python stdout 恒为 UTF-8
- **关闭卡死修复：** `OnClosed` 同步 await UI SynchronizationContext 死锁 → `Task.Run` 隔离
- **清理：** 旧 ttkbootstrap GUI（`gui/`、`gui_webview/`）归档至 `archive/`；spike 目录全删只留 mra_shell；requirements/pyproject 移除 ttkbootstrap；入口改走 sidecar

---

## 2026-08-11

### v0.13.0 活动模块化架构 + 巅峰鉴宝模块 ⚙️
- **版本号：** `v0.13.0`（次版本+1，基于 v0.12.0）
- **模块化框架：** 引入 `ActivityModule` 抽象基类和 `ActivityContext` 共享资源封装，定义统一模块接口（start/stop/cleanup/current_stage）
- **Module Registry：** 集中管理模块元数据与实例创建，支持 GUI 动态切换活动模块
- **RacingModule 提取：** 将"极速狂飙"完整流程从原 controller.py 提取为独立模块，模块内持有自有 MAA Resource/Tasker
- **TreasureModule 桩：** 新增"巅峰鉴宝"活动模块基础框架，支持后续扩展
- **DebugManager 重构：** 引入 `DebugRenderer` 协议，支持模块注入自定义渲染器，renderer 生命周期绑定 token 避免竞态
- **GUI 升级：** 模块选择下拉框，动态断点列表跟随模块切换
- **记录模式删除：** 彻底移除 Record Mode 相关代码（已无用）
- **WGC 截图裁剪：** 底部锚定 16:9 裁剪，避免状态栏干扰

---

## 2026-08-10

### v0.11.1 AIM 死区修正 + 延迟基准抗离群 + 快速截图兜底 🔧
- **版本号：** `v0.11.1`（SemVer 修订号+1，纯 bugfix + 性能优化，基于 v0.11.0）
- **AIM off_center 死区顺序修复：** `_aim_at` 先判死区再判保底的顺序问题，off_center=True（目标偏离中心车道）时 `effective_stop` 从膨胀的 `0.01 + area_ratio × 30` 收缩为 `0.01`；远/中/近区保底力度 15%/25%/40%（远区也加保底），解决帧 239-253"偏航明显仍直行"
- **快速截图（`_cap_fast`）三重兜底：** 句柄获取按 `ctrl.hWnd → ctrl.hwnd → find_game_hwnd()` 三级降级；每步 GDI 调用（GetClientRect/GetDC/CreateCompatibleDC/CreateCompatibleBitmap/GetBitmapBits）加返回值检查；失败日志从 DEBUG 升级为 WARNING 并指明失效环节（之前静默失败看不到）
- **延迟基准抗离群调优：** `_benchmark_latency` 自动调优从"原始 P95"改为"剔除 YOLO 帧中最慢 1 帧后取 P90"，加 1.8× 离群比告警（`P95/P90>1.8` 输出 ⚠），避免一次 Windows 线程调度抖动（如 YOLO 推理 115ms/P95）把帧率从 30FPS 卡死到 15FPS 下限
- **基准测试分帧统计：** 奇偶分离 YOLO 帧 / 非 YOLO 帧，分别报告 P50/P90/P95（10帧样本），分离截图/YOLO/标线/决策单项耗时，快速定位瓶颈
- **主循环全动态化：** `YOLO_INTERVAL = round(fps / 10)`（≈10 Hz YOLO），`SLOW_CHECK = fps`（≈1 Hz 结束检测），`sleep = 1.0/fps - elapsed` 精准节奏；替换原硬编码 `sleep(1/15)` + 固定 `YOLO_INTERVAL=2`

---

## 2026-07-24

### v0.11.0 贪婪决策 + 前馈瞄准 + 记录模式 🎯
- **版本号：** `v0.11.0`
- **贪婪决策优先级：** 金币+奖励车优先（面积优先，面积近时选离中线近的）→ C区防撞 → 障碍车避让 → 无目标，撞车无惩罚所以防撞降级
- **前馈瞄准（`_aim_at`）：** 根据目标大小/深度预测提前停止，动态 stop_zone = 0.01 + min(0.10, area_ratio × 30)，减少转向过度
- **记录模式（Record Mode）：** GUI 勾选后读取物理 XInput 手柄输入，CSV 记录帧号/时间/摇杆/目标/决策数据，用于分析人工操作规律
- **车道保持优化：** `_calc_drift` 工具函数复用漂移计算（d/dd/cum3），变化率检测 `abs(d) < 5px` 提前停止修正
- **删除变道后激活车道保持：** 移除 `force_init` 和 `_prev_reason` 逻辑，只在无目标时激活车道保持
- **防碰撞优先级调整：** C区防撞从第1位降到第2位，金币组从第2位升到第1位
- **Debug 前馈信息：** 右上角显示 offset/stop_zone/dx/移动方向/in_center/停止原因

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
