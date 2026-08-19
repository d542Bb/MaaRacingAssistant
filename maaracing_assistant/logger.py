#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日志模块：Logger 类 + 全局 logger 实例
"""

import sys
from pathlib import Path
from datetime import datetime


class Logger:
    # 日志级别：TRACE < DEBUG < INFO < WARNING < ERROR
    LEVELS = {"TRACE": 0, "DEBUG": 1, "INFO": 2, "WARNING": 3, "ERROR": 4}
    GUI_MIN_LEVEL = "INFO"  # GUI 只显示 INFO 及以上级别

    def __init__(self, log_dir: Path):
        log_dir.mkdir(exist_ok=True)
        self.log_file = log_dir / f"MRA_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        self._lines = []

    def log(self, msg: str, level: str = "INFO"):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] [{level}] {msg}"
        self._lines.append(line)
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def get_lines(self, min_level: str = "INFO"):
        """获取日志，可按级别过滤。GUI 默认只显示 INFO 及以上"""
        min_val = self.LEVELS.get(min_level, 2)
        return [line for line in self._lines
                if self.LEVELS.get(self._extract_level(line), 2) >= min_val]

    @staticmethod
    def _extract_level(line: str) -> str:
        """从日志行中提取级别，如 [INFO] → INFO"""
        parts = line.split("] [")
        if len(parts) >= 2:
            return parts[1].split("]")[0]
        return "INFO"


# 全局日志单例（日志目录 = 项目根 / logs）
logger = Logger(Path(__file__).parent.parent / "logs")
