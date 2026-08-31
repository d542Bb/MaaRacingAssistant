#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MRA Python sidecar —— 唯一业务后端（stdin/stdout JSONL RPC）。

通道契约（与 Step 2 契约测试一致）：
    stdin  = JSONL request only
    stdout = JSONL response/event only（协议专用，入口处强制隔离 print）
    stderr = diagnostics/log only

线程模型：
    main thread → stdin reader（逐行读，立即派发，绝不阻塞）
    handler     → 每个 request 独立线程执行；start 只做校验 + 起 worker，立即响应
    response    → stdout 带写锁

方法（JSONL RPC，供 mra_shell.exe 前端调用）：
    get_initial_state / select_module / get_status / start / stop / fetch_logs / close / shutdown
    get_debug_state / set_debug_mode / set_peep / set_capture_backend（调试页）

运行：python -u -m maaracing_assistant.core.sidecar
"""

import json
import os
import sys
import threading
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from pathlib import Path
from typing import TextIO, cast

from maaracing_assistant import __version__
from maaracing_assistant.core.controller import MaaRacingAssistantController
from maaracing_assistant.core.logger import logger
from maaracing_assistant.core.registry import MODULE_REGISTRY, get_module_info
from maaracing_assistant.core.paths import user_data_dir
from maaracing_assistant.core.window_utils import ensure_dpi_aware, has_physical_controller

# 协议转移用 stderr：_StdoutGuard 把误写 stdout 的第三方 print 转移到这里。
# sys.__stderr__ 类型上可为 None，但运行期解释器必有该流；cast 后复用同一引用。
_STDERR = cast(TextIO, sys.__stderr__)

# --------------------------------------------------------------------------
# 用户偏好持久化（profile）：%APPDATA%/MaaRacingAssistant/profile.json
# 只写/读本程序自己管理的键；文件里出现未知类/键一律忽略，绝不因此崩溃。
# --------------------------------------------------------------------------
_PROFILE_FILENAME = "profile.json"
# 默认选中模块 id（仅作 id 引用，不直接 import 插件包；GUI 进入默认展示鉴宝）
_DEFAULT_MODULE_ID = "treasure"
# 本程序目前持久化的模块配置键（treasure 模块）——回填时只取这些，其余忽略。
_MODULE_CONFIG_KEYS = ("max_daily_loops", "target_session", "treasure_risk_cap", "treasure_mode")


def _profile_path() -> Path:
    """profile 文件路径：与数据库同目录（%APPDATA%/MaaRacingAssistant/ 下）。"""
    return user_data_dir() / _PROFILE_FILENAME


def _load_profile() -> dict:
    """安全读取 profile：文件缺失/损坏/非 dict/IO 异常，一律返回空 dict，不抛异常。"""
    try:
        with open(_profile_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 —— 容错：读不了就当作无偏好
        return {}


def _save_profile(partial: dict) -> None:
    """原子写 profile：仅更新 partial 提供的段，其余段（含未知键）原样保留。

    写失败不抛异常（只记警告），避免干扰主流程。
    """
    try:
        merged = _load_profile()            # 先并合再写，避免覆盖其它写入的数据
        merged.update(partial)
        path = _profile_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
        tmp.replace(path)                   # 原子替换，防半写文件
    except Exception as exc:  # noqa: BLE001
        logger.log(f"[sidecar] 偏好落盘失败: {exc!r}", "WARNING")


class _StdoutGuard:
    """把一切误写 stdout 的输出（第三方库 print 等）转移到 stderr，保证协议通道纯净。"""

    def __init__(self, protocol_stdout):
        self._protocol = protocol_stdout

    def write(self, s: str) -> None:
        _STDERR.write(s)
        _STDERR.flush()

    def flush(self) -> None:
        _STDERR.flush()


class SidecarService:
    """业务 dispatch 层：线程安全（复用 bridge.py 的 _lock/_worker 语义）。"""

    def __init__(self, protocol_stdout):
        self._out = protocol_stdout
        self._out_lock = threading.Lock()
        self._controller = MaaRacingAssistantController()
        self._lock = threading.RLock()
        self._worker = None  # 非 None = start slot 已占用（互斥依据，与 bridge.py 一致）
        # 默认选中鉴宝模块（GUI 进入即默认展示鉴宝；未注册时回退到第一个已注册模块）。
        # 以模块 id 常量引用，避免 sidecar 耦合具体插件包。
        self._selected_module = (
            _DEFAULT_MODULE_ID if _DEFAULT_MODULE_ID in MODULE_REGISTRY
            else (next(iter(MODULE_REGISTRY)) if MODULE_REGISTRY else None)
        )
        try:
            self._stages = get_module_info(self._selected_module)["stages"] if self._selected_module else []
        except KeyError:
            self._stages = []
        self._last_log_count = 0
        self._closed = False
        # 启动即回填上次会话的用户偏好（模块配置缓存 + 调试开关）。
        self._restore_profile()

    # ---------- 协议输出 ----------

    def send(self, obj: dict) -> None:
        line = json.dumps(obj, ensure_ascii=False)
        with self._out_lock:
            self._out.write(line + "\n")
            self._out.flush()

    def _respond(self, rid, ok: bool, data=None, error=None) -> None:
        self.send({"type": "response", "id": rid, "ok": ok, "data": data, "error": error})

    # ---------- dispatch ----------

    def run(self) -> None:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError:
                print(f"[sidecar] malformed stdin line ignored: {line!r}", file=sys.stderr)
                continue
            rid = req.get("id")
            method = req.get("method")
            params = req.get("params") or {}
            # 每个 request 独立 handler 线程：stdin reader 永不阻塞。
            # 非 daemon：stdin EOF 后仍等所有在途请求写完响应再退出（shutdown 响应不能丢）。
            threading.Thread(
                target=self._dispatch, args=(method, params, rid)
            ).start()

    def _dispatch(self, method, params, rid) -> None:
        is_shutdown = method == "shutdown"
        try:
            handler = getattr(self, method, None)
            if handler is None:
                self._respond(rid, False, None, f"unknown method {method}")
                return
            ok, data, error = handler(params)
            self._respond(rid, ok, data, error)
        except Exception as exc:  # noqa: BLE001 —— 任何异常如实回给 shell
            logger.log(f"sidecar dispatch 异常: {exc}", "ERROR")
            self._respond(rid, False, None, repr(exc))
        finally:
            if is_shutdown:
                os._exit(0)  # 响应已发出，立即退出（worker 线程中 sys.exit 无效）

    # ---------- 业务方法（返回 (ok, data, error)） ----------

    def _restore_profile(self) -> None:
        """启动回填上次会话偏好：只取本程序认识的键，未知/非法内容一律忽略。

        - module_config → 并入 _cached_module_config（下次 start 自动注入新实例）；
        - debug 段 → 直接恢复 controller 的调试/peep 开关状态。
        """
        data = _load_profile()
        if not data:
            return
        # 1) 调试开关（容错：仅按 bool 值恢复，其它类型忽略）
        dbg = data.get("debug")
        if isinstance(dbg, dict):
            dm = dbg.get("debug_mode")
            if isinstance(dm, bool):
                self._controller.set_debug_mode(dm)
            peep = dbg.get("peep_enabled")
            if isinstance(peep, bool):
                debug = self._controller.debug
                debug.enable_peep() if peep else debug.disable_peep()
            acg = dbg.get("auto_close_game")
            aem = dbg.get("auto_exit_mra")
            if isinstance(acg, bool) or isinstance(aem, bool):
                # 运行结束自动关闭开关（GUI「运行结束后」卡片），单独恢复不串扰
                self._controller.set_auto_shutdown(
                    close_game=acg if isinstance(acg, bool) else None,
                    exit_mra=aem if isinstance(aem, bool) else None,
                )
            cm = dbg.get("click_mode")
            if isinstance(cm, str):
                try:
                    self._controller.set_click_mode(cm)
                except ValueError:
                    pass  # 非法值忽略，保持默认
            mg = dbg.get("mute_game")
            if isinstance(mg, bool):
                self._controller.set_mute_game(mg)
        # 2) 模块配置（flat dict，含 module_id）→ 只取本程序管理的键，其余未知键忽略
        mc = data.get("module_config")
        if isinstance(mc, dict):
            cache = {k: mc[k] for k in _MODULE_CONFIG_KEYS if k in mc}
            if cache:
                mid = mc.get("module_id")
                if not (isinstance(mid, str) and mid in MODULE_REGISTRY):
                    mid = self._selected_module
                cache["module_id"] = mid
                self._cached_module_config = cache

    def get_initial_state(self, params):
        return (True, {
            "version": __version__.split("+")[0],
            "modules": self._module_list(),
            "selected_module": self._selected_module,
            "stages": list(self._stages),
            "model_ok": self._controller.check_model(),
            "is_running": self._controller.module_active,
        }, None)

    def _module_list(self) -> list:
        return [
            {
                "id": mid,
                "name": info["name"],
                "stages": info["stages"],
                "requires_gamepad_exclusive": info["requires_gamepad_exclusive"],
            }
            for mid, info in (
                (mid, get_module_info(mid)) for mid in MODULE_REGISTRY
            )
        ]

    def select_module(self, params):
        module_id = params.get("module_id")
        try:
            info = get_module_info(module_id)
        except KeyError:
            return (False, None, f"模块不存在: {module_id}")
        with self._lock:
            self._selected_module = module_id
            self._stages = info["stages"]
        logger.log(f"活动模块已切换: {module_id}")
        return (True, {"stages": info["stages"], "module_id": module_id}, None)

    def get_status(self, params):
        with self._lock:
            worker = self._worker
            selected = self._selected_module
        return (True, {
            "is_running": self._controller.module_active,
            "current_stage": self._controller.current_stage,
            "worker_active": worker is not None and worker.is_alive(),
            "selected_module": selected,
        }, None)

    # ---------- 活动模块配置（当前 GUI 用 treasure：每日循环上限；接口为通用 module_config 路由）----------

    def _route_module_config(self):
        """路由到「当前选中模块实例」或「同 id 新建的离线索实例」读 module_config（只读）。

        为什么"未运行时也需要可读"：GUI 未启动时回显模块默认配置 + sidecar 缓存。
          - 活动模块已在跑（controller.active_module 非空）→ 读运行实例（含 _state 实况）；
          - 没在跑 → 用 create_module(module_id, ctx=None) 离线建临时实例读默认值（不保存状态）。
        没定义 get_module_config 的模块（如 racing）返回 None，RPC 层兜底为占位 dict。
        配置写入统一走 set_module_config 写缓存（下次 start 注入），不做运行中热更新。
        """
        with self._lock:
            module_id = self._selected_module
        instance = None
        # 优先用运行中的实例（读实时值）
        if self._controller.active_module is not None and getattr(
            self._controller.active_module, "ID", None
        ) == module_id:
            instance = self._controller.active_module
        if instance is None:
            # 离线模式：ctx=None → create_module/ActivityModule 基类允许（见 treasure_module __init__）
            try:
                from maaracing_assistant.core.registry import create_module as _create
                instance = _create(module_id, None)
            except Exception as exc:
                logger.log(f"[sidecar] 离线建模块{module_id!r}读配置失败: {exc}", "DEBUG")
                instance = None
        if instance is None:
            return None
        getter = getattr(instance, "get_module_config", None)
        return getter() if callable(getter) else None

    def get_module_config(self, params):
        module_id = params.get("module_id") or self._selected_module
        try:
            result = self._route_module_config()
        except Exception as exc:  # noqa: BLE001
            return (False, None, f"读模块配置失败: {exc!r}")
        # result 可能 None（老模块无接口）——返回空 dict + 支持的最小字段，前端不崩。
        if result is None:
            result = {}
        # 运行中：实例权威（含 _state 实况）；未运行：缓存优先（GUI 改过且还没 start 的值）
        live = self._controller.active_module is not None and getattr(
            self._controller.active_module, "ID", None
        ) == module_id
        if not live:
            cache = getattr(self, "_cached_module_config", None) or {}
            if cache.get("module_id") == module_id:
                result.update({k: v for k, v in cache.items() if k != "module_id"})
        result.setdefault("module_id", module_id)
        return (True, result, None)

    def set_module_config(self, params):
        config = params.get("config") or {}
        module_id = params.get("module_id") or self._selected_module
        try:
            # 只写缓存（下次 start 生效），不做运行中热更新：
            # 运行中的模块 GUI 已锁定不可改，配置一律下次「开始」时注入新实例。
            with self._lock:
                old_cache = getattr(self, "_cached_module_config", None) or {}
                # 只在 target_module_id 匹配时才复用旧缓存（跨模块切换不该带旧缓存）
                target = old_cache if old_cache.get("module_id") == module_id else {"module_id": module_id}
                target.update({k: v for k, v in config.items() if k != "module_id"})
                self._cached_module_config = target
        except Exception as exc:  # noqa: BLE001
            return (False, None, f"写模块配置失败: {exc!r}")
        # 返回最新读值（缓存 + 若实例还能回读也可回读；简化就直接回缓存+配置合并视图）
        merged = dict(getattr(self, "_cached_module_config", None) or {"module_id": module_id})
        # 写盘持久化（含 module_id），下次启动由 _restore_profile 回填；失败仅记警告。
        _save_profile({"module_config": dict(self._cached_module_config)})
        return (True, merged, None)

    def start(self, params):
        """快速响应 + worker 线程跑 controller；stop/get_status 在运行期间必须仍可处理。"""
        start_from = params.get("start_from")
        with self._lock:
            module_id = self._selected_module
            stages = list(self._stages)
            # 组装：start_params.module_config（本次 start 带参）∪ _cached_module_config（GUI 先改后 start 的缓存）
            param_cfg = params.get("module_config") if isinstance(params.get("module_config"), dict) else {}
            cache_cfg = (getattr(self, "_cached_module_config", None) or {})
            # 缓存 key 的 module_id 匹配才生效（防止模块切了，老模块缓存污染新模块）
            if cache_cfg.get("module_id") not in (None, module_id):
                cache_cfg = {}
            merged_cfg: dict = {}
            merged_cfg.update({k: v for k, v in cache_cfg.items() if k != "module_id"})
            merged_cfg.update({k: v for k, v in param_cfg.items()})
            start_module_config = merged_cfg or None
            # 保存最后一次合并结果（用于下一次 get_module_config 回读一致）
            if start_module_config:
                target = {"module_id": module_id}
                target.update(start_module_config)
                self._cached_module_config = target
        if module_id is None:
            return (False, None, "未选择活动模块")
        if start_from is not None and start_from not in stages:
            return (False, None, f"断点 {start_from} 不属于模块 {module_id} 的阶段")
        try:
            info = get_module_info(module_id)
        except KeyError:
            return (False, None, f"模块不存在: {module_id}")

        if info["requires_gamepad_exclusive"] and has_physical_controller():
            return (False, None, "请断开所有物理手柄后再运行")

        # 仅对申明了 onnx 能力（如 racing 的 YOLO）的模块校验本地模型存在；
        # 鉴宝等无需模型的模块不再被无条件拦截。
        if "onnx" in info["requires"] and not self._controller.check_model():
            return (False, None, "模型未找到，请检查 assets/model/model.onnx")

        # 启动阶段检测：依赖虚拟手柄的模块（racing）在 ViGEmBus 驱动缺失时提前拦下，
        # 返回结构化错误码 VIGEM_BUS_MISSING，供前端弹「下载并安装 ViGEmBus 驱动」引导。
        if "gamepad" in info["requires"] and not self._controller.gamepad_available():
            return (False, None, (
                "VIGEM_BUS_MISSING: 检测到缺少 ViGEmBus 驱动，无法创建虚拟手柄。"
                "请先点击「下载并安装 ViGEmBus 驱动」完成安装后重试。"
            ))

        with self._lock:
            if self._closed:
                return (False, None, "正在关闭")
            if self._worker is not None:  # slot 已占用，不以 is_alive() 判断
                return (False, None, "已在运行")
            worker = threading.Thread(
                target=self._worker_main,
                args=(module_id, start_from, start_module_config),
                daemon=True,
                name="module-worker",
            )
            self._worker = worker
            worker.start()

        if start_from is not None and start_from != (stages[0] if stages else None):
            logger.log(f"断点模式: 从「{start_from}」开始运行")
        return (True, None, None)

    def _worker_main(self, module_id: str, start_from, module_config: dict | None) -> None:
        try:
            # 模块配置注入：在 start_module 内部 create_module 之后、module.start() 之前调用 set_module_config
            self._controller.start_module(module_id, start_from, module_config=module_config)
        except Exception as exc:
            logger.log(f"运行异常: {exc}", "ERROR")
        finally:
            # 运行结束自动退出程序：与「关闭游戏」同源判定（last_run_natural），
            # 仅"正常完成"生效，报错/手动停止不触发。发 auto_exit 事件 → shell 关闭窗口优雅退出。
            if self._controller.last_run_natural and self._controller.auto_exit_mra:
                self.send({"type": "event", "event": "auto_exit"})
            with self._lock:
                if self._worker is threading.current_thread():
                    self._worker = None

    def stop(self, params):
        with self._lock:
            if self._closed:
                return (True, None, None)
            worker = self._worker
        if worker is not None and worker.is_alive():
            self._controller.stop()
        return (True, None, None)

    def open_vigembus_download(self, params):
        """在用户默认浏览器打开 ViGEmBus 官方下载页，引导安装驱动。

        训练/运行必需的内核驱动无法随包 app-local 分发，只能由用户手动安装一次。
        返回是否已打开发布页（浏览器需保持默认配置）。
        """
        import webbrowser
        url = params.get("url") or "https://github.com/nefarius/ViGEmBus/releases/latest"
        try:
            webbrowser.open(url)
            return (True, {"opened": url}, None)
        except Exception as exc:  # noqa: BLE001
            return (False, None, f"打开 ViGEmBus 下载页失败: {exc}")

    def open_external_url(self, params):
        """在用户默认浏览器打开外部链接（关于页跳转：项目主页 / 报告问题 / 使用文档）。"""
        import webbrowser
        url = params.get("url") or ""
        if not url.lower().startswith(("http://", "https://")):
            return (False, None, "仅支持 http(s) 外部链接")
        try:
            webbrowser.open(url)
            return (True, {"opened": url}, None)
        except Exception as exc:  # noqa: BLE001
            return (False, None, f"打开链接失败: {exc}")

    def open_user_data_folder(self, params):
        """用资源管理器打开用户数据目录（%APPDATA%/MaaRacingAssistant）。

        目录不存在时自动创建，避免首次运行即报错。os.startfile 是 Windows
        原生调起关联程序的方式，比 subprocess.Popen(['explorer', path]) 更稳。
        """
        path = user_data_dir()
        try:
            path.mkdir(parents=True, exist_ok=True)
            os.startfile(str(path))  # type: ignore[attr-defined]  # Windows 特有
            return (True, {"path": str(path)}, None)
        except Exception as exc:  # noqa: BLE001
            return (False, None, f"打开文件夹失败: {exc}")

    # ---------- 关于页：检查更新 / 公告 ----------

    _GITHUB_REPO = "d542Bb/MaaRacingAssistant"
    _RELEASE_URL = f"https://api.github.com/repos/{_GITHUB_REPO}/releases/latest"
    _ANNOUNCEMENT_URLS = (
        f"https://raw.githubusercontent.com/{_GITHUB_REPO}/master/docs/announcement.json",
        f"https://cdn.jsdelivr.net/gh/{_GITHUB_REPO}@master/docs/announcement.json",
    )
    _NET_TIMEOUT = 4.0  # 秒（手动触发可接受）

    @staticmethod
    def _http_get(url: str) -> str:
        """带 UA 的 GET，超时返回空串；不抛网络异常（由调用方处理结果断言）。"""
        req = Request(url, headers={"User-Agent": f"MaaRacingAssistant/{__version__}"})
        with urlopen(req, timeout=SidecarService._NET_TIMEOUT) as resp:
            return resp.read().decode("utf-8")

    def check_update(self, params):
        """查最新 release 与当前版本比较。仅手动触发（GUI 按钮）。"""
        try:
            body = self._http_get(self._RELEASE_URL)
        except HTTPError as exc:
            return (True, {"has_update": False, "error": None, "status": "no_release" if exc.code == 404 else "network"}, None)
        except (URLError, TimeoutError, OSError):
            return (True, {"has_update": False, "error": "无法连接到更新服务器，请稍后再试", "status": "network"}, None)
        try:
            rel = json.loads(body)
            latest = str(rel.get("tag_name", "")).lstrip("v")
            published = rel.get("published_at", "")[:10]
        except (ValueError, TypeError):
            return (True, {"has_update": False, "error": "更新服务器返回异常数据", "status": "parse"}, None)

        cur = ".".join(str(x) for x in self._version_tuple())
        has_update = self._compare_versions(latest, cur) > 0
        return (True, {
            "has_update": has_update,
            "latest_tag": latest,
            "published_at": published,
            "download_url": f"https://github.com/{self._GITHUB_REPO}/releases/latest",
            "error": None,
            "status": "ok",
        }, None)

    def fetch_announcement(self, params):
        """拉取公告（主源 raw + 回退 jsdelivr）。过期返回空；数据异常返回空。"""
        for url in self._ANNOUNCEMENT_URLS:
            try:
                body = self._http_get(url)
                data = json.loads(body)
            except (HTTPError, URLError, TimeoutError, OSError, ValueError, TypeError):
                continue
            if not isinstance(data, dict):
                continue
            until = str(data.get("effective_until", "")).strip()
            if until and until < datetime.now().strftime("%Y-%m-%d"):
                continue  # 过期公告，跳过显示
            return (True, {
                "title": str(data.get("title", "")),
                "body": str(data.get("body", "")),
                "level": str(data.get("level", "info")),
                "date": str(data.get("date", "")),
                "url": str(data.get("url", "")) or "",
                "url_text": str(data.get("url_text", "查看详情")) or "查看详情",
            }, None)
        return (True, {"title": "", "body": "", "level": "none", "date": "", "url": "", "url_text": ""}, None)

    @staticmethod
    def _version_tuple() -> tuple:
        """当前版本 → (主, 次, 修) 元组；dev 后缀取基线。异常回退 (0,0,0)。"""
        import re
        raw = __version__.split("+")[0].split("-")[0]
        m = re.match(r"(\d+)\.(\d+)\.(\d+)", raw)
        if not m:
            return (0, 0, 0)
        return tuple(int(x) for x in m.groups())

    @staticmethod
    def _compare_versions(a: str, b: str) -> int:
        """semver 三段比较，返回 a-b 的符号（>0 表示 a 较新）。非数字段按 0 处理。"""
        import re
        def key(s):
            nums = re.findall(r"\d+", s) or ["0"]
            return tuple(int(x) for x in nums[:3] + ["0"] * (3 - len(nums)))
        ka, kb = key(a), key(b)
        return (ka > kb) - (ka < kb)

    def fetch_logs(self, params):
        lines = logger.get_lines()
        with self._lock:
            start = min(self._last_log_count, len(lines))
            result = lines[start:]
            self._last_log_count = len(lines)
        return (True, {"lines": result}, None)

    def get_today_stats(self, params):
        """读取鉴宝落盘库（data/treasure/treasure.db）今日统计数据（凌晨 5 点日界，与落盘一致）。

        返回 {"bucket": 日界, "summary": daily_summary 今日行或 None, "games": 今日各场明细列表}。
        库不存在/读取失败时 summary=None、games=[]（不抛错，前端显示空看板）。
        """
        from datetime import datetime, timedelta
        import sqlite3

        now = datetime.now()
        day = now.date() if now.hour >= 5 else now.date() - timedelta(days=1)
        bucket = day.isoformat()
        db_path = user_data_dir() / "treasure" / "treasure.db"
        if not db_path.exists():
            return (True, {"bucket": bucket, "summary": None, "games": []}, None)
        try:
            conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
            try:
                row = conn.execute(
                    "SELECT games, win, fail, profit_sum, income_sum, highest_score,"
                    " egg_red, egg_yellow, egg_blue FROM daily_summary WHERE bucket = ?",
                    (bucket,),
                ).fetchone()
                summary = None
                if row is not None:
                    summary = {
                        "games": row[0], "win": row[1], "fail": row[2],
                        "profit_sum": row[3], "income_sum": row[4], "highest_score": row[5],
                        "egg_red": row[6], "egg_yellow": row[7], "egg_blue": row[8],
                    }
                games = [
                    {
                        "game_seq": r[0], "ts": r[1], "auction_result": r[2],
                        "final_price": r[3], "total_price": r[4],
                        "profit": r[5], "income": r[6],
                        "egg_red": r[7], "egg_yellow": r[8], "egg_blue": r[9],
                        "strategy_mode": r[10],
                    }
                    for r in conn.execute(
                        "SELECT game_seq, ts, auction_result, settle_final_price,"
                        " settle_total_price, settle_profit, settle_my_income,"
                        " egg_red, egg_yellow, egg_blue, strategy_mode"
                        " FROM games WHERE bucket = ? ORDER BY game_seq",
                        (bucket,),
                    ).fetchall()
                ]
            finally:
                conn.close()
            return (True, {"bucket": bucket, "summary": summary, "games": games}, None)
        except Exception as e:
            logger.log(f"读取今日看板数据失败: {e}", "WARNING")
            return (True, {"bucket": bucket, "summary": None, "games": []}, None)

    # ---------- 调试页 ----------

    def get_debug_state(self, params):
        debug = self._controller.debug
        return (True, {
            "debug_mode": bool(getattr(self._controller, "_debug_mode", False)),
            "peep_enabled": bool(debug.peep_enabled),
            "capture_backend": self._controller._capture_backend,
            "click_mode": self._controller.click_mode,
            "emergency_stop_enabled": bool(getattr(self._controller, "_emergency_stop_enabled", False)),
            "file_logging": logger.file_logging,
            "auto_close_game": bool(self._controller.auto_close_game),
            "auto_exit_mra": bool(self._controller.auto_exit_mra),
            "mute_game": bool(self._controller.mute_game_enabled),
        }, None)

    def set_debug_mode(self, params):
        enabled = bool(params.get("enabled", False))
        self._controller.set_debug_mode(enabled)
        cur = _load_profile().get("debug")
        cur = cur if isinstance(cur, dict) else {}
        cur["debug_mode"] = bool(enabled)
        _save_profile({"debug": cur})
        logger.log(f"调试截图模式: {'开启' if enabled else '关闭'}")
        return (True, {"debug_mode": enabled}, None)

    def set_file_logging(self, params):
        """日志记录开关：开启后才把日志写入磁盘（user_data_dir/logs，每次开启新建文件）。"""
        enabled = bool(params.get("enabled", False))
        logger.set_file_logging(enabled)
        cur = _load_profile().get("debug")
        cur = cur if isinstance(cur, dict) else {}
        cur["file_logging"] = bool(enabled)
        _save_profile({"debug": cur})
        logger.log(f"日志记录(写盘): {'开启' if enabled else '关闭'}")
        return (True, {"file_logging": enabled}, None)

    def set_peep(self, params):
        enabled = bool(params.get("enabled", False))
        if enabled:
            self._controller.debug.enable_peep()
        else:
            self._controller.debug.disable_peep()
        cur = _load_profile().get("debug")
        cur = cur if isinstance(cur, dict) else {}
        cur["peep_enabled"] = bool(enabled)
        _save_profile({"debug": cur})
        logger.log(f"PEEP 实时预览: {'开启' if enabled else '关闭'}")
        return (True, {"peep_enabled": enabled}, None)

    def get_peep_frame(self, params):
        """取最新 PEEP 预览帧（JPEG base64），供前端数据页内嵌显示。未开启/无帧时返回 frame=None。"""
        import base64
        data = self._controller.debug.get_peep_jpeg()
        if data is None:
            return (True, {"frame": None}, None)
        return (True, {"frame": base64.b64encode(data).decode("ascii")}, None)

    def set_emergency_stop(self, params):
        enabled = bool(params.get("enabled", False))
        self._controller.set_emergency_stop(enabled)
        return (True, {"emergency_stop_enabled": enabled}, None)

    def set_capture_backend(self, params):
        backend = params.get("backend", "wgc_latest")
        self._controller._capture_backend = backend
        logger.log(f"截图方式: {backend}")
        return (True, {"capture_backend": backend}, None)

    def set_click_mode(self, params):
        """切换点击方式：intent(意图显示) / real(真实点击) / background(后台点击)。

        模式持久化到 profile（debug.click_mode），下次启动由 _restore_profile 回填。
        """
        mode = params.get("mode", "real")
        try:
            self._controller.set_click_mode(mode)
        except ValueError as e:
            return (False, None, str(e))
        cur = _load_profile().get("debug")
        cur = cur if isinstance(cur, dict) else {}
        cur["click_mode"] = mode
        _save_profile({"debug": cur})
        logger.log(f"点击方式: {mode}")
        return (True, {"click_mode": mode}, None)

    def set_auto_close_game(self, params):
        enabled = bool(params.get("enabled", False))
        self._controller.set_auto_shutdown(close_game=enabled)
        cur = _load_profile().get("debug")
        cur = cur if isinstance(cur, dict) else {}
        cur["auto_close_game"] = enabled
        _save_profile({"debug": cur})
        logger.log(f"运行结束后自动关闭游戏: {'开启' if enabled else '关闭'}")
        return (True, {"auto_close_game": enabled}, None)

    def set_auto_exit_mra(self, params):
        enabled = bool(params.get("enabled", False))
        self._controller.set_auto_shutdown(exit_mra=enabled)
        cur = _load_profile().get("debug")
        cur = cur if isinstance(cur, dict) else {}
        cur["auto_exit_mra"] = enabled
        _save_profile({"debug": cur})
        logger.log(f"运行结束后自动退出程序: {'开启' if enabled else '关闭'}")
        return (True, {"auto_exit_mra": enabled}, None)

    def set_mute_game(self, params):
        """「运行选项」运行时静音游戏开关：运行期间静音游戏，结束后恢复 100%。

        静音/恢复动作由 controller.start_module 的 finally 统一执行（任何停止路径都恢复）；
        此处只改开关并持久化到 profile（debug.mute_game）。
        """
        enabled = bool(params.get("enabled", False))
        self._controller.set_mute_game(enabled)
        cur = _load_profile().get("debug")
        cur = cur if isinstance(cur, dict) else {}
        cur["mute_game"] = enabled
        _save_profile({"debug": cur})
        logger.log(f"运行时静音游戏: {'开启' if enabled else '关闭'}")
        return (True, {"mute_game": enabled}, None)

    def close(self, params):
        """shell 关闭前的业务清理：置 _closed + 停止 worker。"""
        with self._lock:
            self._closed = True
            worker = self._worker
        if worker is not None and worker.is_alive():
            self._controller.stop()
        logger.log("sidecar 业务已停止", "DEBUG")
        return (True, None, None)

    def shutdown(self, params):
        """graceful shutdown：停止业务，响应后由 _dispatch 退出进程。"""
        return self.close(params)


def main() -> None:
    # DPI awareness 须最早设置：进程级语义，不继承 C# shell 配置（坐标换算前提）
    ensure_dpi_aware()
    # 日志写盘开关回读（profile.debug.file_logging，默认关）：须在 SidecarService
    # 构造前应用，让启动初期的日志也遵守开关状态
    _dbg = _load_profile().get("debug")
    logger.set_file_logging(bool(isinstance(_dbg, dict) and _dbg.get("file_logging", False)))
    protocol_stdout = sys.stdout
    sys.stdout = _StdoutGuard(protocol_stdout)  # 后续一切 print 都走 stderr，协议通道纯净
    try:
        service = SidecarService(protocol_stdout)
    except Exception as exc:
        print(f"[sidecar] 初始化失败: {exc}", file=sys.stderr)
        _STDERR.flush()
        os._exit(1)
    service.run()


if __name__ == "__main__":
    main()
