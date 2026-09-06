#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
navkit 检测计划编译器——assets(v3) → DetectionPlan（帧循环消费的唯一检测真源）。

纯标准库：不 import cv2 / numpy / maa / vgamepad。
坐标换算与三段式访问复用 `core.roi_config.ROIConfig`（F1 的处置：不另造底座）。

DetectionPlan 是 `detector.py` 的唯一输入（S1 后 detector 不再直读 treasure_rois.json）：

    plan.stage_order    → 阶段顺序（GUI 断点契约，与 STAGE_ORDER 等值）
    plan.global_anchors → 恒全量锚点（不变量 I-1）
    plan.active[stage]  → 阶段感知清单（取代 _STAGE_PERCEPTION）
    plan.ocr_keys[stage]→ 阶段 OCR keys（取代 _STAGE_OCR_KEYS）
    plan.spec[name]     → AnchorSpec（rect/templates/threshold/scales/arbitration/guarded_by）
    plan.scales         → 唯一尺度表（G3：全仓只出现一处定义）

编译确定性：同输入两次编译产出等值对象；MAA pipeline 的编译另见 compile_route.py。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Mapping

from ..roi_config import ROIConfig
from .assets import ANY_STAGE, Anchor, Assets

__all__ = [
    "ROUND_PHASE_STAGE",
    "AnchorSpec",
    "DetectionPlan",
    "compile_detection",
]

# __round_phase__：出价面板阶段的内部标记（取代 v2 `_ROI_STAGE` 的同名哨兵值）。
# 命中后按 arbitration.round_from_template / _last_round 决定具体「第N回合出价」。
ROUND_PHASE_STAGE = "__round_phase__"


@dataclass(frozen=True)
class AnchorSpec:
    """单锚点的可执行规格（detector 每帧按它匹配）。"""

    name: str
    kind: str
    stage: str | None          # 命中后归属阶段；None=仅作信号（不出现在结果里）
    stage_priority: int        # 检测优先级（v2 _ROI_STAGE.priority 的替代）
    rect: tuple[float, float, float, float]
    templates: tuple[str, ...] = ()
    threshold: float | None = None       # None → 用 plan.default_threshold
    scales: tuple[float, ...] | None = None  # None → 用 plan.scales
    arbitration: Mapping = field(default_factory=dict)
    guarded_by: str | None = None


@dataclass(frozen=True)
class DetectionPlan:
    """编译产物：帧循环的检测真源。"""

    stage_order: tuple[str, ...]
    global_anchors: tuple[str, ...]
    active: Mapping[str, frozenset[str]]       # stage → 本阶段感知锚点
    ocr_keys: Mapping[str, frozenset[str]]     # stage → OCR keys
    spec: Mapping[str, AnchorSpec]             # 锚点名 → 规格（含 ocr 类）
    scales: tuple[float, ...]                  # 唯一尺度表（G3）
    default_threshold: float                   # 唯一默认阈值
    margin_default: float
    detect_anchors: tuple[str, ...]            # 阶段检测锚点（不含 appraiser/actions 独立匹配）
    dynamic_narrow: Mapping[str, str]          # stage → "code:xxx" 指针
    stage_stage: Mapping[str, str]             # 锚点 → 命中阶段（含 ROUND_PHASE_STAGE 哨兵）

    def active_for(self, stage: str | None) -> frozenset[str] | None:
        """当前阶段的激活集；未登记返回 None（运行时回退全量检测，既有安全兜底）。"""
        if stage is None:
            return None
        return self.active.get(stage)

    def ocr_for(self, stage: str | None) -> frozenset[str] | None:
        """当前阶段的 OCR keys；未登记返回 None（运行时语义=全量）。"""
        if stage is None:
            return None
        return self.ocr_keys.get(stage)


# ------------------------------------------------------------------
# 编译
# ------------------------------------------------------------------


