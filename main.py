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
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import At, Image, Reply
from astrbot.api.star import Context, Star, StarTools, register

from .core.client import MemeClient, MemeClientError
from .core.models import MemeInfo
from .core.parser import collect_materials
from .core.render import render_command_help, render_meme_list
from .core.store import MemeStore

PLUGIN_NAME = "astrbot_plugin_meme_lite"

# 这些前缀属于命令，catch-all 生成器要让路
_RESERVED = (
    "表情列表", "表情帮助", "表情搜索", "表情查找", "表情详情", "表情信息",
    "表情统计", "表情黑名单", "表情包状态", "表情包帮助", "表情help", "全部表情",
    "表情参数", "表情排行", "搜表情", "禁用列表",
    "随机表情", "更新表情", "刷新表情", "重载表情",
    "禁用表情", "启用表情", "全局禁用表情", "全局启用表情",
    "屏蔽表情", "解除屏蔽", "取消屏蔽",
    "开启表情包", "关闭表情包",
)

# AstrBot 会把非首位的 @ 段拼成 " @昵称(QQ) " 塞进 message_str。
# 头像已由 At 段单独取过，这段文本必须从台词里剔掉，否则：
#   - 要求 0 段文字的表情会判定素材不符（直接失败）
#   - 要求 1 段文字的表情会把 "@张三(123456)" 当成台词念出来
_MENTION_RE = re.compile(r"@[^\s@()]*\(\d+\)")

# `bq` 是插件自己的前缀，AstrBot 的 @filter.command 只认框架唤醒前缀（/ 等），
# 两者不互通 —— 所以 `bq表情列表` 不会命中命令过滤器。这里做一层转发：
# 剥掉插件前缀后若以命令名开头，直接调用对应 handler（按命令名长度降序匹配）。
_CMD_ROUTES = tuple(
    sorted(
        (
            ("表情帮助", "cmd_help"),
            ("表情包帮助", "cmd_help"),
            ("表情列表", "cmd_list"),
            ("全部表情", "cmd_list"),
            ("表情help", "cmd_list"),
            ("表情搜索", "cmd_search"),
            ("表情查找", "cmd_search"),
            ("搜表情", "cmd_search"),
            ("表情详情", "cmd_info"),
            ("表情信息", "cmd_info"),
            ("表情参数", "cmd_info"),
            ("表情统计", "cmd_stats"),
            ("表情排行", "cmd_stats"),
            ("随机表情", "cmd_random"),
            ("禁用表情", "cmd_disable"),
            ("启用表情", "cmd_enable"),
            ("全局禁用表情", "cmd_global_disable"),
            ("全局启用表情", "cmd_global_enable"),
            ("表情黑名单", "cmd_blacklist"),
            ("禁用列表", "cmd_blacklist"),
            ("屏蔽表情", "cmd_shield"),
            ("解除屏蔽", "cmd_unshield"),
            ("取消屏蔽", "cmd_unshield"),
            ("开启表情包", "cmd_group_on"),
            ("关闭表情包", "cmd_group_off"),
            ("表情包状态", "cmd_group_status"),
            ("更新表情", "cmd_refresh"),
            ("刷新表情", "cmd_refresh"),
            ("重载表情", "cmd_refresh"),
        ),
        key=lambda pair: -len(pair[0]),
    )
)

