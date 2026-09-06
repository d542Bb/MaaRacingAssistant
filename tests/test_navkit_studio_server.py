# -*- coding: utf-8 -*-
"""DebugStudio server（P3 整合）集成单测。

验证通用 server 用 core + adapters/treasure 装配后：
- 通用端点（list_sessions / list_templates / template_status / rois / 静态）可用；
- treasure 领域端点（ocr_recognize / eggs_recognize）已通过 adapter 注册到 extra_handlers。
不启动真实进程，直接构造 StudioState + Handler 做内存级验证（避免依赖真实截图目录）。
"""
from __future__ import annotations

import json

import pytest

from tools.navkit.server import (
    Handler,
    StudioState,
    build_state,
    ensure_rois,
    load_rois,
)


@pytest.fixture
def state():
    """构造 treasure adapter 装配的 StudioState（不落盘到真实 ROIS_FILE，改用 tmp）。"""
    dup = build_state("treasure")
    return dup


class TestStudioBuild:
    def test_build_treasure(self, state):
        assert state.defs.name == "treasure"
        assert "stage" in state.defs.categories
        assert state.session_browser is not None

    def test_adapter_registers_ocr_eggs_endpoints(self, state):
        # adapter 注册了领域端点 → server 能转发
        assert "/api/ocr_recognize" in state.extra_handlers["POST"]
        assert "/api/eggs_recognize" in state.extra_handlers["POST"]

    def test_default_categories(self, state):
        assert state.defs.categories == ("stage", "actions", "ocr", "appraisers", "eggs")

    def test_unsupported_module_raises(self):
        with pytest.raises(ValueError):
            build_state("racing")  # racing adapter 尚未实现

    def test_static_dir_exists(self, state):
        assert (state.static_dir / "index.html").is_file()
        assert (state.static_dir / "app.js").is_file()
        assert (state.static_dir / "style.css").is_file()

    def test_state_module_name_derived(self, state):
        # 资产/路径派生的统一模块名（adapter 模块名末段），供 assets 路径与
        # from_document(module=...) 共用，避免散落硬编码
        assert state.module_name == "treasure"

    def test_assets_path_for_module(self):
        # 资产真源路径按模块名派生：目录与文件名都跟随模块，
        # 防止未来 racing 接入时写出 treasure_assets.json 的错位文件
        from tools.navkit.server import assets_path_for
        p = assets_path_for("treasure")
        assert p.name == "treasure_assets.json"
        assert p.parent.name == "config"
        q = assets_path_for("racing")
        assert q.name == "racing_assets.json"
        assert q.parent.name == "config"
        assert "racing" in q.parts


class TestEnsureLoadRois:
    def test_ensure_then_load_roundtrip(self, state, tmp_path):
        # 改用 tmp 目录的 ROI 文件，避免污染真实 treasure_rois.json
        state.rois_file = tmp_path / "rois.json"
        data = ensure_rois(state)
        assert isinstance(data, dict)
        assert data.get("_schema_ver") == 2
        loaded = load_rois(state)
        assert loaded == data

    def test_ensure_fills_default_categories(self, state, tmp_path):
        state.rois_file = tmp_path / "rois.json"
        data = ensure_rois(state)
        for cat in ("stage", "actions", "ocr", "appraisers", "eggs"):
            assert isinstance(data.get(cat), dict)
        # 缺省 actions/appraisers 被补填
        assert "bid_confirm_red_btn" in data["actions"]
        assert "appraiser_p1_caroline" in data["appraisers"]

    def test_ensure_rois_skips_rewrite_when_complete(self, state, tmp_path):
        # ROI 文件已是完整配置时，ensure_rois 不得重写落盘（启动不应有写副作用）：
        # 落点是运行时真源（git 跟踪），每次启动都重写会制造无意义的脏文件
        import time
        from tools.navkit.adapters import treasure as ta
        f = tmp_path / "rois.json"
        data = {"_schema_ver": 2, "reference_size": [1280, 720],
                **{c: {} for c in ta.CATEGORIES}}
        for cat, items in ta.DEFAULT_ITEMS.items():
            data[cat].update(json.loads(json.dumps(items)))
        f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        state.rois_file = f
        mtime_before = f.stat().st_mtime_ns
        time.sleep(0.01)
        ensure_rois(state)
        assert f.stat().st_mtime_ns == mtime_before


class TestHandlerTemplateStatus:
    def test_template_status_structure(self, state, tmp_path):
        state.rois_file = tmp_path / "rois.json"
        ensure_rois(state)
        Handler.state = state
        h = Handler.__new__(Handler)
        res = h._template_status()  # 返回 dict（由 do_GET 负责发送）
        assert "listed" in res
        assert "referenced" in res
        assert "unassigned" in res
        assert "dangling" in res