def compile_detection(assets: Assets) -> DetectionPlan:
    """assets → DetectionPlan。要求 assets 已通过校验（本函数不做重复校验，
    调用方必须先跑 validate_assets——编译器信任校验器，边界清晰）。"""
    # ROIConfig 承担坐标契约与三段式访问（F1：复用底座）
    rois_payload = {}
    for name, anchor in assets.anchors.items():
        rois_payload[name] = {
            "rect": list(anchor.rect.as_list()),
            "templates": list(anchor.templates),
            "threshold": anchor.threshold,
        }
    stages_payload = {
        "order": list(assets.stage_order),
        "global_anchors": list(assets.global_anchors),
        "definitions": {
            name: {"active_rois": list(d.anchors)} for name, d in assets.stage_defs.items()
        },
    }
    ROIConfig.from_dict(
        {"_schema_ver": 1, "reference_size": list(assets.reference_size),
         "rois": rois_payload, "stages": stages_payload}
    )  # 构造成功即说明坐标契约全量成立（兜底校验，正常路径由 validate 负责）

    # ---- spec：每个锚点的可执行规格 ----
    specs: dict[str, AnchorSpec] = {}
    stage_stage: dict[str, str] = {}

    # transitions 上纸后的派生映射：信号锚点 → 目标阶段（§4.4，transitions 的运行时消费形态）
    for tr in assets.transitions:
        stage_stage.setdefault(tr.on, _transition_target_stage(tr.to))

    for name, anchor in assets.anchors.items():
        arb = anchor.arbitration
        stage = stage_stage.get(name)
        specs[name] = AnchorSpec(
            name=name,
            kind=anchor.kind,
            stage=stage,
            stage_priority=_anchor_priority(anchor, name in set(assets.global_anchors)),
            rect=anchor.rect.as_list(),
            templates=anchor.templates,
            threshold=anchor.threshold,
            scales=anchor.scales,
            arbitration=asdict(arb) if arb is not None else {},
            guarded_by=anchor.guarded_by,
        )

    # ---- active / ocr_keys ----
    active: dict[str, frozenset[str]] = {}
    ocr_keys: dict[str, frozenset[str]] = {}
    dynamic: dict[str, str] = {}
    for stage_name, sd in assets.stage_defs.items():
        active[stage_name] = frozenset(sd.anchors)
        ocr_keys[stage_name] = frozenset(sd.ocr)
        by = sd.dynamic_narrow.get("by")
        if by:
            dynamic[stage_name] = str(by)

    # 回合出价阶段如果 order 里给了但没有 definitions（W05 允许），由 global_anchors 兜底
    active_names = {name for values in active.values() for name in values}
    detect_anchors = tuple(
        name for name, spec in specs.items()
        if spec.kind == "template" and spec.templates
        and (name in active_names or name in assets.global_anchors)
    )

    return DetectionPlan(
        stage_order=tuple(assets.stage_order),
        global_anchors=tuple(assets.global_anchors),
        active=active,
        ocr_keys=ocr_keys,
        spec=specs,
        scales=tuple(assets.match.scales),
        default_threshold=float(assets.match.threshold),
        margin_default=float(assets.match.margin_default),
        detect_anchors=detect_anchors,
        dynamic_narrow=dynamic,
        stage_stage=stage_stage,
    )


def _transition_target_stage(to: str) -> str:
    """transitions.to → 命中后归属阶段。

    `$round` / `same` 表示"命中即进入出价面板"——归属 ROUND_PHASE_STAGE 哨兵，
    由 detector 按回合逻辑实例化具体阶段名（与 v2 `__round_phase__` 语义一致）。
    """
    if to in ("$round", "same"):
        return ROUND_PHASE_STAGE
    return to


def _anchor_priority(anchor: Anchor, is_global: bool) -> int:
    """锚点检测优先级（v2 `_ROI_STAGE.priority` 的替代，数值大者先扫）。

    v2 优先级语义：弹窗 110/105 > 结算 100/90 > 出价面板 80/70 > 进入链 50/60。
    v3 用 `order` 表达，运行时换算为 `1000 - order`（缺省 order=0 → 1000，排最前；
    实际数据里弹窗类均 < 1000，故顺序可控）。

    **全局锚点不参与加权。** "全局"的语义是"每一帧都并入扫描集合"（由调用方
    把 `global_anchors` 并入 active 实现，不变量 I-1），而不是"优先扫描"。
    曾在此给全局锚点 +100，结果大厅锚点被抬到 150 压过弹窗的 110——
    弹窗期间大厅是不可见的，先扫大厅只会让判定偏离 v2 行为。
    """
    del is_global  # 保留形参以表明这里是有意为之，不是漏考虑
    return 1000 - (anchor.order or 0)
