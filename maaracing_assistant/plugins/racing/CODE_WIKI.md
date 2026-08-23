# MaaRacingAssistant — Code Wiki · 赛车域

> 《巅峰极速》"极速狂飙"活动 —— **自动驾驶循环（RacingLoop）** 与 **赛车活动模块（RacingModule）** 专属文档。
> 聚焦赛车控制核心：决策算法 / 前馈瞄准 / 防碰撞 / 车道保持 / 性能调优 / 活动流程。
>
> 配套文档：
> - 主文档：[docs/CODE_WIKI.md](../../docs/CODE_WIKI.md)（架构 / 导航引擎 / 配置 / 调试 / GUI）
> - 鉴宝域：[../treasure/CODE_WIKI.md](../treasure/CODE_WIKI.md)

---

## 目录

1. [RacingLoop 赛车自动驾驶循环](#1-racingloop-赛车自动驾驶循环)
2. [RacingModule 赛车活动模块](#2-racingmodule-赛车活动模块)
3. [RacingDebugRenderer 调试渲染器](#3-racingdebugrenderer-调试渲染器)
4. [决策算法详解](#4-决策算法详解)
5. [赛车控制参数](#5-赛车控制参数)
6. [RacingLoop._run_impl 核心循环](#6-racingloop_run_impl-核心循环)
7. [赛车控制坑点](#7-赛车控制坑点)

---

## 1. RacingLoop 赛车自动驾驶循环

[racing_loop.py](file:///d:/maaracing_assistant/maaracing_assistant/plugins/racing/loop.py)

**职责**：
- WGC 持久化后台截图（wgcap.py 零拷贝帧访问，~0.5ms/帧）
- YOLO 跳帧推理（每2帧推理1次，中间帧复用缓存）
- HSV 黄色标线检测（HoughLinesP，单边选择）
- 三区防碰撞体系（A区安全/B区警戒/C区强制）
- C区反打修正（2帧突发+5帧归中滑行）
- 前馈瞄准（动态stop_zone+目标移动预测+预见性提前收敛）
- moving_to_center 单一衰减（近区回摆×mv 合并禁止双重相乘）
- 转向输出平滑（低通滤波α=0.80 + 转向率限制22000/帧）
- 障碍车框重叠避让（中心区L2c~R2c才躲）
- 闭环车道保持（漂移趋势自适应力度，按侧分支方向）
- 动态地平线推断（从远处小车群y值锁死）
- 透视梯形车道分界线计算
- 结束画面检测（商店弹窗/回合1结束模板）
- 记录模式（读取物理手柄，CSV存盘供分析）
- 截图后端可配置：wgc_latest（默认） / maa_fallback

**核心类**：`RacingLoop(CustomAction)`

继承 MAA `CustomAction`，支持两种运行方式：
- `run(context, argv)`: MAA Pipeline 调用入口
- `run_direct(ctrl)`: 绕过 Pipeline 直接运行（实际使用）

**路面 ROI**：`(0, 201, 1280, 561)` — 裁剪顶部分数条和底部仪表盘

**跳帧策略**：
- YOLO_INTERVAL = 2：每2帧推理1次
- SLOW_CHECK = 15：每秒（~15fps）检查一次结束画面
- 中间帧复用上一帧检测结果

**关键方法速查**（完整决策逻辑见 §4）：

| 方法/属性 | 说明 |
|-----------|------|
| `__init__(model_path, debug, record_mode)` | 初始化YOLO检测器、加载结束模板 |
| `run(context, argv)` | MAA CustomAction入口 |
| `run_direct(ctrl)` | 直接运行（绕过MAA Pipeline） |
| `stop()` | 停止运行、销毁手柄、重置所有状态 |
| `_run_impl(ctrl)` | 赛车控制核心循环（见 §6） |
| `_create_pad()` | 创建新手柄+3次归零握手清残留 |
| `_destroy_pad()` | 销毁手柄 |
| `_steer(direction)` | 设置左摇杆方向（-1/0/1=全量，±2000~32767=比例） |
| `_apply_trigger(value)` | 设置右扳机油门（0-255） |
| `_cap(ctrl)` | 截图（MAA PostScreencap，BGR→RGB） |
| `_detect_lane(img)` | HoughLinesP检测黄色标线，单边选择返回{side, pos} |
| `_detect_horizon(all_raw, h, w)` | 从低置信度远处小车推断地平线，首次锁死 |
| `_lane_boundaries_at_y(y, h, w)` | 透视投影计算y深度处的6条车道分界线 |
| `_is_end(img)` | 模板匹配检测本轮是否结束 |
| `_wall_avoidance(lane, w)` | 三区防碰撞：返回(zone, dir) |
| `_lane_keep(lane)` | 闭环车道保持，返回比例摇杆值 |
| `_aim_at(target, w, h, lane)` | 前馈瞄准，返回转向值（三区变力度） |
| `_avoid(cars, w, h)` | 障碍车避让（框重叠才躲） |
| `_decide(coins, cars, bonus, lane, w, h, ...)` | 全局贪婪决策（4级优先级） |
| `_calc_drift(hist)` | 从位置历史计算d/dd/cum3（漂移/加速度/3帧累计） |
| `_get_zone(cy, bh)` | 根据y位置判断远/中/近区（0/1/2） |
| `_zone_boundaries` | 属性：返回(horizon, far_bot, mid_bot, roi_bot) |
| `_end_reason` | 属性：最近一次结束原因（"商店弹窗"/"回合1结束"） |
| `ROI` | 类属性：路面裁剪区 (0,201,1280,561) |

---

## 2. RacingModule 赛车活动模块

[racing_module.py](file:///d:/maaracing_assistant/maaracing_assistant/plugins/racing/module.py)

**职责**：
- 活动模块实现（`ActivityModule` 子类，`ID="racing"`，`NAME="极速狂飙"`）
- **MAA Resource / Tasker / RacingLoop 归属模块内部创建与管理**（不再由主控 controller 统一持有）
- 大厅层（归位 → 导航一 → 导航二）+ 对局层（导航三 → 弹窗 → 确认上阵 → 比赛循环）
- 7 阶段顺序（GUI 断点选择用）：

```
归位 → 导航一(极速狂飙入口) → 导航二(开始挑战) → 导航三(寻找对手)
→ 商店弹窗处理 → 确认上阵 → 比赛(Pipeline)
```

**模块声明**：
- `REQUIRES_GAMEPAD_EXCLUSIVE = True`：独占虚拟手柄
- `REQUIRES = frozenset({"capture", "gamepad"})`：依赖捕获与手柄能力（经 ctx 窄入口注入）

**关键接口**：

| 方法 | 说明 |
|------|------|
| `start(start_from)` | 启动流程（阻塞，worker 线程运行），支持断点阶段选择 |
| `stop()` | 幂等停止：先停 racing_loop，再中断 MAA Pipeline（`tasker.post_stop()`） |
| `cleanup()` | 幂等释放模块资源（renderer 由 Context.close 释放） |
| `current_stage` | 属性：当前阶段名 |
| `_ensure_pipeline()` | 首次比赛前创建 Tasker/Resource/RacingLoop 并 post_bundle + bind + add_context_sink |

**流程要点（与老 controller 编排的区别）**：
- **MAA 对象模块自有**：`_ensure_pipeline` 在首次比赛前懒创建，之后复用；`ctx.bind_tasker(tasker, resource)` 经 ctx 窄入口绑定，模块不接触高权限 Win32Controller
- **导航三等待**：`nav._wait_for_template("find_opponent_template", timeout=15)` 等页面出现再点按钮
- **断点只首轮生效**：`skip_until` 只在首轮生效，之后循环走完整流程
- **比赛异常重试**：`run_direct` 运行 <3 秒判定异常，最多重试 3 次；结束原因 `"商店弹窗"` → `handle_store_popup()`
- **手柄隔离**：每次比赛前 `ctx.gamepad.reset_device()` 销毁导航手柄，避免 RacingLoop 创建第二个手柄冲突

**运行流程**：

```
1. ctx.connect()（幂等兜底）→ 创建 Navigation（传 ctx 兼容桥接）
2. 安装 RacingDebugRenderer（Context ExitStack 接管生命周期）
3. 解析断点 skip_until（start_from 在 STAGE_ORDER 中 → index）
4. 大厅层：归位 → 导航一（≤3次重试，失败销毁手柄复位+重新归位）
   └─ 成功进入对局层循环
5. 对局层：导航二（≤6次重试，连续3次失败且首局 → 回大厅重新导航一）
   ├─ _in_match=True
   ├─ 导航三（≤6次重试，先等 find_opponent 模板15s）
   ├─ 商店弹窗处理 → 确认上阵（_ensure_cursor + navigate_to_button）
   ├─ 比赛（racing_loop.run_direct，≤3次重试）
   ├─ skip_until=0、_in_match=False → continue（回导航二循环）
   └─ 对局层跳出 → 回大厅层循环
6. finally：_current_stage=None、gamepad.reset_device()
```

---

## 3. RacingDebugRenderer 调试渲染器

[racing_renderer.py](file:///d:/maaracing_assistant/maaracing_assistant/plugins/racing/renderer.py)

**职责**：极速狂飙调试渲染器，通过 DebugManager 的 renderer token 机制接入渲染管线。

| 方法 | 说明 |
|------|------|
| `__init__(debug)` | 接收 DebugManager 引用 |
| `render_full(frame_bgr, state)` | 全量绘制（存盘用），复用 DebugManager 默认视图 |
| `render_peep(frame_bgr, state)` | 精简绘制（PEEP 实时预览用），复用 DebugManager 默认视图 |

> **阶段规划**：阶段一（当前）委托 DebugManager 内置默认视图（`_render_full`/`_render_peep`），行为与重构前一致、零重复；阶段二将 `_draw_*` 绘制逻辑迁入此类，DebugManager 只保留基础设施（PEEP 线程 / frame buffer / 文件存盘）。

---

## 4. 决策算法详解

### 4.1 贪婪决策优先级（_decide）

```
1️⃣ 金币+跳板车（奖励目标）
   ├─ 合并coins和bonus_cars
   ├─ 评分 = area - center_dist×0.1（面积优先，中线距离tie-break）
   ├─ _aim_at(target) 前馈瞄准
   ├─ B区检查：往墙方向则取消，返回"防撞B区阻挡"
   └─ 返回转向值 + 目标类型 + 区域+方向+面积

2️⃣ C区防撞（强制）
   └─ wall_zone==2 → 返回wall_dir强制转向

3️⃣ 障碍车避让
   ├─ 只看y>35%h的近车
   ├─ _avoid(cars) 透视框重叠判断
   │   ├─ 目标框75%宽度在L2c~R2c中心区才躲
   │   ├─ 力度：远区50%(16383)/中近区100%(32767)
   │   ├─ 优先向障碍物对侧躲（偏右→左躲）
   │   └─ 检查目标车道是否被其他车占据
   ├─ aim==0 → 障碍物不在路径，穿透到无目标逻辑！
   └─ B区检查：反向躲避尝试

4️⃣ 无目标
   ├─ 标线丢失+有wall_memory → 轻柔回带
   └─ 否则 → 返回0（直行，后续由_lane_keep接管）
```

### 4.2 前馈瞄准算法（_aim_at）

```
输入：target=(cx, cy, bw, bh)
1. offset = (cx - center_x) / (w/2)  （归一化-1~1）
2. area_ratio = bw*bh / (w*h)
3. zone = _get_zone(bottom_y)  （远0/中1/近2）
4. in_center：目标75%宽度在透视中心区L2c~R2c
5. stop_zone = 0.01 + min(0.10, area_ratio×30)  （动态停止区，目标越大越宽容）
6. 帧间dx/dy计算目标移动速度
7. moving_to_center：目标正向中心移动

8. ★ v0.11.1 关键修复：先算off_center保底，再判死区（顺序不能反！）
   off_center = not in_center（目标不在中心车道）
   ├─ off_center=True（已偏离中线车道）：
   │   ├─ 远区 strength_min = 0.15 （15%）
   │   ├─ 中区 strength_min = 0.25 （25%）
   │   ├─ 近区 strength_min = 0.40 （40%，防再拖）
   │   └─ effective_stop = 0.01  （死区收紧，不再用膨胀的stop_zone）
   └─ off_center=False（在中心车道内）：
       ├─ 远/中/近 strength_min = 0 / 0 / 0
       └─ effective_stop = stop_zone   （in_center时宽容，差一点点不计较）
   核心原理：
   - 先判保底后判死区：避免stop_zone随area_ratio膨胀到0.11时
     把0.102的偏移吞掉（帧239-253案例：目标偏-0.059~-0.144仍死区0直冲）
   - 例：stop_zone=0.077，offset=-0.059
       旧逻辑：0.059<0.077 → 死区=0 → 直冲
       新逻辑：off_center=True → effective_stop=0.01 → offset>0.01 → 修正

9. raw_strength = sqrt( abs(offset² / (1 - offset²)) )  （AIM连续映射）
10. strength = max(strength_min, zone_coef × raw_strength)  （+保底）
11. 前馈停止（仅in_center）：abs(offset)<stop_zone 且 moving_to_center → 返回0
12. 死区：abs(offset) < effective_stop → 返回0
13. zone==近 → 防撞降低系数 0.6×
14. 返回 sign × strength × 32767
```

> 典型输出力度（off_center=True 例）：
> - 远区 offset=0.06 → raw=0.215，max(0.15, 0.5×0.215)=max(0.15,0.108)=**15%**
> - 中区 offset=0.06 → raw=0.215，max(0.25, 1.0×0.215)=**25%**
> - 近区 offset=0.06 → raw=0.215，max(0.40, 0.6×0.215)=**40%**

### 4.3 三区防碰撞（_wall_avoidance）

基于单边（左或右）黄色标线追踪：

```
标线位置 pos（单边，在ROI中点处的x坐标）：

左墙侧（side=="left"，pos为左标线x）：
  A区（安全）：pos <= 350
  B区（警戒）：350 < pos <= 450
    └─ ddL>5且d>0（标线仍在加速右移=车在往左冲墙）→ 返回(1, 1)阻挡往右决策
  C区（强制）：pos > 450 且 cum3>10（3帧累计右移>10px）→ 返回(2, 1)强制右转

右墙侧（side=="right"，pos为右标线x）：
  A区（安全）：pos >= 930
  B区（警戒）：830 <= pos < 930
    └─ ddR<-5且d<0 → 返回(1, -1)阻挡往左决策
  C区（强制）：pos < 830 且 cum3<-10 → 返回(2, -1)强制左转

wall_memory：标线存在时记录靠墙状态，标线丢失+无YOLO目标时轻柔回带
```

**C区反打策略**（在_run_impl中）：
```
触发防撞C区 → _c_burst=2帧满打 → _c_coast=5帧强制归中滑行
→ 改变车头指向 → 让车自然滑离墙 → 重评估
（不持续满打，防止从一墙冲到另一墙弹乒乓）
```

### 4.4 闭环车道保持（_lane_keep）

```
仅在direction==0（无目标/直行）时激活
1. 5帧未激活或换侧 → 清空历史重来
2. 维护30帧标线位置历史 _keep_hist
3. d（最近1帧变化）、dd（加速度）、cum3（3帧累计）
4. abs(cum3)>=30 → 激活/升档：
   - 首次激活 strength=0.5(50%)
   - 方向反转（过冲）→ 升档
   - dd>0（仍在加速漂移）→ 升档min(1.0)
   - dd<=-2（快速收敛）→ 降档max(0.5)
5. strength>0且abs(d)<5（位置已稳定）→ 快速降档
6. abs(cum3)<25 → 快速降档直至关闭
7. 完全关闭后 _keep_cooldown=8帧冷却
8. 返回 keep_dir × strength × 32767（比例值）
```

---

## 5. 赛车控制参数

| 常量 | 值 | 说明 |
|------|-----|------|
| 赛车摇杆MAX | 32767 | XInput标准范围 |
| 赛车目标帧率 | 动态 (15~30) | `_benchmark_latency` 调优后写入 `_target_fps` |
| YOLO推理间隔 | 动态 = round(fps/10) | ≈10 Hz 实际更新频率 |
| 结束检测间隔 | 动态 = fps | ≈1 Hz 实际检测频率 |
| 油门（防撞） | 120 | 防撞时低油门 |
| 油门（避障） | 180 | 避障时中油门 |
| 油门（金币） | 200 | 吃金币时较高油门 |
| 油门（直行） | 255 | 直行满油门 |
| 远区力度系数 | 0.5 | 远区基础系数 |
| 中区力度系数 | 1.0 | 中区基础系数 |
| 近区力度系数 | 0.6 | 近区防撞降低系数 |
| off_center远区保底 | 15%（v0.11.1） | 目标不在中线车道时最低力度 |
| off_center中区保底 | 25%（v0.11.1） |  |
| off_center近区保底 | 40%（v0.11.1） |  |
| stop_zone公式 | 0.01 + min(0.10, area_ratio×30) | 仅 in_center=True 生效 |
| off_center死区effective_stop | 0.01（v0.11.1） | 偏离中线车道收紧死区，防止吞偏移 |
| C区突发帧数 | 2帧 | 短促打满改变指向 |
| C区滑行帧数 | 5帧 | 归中滑行远离墙 |
| 车道保持漂移阈值 | 30px（3帧累计） | 激活车道保持 |
| 车道保持收敛阈值 | 5px（1帧变化） | 提前停止修正 |
| 车道保持冷却 | 8帧 | 关闭后冷却时间 |
| 车道保持最大历史 | 30帧 | 位置历史长度 |
| 标线HSV下限 | [20,80,80] | 含阴影暗黄色 |
| HoughLinesP阈值 | 60 | 最小线段长度40，最大间隔40 |
| 地平线推断最小帧数 | 40帧 | 等镜头稳定 |
| 地平线推断小车最大面积 | 400px² | 排除近处大车 |
| 地平线推断最小车数 | 3个 | 1/4分位锁死 |

### 5.1 延迟基准 & 性能调优参数（v0.11.1 新增）

| 参数 | 位置 | 值 | 说明 |
|------|------|-----|------|
| `_use_fast_cap` | `RacingLoop.__init__` | 默认 `True` | 是否启用直接 BitBlt 截图（~3-7ms），失败降级 MAA |
| `_target_fps` | `RacingLoop.__init__` | 默认 `15`（基准后动态改写） | 目标 FPS（钳位 15~30） |
| `_benchmark_latency` 采样帧 | `_benchmark_latency` | 20 帧（10 帧 YOLO + 10 帧非 YOLO） | 启动前诊断样本量 |
| 剔帧策略 | 同上 | 剔最慢 1 帧（样本≥5时） | 抗离群值剔除量 |
| 分位统计 | 同上 | P50 / P90_trim / P95_raw | 稳健统计组合 |
| FPS 公式 | 同上 | `fps = round(950 / yolo_p90_trim)` | 5% 裕量（预算 105% P90_trim） |
| FPS 钳位 | 同上 | `max(15, min(30, fps))` | 下限15防失控/上限30=YOLO推理~30ms物理上限 |
| 离群告警阈值 | 同上 | `P95_raw / P90_trim > 1.8×` | 输出 ⚠ 提醒调度抖动 |
| YOLO 间隔公式 | 同上 | `YOLO_INTERVAL = round(fps/10)` | ≈10 Hz 更新频率 |
| 结束检测公式 | 同上 | `SLOW_CHECK = fps` | ≈1 Hz 检测频率 |
| 帧率 sleep 公式 | `_run_impl` 尾 | `sleep(max(0, 1/target_fps - elapsed))` | 精准节奏，补偿前序耗时 |

### 5.2 快速截图（BitBlt）兜底链路

按优先级从高到低尝试，每步失败打 WARNING 日志指明环节：

| 步骤 | 调用 | 失败原因 |
|------|------|----------|
| 1 | `ctrl.hWnd`（驼峰） | Win32Controller 属性名不叫 hWnd |
| 2 | `ctrl.hwnd`（小写） | 其他实现用小写 |
| 3 | `find_game_hwnd()`（window_utils） | 都拿不到 → 扫窗口标题/类名 |
| 4 | `GetClientRect(hwnd)` | 句柄无效/窗口最小化 |
| 5 | `GetDC(hwnd)` | 无 GDI 权限 |
| 6 | `CreateCompatibleDC` / `CreateCompatibleBitmap` | GDI 资源耗尽 |
| 7 | `GetBitmapBits` | 位图格式不兼容/尺寸异常 |
| *全部失败* | 降级 MAA `ctrl.post_processor.screenshot` | `_use_fast_cap=False`，不再重试 |

---

## 6. RacingLoop._run_impl 核心循环

```
0. 启动前：_benchmark_latency(ctrl) 延迟基准测试（v0.11.0+）
   ├─ 采样 20 帧（10 帧 YOLO / 10 帧纯视觉）
   ├─ 分项计时：截图 / 标线 / YOLO推理 / 决策
   ├─ 分帧统计：YOLO帧 vs 非YOLO帧（奇偶帧分流）
   ├─ 抗离群调优：剔 YOLO 帧最慢 1 帧 → 取 P90
   │   ├─ yolo_fps = round(950 / yolo_p90_trim)  # 5%裕量
   │   ├─ 钳位: max(15, min(30, tuned_fps))
   │   ├─ 离群告警: P95_raw / P90_trim > 1.8× 输出⚠
   │   └─ 输出格式:
   │       自动调优(最低延迟): 15→XX FPS (剔除1帧后YOLO P90=XXms + 5%裕量，预算=XXms/帧)
   │       YOLO 间隔: 每 N 帧一次 (≈XX Hz)
   ├─ 结果写入实例字段：
   │   ├─ _target_fps  (默认15 → 调优后值)
   │   └─ _use_fast_cap (默认True → 失败降级False)
   └─ 失败处理：快速截图失效降级 MAA + WARNING 日志（含具体失效环节）

1. 初始化
   ├─ _running=True, frame_id=0
   ├─ 记录模式：打开CSV文件
   └─ 正常模式：_create_pad() + _apply_trigger(255)（按住油门起步）

2. 主循环（while _running，目标由_benchmark_latency动态确定，15~30 FPS）
   ├─ _cap(ctrl) 截图：
   │   ├─ _use_fast_cap=True → _cap_fast() 直接 BitBlt（P50≈3~7ms）
   │   │   ├─ 句柄兜底链：ctrl.hWnd → ctrl.hwnd → find_game_hwnd()
   │   │   ├─ 每步 GDI 检查：GetClientRect/GetDC/CreateCompatibleDC/CreateCompatibleBitmap/GetBitmapBits
   │   │   └─ 失败 → WARNING + 置_use_fast_cap=False + 降级 MAA
   │   └─ 否则 → MAA ctrl.post_processor.screenshot（P50≈25ms）
   ├─ frame_id++
   ├─ _detect_lane(img) 每帧检测黄色标线（开销低，P50≈1~2ms）
   ├─ 每 SLOW_CHECK 帧：_is_end(img) 检测结束画面（SLOW_CHECK = fps → ≈1 Hz）
   │   └─ 匹配 → _steer(0) → return True
   ├─ 每 YOLO_INTERVAL 帧：YOLO推理 det(img, roi=ROI)
   │   ├─ YOLO_INTERVAL = round(fps / 10) → ≈10 Hz
   │   │   (30FPS→每3帧=10Hz，15FPS→每2帧=7.5Hz)
   │   └─ 结果缓存到 _cached_coins/cars/bonus
   ├─ 中间帧复用缓存
   ├─ _detect_horizon(all_raw) 动态地平线（前40帧跳过）
   ├─ _wall_avoidance(lane, w) 防碰撞检查
   ├─ _decide(...) 全局决策 → (direction, reason, detail)
   ├─ direction==0且无目标 → _lane_keep(lane) 车道保持
   ├─ C区突发修正逻辑：
   │   ├─ _c_burst>0：持续突发转向
   │   ├─ _c_coast>0：强制归中滑行
   │   └─ 触发防撞 → _c_burst=2, _c_coast=5
   ├─ 记录模式：读取物理手柄→写CSV→continue
   ├─ 正常模式：_steer(steer_val)（值变化时才发送）
   ├─ DEBUG/PEEP：save_frame() 渲染调试帧
   └─ 帧率控制：sleep(max(0, 1/_target_fps - elapsed))

3. 清理
   ├─ 记录模式：关闭CSV
   └─ 正常模式：_destroy_pad()
```

---

## 7. 赛车控制坑点

| 坑点 | 说明 |
|------|------|
| 防碰撞三区体系 | A区无干预/B区仅阻挡往墙方向决策/C区强制突发修正 |
| C区反打思维 | 2帧突发+5帧归中滑行，防止从右墙冲到左墙弹乒乓 |
| 标线不推断对侧 | _detect_lane只返回真实检出的side+pos，调用方必须用.get()安全访问 |
| 防撞记忆wall_memory | 标线存在时记录靠墙状态，标线丢失+无YOLO目标时轻柔回带 |
| _aim_at/_avoid无标线边界约束 | 防碰撞由独立模块负责，金币/避障可自由变道 |
| NMS per-class索引映射 | NMS返回类别内下标，需经cls_local→mask_indices两级映射回原数组 |
| _avoid框重叠检测 | `left<R2c and right>L2c`，不用中心点，用车框左右沿判断 |
| 避障穿透 | _avoid返回0时_decide不返回避障，落到金币/跳板车逻辑 |
| 车道保持方向统一 | 左右标线侧`new_dir=1 if diff>0 else -1`，右标线侧不能取反 |
| 地平线推断 | 低置信度(≤0.25)小面积(<400px²)小车，前40帧不推断，≥3车取1/4分位锁死 |
| _lane_boundaries_at_y透视 | 消失点(cx,horizon)经测量点线性外推，L2c/R2c在(0.22,1.00)测量 |
| 标线单边选择 | side_score=总长度×角度一致性，择优选一侧，只返回{side, pos} |
| 侧区金币扣分 | 紧贴墙壁侧的金币权重扣分，鼓励往安全侧转向 |
| 转向平滑 | alpha=0.6固定值（校准模块已删除，受干扰性太强） |
| C区cum3位移过滤 | 3帧累计位移>10px才触发，防止车道1正常行驶误触 |
| HSV阴影标线检测 | S/V下限从150降至80，可识别阴影下的暗黄色标线 |
| 导航手柄销毁 | 进入RacingLoop前必须销毁导航手柄，避免创建第二个手柄游戏不识别 |
