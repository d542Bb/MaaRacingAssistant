#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NavKit 核心包（模块开发模式统一计划 · P3）。

把「会话/文件白名单 / ROI 类目 / 帧模板读取 / 渲染预览」等**与内容无关**的骨架，
从 existsing NavKit 控制台 抽成可复用核心，供各模块 adapter 认领各自的类目。
不包含任何 treasure / racing 领域内容（OCR、出价、车道等都在 adapter 侧）。
"""