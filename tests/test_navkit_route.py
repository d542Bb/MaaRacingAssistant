#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S3 路由编译单测。"""
from __future__ import annotations

import json
from pathlib import Path

from maaracing_assistant.core.navkit import Assets, compile_routes, compile_routes_json

ASSETS = Path(__file__).resolve().parents[1] / "maaracing_assistant/plugins/treasure/resources/config/treasure_assets.json"
IMAGE = Path(__file__).resolve().parents[1] / "maaracing_assistant/plugins/treasure/resources/image"


def load_assets():
    return Assets.load(ASSETS, module="treasure", image_dirs=(IMAGE,))


def test_routes_are_deterministic():
    assets = load_assets()
    assert compile_routes_json(assets) == compile_routes_json(assets)


def test_hall_route_has_confirm_chain_and_terminal_nodes():
    data = compile_routes(load_assets())
    nodes = {k: v for k, v in data.items() if not k.startswith("_")}
    start = "treasure::hall_to_treasure::0::hall_peak_appraise_card"
    second = "treasure::hall_to_treasure::1::goto_appraise_btn"
    terminal = "treasure::hall_to_treasure::1::hall_session_cards::confirm"
    assert start in nodes
    assert second in nodes
    assert nodes[start]["next"] == [second]
    assert nodes[second]["next"] == [terminal]
    assert nodes[terminal]["action"] == "DoNothing"
    assert nodes[terminal].get("next", []) == []


def test_point_route_uses_guarded_fallback():
    data = compile_routes(load_assets())
    name = "treasure::session_to_matching::0::session_start_match_click"
    node = data[name]
    param = node["custom_recognition_param"]
    assert node["custom_action"] == "MRA_Click"
    assert param["templates"] == []
    assert "fallback_pct" in param
    assert param["guarded_by"] == "hall_session_cards"


def test_generated_header_is_auditable():
    assets = load_assets()
    data = compile_routes(assets)
    assert data["_generated"] is True
    assert data["source_hash"] == assets.source_hash
