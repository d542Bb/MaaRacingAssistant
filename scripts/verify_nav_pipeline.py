#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跳转图离线自检：不需要连游戏窗口，只验三件事。

  1. 带 // 注释的 pipeline JSON 能否被 MAA 框架加载并解析成节点；
  2. 每个节点的识别/动作/边是否解析成了我们期望的形状；
  3. 两个桥（MRA_Template 识别、MRA_Click 动作）能否注册进 Resource。

用法：  python scripts/verify_nav_pipeline.py
退出码： 0 = 全部通过；1 = 有失败项（失败原因逐条打印）。
"""
from __future__ import annotations

import re
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from maa.resource import Resource  # noqa: E402

HALL = ROOT / "maaracing_assistant" / "core" / "resources" / "pipeline" / "hall.json"
RACING_NAV = (ROOT / "maaracing_assistant" / "plugins" / "racing"
              / "resources" / "pipeline" / "racing_nav.json")

_NODE_RE = re.compile(r'^\s{4}"([^"]+)"\s*:\s*\{', re.M)

failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        failures.append(msg)


def node_names(path: Path) -> list[str]:
    return _NODE_RE.findall(path.read_text(encoding="utf-8"))


def main() -> int:
    from maaracing_assistant.core.nav_graph import (ACTION_NAME, RECOGNIZER_NAME,
                                                    ClickAction, NavGraph, TemplateRecognizer)

    res = Resource()

    for path in (HALL, RACING_NAV):
        print(f"\n[1] 加载 {path.relative_to(ROOT)}")
        names = node_names(path)
        check(bool(names), f"{path.name} 里解析出 {len(names)} 个节点")
        job = res.post_pipeline(str(path)).wait()
        check(job.succeeded, f"post_pipeline 成功（带 // 注释）→ done={job.status.done} "
                             f"succeeded={job.succeeded}")
        for n in names:
            data = res.get_node_data(n)
            check(data is not None, f"节点「{n}」已被框架收录")

    print("\n[2] 关键节点形状")
    entry = res.get_node_data("极速狂飙_从大厅进入")
    if entry:
        reco, act = entry["recognition"], entry["action"]
        check(reco["type"] == "Custom", f"入口识别类型 = {reco['type']}")
        check(reco["param"]["custom_recognition"] == RECOGNIZER_NAME,
              f"入口识别桥 = {reco['param']['custom_recognition']}")
        check(act["type"] == "Custom" and act["param"]["custom_action"] == ACTION_NAME,
              f"入口动作桥 = {act['param']['custom_action']}")
        check("已到达极速狂飙页" in [a["name"] for a in entry["next"]],
              f"入口 next = {[a['name'] for a in entry['next']]}")
        check(entry["rate_limit"] == 600 and entry["timeout"] == 45000,
              f"入口节奏 rate_limit={entry['rate_limit']} timeout={entry['timeout']}")
        term = res.get_node_data("已到达极速狂飙页")
        check(term["action"]["type"] == "DoNothing", "终点确认节点是 DoNothing")
        check(not term["next"], "终点确认节点无 next（图到此结束）")
        check(term["recognition"]["param"]["custom_recognition_param"]["templates"]
              == ["activity_page_template"], "终点确认用的是既有页面模板")
    absent = res.get_node_data("极速狂飙_已进入匹配")
    if absent:
        param = absent["recognition"]["param"]["custom_recognition_param"]
        check(param.get("expect_absent") is True, "「模板消失才算到位」写在识别参数里")
        check(absent["action"]["type"] == "DoNothing", "消失校验节点不点击")

    print("\n[3] 桥注册")
    dummy_ctx = types.SimpleNamespace()
    graph = NavGraph(dummy_ctx)
    check(isinstance(graph, NavGraph), "NavGraph 可在无游戏窗口下构造（装配不触碰宿主能力）")
    check(graph._resource is not res, "每个模块的图实例各自持有 Resource")
    ok_reco = res.register_custom_recognition(RECOGNIZER_NAME, TemplateRecognizer(graph))
    ok_act = res.register_custom_action(ACTION_NAME, ClickAction(graph))
    check(bool(ok_reco), f"识别桥注册为 {RECOGNIZER_NAME}")
    check(bool(ok_act), f"动作桥注册为 {ACTION_NAME}")
    from maa.custom_action import CustomAction
    from maa.custom_recognition import CustomRecognition
    check(issubclass(TemplateRecognizer, CustomRecognition), "识别桥继承 CustomRecognition")
    check(issubclass(ClickAction, CustomAction), "动作桥继承 CustomAction")

    print("\n[4] 桥逻辑（合成帧，不需要游戏窗口）")
    check_bridges(TemplateRecognizer, ClickAction, NavGraph, RECOGNIZER_NAME, ACTION_NAME)

    print("\n" + ("全部通过" if not failures else f"失败 {len(failures)} 项"))
    for f in failures:
        print("  - " + f)
    return 0 if not failures else 1


def check_bridges(TemplateRecognizer, ClickAction, NavGraph, reco_name, act_name) -> None:
    """造一张已知贴图的假帧，验识别桥的四条行为：命中 / 消失校验 / 兜底 / 空配置。"""
    import json
    import tempfile
    from pathlib import Path

    import cv2
    import numpy as np

    rng = np.random.default_rng(7)
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    frame[:, :, :] = (10, 12, 14)
    patch = rng.integers(60, 255, size=(30, 60, 3), dtype=np.uint8)
    x, y = 800, 500
    frame[y:y + 30, x:x + 60] = patch

    tmp = Path(tempfile.mkdtemp())
    cv2.imwrite(str(tmp / "unit_tpl.png"), cv2.cvtColor(patch, cv2.COLOR_RGB2BGR))

    class FakeCapture:
        def screenshot(self):
            return frame

    class FakeLifecycle:
        running = True

        @staticmethod
        def sleep(_s):
            return True

    graph = NavGraph(types.SimpleNamespace(
        capture=FakeCapture(), lifecycle=FakeLifecycle(),
        click_mode="gamepad", intent_mode=False, hwnd=0))
    graph.image_dirs = [tmp]
    reco = TemplateRecognizer(graph)

    def run_reco(param: dict):
        argv = types.SimpleNamespace(custom_recognition_param=json.dumps(param, ensure_ascii=False),
                                     node_name="单测节点")
        return reco.analyze(None, argv)

    got = run_reco({"templates": ["unit_tpl"], "threshold": 0.7, "scales": [1.0]})
    check(got.box == (x, y, x + 60, y + 30), f"识别桥命中框回到贴图位置 → {got.box}")

    got = run_reco({"templates": ["unit_tpl"], "threshold": 0.7, "scales": [1.0],
                    "roi": [0.5, 0.5, 1.0, 1.0]})
    check(got.box == (x, y, x + 60, y + 30), f"归一化 roi 换算正确（全图框内仍命中）→ {got.box}")

    got = run_reco({"templates": ["unit_tpl"], "threshold": 0.7, "scales": [1.0],
                    "roi": [0.0, 0.0, 0.4, 0.4]})
    check(got.box is None, "目标在 roi 之外 → 不命中（roi 真的在限制搜索区）")

    got = run_reco({"templates": ["unit_tpl"], "threshold": 0.7, "scales": [1.0],
                    "expect_absent": True})
    check(got.box is None, "expect_absent：模板还在 → 不算到位")
    got = run_reco({"templates": ["missing_tpl"], "threshold": 0.9, "scales": [1.0],
                    "expect_absent": True})
    check(got.box is not None, "expect_absent：模板消失 → 判到位")

    got = run_reco({"templates": ["missing_tpl"], "threshold": 0.9, "scales": [1.0],
                    "fallback_pct": [0.25, 0.5]})
    cx, cy = (got.box[0] + got.box[2]) // 2, (got.box[1] + got.box[3]) // 2
    check((cx, cy) == (320, 360), f"模板缺失时退到百分比兜底 → 中心 {cx},{cy}")

    got = run_reco({"templates": [], "threshold": 0.7, "expect_absent": True})
    check(got.box is None, "空 templates + expect_absent 必须判失败（防假成功放行）")

    got = run_reco({"templates": ["missing_tpl"], "threshold": 0.9})
    check(got.box is None, "模板缺失且无兜底 → 不命中（走框架重试到 timeout）")


if __name__ == "__main__":
    sys.exit(main())
