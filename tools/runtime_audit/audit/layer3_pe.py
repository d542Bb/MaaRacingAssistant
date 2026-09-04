"""Layer 3：Native PE 依赖图。

用 pefile 分析 .exe/.dll/.pyd 的导入表（含延迟加载导入），输出 文件->依赖DLL 边。
系统 DLL 依据 config.PE_SKIP_BASENAMES 跳过；边保留相对发行根的路径当可解析。
"""
from __future__ import annotations

import pefile
from pathlib import Path

from . import config


class PeGraph:
    def __init__(self, exp_root: Path):
        self.exp_root = exp_root
        self.edges: dict[str, set[str]] = {}    # rel_native -> set(dll_basename)
        self.errors: list[str] = []

    def scan_root(self) -> None:
        for p in _collect(self.exp_root):
            self._scan_one(p)

    def _scan_one(self, p: Path) -> None:
        rel = p.relative_to(self.exp_root).as_posix()
        deps: set[str] = set()
        try:
            pe = pefile.PE(str(p), fast_load=True)
            pe.parse_data_directories(
                directories=[
                    pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
                    pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"],
                ]
            )
            for entry in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []) or []:
                name = (entry.dll or b"").decode(errors="replace")
                if name:
                    deps.add(name)
            for entry in getattr(pe, "DIRECTORY_ENTRY_DELAY_IMPORT", []) or []:
                name = (entry.dll or b"").decode(errors="replace")
                if name:
                    deps.add(name)
            pe.close()
        except Exception as e:  # pefile 无法解析（非 PE / 损坏）也记录
            self.errors.append(f"{rel}: {e}")
        self.edges[rel] = deps


def _collect(root: Path) -> list[Path]:
    out = []
    for ext in config.NATIVE_SUFFIXES:
        out.extend(root.rglob("*" + ext))
    return [p for p in out if "__pycache__" not in p.parts]


def relevant_dep(dll_name: str) -> bool:
    """系统/编译器运行库 DLL 不需要往下追。"""
    low = dll_name.lower()
    for skip in config.PE_SKIP_BASENAMES:
        s = skip.lower()
        if s.endswith("*"):
            if low.startswith(s[:-1]):
                return False
        elif low == s:
            return False
    # 常见 VC/exe runtime
    return not any(x in low for x in ("vcruntime", "msvcp", "ucrtbase", "concrt"))


def edge_set_for(pe_edges: dict[str, set[str]]) -> set[str]:
    """返回【在发行根内存在对应文件】的、可进一步的依赖名集合（相对路径）。"""
    return set()  # 具体解析在 classify 阶段处理