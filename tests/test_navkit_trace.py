#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S2 trace 记录器单测。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from maaracing_assistant.core.navkit import FrameTrace, TraceWriter, json_safe


def test_frame_trace_has_reconstruction_fields():
    trace = FrameTrace(
        frame=7,
        stage="第1回合出价",
        round_no=1,
        scores={"round_big_banner": 0.91},
        hit_anchor="round_big_banner",
        active_used={"round_big_banner", "smart_bid_btn"},
        intent={"key": "bid_main_red_btn", "center": (0.5, 0.8)},
        click_result={"ok": False, "device_lost": False},
        plan_version="v3",
        timestamp_ms=123,
    )
    data = trace.as_dict()
    assert data["frame"] == 7
    assert data["scores"]["round_big_banner"] == 0.91
    assert sorted(data["active_used"]) == ["round_big_banner", "smart_bid_btn"]
    assert data["intent"]["center"] == [0.5, 0.8]


def test_trace_writer_appends_compact_jsonl(tmp_path: Path):
    with TraceWriter(tmp_path, keep_sessions=10, session_name="session_20260906_080000") as writer:
        writer.write(FrameTrace(frame=1, stage="大厅", round_no=None, timestamp_ms=1))
        writer.write({"frame": 2, "stage": "出价", "scores": {"x": 0.5}})
    path = tmp_path / "session_20260906_080000" / "trace.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["frame"] == 1
    assert json.loads(lines[1])["frame"] == 2


def test_trace_writer_rejects_invalid_session_name(tmp_path: Path):
    with pytest.raises(ValueError):
        TraceWriter(tmp_path, session_name="not-a-session")


def test_trace_prunes_old_sessions(tmp_path: Path):
    for name in ("session_20260901_000000", "session_20260902_000000", "session_20260903_000000"):
        (tmp_path / name).mkdir()
    writer = TraceWriter(tmp_path, keep_sessions=2, session_name="session_20260904_000000")
    removed = writer.prune()
    writer.close()
    assert (tmp_path / "session_20260904_000000").exists()
    assert (tmp_path / "session_20260903_000000").exists()
    assert not (tmp_path / "session_20260901_000000").exists()
    assert len(removed) == 2


def test_json_safe_does_not_leak_paths_or_objects():
    class Box:
        pass

    converted = json_safe({"box": (1, 2), "obj": Box()})
    assert converted["box"] == [1, 2]
    assert isinstance(converted["obj"], str)
