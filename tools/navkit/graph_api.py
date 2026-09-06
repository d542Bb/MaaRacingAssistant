#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NavKit 结构树数据转换（纯标准库）。"""
from __future__ import annotations

from typing import Any


def graph_document(doc: dict[str, Any]) -> dict[str, Any]:
    anchors = doc.get("anchors") or {}
    nodes: list[dict[str, Any]] = []
    for name, anchor in anchors.items():
        nodes.append({
            "id": name,
            "label": anchor.get("label", name),
            "kind": anchor.get("kind"),
            "owner": anchor.get("owner"),
            "page": anchor.get("page"),
            "order": anchor.get("order"),
            "guarded_by": anchor.get("guarded_by"),
            "templates": anchor.get("templates", []),
            "dynamic": False,
        })
    for stage, definition in (doc.get("stages", {}).get("definitions", {}) or {}).items():
        nodes.append({
            "id": "stage:" + stage,
            "label": stage,
            "kind": "stage",
            "page": definition.get("page"),
            "anchors": definition.get("anchors", []),
            "ocr": definition.get("ocr", []),
            "dynamic": bool(definition.get("dynamic_narrow")),
        })
    edges: list[dict[str, Any]] = []
    for transition in doc.get("transitions", []) or []:
        edges.append({"from": transition.get("stage"), "on": transition.get("on"), "to": transition.get("to"), "kind": "transition"})
    for route, definition in (doc.get("routes", {}) or {}).items():
        for index, step in enumerate(definition.get("steps", []) or []):
            edges.append({"from": route + ":" + str(index), "on": step.get("target"), "to": step.get("confirm"), "kind": "route"})
    referenced = {e.get("on") for e in edges} | {g for n in nodes for g in [n.get("guarded_by")] if g}
    orphans = [n["id"] for n in nodes if n.get("kind") in {"template", "point", "ocr"} and n["id"] not in referenced]
    return {
        "module": doc.get("_module"),
        "stage_order": doc.get("stages", {}).get("order", []),
        "pages": doc.get("pages", {}),
        "nodes": nodes,
        "edges": edges,
        "orphans": orphans,
        "unguarded_points": [n["id"] for n in nodes if n.get("kind") == "point" and not n.get("guarded_by")],
    }
