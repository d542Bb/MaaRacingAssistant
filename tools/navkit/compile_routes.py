#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""编译鉴宝 v3 routes 到 resources/generated/pipeline/treasure_routes.json。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_PROJ = Path(__file__).resolve().parents[2]
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from maaracing_assistant.core.navkit import Assets, compile_routes_json  # noqa: E402

ASSETS = _PROJ / "maaracing_assistant/plugins/treasure/resources/config/treasure_assets.json"
OUT = _PROJ / "maaracing_assistant/plugins/treasure/resources/generated/pipeline/treasure_routes.json"
IMAGE = _PROJ / "maaracing_assistant/plugins/treasure/resources/image"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    assets = Assets.load(ASSETS, module="treasure", image_dirs=(IMAGE,))
    generated = compile_routes_json(assets)
    if args.check:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != generated:
            print("[compile_routes] 生成物与重新编译结果不一致", file=sys.stderr)
            return 1
        print("[compile_routes] --check 通过")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(generated, encoding="utf-8")
    print(f"[compile_routes] 已写入 {OUT.relative_to(_PROJ)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