# 帮助图内容：(命令模板, 示例模板, 说明)。`<P>` 渲染前替换成真实前缀。
_HELP_SECTIONS: List[Tuple[str, List[Tuple[str, str, str]]]] = [
    (
        "出图",
        [
            (
                "<P><关键词> [文字/图/@QQ/自己]",
                "<P>摸头 自己",
                "关键词直接触发对应表情。`自己`=你的头像，`@123456`=指定 QQ 头像；"
                "多图表情素材不够时会自动把你补到第一位。",
            ),
            (
                "<P>随机表情 [文字/图/@]",
                "<P>随机表情 摸头",
                "在满足数量约束、未被禁用且未被屏蔽的表情里随机一个。",
            ),
        ],
    ),
    (
        "查询",
        [
            (
                "<P>表情列表",
                "<P>表情列表",
                "渲染全部表情总览图，分「可用 / 本会话禁用 / 全局禁用」三段。",
            ),
            (
                "<P>表情搜索 <关键词>",
                "<P>表情搜索 猫",
                "在关键词与标签里模糊搜索。",
            ),
            (
                "<P>表情详情 <关键词>",
                "<P>表情详情 摸头",
                "查看该表情的图片需求、文字需求、默认文字与可选参数。",
            ),
            (
                "<P>表情统计 [条数]",
                "<P>表情统计 10",
                "今日调用排行，默认前 10 条。",
            ),
        ],
    ),
    (
        "个人管理",
        [
            (
                "<P>禁用表情 <关键词>",
                "<P>禁用表情 摸头",
                "只在本会话屏蔽你自己的触发，不影响别人。",
            ),
            (
                "<P>启用表情 <关键词>",
                "<P>启用表情 摸头",
                "撤销你自己的禁用。",
            ),
            (
                "<P>屏蔽表情 [关键词]",
                "<P>屏蔽表情 摸头",
                "别人 @你 时不能对你使用这些表情，自己用不受影响；不带参数看当前屏蔽列表。",
            ),
            (
                "<P>解除屏蔽 <关键词>",
                "<P>解除屏蔽 摸头",
                "撤销个人屏蔽。",
            ),
        ],
    ),
    (
        "本会话开关",
        [
            (
                "<P>开启表情包",
                "<P>开启表情包",
                "恢复整个插件在本会话的全部指令。",
            ),
            (
                "<P>关闭表情包",
                "<P>关闭表情包",
                "关闭后除「开启表情包 / 表情包状态」外全部忽略。",
            ),
            (
                "<P>表情包状态",
                "<P>表情包状态",
                "查看开关状态、索引数量、个人禁用数量。",
            ),
        ],
    ),
]

# 仅管理员可见
_ADMIN_HELP_SECTIONS: List[Tuple[str, List[Tuple[str, str, str]]]] = [
    (
        "管理员",
        [
            (
                "<P>全局禁用表情 <关键词>",
                "<P>全局禁用表情 摸头",
                "对所有人禁用该表情（白名单模式）。",
            ),
            (
                "<P>全局启用表情 <关键词>",
                "<P>全局启用表情 摸头",
                "恢复所有人可用（黑名单模式）。",
            ),
            (
                "<P>表情黑名单",
                "<P>表情黑名单",
                "查看当前被全局禁用的表情清单。",
            ),
            (
                "<P>更新表情",
                "<P>更新表情",
                "重新探测后端并拉取全部表情索引。",
            ),
        ],
    ),
]


def _help_sections(prefix: str, is_admin: bool) -> List[Tuple[str, List[Tuple[str, str, str]]]]:
    """把模板里的 `<P>` 换成真实前缀，并按权限拼出分组。"""
    rows = _HELP_SECTIONS + (_ADMIN_HELP_SECTIONS if is_admin else [])
    return [
        (
            name,
            [
                (cmd.replace("<P>", prefix), eg.replace("<P>", prefix), desc)
                for cmd, eg, desc in items
            ],
        )
        for name, items in rows
    ]


