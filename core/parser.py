"""从消息中解析表情生成所需的素材与选项。

支持 token：
- ``自己``         → 用发送者头像
- ``@123456``      → 指定 QQ 头像（AstrBot 的 At 段也会自动并入）
- ``--<选项> <值>`` → 表情专属选项
- 其余文本         → 作为台词（单段文字的表情：整段保留，可含空格）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .models import MemeInfo
from .utils import compress_if_needed, fetch_image, fetch_qq_avatar


@dataclass
class ParseResult:
    images: List[Tuple[str, bytes]] = field(default_factory=list)
    texts: List[str] = field(default_factory=list)
    options: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    at_names: List[str] = field(default_factory=list)


def _coerce(value: str, value_type: str) -> Any:
    v = value.strip()
    if value_type in ("boolean", "bool"):
        return v.lower() in ("1", "true", "yes", "on", "是", "开")
    if value_type in ("integer", "int"):
        try:
            return int(v)
        except ValueError:
            return v
    if value_type in ("number", "float"):
        try:
            return float(v)
        except ValueError:
            return v
    return v


def split_options(info: MemeInfo, rest: str) -> Tuple[str, Dict[str, Any]]:
    """从文本里摘出 `--opt value` 形式的选项，返回 (剩余文本, 选项字典)。"""
    if not rest:
        return "", {}

    long_to_dest = {}
    for opt in info.options:
        for alias in opt.aliases:
            if alias.startswith("--"):
                long_to_dest[alias[2:]] = opt
        if opt.dest:
            long_to_dest.setdefault(opt.dest, opt)

    tokens = rest.split()
    kept: List[str] = []
    options: Dict[str, Any] = {}
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("--") and tok[2:] in long_to_dest:
            opt = long_to_dest[tok[2:]]
            if opt.takes_value and i + 1 < len(tokens):
                options[opt.dest] = _coerce(tokens[i + 1], opt.value_type)
                i += 2
                continue
            if not opt.takes_value:
                options[opt.dest] = True
                i += 1
                continue
        kept.append(tok)
        i += 1

    return " ".join(kept).strip(), options


async def collect_materials(
    info: MemeInfo,
    rest_text: str,
    *,
    sender_id: str,
    sender_name: str,
    at_ids: List[str],
    image_urls: List[str],
    max_image_mb: float = 5.0,
) -> ParseResult:
    """按表情需求收集素材。

    - 图片：消息自带图 + `@某人` + `自己`
    - 文字：剩余文本；若表情只需 1 段文字，则整段保留
    """
    result = ParseResult()

    # 1) 消息里附带的图片
    for url in image_urls:
        data = await fetch_image(url)
        if data:
            result.images.append(("image", compress_if_needed(data, max_image_mb)))
        else:
            result.notes.append("有一张图片下载失败")

    # 2) 选项
    text_part, options = split_options(info, rest_text)
    result.options = options

    # 3) `自己` 与 `@某人`
    tokens = text_part.split()
    keep_tokens: List[str] = []
    need_self = False
    for tok in tokens:
        if tok == "自己":
            need_self = True
        else:
            keep_tokens.append(tok)

    if need_self:
        avatar = await fetch_qq_avatar(sender_id)
        if avatar:
            result.images.append((sender_name or sender_id, compress_if_needed(avatar, max_image_mb)))
            result.at_names.append(sender_name or sender_id)
        else:
            result.notes.append("取不到你的头像")

    for uid in at_ids:
        uid = str(uid)
        if not uid.isdigit():
            continue
        if uid == str(sender_id) and need_self:
            continue
        avatar = await fetch_qq_avatar(uid)
        if avatar:
            result.images.append((uid, compress_if_needed(avatar, max_image_mb)))
            result.at_names.append(uid)
        else:
            result.notes.append(f"取不到 @{uid} 的头像")

    # 4) 文字
    remaining = " ".join(keep_tokens).strip()
    if remaining:
        if info.max_texts == 1:
            result.texts = [remaining.replace("\\n", "\n")]
        else:
            result.texts = [t for t in remaining.split("|") if t.strip()]

    # 5) 兜底：需要图但一张都没有 → 用发送者头像
    if info.min_images >= 1 and not result.images:
        avatar = await fetch_qq_avatar(sender_id)
        if avatar:
            result.images.append((sender_name or sender_id, compress_if_needed(avatar, max_image_mb)))
            result.at_names.append(sender_name or sender_id)

    # 6) 兜底：需要文字且后端有默认值
    if info.min_texts > 0 and len(result.texts) < info.min_texts:
        if info.default_texts:
            result.texts = list(info.default_texts)
        elif result.at_names:
            result.texts = result.at_names[: info.min_texts]

    return result
