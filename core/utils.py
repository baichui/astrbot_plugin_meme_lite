"""素材（图片 / 头像）获取与轻量处理。"""

from __future__ import annotations

from typing import Optional

import httpx
from astrbot.api import logger

_AVATAR_URL = "https://q1.qlogo.cn/g?b=qq&nk={uid}&s=640"

_DEFAULT_TIMEOUT = 20.0


async def fetch_qq_avatar(uid: str) -> Optional[bytes]:
    """取 QQ 头像字节。"""
    uid = str(uid).strip()
    if not uid.isdigit():
        return None
    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(_AVATAR_URL.format(uid=uid))
        if resp.status_code == 200 and resp.content:
            return resp.content
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[meme_lite] 取头像失败 uid={uid}: {e}")
    return None


async def fetch_image(url: str) -> Optional[bytes]:
    """下载图片（用于消息里的图片 / 引用消息图片）。"""
    url = (url or "").strip()
    if not url:
        return None
    if url.startswith("base64://"):
        import base64

        try:
            return base64.b64decode(url[len("base64://") :])
        except Exception:  # noqa: BLE001
            return None
    if url.startswith("file://"):
        from pathlib import Path

        try:
            return Path(url[len("file://") :]).read_bytes()
        except Exception:  # noqa: BLE001
            return None
    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url)
        if resp.status_code == 200 and resp.content:
            return resp.content
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[meme_lite] 下载图片失败：{e}")
    return None


def compress_if_needed(data: bytes, max_mb: float = 5.0) -> bytes:
    """超过上限则压缩（转 JPEG 并降质量），避免拖慢后端生成。"""
    if not data:
        return data
    limit = int(max_mb * 1024 * 1024)
    if limit <= 0 or len(data) <= limit:
        return data

    try:
        import io

        from PIL import Image
    except ImportError:  # pragma: no cover
        logger.warning("[meme_lite] 未安装 Pillow，跳过压缩")
        return data

    try:
        img = Image.open(io.BytesIO(data))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        if max(img.size) > 1600:
            ratio = 1600 / max(img.size)
            img = img.resize((int(img.width * ratio), int(img.height * ratio)))

        for quality in (85, 70, 55, 40):
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality)
            out = buf.getvalue()
            if len(out) <= limit:
                return out
        return out
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[meme_lite] 图片压缩失败，透传原图：{e}")
        return data
