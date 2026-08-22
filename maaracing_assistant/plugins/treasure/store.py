# -*- coding: utf-8 -*-
"""鉴宝落盘子域：结构化落盘（SQLite：games 明细 + daily_summary 当日汇总）与会话总结。

TreasureStore 持有模块实例引用（与 treasure_renderer 持 debug 引用同模式），
把落盘/DB 职责从 TreasureModule 移出，让模块本体保持「状态机 + 编排」。

日界约定：凌晨 5 点为界（与 module._refresh_daily_bucket 一致），
落盘失败仅告警，不阻断自动化决策（记录是附加功能）。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

from maaracing_assistant.core.logger import logger


class TreasureStore:
    """鉴宝落盘存储：DB 连接管理 + 场次/当日汇总写入 + 会话总结。"""

    def __init__(self, module):
        """module: TreasureModule 实例（状态机与落盘字段的所有者）。"""
        self._m = module

    # ---------- 日界 ----------

    def current_bucket_str(self, now: datetime | None = None) -> str:
        """凌晨 5 点为界的日期桶：05:00 ~ 次日 04:59 属同一天。"""
        now = now or datetime.now()
        day = now.date() if now.hour >= 5 else now.date() - timedelta(days=1)
        return day.isoformat()

    # ---------- DB 连接 ----------

    def ensure_db(self) -> None:
        """打开落盘库并建表（幂等）。start 时调用；失败仅告警（记录功能不阻断自动化）。"""
        if self._m._db_conn is not None:
            return
        try:
            if self._m._data_dir is None:
                return
            self._m._data_dir.mkdir(parents=True, exist_ok=True)
            self._m._db_conn = sqlite3.connect(str(self._m._data_dir / "treasure.db"))
            self._m._db_conn.execute("PRAGMA journal_mode=WAL")
            self._m._db_conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS games (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    bucket TEXT NOT NULL,
                    game_seq INTEGER NOT NULL,
                    auction_result TEXT,
                    settle_final_price INTEGER,
                    settle_total_price INTEGER,
                    settle_profit INTEGER,
                    settle_my_income INTEGER,
                    daily_high_score INTEGER,
                    egg_red INTEGER NOT NULL DEFAULT 0,
                    egg_yellow INTEGER NOT NULL DEFAULT 0,
                    egg_blue INTEGER NOT NULL DEFAULT 0,
                    h_prices TEXT NOT NULL DEFAULT '[]',
                    our_bids TEXT NOT NULL DEFAULT '[]',
                    player_bids TEXT NOT NULL DEFAULT '{}',
                    my_rank INTEGER,
                    balance INTEGER,
                    strategy_mode TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_games_bucket ON games(bucket);
                CREATE TABLE IF NOT EXISTS daily_summary (
                    bucket TEXT PRIMARY KEY,
                    games INTEGER NOT NULL DEFAULT 0,
                    win INTEGER NOT NULL DEFAULT 0,
                    fail INTEGER NOT NULL DEFAULT 0,
                    profit_sum INTEGER NOT NULL DEFAULT 0,
                    income_sum INTEGER NOT NULL DEFAULT 0,
                    highest_score INTEGER NOT NULL DEFAULT 0,
                    egg_red INTEGER NOT NULL DEFAULT 0,
                    egg_yellow INTEGER NOT NULL DEFAULT 0,
                    egg_blue INTEGER NOT NULL DEFAULT 0
                );
                """
            )
            # 迁移：旧库 games 表可能缺少 strategy_mode 列（CREATE TABLE IF NOT EXISTS 不会改已有表）
            try:
                cols = [r[1] for r in self._m._db_conn.execute("PRAGMA table_info(games)").fetchall()]
                if "strategy_mode" not in cols:
                    self._m._db_conn.execute("ALTER TABLE games ADD COLUMN strategy_mode TEXT")
            except Exception as e:
                logger.log(f"[鉴宝落盘] games 表迁移失败（strategy_mode 列缺失）: {e}", "WARNING")
        except Exception as e:
            self._m._db_conn = None
            logger.log(f"[鉴宝落盘] SQLite 初始化失败（数据不记录，不影响自动化）: {e}", "WARNING")

    def close_db(self) -> None:
        """提交未完成事务并关闭落盘连接（模块收尾调用）。"""
        if self._m._db_conn is None:
            return
        try:
            self._m._db_conn.commit()
            self._m._db_conn.close()
        except Exception as e:
            logger.log(f"[鉴宝落盘] 关闭连接失败: {e}", "WARNING")
        finally:
            self._m._db_conn = None

    # ---------- 场次落盘 ----------

    def flush_game_record(self) -> None:
        """完成一场 → 写入 SQLite：games 明细一行 + daily_summary 当日累计（UPSERT）。

        调用时机：回大厅且确认为「完整走完一场」，且本场字段（结算/彩蛋/积分）尚未清空。
        失败仅告警不阻断主循环（数据记录不影响自动化决策）。
        """
        if self._m._db_conn is None:
            return
        try:
            self._m._refresh_daily_bucket()  # 先对齐日界（跨凌晨5点重置计数），再算本场序号
            bucket = self.current_bucket_str()
            game_seq = self._m._session_daily_done_count + 1  # 落盘后 done+1，这里 +1 即本场号
            ec = self._m._egg_counts or {}
            # 本场出价策略模式：profit=赚钱 / egg=赚蛋（以策略实例实际 mode 为准，config 注入可能覆盖默认）
            strategy_mode = getattr(self._m._strategy, "mode", None) or self._m._treasure_mode
            conn = self._m._db_conn
            conn.execute(
                """INSERT INTO games (ts, bucket, game_seq, auction_result,
                   settle_final_price, settle_total_price, settle_profit, settle_my_income,
                   daily_high_score, egg_red, egg_yellow, egg_blue,
                   h_prices, our_bids, player_bids, my_rank, balance, strategy_mode)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    bucket, game_seq,
                    self._m._auction_result,                      # "win" / "fail" / null
                    self._m._settle_final_price,                  # 成交价
                    self._m._settle_total_price,                  # 系统估值
                    self._m._settle_profit,                       # 利润（中标者盈亏，可负）
                    self._m._settle_my_income,                    # 本场收入（分红）
                    self._m._daily_high_score,                    # 今日最高积分
                    int(ec.get("red") or 0), int(ec.get("yellow") or 0), int(ec.get("blue") or 0),
                    json.dumps(self._m._h_prices, ensure_ascii=False),
                    json.dumps(self._m._our_bids, ensure_ascii=False),
                    json.dumps({k: list(v) for k, v in self._m._player_bids.items()}, ensure_ascii=False),
                    self._m._my_rank, self._m._my_balance,
                    strategy_mode,
                ),
            )
            # 当日汇总 UPSERT：读旧行累加
            row = conn.execute(
                "SELECT games, win, fail, profit_sum, income_sum, highest_score,"
                " egg_red, egg_yellow, egg_blue FROM daily_summary WHERE bucket = ?",
                (bucket,),
            ).fetchone()
            g, w, fl, ps, inc, hs, er, ey, eb = row if row else (0, 0, 0, 0, 0, 0, 0, 0, 0)
            p = int(self._m._settle_profit) if isinstance(self._m._settle_profit, (int, float)) else 0
            inc_ = int(self._m._settle_my_income) if isinstance(self._m._settle_my_income, (int, float)) else 0
            hs_ = int(self._m._daily_high_score) if isinstance(self._m._daily_high_score, (int, float)) else 0
            conn.execute(
                """INSERT INTO daily_summary (bucket, games, win, fail, profit_sum,
                   income_sum, highest_score, egg_red, egg_yellow, egg_blue)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(bucket) DO UPDATE SET
                     games = excluded.games,
                     win = excluded.win,
                     fail = excluded.fail,
                     profit_sum = excluded.profit_sum,
                     income_sum = excluded.income_sum,
                     highest_score = MAX(highest_score, excluded.highest_score),
                     egg_red = excluded.egg_red,
                     egg_yellow = excluded.egg_yellow,
                     egg_blue = excluded.egg_blue""",
                (bucket, g + 1, w + (1 if self._m._auction_result == "win" else 0),
                 fl + (1 if self._m._auction_result == "fail" else 0),
                 ps + p, inc + inc_, max(hs, hs_),
                 er + int(ec.get("red") or 0), ey + int(ec.get("yellow") or 0), eb + int(ec.get("blue") or 0)),
            )
            conn.commit()
            logger.log(
                f"[鉴宝落盘] 已记录第 {game_seq} 场: 策略={strategy_mode} 结果={self._m._auction_result or '-'} "
                f"成交={self._m._settle_final_price or 0:,} 利润={self._m._settle_profit or 0:,} "
                f"收入={self._m._settle_my_income or 0:,} 彩蛋="
                f"红{int(ec.get('red') or 0)}黄{int(ec.get('yellow') or 0)}蓝{int(ec.get('blue') or 0)}",
                "INFO",
            )
        except Exception as e:
            try:
                if self._m._db_conn is not None:
                    self._m._db_conn.rollback()
            except Exception:
                pass
            logger.log(f"[鉴宝落盘] 写入失败: {e}", "WARNING")

    # ---------- 会话总结 ----------

    def log_session_summary(self):
        """会话总结：逐行输出，首行「鉴宝观察会话总结」被前端识别为区块卡片头（可展开分组）。"""
        m = self._m
        lines: list[str] = ["鉴宝观察会话总结"]
        lines.append(f"  阶段记录     : {m._current_stage or '-'}（结束时）")
        if m._round_no is not None:
            lines.append(f"  结束回合     : {m._round_no}")
        if m._h_prices:
            lines.append(f"  系统 H 记录  : {m._h_prices}")
        if m._our_bids:
            lines.append(f"  我方出价记录 : {m._our_bids}")
        if m._h_prices and max(m._h_prices) > 0:
            H_max = max(x for x in m._h_prices if x > 0)
            lines.append(f"  H_max        : {H_max:,}  → 估值区间 ≈ {int(H_max*1.33):,} ~ {int(H_max*1.44):,}")
        if m._settle_my_income is not None or m._settle_profit is not None:
            lines.append(
                f"  本场结算     : 收入 {m._settle_my_income or 0:,} / 利润 {m._settle_profit or 0:,}"
                f"（成交价 {m._settle_final_price or 0:,} / 估值 {m._settle_total_price or 0:,}）"
            )
        if m._egg_counts is not None:
            lines.append(
                f"  彩蛋数量     : 红{m._egg_counts.get('red', 0)} "
                f"黄{m._egg_counts.get('yellow', 0)} 蓝{m._egg_counts.get('blue', 0)}"
            )
        if m._daily_high_score is not None:
            lines.append(f"  今日最高积分 : {m._daily_high_score:,}")
        if m._session_dir:
            lines.append(f"  保存帧数     : {m._saved_frames} (raw 全量) / {m._debug_saved} (debug 图)")
            lines.append(f"  调试目录     : {m._session_dir}")
        for _ln in lines:
            logger.log(_ln, "INFO")
