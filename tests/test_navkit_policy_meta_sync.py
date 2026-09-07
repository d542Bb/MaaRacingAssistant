#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NavKit P2 契约漂移测试（只读守卫）：前端 policyMeta.js 的 CONTRACT_MIRROR
必须与后端 policy.py 常量集合一致，漂移即失败（本地 / CI）。

比对语义为集合（无序契约，见 docs/plan/P2_PLAN.md §3.2）：Python 侧常量的
书写顺序不构成契约，前端展示顺序由 PRESENTATION 层自行定义。本测试只读取
后端常量做比对，不 import / 执行任何修改路径，不改 schema / 校验 / API。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from maaracing_assistant.core.navkit import (
    DECISION_SOURCES,
    EFFECT_WHITELIST,
    FACT_FIELDS,
    OP_WHITELIST,
    policy as policy_mod,
)

_FRONTEND_META = (
    Path(__file__).resolve().parents[1]
    / "tools/navkit/frontend/src/policyMeta.js"
)


def _extract_mirror_object(source: str) -> dict:
    """从 policyMeta.js 源码中截取 `export const CONTRACT_MIRROR = {...};`
    的对象字面量并按 JSON 解析（该字面量保持 JSON 兼容格式）。"""
    marker = "export const CONTRACT_MIRROR = "
    start = source.index(marker) + len(marker)
    if source[start] != "{":
        raise AssertionError("CONTRACT_MIRROR 必须以对象字面量开始（保持 JSON 兼容格式）")
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(source)):
        ch = source[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                text = source[start : i + 1]
                tail = source[i + 1 :].lstrip()
                assert tail.startswith(";"), "CONTRACT_MIRROR 字面量后应紧跟分号"
                return json.loads(text)
    raise AssertionError("CONTRACT_MIRROR 对象字面量未闭合")


@pytest.fixture(scope="module")
def mirror() -> dict:
    return _extract_mirror_object(_FRONTEND_META.read_text(encoding="utf-8"))


def test_fact_fields_sync(mirror):
    assert set(mirror["FACT_FIELDS"]) == set(FACT_FIELDS), (
        f"FACT_FIELDS 漂移：仅前端 {sorted(set(mirror['FACT_FIELDS']) - set(FACT_FIELDS))} / "
        f"仅后端 {sorted(set(FACT_FIELDS) - set(mirror['FACT_FIELDS']))}"
    )


def test_op_whitelist_sync(mirror):
    assert set(mirror["OP_WHITELIST"]) == set(OP_WHITELIST), (
        f"OP_WHITELIST 漂移：仅前端 {sorted(set(mirror['OP_WHITELIST']) - set(OP_WHITELIST))} / "
        f"仅后端 {sorted(set(OP_WHITELIST) - set(mirror['OP_WHITELIST']))}"
    )


def test_decision_sources_sync(mirror):
    assert set(mirror["DECISION_SOURCES"]) == set(DECISION_SOURCES), (
        f"DECISION_SOURCES 漂移：仅前端 {sorted(set(mirror['DECISION_SOURCES']) - set(DECISION_SOURCES))} / "
        f"仅后端 {sorted(set(DECISION_SOURCES) - set(mirror['DECISION_SOURCES']))}"
    )


def test_effect_whitelist_sync(mirror):
    assert set(mirror["EFFECT_WHITELIST"]) == set(EFFECT_WHITELIST), (
        f"EFFECT_WHITELIST 漂移：仅前端 {sorted(set(mirror['EFFECT_WHITELIST']) - set(EFFECT_WHITELIST))} / "
        f"仅后端 {sorted(set(EFFECT_WHITELIST) - set(mirror['EFFECT_WHITELIST']))}"
    )


def test_wait_keys_sync(mirror):
    backend = set(policy_mod._WAIT_KEYS)
    frontend = set(mirror["WAIT_KEYS"])
    assert frontend == backend, (
        f"WAIT_KEYS 漂移：仅前端 {sorted(frontend - backend)} / 仅后端 {sorted(backend - frontend)}"
    )


def test_tuning_keys_sync(mirror):
    backend = policy_mod._TUNING_KEYS
    frontend = mirror["TUNING_KEYS"]
    assert set(frontend) == set(backend), (
        f"TUNING_KEYS 区名漂移：仅前端 {sorted(set(frontend) - set(backend))} / "
        f"仅后端 {sorted(set(backend) - set(frontend))}"
    )
    for section in backend:
        fe = set(frontend[section])
        be = set(backend[section])
        assert fe == be, (
            f"TUNING_KEYS.{section} 键漂移：仅前端 {sorted(fe - be)} / 仅后端 {sorted(be - fe)}"
        )
