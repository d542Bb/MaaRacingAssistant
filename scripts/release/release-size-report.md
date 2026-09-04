# Release Size Report

- version: 0.19.0
- generated: 2026-09-04T20:25:35Z
- configuration: Release (all SAFE pruning on)
- git: 36a1088

## 1. 最终体积
| part | MB |
|---|---|
| runtime | 330.36 |
| app | 154.58 |
| sidecar | 1.66 |
| other | 12.64 |
| total unpacked | 499.24 |
| zip | 211.37 |

## 2. vs baseline (exp7)
| | baseline | release | delta |
|---|---|---|---|
| total | 503.23 | 499.24 | -3.99 |
| zip | 212.98 | 211.37 | -1.61 |

## 3. Regression
no regression

## 4. 构建来源（可复现性证明）
- Source runtime: `build\runtime-full\python`（Python 3.11.9 embed + `requirements-runtime-lock.txt` 全量 pip，非任何 expN）
- 未裁剪 Full Runtime baseline: 473.49 MB（含 pip 预编译 pyc 74.89 MB，assemble 统一清理，历史口径即无 pyc）
- 未裁剪验尸: sympy/mpmath/MaaAgentBinary/_avif/ORT capi dll/numpy dev/pyi×299/bin exe×8 全部存在；核心 pyd/dll/models 完整
- app: dotnet publish 从源码全新构建（publish-cache 指纹重算）
- Production pruning: 10 项全部执行；Guard/反向验证无 PRUNING-FAIL / PRUNING-REGRESSION

## 5. Smoke test（正式产物实测）
| 项 | 结果 |
|---|---|
| Python import self-check（assemble 内置） | PASS |
| NumPy（linalg/random/fft） | PASS |
| OpenCV 5.0.0（cvtColor/putText/imencode/imdecode） | PASS |
| ORT/DML 真实推理（model.onnx, DmlExecutionProvider） | PASS |
| RapidOCR（构造+推理） | PASS |
| windows_capture / vgamepad | PASS |
| MaaFramework Toolkit/Tasker/Resource + 桌面枚举 | PASS |
| Win32Controller（MRA 实际控制路径） | PASS |
| Racing（manifest/module/loop） | PASS |
| Treasure（manifest/module/ocr/strategy/store + 业务逻辑） | PASS |
| yolo_detector / wgcap | PASS |
| 截图 WgcCapture | NOT VERIFIED（hwnd=0 环境限制，与历次实验口径一致） |
| GUI | NOT FULLY VERIFIED（requireAdministrator，不弹 UAC） |

> **限定说明**：GUI 与真实窗口抓帧仍未在本轮 smoke test 中完成实机验证，因为 mra_shell.exe 的
> requireAdministrator 会触发 UAC；不过 Python、Win32Controller、YOLO/DML、RapidOCR、
> MaaFramework、Racing、Treasure、截图构造和手柄链均已完成验证。

## 6. 最终结论
Production Release = **READY**
