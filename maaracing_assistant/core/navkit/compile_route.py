#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""schema v3 routes → MAA pipeline JSON 编译器（S3）。

纯标准库。输出稳定、无时间戳，便于 CI 做"重新编译 == 磁盘生成物"比对。
生成物文件头包含 `_generated` 与 `source_hash`，不应手改。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .assets import Assets, Route, RouteStep, route_node_name

__all__ = ["compile_routes", "compile_routes_json", "generated_header"]


def generated_header(assets: Assets) -> dict[str, Any]:
    return {
        "_generated": True,
        "_source": str(assets.source_path.name if assets.source_path else "assets.json"),
        "source_hash": assets.source_hash,
    }


def compile_routes(assets: Assets) -> dict[str, Any]:
    """编译所有 routes，返回确定性 dict。"""
    out: dict[str, Any] = generated_header(assets)
    for route_name, route in assets.routes.items():
        for index, step in enumerate(route.steps):
            node_name = route_node_name(assets.module, route_name, index, step.target)
            node: dict[str, Any] = {
                "name": node_name,
                "recognition": "Custom",
                "custom_recognition": "MRA_Template",
                "custom_recognition_param": _recognition_param(assets, step.target),
                "action": "DoNothing",
                "next": [],
            }
            if step.action == "click":
                node["action"] = "Custom"
                node["custom_action"] = "MRA_Click"
                node["custom_action_param"] = {
                    "timeout_s": (step.timeout_ms or 45000) / 1000.0,
                    "wait_after_ms": step.rate_limit_ms or 600,
                }
            elif step.action == "press":
                node["action"] = "Custom"
                node["custom_action"] = "MRA_Press"
                node["custom_action_param"] = dict(step.press)
            if step.confirm:
                if index + 1 < len(route.steps):
                    # 下一步的 target 就是 confirm 时，沿用下一步节点；否则编一个只负责
                    # 证明页面已到达的 DoNothing 确认节点。
                    next_step = route.steps[index + 1]
                    if next_step.target == step.confirm:
                        node["next"] = [route_node_name(assets.module, route_name, index + 1, step.confirm)]
                    else:
                        confirm_name = route_node_name(assets.module, route_name, index, step.confirm + "::confirm")
                        node["next"] = [confirm_name]
                        out[confirm_name] = _confirm_node(assets, confirm_name, step.confirm)
                else:
                    confirm_name = route_node_name(assets.module, route_name, index, step.confirm + "::confirm")
                    node["next"] = [confirm_name]
                    out[confirm_name] = _confirm_node(assets, confirm_name, step.confirm)
            out[node_name] = node
    return out


def _confirm_node(assets: Assets, name: str, confirm: str) -> dict[str, Any]:
    """末步确认节点：识别到页面锚点即完成，不执行点击、不再有 next。"""
    return {
        "name": name,
        "recognition": "Custom",
        "custom_recognition": "MRA_Template",
        "custom_recognition_param": _recognition_param(assets, confirm),
        "action": "DoNothing",
        "timeout": 45000,
    }


def _recognition_param(assets: Assets, target: str) -> dict[str, Any]:
    anchor = assets.anchors[target]
    data: dict[str, Any] = {
        "templates": list(anchor.templates),
        "threshold": anchor.threshold if anchor.threshold is not None else assets.match.threshold,
        "scales": list(anchor.scales or assets.match.scales),
        "roi": anchor.rect.as_list(),
    }
    if anchor.kind == "point":
        cx, cy = anchor.rect.center_norm()
        data["fallback_pct"] = [cx, cy]
        data["guarded_by"] = anchor.guarded_by
    if anchor.kind == "template":
        data["expect_absent"] = False
    return data


def compile_routes_json(assets: Assets) -> str:
    """返回稳定 JSON 文本（末尾换行）。"""
    return json.dumps(compile_routes(assets), ensure_ascii=False, indent=2, sort_keys=False) + "\n"