@register(PLUGIN_NAME, "AwA", "表情包制作（轻量版）", "1.0.7")
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
            base_url=str(self._cfg("api_url", "")),
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

    # ------------------------------------------------------------------
    # 插件级黑白名单（语义对齐 astrbot_plugin_media_parser）
    # 优先级：管理员 > 个人白名单 > 个人黑名单 > 群组白名单 > 群组黑名单；
    # 未命中任何规则时：白名单启用=拒绝，未启用=允许。
    # 名单只限制表情生成，帮助/列表/统计/管理命令不受限。
    # ------------------------------------------------------------------
    def _permission_denied(self, event: AstrMessageEvent) -> bool:
        perm = self._cfg("permissions")
        if not isinstance(perm, dict):
            return False
        admin_id = str(perm.get("admin_id") or "").strip()
        sender_id = str(event.get_sender_id() or "").strip()
        if admin_id and sender_id == admin_id:
            return False

        group_id = str(event.get_group_id() or "").strip()
        is_private = not group_id or group_id == "0"

        def _ids(section: Any, key: str) -> list:
            if not isinstance(section, dict):
                return []
            vals = section.get(key) or []
            if not isinstance(vals, (list, tuple)):
                return []
            return [str(v).strip() for v in vals]

        wl = perm.get("whitelist") if isinstance(perm.get("whitelist"), dict) else {}
        bl = perm.get("blacklist") if isinstance(perm.get("blacklist"), dict) else {}

        allowed = None
        if wl and wl.get("enable") and sender_id in _ids(wl, "user"):
            allowed = True
        elif bl and bl.get("enable") and sender_id in _ids(bl, "user"):
            allowed = False
        elif not is_private and wl and wl.get("enable") and group_id in _ids(wl, "group"):
            allowed = True
        elif not is_private and bl and bl.get("enable") and group_id in _ids(bl, "group"):
            allowed = False

        if allowed is None:
            allowed = not bool(wl and wl.get("enable"))
        return not allowed

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
    def _strip_mentions(text: str, event: AstrMessageEvent) -> str:
        """把 message_str 里残留的 @昵称(QQ) 从台词中剔除。

        引用链：AstrBot 的 aiocqhttp 适配器在处理非首位的 @ 时，会把
        ``@昵称(QQ号)`` 追加进 message_str（见 aiocqhttp_platform_adapter.py）。
        而头像已经由 ``_at_ids`` 从 At 段单独取过，这里不清掉就会把 @ 当成台词。
        """
        if not text:
            return ""
        # 先按 At 段的真实昵称精确替换（段里没昵称时跳过，交给下面的正则兜底）
        for seg in event.message_obj.message:
            if not isinstance(seg, At):
                continue
            qq = str(seg.qq)
            name = str(getattr(seg, "name", "") or "")
            candidates = [f"@{qq}", f"@({qq})", f"[At:{qq}]"]
            if name:
                candidates.append(f"@{name}({qq})")
            for tok in candidates:
                text = text.replace(tok, " ")
        # 兜底：段里昵称与文本里的写法不一致（含全角括号等）时按模式清
        text = _MENTION_RE.sub(" ", text)
        return " ".join(text.split())

    @staticmethod
    def _own_image_urls(event: AstrMessageEvent) -> List[str]:
        """当前这条消息自己带的图。"""
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

    @staticmethod
    def _has_reply(event: AstrMessageEvent) -> bool:
        try:
            return any(isinstance(seg, Reply) for seg in event.message_obj.message)
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    async def _quoted_image_urls(event: AstrMessageEvent) -> List[str]:
        """引用消息里的图。

        引用的图片**不在**当前消息的 Image 段里 —— AstrBot 的 aiocqhttp 适配器把被引用
        消息整条塞进 ``Reply.chain``（拿不到时只留 ``Reply.id``），所以必须走框架自带的
        ``extract_quoted_message_images``：它先看 chain，chain 里没有再用 get_msg 回捞。
        不加这一步，引用图片发 `bq摸头` 会退化成拿发送者头像当素材。
        """
        if not MemeLitePlugin._has_reply(event):
            return []
        try:
            from astrbot.core.utils.quoted_message import extract_quoted_message_images

            refs = await extract_quoted_message_images(event)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[meme_lite] 取引用消息图片失败：{e}")
            return []
        return [str(r) for r in refs if r]

    async def _image_urls(self, event: AstrMessageEvent) -> List[str]:
        """素材图：自己的图在前，引用消息里的图在后（去重保序）。"""
        urls = self._own_image_urls(event)
        for url in await self._quoted_image_urls(event):
            if url not in urls:
                urls.append(url)
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
        # 插件级黑白名单：唯一出图口子，统一在此拦截（自动触发时静默，主动调用回提示）
        if self._permission_denied(event):
            return None, "本会话未开放表情包功能"
        channel = event.unified_msg_origin
        # @ 的人由 At 段单独取头像，文本里的 "@昵称(QQ)" 不能当台词（见 _strip_mentions）
        rest_text = self._strip_mentions(rest_text, event)
        sender_id = str(event.get_sender_id())
        sender_name = event.get_sender_name() or sender_id

        parsed = await collect_materials(
            info,
            rest_text,
            sender_id=sender_id,
            sender_name=sender_name,
            at_ids=self._at_ids(event),
            image_urls=await self._image_urls(event),
            max_image_mb=float(self._cfg("max_image_size_mb", 5)),
        )

        # 数量策略对齐 GsCore 的默认值（extra_text_policy / extra_image_policy = drop）：
        # 多出来的素材直接裁掉，只有"不够"才报错。
        # 必要性：单字关键词太容易把后面的字吃掉 —— `电死你` 命中的是关键词 `电`
        # （zaoleipi），剩下「死你」会被当成台词，而该表情 max_texts=0；
        # 不裁就会误判素材不符，GsCore 侧因为默认 drop 所以照样能出图。
        if info.max_images >= 0 and len(parsed.images) > info.max_images:
            parsed.images = parsed.images[: info.max_images]
            parsed.at_names = parsed.at_names[: info.max_images]
        if info.max_texts >= 0 and len(parsed.texts) > info.max_texts:
            parsed.texts = parsed.texts[: info.max_texts]

        if not info.accepts(len(parsed.images), len(parsed.texts)):
            need_img = info.image_requirement_text()
            need_txt = info.text_requirement_text()
            msg = (
                f"「{info.key}」需要图片 {need_img} 张、文字 {need_txt} 段，"
                f"当前收到 {len(parsed.images)} 张图 / {len(parsed.texts)} 段文字"
            )
            # 多图表情（2 人及以上）常见于"揍/击剑/双喜"这类，给一句怎么补人的提示
            if info.min_images >= 2 and len(parsed.images) < info.min_images:
                kw = info.keywords[0] if info.keywords else info.key
                msg += (
                    f"\n💡 这个表情要 {info.min_images} 个人的头像："
                    f"发「{kw} @某人 @某人」，或「{kw} 自己 @某人」（自己算第 1 个人）"
                )
            return None, msg

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
        has_prefix = False
        if prefix:
            if not raw.startswith(prefix):
                return
            raw = raw[len(prefix) :].strip()
            if not raw:
                return
            has_prefix = True

        # `bq表情列表` 这类：插件前缀 + 命令名 → 转给对应 handler
        if has_prefix:
            for cmd_name, handler_name in _CMD_ROUTES:
                if raw.startswith(cmd_name):
                    event.set_extra("meme_lite_done", True)
                    # 换成剥掉前缀后的文本，让 handler 里的 split(maxsplit=1) 取参正确
                    event.message_str = raw
                    async for result in getattr(self, handler_name)(event):
                        yield result
                    event.stop_event()
                    return

        if not self.client.is_ready:
            return
        if self._cooldown_hit(channel) and not has_prefix:
            return

        # 无前缀时要求关键词 ≥2 字：单字（搓 / 哈 / 摸 / 亲…）太容易撞上正常聊天，
        # 如「搓破过好几次了」会命中 twist。带前缀属于主动调用，单字也放行。
        info = self._match_keyword(raw, min_kw_len=1 if has_prefix else 2)
        if info is None:
            return
        reason = self._can_use(channel, info.key)
        if reason:
            return

        event.set_extra("meme_lite_done", True)
        rest = raw[len(info.matched_keyword(raw) or "") :].strip()
        path, note = await self._generate(event, info, rest)
        if not path:
            # 自动触发不满足条件时静默，避免往群里回「需要图片 X 张…」这类提示
            if has_prefix and note:
                yield event.plain_result(note)
            return
        event.stop_event()
        if note:
            yield event.plain_result(note)
        yield event.image_result(path)

    def _match_keyword(self, text: str, min_kw_len: int = 1) -> Optional[MemeInfo]:
        """在本地索引里找以 text 开头命中的最长关键词。

        Args:
            min_kw_len: 关键词最短长度。无前缀的自动触发建议传 ``2``，
                避免「搓」「哈」这类单字词撞上正常聊天。
        """
        best: Optional[MemeInfo] = None
        best_len = 0
        for info in self.client.all_infos():
            kw = info.matched_keyword(text)
            if kw and len(kw) >= min_kw_len and len(kw) > best_len:
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
        n_img = len(await self._image_urls(event))

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
        """列出全部可用表情（渲染成图，分段显示可用 / 本会话禁用 / 全局禁用）。"""
        if not self.client.is_ready:
            yield event.plain_result("表情索引还没就绪，先发一次 /更新表情")
            return
        channel = event.unified_msg_origin

        items: List[Tuple[str, str, bool, bool]] = []
        for info in self.client.all_infos():
            keywords = "、".join(info.keywords) if info.keywords else info.key
            gd = self.store.is_global_disabled(info.key)
            ud = (not gd) and self.store.is_user_disabled(channel, info.key)
            items.append((info.key, keywords, ud, gd))

        prefix = str(self._cfg("trigger_prefix", "") or "")
        subtitle = (
            f"共 {len(items)} 个表情 ｜ 后端：{self.client.backend.upper()} ｜ "
            f"用法：{prefix}关键词 + 图片/文字/@QQ号/自己"
        )
        try:
            img = await asyncio.to_thread(
                render_meme_list, items, title="表情列表", subtitle=subtitle
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[meme_lite] 渲染表情列表失败，退回文字：{e}")
            yield event.plain_result(self._plain_list(items, prefix))
            return

        yield event.image_result(self._save_tmp(img))

    @staticmethod
    def _plain_list(items: List[Tuple[str, str, bool, bool]], prefix: str) -> str:
        """渲染失败时的纯文字兜底（长列表截断，避免超长消息）。"""
        usable = [k for k, _, ud, gd in items if not ud and not gd]
        mine = [k for k, _, ud, gd in items if ud and not gd]
        glob = [k for k, _, _, gd in items if gd]
        lines = [f"📋 可用表情（{len(usable)}）", "、".join(usable[:400])]
        if mine:
            lines.append(f"\n🚫 本会话禁用（{len(mine)}）：{'、'.join(mine[:80])}")
        if glob:
            lines.append(f"\n⛔ 全局禁用（{len(glob)}）：{'、'.join(glob[:80])}")
        lines.append(f"\n用法：{prefix}关键词，如「{prefix}摸头」；搜关键词用 {prefix}表情搜索")
        return "\n".join(lines)

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
        """查看用法（渲染成帮助图，管理员多一栏全局命令）。"""
        prefix = str(self._cfg("trigger_prefix", "") or "")
        sections = _help_sections(prefix, self._require_admin(event))
        subtitle = (
            f"前缀 {prefix or '（无）'} ｜ 后端 {self.client.backend.upper()} ｜ "
            f"表情 {len(self.client.all_infos())} 个"
        )
        footer = (
            f"示例：{prefix}摸头 自己 ／ {prefix}表情详情 摸头 看单个表情的参数。"
            f"关键词不做前缀时也支持自动触发（需 ≥2 字）。"
        )
        try:
            img = await asyncio.to_thread(
                render_command_help,
                sections,
                title="表情帮助",
                subtitle=subtitle,
                footer=footer,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[meme_lite] 渲染帮助图失败，退回文字：{e}")
            yield event.plain_result(self._plain_help(prefix))
            return

        yield event.image_result(self._save_tmp(img))

    @staticmethod
    def _plain_help(prefix: str) -> str:
        """渲染失败时的纯文字兜底。"""
        p = prefix
        return (
            "🎭 表情包制作（轻量版）\n"
            f"【出图】{p}<关键词> [文字/图/@QQ/自己]\n"
            f"【随机】{p}随机表情 [文字/图/@]\n"
            f"【查询】{p}表情列表 ｜ {p}表情搜索 <词> ｜ {p}表情详情 <关键词> ｜ {p}表情统计 [条数]\n"
            f"【个人】{p}禁用表情 <词> ｜ {p}启用表情 <词> ｜ {p}屏蔽表情 <词> ｜ {p}解除屏蔽 <词>\n"
            f"【本群】{p}开启表情包 ｜ {p}关闭表情包 ｜ {p}表情包状态\n"
            f"【管理】{p}全局禁用表情 <词> ｜ {p}全局启用表情 <词> ｜ {p}表情黑名单 ｜ {p}更新表情（管理员）"
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
