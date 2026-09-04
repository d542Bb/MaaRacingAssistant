# Runtime Pruning Policy

正式 Release 的裁剪白名单（`assemble.ps1 -Configuration Release` 默认全部启用）。

每个条目都经过了独立单变量实验验证。修改依赖 / WindowsAppSDK / Python / MaaFw 版本时，
若本白名单匹配不到目标文件，`-Configuration Release` 会**发布失败**（PRUNING-FAIL），而不是悄悄漏删。

## Production-safe removals

\| 项 | 来源实验 | 收益 | 验证结论 |
\|---|---:|---|
\| WindowsAppSDK AI/ML 死链 | exp1 | \~43.65 MB | SAFE |
\| WindowsAppSDK Widgets 死链 | exp2 | \~2.49 MB | SAFE |
\| Python ORT `capi\onnxruntime.dll` | exp3 | \~20.13 MB | SAFE（pyd 自带 ORT 引擎） |
\| PIL `_avif` native ext | exp4A | \~7.52 MB | SAFE（惰性，项目零 avif 路径） |
\| `.NET` crash diagnostics | exp4B | \~4.73 MB | **SAFE FOR NORMAL OPERATION** |
\| NumPy dev/build 目录 | exp5A | \~1.87 MB | SAFE |
\| `.pyi` typing stubs | exp5B-1 | \~1.13 MB | SAFE |
\| Python console wrappers | exp5E-1 | \~0.83 MB | SAFE |
\| SymPy | exp6 | \~25.37 MB | **SAFE FOR CURRENT MRA** |
\| MaaAgentBinary | exp7 | \~12.53 MB | **SAFE FOR CURRENT MRA** |

## 注意与代价

- **Crash diagnostics**：删除后没有 application-level createdump / DAC (`mscordaccore`)，
  降低崩溃转储与 SOS 分析能力；Windows Error Reporting OS 级 dump 仍可配置。
  保留 `mscordbi.dll`（debugger attach 能力）。

- **SymPy**：仅当前 MRA 运行路径安全。若未来使用 onnxruntime 的离线
  `symbolic_shape_infer` / `transformers` 工具，需恢复依赖。

- **MaaAgentBinary**：Android/ADB 路径不再支持。当前 MRA 的 `Win32Controller`
  （Win32 截图 + 手柄）不受影响。

## 明确不纳入正式裁剪

| 项                                                         | 原因                                         |
| --------------------------------------------------------- | ------------------------------------------ |
| `pygrun` (6 KB)                                           | 收益过低，不值得增加规则复杂度                            |
| `mpmath`                                                  | 不因"看似无用"顺手删，等真正需要时独立实验                     |
| `mscordbi.dll`                                            | 保留 debugger attach，1.18 MB 不值得牺牲           |
| `numpy.typing`                                            | 收益 \~0.14 MB，进入第三方源码层，不划算                  |
| dist-info `RECORD`                                        | `importlib.metadata.files()` 语义可能依赖，价值低于风险 |
| `INSTALLER/WHEEL/REQUESTED`                               | 收益仅 2.8 KB，不纳入                             |
| `PublishTrimmed` / WindowsAppSDKSelfContained 调整 / TFM 调整 | 大决策，未验证，禁止盲开                               |
| DirectML hardlink                                         | 未纳入                                        |

## 验证状态

- 最后验证版本：`0.19.0`

- 关联 baseline：`exp7`（total 503.23 MB / zip 212.98 MB）

- 反向验证（防误删）由 `assemble.ps1` 5.5 段自动执行：确认应删项不存在、应保留项存在。

- Size gate：`assemble.ps1` 8 段，与 baseline 对比，delta > ±5MB 判 SIZE REGRESSION。

