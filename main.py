"""表情包制作（轻量版）—— AstrBot 插件主入口。

直连 meme-generator 后端（与 GsCore 侧共用同一个服务），
提供关键词出图、随机表情、查询、管理、统计、分群开关等能力。

来源与致谢
----------
后端能力（本插件全部出图能力来源）：
    MemeCrafters/meme-generator        Python 版, MIT
        https://github.com/MemeCrafters/meme-generator
    MemeCrafters/meme-generator-rs     Rust 版, MIT
        https://github.com/MemeCrafters/meme-generator-rs

功能设计 / 命令体系参考：
    Loping151/core_plugin_memes        作者 Loping151 <wangkailing151@gmail.com>
        许可 GPL-3.0-or-later
        https://github.com/Loping151/core_plugin_memes

说明：本插件为独立实现（自行编写代码、直接对接 HTTP 接口），未复制上述项目源码。
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import At, Image
from astrbot.api.star import Context, Star, StarTools, register

from .core.client import MemeClient, MemeClientError
from .core.models import MemeInfo
from .core.parser import collect_materials
from .core.store import MemeStore

PLUGIN_NAME = "astrbot_plugin_meme_lite"

# 这些前缀属于命令，catch-all 生成器要让路
_RESERVED = (
    "表情列表", "表情帮助", "表情搜索", "表情查找", "表情详情", "表情信息",
    "表情统计", "表情黑名单", "表情包状态", "表情包帮助",
    "随机表情", "更新表情", "刷新表情",
    "禁用表情", "启用表情", "全局禁用表情", "全局启用表情",
    "屏蔽表情", "解除屏蔽", "取消屏蔽",
    "开启表情包", "关闭表情包",
)


@register(PLUGIN_NAME, "AwA", "表情包制作（轻量版）", "1.0.0")
class MemeLitePlugin(Star):
    def __init__(self, context: Context, config) -> None:
        super().__init__(context)
        self.config = config

        data_dir = Path(StarTools.get_data_dir(plugin_name=PLUGIN_NAME))
        data_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir = data_dir
        self.tmp_dir = data_dir / "tmp"
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

        self.client = MemeClient(
            base_url=str(self._cfg("api_url", "https://meme.xlinxc.cn")),
            timeout=float(self._cfg("request_timeout", 60)),
            cache_path=data_dir / "meme_index.json",
            cache_hours=float(self._cfg("index_cache_hours", 24)),
        )
        self.store = MemeStore(data_dir / "state.json")

        self._cooldown: Dict[str, float] = {}
        self._index_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # 基础工具
    # ------------------------------------------------------------------
    def _cfg(self, key: str, default: Any = None) -> Any:
        try:
            val = self.config.get(key, default)
        except Exception:  # noqa: BLE001
            return default
        return default if val is None else val

    @property
    def enabled(self) -> bool:
        return bool(self._cfg("enable", True))

    def _clean_tmp(self) -> None:
        """清理超过 30 分钟的临时图片。"""
        try:
            cutoff = time.time() - 1800
            for f in self.tmp_dir.glob("meme_*"):
                if f.stat().st_mtime < cutoff:
                    f.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass

    def _save_tmp(self, data: bytes) -> str:
        self._clean_tmp()
        ext = "png"
        if data[:2] == b"\xff\xd8":
            ext = "jpg"
        elif data[:3] == b"GIF":
            ext = "gif"
        elif data[:4] == b"RIFF":
            ext = "webp"
        path = self.tmp_dir / f"meme_{int(time.time() * 1000)}.{ext}"
        path.write_bytes(data)
        return str(path)

    @staticmethod
    def _at_ids(event: AstrMessageEvent) -> List[str]:
        ids: List[str] = []
        try:
            # 排除 @全体 与 @机器人自己：@bot 是唤醒手段，不是"要出图的人"，
            # 否则会拿机器人头像当素材。真正的人由 sender 兜底提供。
            self_id = str(event.get_self_id())
            for seg in event.message_obj.message:
                if isinstance(seg, At) and str(seg.qq) not in ("all", self_id):
                    ids.append(str(seg.qq))
        except Exception:  # noqa: BLE001
            pass
        return ids

    @staticmethod
    def _image_urls(event: AstrMessageEvent) -> List[str]:
        urls: List[str] = []
        try:
            for seg in event.message_obj.message:
                if isinstance(seg, Image):
                    url = getattr(seg, "url", None) or getattr(seg, "file", None)
                    if url:
                        urls.append(str(url))
        except Exception:  # noqa: BLE001
            pass
        return urls

    def _can_use(self, channel: str, key: str) -> Optional[str]:
        """检查是否可用；返回 None 表示可用，否则返回拒绝原因。"""
        if self.store.is_global_disabled(key):
            return f"表情「{key}」已被全局禁用"
        if self.store.is_user_disabled(channel, key):
            return f"表情「{key}」已对本会话禁用"
        return None

    def _cooldown_hit(self, channel: str) -> bool:
        cd = int(self._cfg("cooldown_seconds", 5) or 0)
        if cd <= 0:
            return False
        now = time.time()
        if now - self._cooldown.get(channel, 0.0) < cd:
            return True
        self._cooldown[channel] = now
        return False

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    async def initialize(self) -> None:
        loaded = self.client.load_cache()
        auto = bool(self._cfg("auto_refresh_index", True))
        need_refresh = not loaded or auto
        if need_refresh:
            self._index_task = asyncio.create_task(self._refresh_index(save=True))
        logger.info(
            f"[meme_lite] 初始化完成（缓存{'已加载' if loaded else '未命中'}，"
            f"{'后台刷新中' if need_refresh else '跳过刷新'}）"
        )

    async def terminate(self) -> None:
        if self._index_task and not self._index_task.done():
            self._index_task.cancel()

    async def _refresh_index(self, save: bool = True) -> Tuple[int, int]:
        try:
            ok, fail = await self.client.refresh_all()
            if save and ok:
                self.client.save_cache()
            logger.info(f"[meme_lite] 表情索引刷新完成：成功 {ok}，失败 {fail}")
            return ok, fail
        except Exception as e:  # noqa: BLE001
            logger.error(f"[meme_lite] 索引刷新失败：{e}")
            return 0, 0

    # ------------------------------------------------------------------
    # 生成核心
    # ------------------------------------------------------------------
    async def _generate(
        self,
        event: AstrMessageEvent,
        info: MemeInfo,
        rest_text: str,
        *,
        show_info: bool = False,
    ) -> Tuple[Optional[str], Optional[str]]:
        """返回 (图片路径, 提示文本)。"""
        channel = event.unified_msg_origin
        sender_id = str(event.get_sender_id())
        sender_name = event.get_sender_name() or sender_id

        parsed = await collect_materials(
            info,
            rest_text,
            sender_id=sender_id,
            sender_name=sender_name,
            at_ids=self._at_ids(event),
            image_urls=self._image_urls(event),
            max_image_mb=float(self._cfg("max_image_size_mb", 5)),
        )

        if not info.accepts(len(parsed.images), len(parsed.texts)):
            need_img = info.image_requirement_text()
            need_txt = info.text_requirement_text()
            return None, (
                f"「{info.key}」需要图片 {need_img} 张、文字 {need_txt} 段，"
                f"当前收到 {len(parsed.images)} 张图 / {len(parsed.texts)} 段文字"
            )

        user_infos = [
            {"name": name, "gender": "unknown"} for name in (parsed.at_names or [])
        ] or None

        try:
            data = await self.client.generate(
                info.key, parsed.images, parsed.texts, parsed.options, user_infos=user_infos
            )
        except MemeClientError as e:
            return None, f"生成失败：{e.message}"
        except Exception as e:  # noqa: BLE001
            logger.exception(f"[meme_lite] 生成异常：{e}")
            return None, f"生成失败：{e}"

        if bool(self._cfg("enable_stats", True)):
            try:
                self.store.record(channel, info.key)
            except Exception:  # noqa: BLE001
                pass

        note = "\n".join(parsed.notes) if parsed.notes else None
        if show_info:
            kw = info.keywords[0] if info.keywords else info.key
            head = f"指令：{kw}"
            note = f"{head}\n{note}" if note else head
        return self._save_tmp(data), note

    # ------------------------------------------------------------------
    # catch-all：关键词直接出图
    # ------------------------------------------------------------------
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        if not self.enabled or event.get_extra("meme_lite_done"):
            return

        channel = event.unified_msg_origin
        if self.store.is_group_off(channel):
            return

        raw = (event.message_str or "").strip()
        if not raw:
            return
        for reserved in _RESERVED:
            if raw.startswith(reserved):
                return

        prefix = str(self._cfg("trigger_prefix", "") or "")
        if prefix:
            if not raw.startswith(prefix):
                return
            raw = raw[len(prefix) :].strip()
            if not raw:
                return

        if not self.client.is_ready:
            return
        if self._cooldown_hit(channel) and not prefix:
            return

        info = self._match_keyword(raw)
        if info is None:
            return
        reason = self._can_use(channel, info.key)
        if reason:
            return

        event.set_extra("meme_lite_done", True)
        rest = raw[len(info.matched_keyword(raw) or "") :].strip()
        path, note = await self._generate(event, info, rest)
        if not path:
            if note:
                yield event.plain_result(note)
            return
        event.stop_event()
        if note:
            yield event.plain_result(note)
        yield event.image_result(path)

    def _match_keyword(self, text: str) -> Optional[MemeInfo]:
        """在本地索引里找以 text 开头命中的最长关键词。"""
        best: Optional[MemeInfo] = None
        best_len = 0
        for info in self.client.all_infos():
            kw = info.matched_keyword(text)
            if kw and len(kw) > best_len:
                best, best_len = info, len(kw)
        return best

    # ------------------------------------------------------------------
    # 核心命令
    # ------------------------------------------------------------------
    @filter.command("随机表情")
    async def cmd_random(self, event: AstrMessageEvent):
        """随机出一张当前可用的表情。"""
        if not self.enabled:
            return
        channel = event.unified_msg_origin
        if self.store.is_group_off(channel):
            yield event.plain_result("本会话已关闭表情包功能")
            return
        if not self.client.is_ready:
            yield event.plain_result("表情索引还没就绪，先发一次 /更新表情 试试")
            return

        parsed_rest = (event.message_str or "").replace("随机表情", "", 1).strip()
        n_at = len(self._at_ids(event))
        n_img = len(self._image_urls(event))

        pool: List[MemeInfo] = []
        for info in self.client.all_infos():
            if self._can_use(channel, info.key):
                continue
            if info.max_images < n_img + n_at:
                continue
            if info.auto_fillable(n_img + n_at) or info.accepts(n_img + n_at, 1):
                pool.append(info)

        if not pool:
            yield event.plain_result("没有符合当前图片/文字数量的可用表情")
            return

        import random

        info = random.choice(pool)
        path, note = await self._generate(event, info, parsed_rest, show_info=True)
        if not path:
            yield event.plain_result(note or "生成失败")
            return
        if note:
            yield event.plain_result(note)
        yield event.image_result(path)

    # ------------------------------------------------------------------
    # 查询三件套
    # ------------------------------------------------------------------
    @filter.command("表情列表", alias={"表情help", "全部表情"})
    async def cmd_list(self, event: AstrMessageEvent):
        """列出全部可用表情（分段显示）。"""
        if not self.client.is_ready:
            yield event.plain_result("表情索引还没就绪，先发一次 /更新表情")
            return
        channel = event.unified_msg_origin
        infos = self.client.all_infos()

        usable: List[str] = []
        disabled_mine: List[str] = []
        disabled_global: List[str] = []
        for info in infos:
            name = info.keywords[0] if info.keywords else info.key
            if self.store.is_global_disabled(info.key):
                disabled_global.append(name)
            elif self.store.is_user_disabled(channel, info.key):
                disabled_mine.append(name)
            else:
                usable.append(name)

        lines = [f"📋 可用表情（{len(usable)}）", "、".join(usable[:400])]
        if disabled_mine:
            lines.append(f"\n🚫 本会话禁用（{len(disabled_mine)}）：{'、'.join(disabled_mine[:80])}")
        if disabled_global:
            lines.append(f"\n⛔ 全局禁用（{len(disabled_global)}）：{'、'.join(disabled_global[:80])}")
        lines.append("\n用法：直接发关键词，如「摸头」；搜关键词用 /表情搜索")
        yield event.plain_result("\n".join(lines))

    @filter.command("表情搜索", alias={"表情查找", "搜表情"})
    async def cmd_search(self, event: AstrMessageEvent):
        """按关键词/标签搜表情。"""
        query = (event.message_str or "").split(maxsplit=1)
        q = query[1].strip() if len(query) > 1 else ""
        if not q:
            yield event.plain_result("用法：/表情搜索 <关键词>")
            return
        if not self.client.is_ready:
            yield event.plain_result("表情索引还没就绪，先发一次 /更新表情")
            return

        hits = self.client.search_local(q, limit=25)
        if not hits:
            yield event.plain_result(f"没搜到跟「{q}」相关的表情")
            return
        lines = [f"🔍 「{q}」相关表情（{len(hits)}）"]
        for info in hits:
            kw = info.keywords[0] if info.keywords else info.key
            lines.append(
                f"  • {kw}  [{info.key}]  图{info.image_requirement_text()} / 文{info.text_requirement_text()}"
            )
        lines.append("\n看详情：/表情详情 <关键词>")
        yield event.plain_result("\n".join(lines))

    @filter.command("表情详情", alias={"表情信息", "表情参数"})
    async def cmd_info(self, event: AstrMessageEvent):
        """查看某个表情的详细参数。"""
        parts = (event.message_str or "").split(maxsplit=1)
        q = parts[1].strip() if len(parts) > 1 else ""
        if not q:
            yield event.plain_result("用法：/表情详情 <关键词>")
            return
        if not self.client.is_ready:
            yield event.plain_result("表情索引还没就绪，先发一次 /更新表情")
            return

        hit = self._match_keyword(q)
        if hit is None:
            hits = self.client.search_local(q, limit=1)
            hit = hits[0] if hits else None
        if hit is None:
            yield event.plain_result(f"没找到「{q}」这个表情")
            return

        lines = [
            f"🎭 {hit.key}",
            f"关键词：{'、'.join(hit.keywords) or '（无）'}",
            f"图片需求：{hit.image_requirement_text()}",
            f"文字需求：{hit.text_requirement_text()}",
        ]
        if hit.default_texts:
            lines.append(f"默认文字：{' | '.join(hit.default_texts)}")
        if hit.options:
            lines.append(f"可选参数（{len(hit.options)}）：")
            for opt in hit.options[:12]:
                lines.append(f"  {opt.display}  {opt.help_text}")
        lines.append(f"\n用法：{hit.keywords[0] if hit.keywords else hit.key} [文字] [自己|@某人]")
        yield event.plain_result("\n".join(lines))

    # ------------------------------------------------------------------
    # 管理：个人 / 全局 禁用
    # ------------------------------------------------------------------
    @filter.command("禁用表情")
    async def cmd_disable(self, event: AstrMessageEvent):
        """在本会话禁用某个表情。"""
        target = self._arg1(event)
        if not target:
            yield event.plain_result("用法：/禁用表情 <关键词>")
            return
        key = self._resolve_key(target)
        if not key:
            yield event.plain_result(f"没找到「{target}」这个表情")
            return
        self.store.disable_user(event.unified_msg_origin, key)
        yield event.plain_result(f"✅ 已在本会话禁用「{key}」")

    @filter.command("启用表情")
    async def cmd_enable(self, event: AstrMessageEvent):
        """恢复本会话被禁用的表情。"""
        target = self._arg1(event)
        if not target:
            yield event.plain_result("用法：/启用表情 <关键词>")
            return
        key = self._resolve_key(target) or target
        self.store.enable_user(event.unified_msg_origin, key)
        yield event.plain_result(f"✅ 已在本会话恢复「{key}」")

    @filter.command("全局禁用表情")
    async def cmd_global_disable(self, event: AstrMessageEvent):
        """[管理员] 全局禁用某个表情。"""
        if not self._require_admin(event):
            yield event.plain_result("只有管理员可以全局禁用表情")
            return
        target = self._arg1(event)
        if not target:
            yield event.plain_result("用法：/全局禁用表情 <关键词>")
            return
        key = self._resolve_key(target)
        if not key:
            yield event.plain_result(f"没找到「{target}」这个表情")
            return
        self.store.disable_global(key)
        yield event.plain_result(f"⛔ 已全局禁用「{key}」")

    @filter.command("全局启用表情")
    async def cmd_global_enable(self, event: AstrMessageEvent):
        """[管理员] 解除全局禁用。"""
        if not self._require_admin(event):
            yield event.plain_result("只有管理员可以全局启用表情")
            return
        target = self._arg1(event)
        if not target:
            yield event.plain_result("用法：/全局启用表情 <关键词>")
            return
        key = self._resolve_key(target) or target
        self.store.enable_global(key)
        yield event.plain_result(f"✅ 已全局恢复「{key}」")

    @filter.command("表情黑名单", alias={"禁用列表"})
    async def cmd_blacklist(self, event: AstrMessageEvent):
        """查看全局禁用列表。"""
        keys = self.store.global_disabled_list()
        if not keys:
            yield event.plain_result("全局禁用列表是空的")
            return
        names = []
        for k in keys:
            info = self.client.get_cached(k)
            names.append(f"{k}（{info.keywords[0]}）" if info and info.keywords else k)
        yield event.plain_result(f"⛔ 全局禁用（{len(names)}）：\n" + "、".join(names))

    # ------------------------------------------------------------------
    # 管理：屏蔽（不让别人拿我当素材）
    # ------------------------------------------------------------------
    @filter.command("屏蔽表情")
    async def cmd_shield(self, event: AstrMessageEvent):
        """屏蔽某表情（别人拿你当素材时会被拦）。"""
        target = self._arg1(event)
        if not target:
            yield event.plain_result("用法：/屏蔽表情 <关键词>")
            return
        key = self._resolve_key(target)
        if not key:
            yield event.plain_result(f"没找到「{target}」这个表情")
            return
        self.store.shield(event.unified_msg_origin, str(event.get_sender_id()), key)
        yield event.plain_result(f"✅ 已屏蔽「{key}」")

    @filter.command("解除屏蔽", alias={"取消屏蔽"})
    async def cmd_unshield(self, event: AstrMessageEvent):
        """解除屏蔽。"""
        target = self._arg1(event)
        if not target:
            yield event.plain_result("用法：/解除屏蔽 <关键词>")
            return
        key = self._resolve_key(target) or target
        self.store.unshield(event.unified_msg_origin, str(event.get_sender_id()), key)
        yield event.plain_result(f"✅ 已解除屏蔽「{key}」")

    # ------------------------------------------------------------------
    # 统计 / 分群开关 / 索引刷新
    # ------------------------------------------------------------------
    @filter.command("表情统计", alias={"表情排行"})
    async def cmd_stats(self, event: AstrMessageEvent):
        """当日调用排行。"""
        arg = self._arg1(event)
        limit = int(arg) if arg and arg.isdigit() else 10
        channel = event.unified_msg_origin
        rows = self.store.top(channel, limit)
        scope = "本会话"
        if not rows:
            rows = self.store.top_all(limit)
            scope = "全部会话（本会话今日还没记录）"

        if not rows:
            yield event.plain_result("今天还没有表情调用记录")
            return
        lines = [f"📊 今日表情调用排行 · {scope}"]
        for i, (key, n) in enumerate(rows, 1):
            info = self.client.get_cached(key)
            kw = f"（{info.keywords[0]}）" if info and info.keywords else ""
            lines.append(f"  {i}. {key}{kw} × {n}")
        yield event.plain_result("\n".join(lines))

    @filter.command("开启表情包")
    async def cmd_group_on(self, event: AstrMessageEvent):
        """在本会话开启表情包功能。"""
        self.store.set_group_off(event.unified_msg_origin, False)
        yield event.plain_result("✅ 本会话已开启表情包功能")

    @filter.command("关闭表情包")
    async def cmd_group_off(self, event: AstrMessageEvent):
        """在本会话关闭表情包功能。"""
        self.store.set_group_off(event.unified_msg_origin, True)
        yield event.plain_result("🚫 本会话已关闭表情包功能")

    @filter.command("表情包状态")
    async def cmd_group_status(self, event: AstrMessageEvent):
        """查看本会话表情包开关状态。"""
        channel = event.unified_msg_origin
        off = self.store.is_group_off(channel)
        s = self.store.summary()
        lines = [
            f"本会话表情包：{'🚫 已关闭' if off else '✅ 已开启'}",
            f"索引状态：{'就绪' if self.client.is_ready else '未就绪'}"
            f"（{len(self.client.all_infos())} 个表情）",
            f"本会话个人禁用：{len(self.store.user_disabled_list(channel))} 个",
            f"全局禁用：{s['global_disabled']} 个 ｜ 屏蔽记录：{s['shielded_slots']} 条",
        ]
        yield event.plain_result("\n".join(lines))

    @filter.command("更新表情", alias={"刷新表情", "重载表情"})
    async def cmd_refresh(self, event: AstrMessageEvent):
        """重新拉取表情索引。"""
        if not self._require_admin(event):
            yield event.plain_result("只有管理员可以刷新表情索引")
            return
        yield event.plain_result("正在刷新表情索引，请稍候…")
        ok, fail = await self._refresh_index(save=True)
        yield event.plain_result(f"✅ 刷新完成：成功 {ok} 个，失败 {fail} 个")

    @filter.command("表情帮助", alias={"表情包帮助"})
    async def cmd_help(self, event: AstrMessageEvent):
        """查看用法。"""
        yield event.plain_result(
            "🎭 表情包制作（轻量版）\n"
            "【出图】直接发关键词，如「摸头」/「摸头 你好」/「摸头 自己」/「摸头 @某人」\n"
            "【随机】/随机表情 [自己|@某人]\n"
            "【查询】/表情列表 ｜ /表情搜索 <词> ｜ /表情详情 <关键词>\n"
            "【管理】/禁用表情 <词> ｜ /启用表情 <词>\n"
            "        /全局禁用表情 <词> ｜ /全局启用表情 <词> ｜ /表情黑名单 （管理员）\n"
            "【屏蔽】/屏蔽表情 <词> ｜ /解除屏蔽 <词>\n"
            "【其它】/表情统计 [n] ｜ /开启表情包 ｜ /关闭表情包 ｜ /表情包状态\n"
            "        /更新表情（管理员，刷新索引）"
        )

    # ------------------------------------------------------------------
    # 小工具
    # ------------------------------------------------------------------
    @staticmethod
    def _arg1(event: AstrMessageEvent) -> str:
        parts = (event.message_str or "").split(maxsplit=1)
        return parts[1].strip() if len(parts) > 1 else ""

    def _resolve_key(self, text: str) -> Optional[str]:
        """把用户输入（关键词或 key）解析成表情 key。"""
        text = (text or "").strip()
        if not text:
            return None
        if self.client.get_cached(text):
            return text
        info = self._match_keyword(text)
        if info is not None:
            return info.key
        hits = self.client.search_local(text, limit=1)
        return hits[0].key if hits else None

    @staticmethod
    def _require_admin(event: AstrMessageEvent) -> bool:
        try:
            return bool(event.is_admin())
        except Exception:  # noqa: BLE001
            return False
