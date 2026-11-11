"""素材（图片 / 头像）获取与轻量处理。"""

from __future__ import annotations

from typing import Optional

from astrbot.api import logger

from .client import new_client

# qlogo 有 q1–q4 四个节点；单节点 DNS/网络抽风时轮换重试，别一棍子打死
_AVATAR_HOSTS = ("q1.qlogo.cn", "q2.qlogo.cn", "q3.qlogo.cn", "q4.qlogo.cn")
# 先要 640；动图（GIF）头像在 640 档 qlogo 会退化成 40×40 的默认企鹅占位图，
# 这时降到 140 —— 动图账号只在这个档位才返回真实的 100×100 小动图。
_AVATAR_SIZES = (640, 140)

_DEFAULT_TIMEOUT = 20.0


def _is_placeholder(data: bytes, want: int) -> bool:
    """判断 qlogo 返回的是不是"默认头像"占位图。

    实测：动图头像请求 s=640 会拿到 40×40 JPEG（红围巾企鹅），s=140 才是
    100×100 的真实动图。用「实际边长远小于请求边长」识别。
    """
    try:
        import io

        from PIL import Image

        im = Image.open(io.BytesIO(data))
        longest = max(im.size)
        im.close()
        return longest * 2 < want
    except Exception:  # noqa: BLE001
        return len(data) < 2048


async def fetch_qq_avatar(uid: str, appid: Optional[str] = None) -> Optional[bytes]:
    """取 QQ 头像字节。

    - QQ 号（纯数字）：q1–q4 节点轮换，自动跳过动图头像的 40×40 占位图；
    - 非 QQ 号（QQ 官方机器人的 openid）：走 ``q.qlogo.cn/qqapp/{appid}/{openid}/``
      专用接口，需要传入 appid；没有 appid 就取不到，返回 None。
    """
    uid = str(uid).strip()
    if uid.isdigit():
        return await _fetch_qlogo_avatar(uid)
    if appid:
        return await _fetch_official_avatar(uid, str(appid).strip())
    return None


def resolve_official_appid(platform_id: str = "") -> Optional[str]:
    """从 AstrBot 全局配置里找 QQ 官方机器人的 appid。

    优先匹配 ``platform_id``（平台实例 id），找不到就退回第一个
    ``type == "qq_official"`` 且填了 appid 的实例。
    """
    try:
        from astrbot.core import astrbot_config
    except Exception:  # noqa: BLE001
        return None
    fallback: Optional[str] = None
    for p in astrbot_config.get("platform") or []:
        if not isinstance(p, dict) or p.get("type") != "qq_official":
            continue
        appid = str(p.get("appid") or "").strip()
        if not appid:
            continue
        if platform_id and p.get("id") == platform_id:
            return appid
        if fallback is None:
            fallback = appid
    return fallback


async def _fetch_qlogo_avatar(uid: str) -> Optional[bytes]:
    fallback: Optional[bytes] = None
    last_err: Optional[str] = None
    for host in _AVATAR_HOSTS:
        url = f"https://{host}/g?b=qq&nk={{uid}}&s={{size}}"
        try:
            async with new_client(_DEFAULT_TIMEOUT, follow_redirects=True) as client:
                for size in _AVATAR_SIZES:
                    resp = await client.get(url.format(uid=uid, size=size))
                    if resp.status_code != 200 or not resp.content:
                        continue
                    if not _is_placeholder(resp.content, size):
                        return resp.content
                    if fallback is None:
                        fallback = resp.content
            break  # 该节点全档位成功（含占位图），不再换节点
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"
            logger.warning(f"[meme_lite] 取头像失败 {host} uid={uid}: {e}")
    if last_err and fallback is None:
        logger.warning(f"[meme_lite] 取头像最终失败 uid={uid}（所有节点均不可达）")
    return fallback


async def fetch_image(url: str) -> Optional[bytes]:
    """取图片字节（用于消息里的图片 / 引用消息图片）。

    素材有三种形态，都要认：
    - ``http(s)://``            网络图
    - ``base64://`` / ``data:`` 内联
    - ``file://`` 或**裸本地路径** —— AstrBot 的引用解析器
      （``extract_quoted_message_images``）在图片已缓存到本地时，返回的就是本地路径，
      不认它就会报 ``Request URL is missing an 'http://' or 'https://' protocol``。
    """
    url = (url or "").strip()
    if not url:
        return None
    if url.startswith("base64://"):
        import base64

        try:
            return base64.b64decode(url[len("base64://") :])
        except Exception:  # noqa: BLE001
            return None
    if url.startswith("data:"):
        import base64

        head, _, payload = url.partition(",")
        if ";base64" not in head:
            return None
        try:
            return base64.b64decode(payload)
        except Exception:  # noqa: BLE001
            return None
    if url.startswith("file://"):
        from pathlib import Path

        try:
            return Path(url[len("file://") :]).read_bytes()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[meme_lite] 读取本地图片失败 {url[:80]}: {e}")
            return None
    if not url.startswith(("http://", "https://")):
        from pathlib import Path

        path = Path(url)
        if path.is_file():
            try:
                return path.read_bytes()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[meme_lite] 读取本地图片失败 {url[:80]}: {e}")
                return None
        logger.warning(f"[meme_lite] 无法识别的图片来源（既不是链接也不是本地文件）：{url[:80]}")
        return None
    try:
        async with new_client(_DEFAULT_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url)
        if resp.status_code == 200 and resp.content:
            return resp.content
        logger.warning(f"[meme_lite] 下载图片失败 HTTP {resp.status_code}：{url[:80]}")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[meme_lite] 下载图片失败：{e} ({url[:80]})")
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


# 官方机器人 openid 头像只有两档可用：640（原图）与 100
_OFFICIAL_AVATAR_SIZES = (640, 100)


async def _fetch_official_avatar(openid: str, appid: str) -> Optional[bytes]:
    """取 QQ 官方机器人用户的头像。

    接口：``https://q.qlogo.cn/qqapp/{appid}/{openid}/{size}``，
    size 可用 640 / 100（实测 140 档不可用）。openid 不经校验的话
    qlogo 对不存在的组合返回 404 / 空，直接按失败处理。
    """
    last_err: Optional[str] = None
    for size in _OFFICIAL_AVATAR_SIZES:
        url = f"https://q.qlogo.cn/qqapp/{appid}/{openid}/{size}"
        try:
            async with new_client(_DEFAULT_TIMEOUT, follow_redirects=True) as client:
                resp = await client.get(url)
                if resp.status_code == 200 and resp.content:
                    return resp.content
                last_err = f"HTTP {resp.status_code}"
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"
            logger.warning(f"[meme_lite] 取官方头像失败 appid={appid} openid={openid[:8]}…: {e}")
    if last_err:
        logger.warning(f"[meme_lite] 取官方头像最终失败 openid={openid[:8]}…: {last_err}")
    return None
