"""Layer 4：真实 native 模块加载采集。

V0.1 策略（安全、无 GUI）：
  - 主要依据 Layer2 的 sys.modules 快照：native 扩展（.pyd/.so）被 import 即 load。
    .py/.pyd/.so 的统一标记由 layer2_trace.mark_py_runtime 完成。
  - 本层补充标记【非 Python 扩展】的 native DLL（.exe/.dll 中被 sys.modules 间接暴露的，
    以及解释器进程必然加载的解释器/DLL 依赖）。
  - 系统 DLL（vcruntime140、ucrtbase 等）被解释器进程隐式加载，标记 RUNTIME-LOADED。
"""
from __future__ import annotations

from pathlib import Path

_IMPLICIT_BASENAMES = {
    "python311.dll", "python3.dll", "vcruntime140.dll", "vcruntime140_1.dll",
    "libssl-3.dll", "libcrypto-3.dll", "sqlite3.dll", "libffi-8.dll",
}


def mark_native_runtime(file_records: dict, module_files: set, trace_out: Path = None):
    """把解释器隐式依赖 DLL 及 sys.modules 快照中暴露的 native 标为 RUNTIME-LOADED=YES。"""
    reload_set = {Path(r).as_posix() for r in module_files}
    for rel, rec in file_records.items():
        rp = Path(rel).as_posix()
        # 已由 layer2_trace 标记的扩展跳过
        if rec.runtime_loaded in ("YES", "NO") and rp.endswith((".py", ".pyd", ".so")):
            continue
        base = Path(rp).name
        if base in _IMPLICIT_BASENAMES:
            rec.runtime_loaded = "YES"
        elif rp.endswith(".dll") and rp in reload_set:
            rec.runtime_loaded = "YES"