"""本地状态：个人/全局禁用、屏蔽、分群开关、调用统计。

以单个 JSON 文件持久化，文件放在插件数据目录下。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from astrbot.api import logger


class MemeStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._global_disabled: Set[str] = set()
        self._user_disabled: Dict[str, Set[str]] = {}
        self._shielded: Dict[str, Set[str]] = {}
        self._group_off: Set[str] = set()
        # date -> channel -> key -> count
        self._stats: Dict[str, Dict[str, Dict[str, int]]] = {}
        self.load()

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------
    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[meme_lite] 状态文件读取失败：{e}")
            return

        self._global_disabled = {str(k) for k in (raw.get("global_disabled") or [])}
        self._user_disabled = {
            str(c): {str(k) for k in keys}
            for c, keys in (raw.get("user_disabled") or {}).items()
        }
        self._shielded = {
            str(c): {str(k) for k in keys}
            for c, keys in (raw.get("shielded") or {}).items()
        }
        self._group_off = {str(g) for g in (raw.get("group_off") or [])}
        self._stats = {
            str(d): {
                str(c): {str(k): int(v) for k, v in keys.items()}
                for c, keys in chans.items()
            }
            for d, chans in (raw.get("stats") or {}).items()
        }

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "global_disabled": sorted(self._global_disabled),
                "user_disabled": {c: sorted(k) for c, k in self._user_disabled.items()},
                "shielded": {c: sorted(k) for c, k in self._shielded.items()},
                "group_off": sorted(self._group_off),
                "stats": self._stats,
            }
            self.path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[meme_lite] 状态文件写入失败：{e}")

    # ------------------------------------------------------------------
    # 全局禁用
    # ------------------------------------------------------------------
    def is_global_disabled(self, key: str) -> bool:
        return key in self._global_disabled

    def disable_global(self, key: str) -> None:
        self._global_disabled.add(key)
        self.save()

    def enable_global(self, key: str) -> None:
        self._global_disabled.discard(key)
        self.save()

    def global_disabled_list(self) -> List[str]:
        return sorted(self._global_disabled)

    # ------------------------------------------------------------------
    # 个人禁用（按会话）
    # ------------------------------------------------------------------
    def is_user_disabled(self, channel: str, key: str) -> bool:
        return key in self._user_disabled.get(channel, set())

    def disable_user(self, channel: str, key: str) -> None:
        self._user_disabled.setdefault(channel, set()).add(key)
        self.save()

    def enable_user(self, channel: str, key: str) -> None:
        keys = self._user_disabled.get(channel)
        if keys:
            keys.discard(key)
            if not keys:
                self._user_disabled.pop(channel, None)
        self.save()

    def user_disabled_list(self, channel: str) -> List[str]:
        return sorted(self._user_disabled.get(channel, set()))

    # ------------------------------------------------------------------
    # 屏蔽（不让别人拿我当素材）
    # ------------------------------------------------------------------
    @staticmethod
    def shield_slot(channel: str, uid: str) -> str:
        return f"{channel}|{uid}"

    def is_shielded(self, channel: str, uid: str, key: str) -> bool:
        return key in self._shielded.get(self.shield_slot(channel, uid), set())

    def shield(self, channel: str, uid: str, key: str) -> None:
        self._shielded.setdefault(self.shield_slot(channel, uid), set()).add(key)
        self.save()

    def unshield(self, channel: str, uid: str, key: str) -> None:
        slot = self.shield_slot(channel, uid)
        keys = self._shielded.get(slot)
        if keys:
            keys.discard(key)
            if not keys:
                self._shielded.pop(slot, None)
        self.save()

    def shielded_list(self, channel: str, uid: str) -> List[str]:
        return sorted(self._shielded.get(self.shield_slot(channel, uid), set()))

    # ------------------------------------------------------------------
    # 分群开关
    # ------------------------------------------------------------------
    def is_group_off(self, channel: str) -> bool:
        return channel in self._group_off

    def set_group_off(self, channel: str, off: bool) -> None:
        if off:
            self._group_off.add(channel)
        else:
            self._group_off.discard(channel)
        self.save()

    # ------------------------------------------------------------------
    # 统计
    # ------------------------------------------------------------------
    @staticmethod
    def _today() -> str:
        return time.strftime("%Y-%m-%d")

    def record(self, channel: str, key: str) -> None:
        day = self._today()
        self._stats.setdefault(day, {}).setdefault(channel, {})
        bucket = self._stats[day][channel]
        bucket[key] = bucket.get(key, 0) + 1
        self.save()

    def top(self, channel: str, limit: int = 10) -> List[Tuple[str, int]]:
        """当日该会话的调用排行。"""
        bucket = (self._stats.get(self._today()) or {}).get(channel) or {}
        return sorted(bucket.items(), key=lambda x: -x[1])[:limit]

    def top_all(self, limit: int = 10) -> List[Tuple[str, int]]:
        """当日全部会话合计排行。"""
        merged: Dict[str, int] = {}
        for chans in (self._stats.get(self._today()) or {}).values():
            for k, v in chans.items():
                merged[k] = merged.get(k, 0) + v
        return sorted(merged.items(), key=lambda x: -x[1])[:limit]

    def purge_old_stats(self, keep_days: int = 7) -> None:
        """只保留最近 N 天的统计，避免文件无限膨胀。"""
        if keep_days <= 0:
            return
        cutoff = time.strftime("%Y-%m-%d", time.localtime(time.time() - keep_days * 86400))
        removed = [d for d in self._stats if d < cutoff]
        for d in removed:
            self._stats.pop(d, None)
        if removed:
            self.save()

    # ------------------------------------------------------------------
    # 杂项
    # ------------------------------------------------------------------
    def summary(self) -> Dict[str, Any]:
        return {
            "global_disabled": len(self._global_disabled),
            "user_disabled_sessions": len(self._user_disabled),
            "shielded_slots": len(self._shielded),
            "group_off": len(self._group_off),
            "stat_days": len(self._stats),
        }
