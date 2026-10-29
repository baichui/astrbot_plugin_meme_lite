"""meme-generator 后端客户端。

兼容 Python 版（`/memes/...`）与 Rust 版（`/meme/...`）两套接口，
启动时自动探测；探测结果与索引会缓存到插件数据目录，避免每次重拉。

来源与致谢：
    后端接口与生成能力来自 MemeCrafters/meme-generator (MIT)
    与 MemeCrafters/meme-generator-rs (MIT)。
    接口路径与字段解析口径参考 Loping151/core_plugin_memes (GPL-3.0-or-later)。
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from astrbot.api import logger

from .models import MemeInfo

_BACKEND_PY = "py"
_BACKEND_RS = "rs"


class MemeClientError(Exception):
    """后端交互异常。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def new_client(timeout: float, **kwargs: Any) -> httpx.AsyncClient:
    """创建强制走 IPv4 的 httpx 客户端。

    手机端（尤其安卓一体包）默认会优先解析到 Cloudflare 的 IPv6 记录，
    而 httpx 不像浏览器那样在 IPv6 不通时快速回退 IPv4，往往一路超时，
    最后只表现为「无法连接后端」。绑定本地 0.0.0.0 可强制 IPv4 出站。
    若当前环境不支持该绑定参数，则退回普通客户端，不影响可用性。
    """
    try:
        transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0")
        return httpx.AsyncClient(timeout=timeout, transport=transport, **kwargs)
    except Exception:  # noqa: BLE001
        return httpx.AsyncClient(timeout=timeout, **kwargs)


class MemeClient:
    """表情后端的统一封装。"""

    def __init__(
        self,
        base_url: str,
        timeout: float = 60.0,
        cache_path: Optional[Path] = None,
        cache_hours: float = 24.0,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.timeout = max(5.0, float(timeout))
        self.cache_path = cache_path
        self.cache_hours = max(0.0, float(cache_hours))

        self._backend: Optional[str] = None
        self._keys: List[str] = []
        self._info: Dict[str, MemeInfo] = {}
        self._last_refresh: float = 0.0
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # 基础
    # ------------------------------------------------------------------
    def _url(self, path: str) -> str:
        if not self.base_url:
            raise MemeClientError("未配置后端地址（api_url）")
        return self.base_url + path

    @staticmethod
    def _raise_for_error(resp: httpx.Response) -> None:
        if resp.status_code >= 400:
            raise MemeClientError(f"后端返回 {resp.status_code}：{resp.text[:120]}")

    async def detect_backend(self, force: bool = False) -> str:
        """探测后端版本：Python 版走 `/memes/keys`，Rust 版走 `/meme/keys`。"""
        if self._backend and not force:
            return self._backend

        if not self.base_url:
            raise MemeClientError("未配置后端地址（api_url）")

        reasons: List[str] = []
        async with new_client(10) as client:
            for backend, path in ((_BACKEND_PY, "/memes/keys"), (_BACKEND_RS, "/meme/keys")):
                try:
                    resp = await client.get(self._url(path))
                    if resp.status_code == 200 and isinstance(resp.json(), list):
                        self._backend = backend
                        logger.info(f"[meme_lite] 后端探测：{backend} 版")
                        return backend
                    reasons.append(f"{path} 返回 HTTP {resp.status_code}")
                except Exception as e:  # noqa: BLE001
                    reasons.append(f"{path} 连接失败（{type(e).__name__}: {e}）")
                    logger.debug(f"[meme_lite] {path} 探测失败：{e}")

        raise MemeClientError(
            f"无法连接 meme 后端（{self.base_url}）：" + "；".join(reasons)
        )

    @property
    def is_ready(self) -> bool:
        return bool(self._keys) and bool(self._info)

    @property
    def backend(self) -> str:
        """当前后端标识（py / rs），未探测到时返回 ``?``。"""
        return self._backend or "?"

    @property
    def last_refresh(self) -> float:
        return self._last_refresh

    # ------------------------------------------------------------------
    # 索引（keys + infos）
    # ------------------------------------------------------------------
    async def get_keys(self, force: bool = False) -> List[str]:
        if self._keys and not force:
            return self._keys
        backend = await self.detect_backend()
        path = "/memes/keys" if backend == _BACKEND_PY else "/meme/keys"
        async with new_client(self.timeout) as client:
            resp = await client.get(self._url(path))
        self._raise_for_error(resp)
        keys = resp.json()
        if not isinstance(keys, list):
            raise MemeClientError("后端返回的 keys 格式异常")
        self._keys = sorted(str(k) for k in keys)
        return self._keys

    async def get_info(self, key: str, force: bool = False) -> MemeInfo:
        if not force and key in self._info:
            return self._info[key]
        backend = await self.detect_backend()
        path = f"/memes/{key}/info" if backend == _BACKEND_PY else f"/meme/{key}/info"
        async with new_client(self.timeout) as client:
            resp = await client.get(self._url(path))
        self._raise_for_error(resp)
        raw = resp.json()
        info = MemeInfo.from_py(raw) if backend == _BACKEND_PY else MemeInfo.from_rs(raw)
        self._info[key] = info
        return info

    def get_cached(self, key: str) -> Optional[MemeInfo]:
        """只从内存索引取，不发请求。"""
        return self._info.get(key)

    def all_infos(self) -> List[MemeInfo]:
        return list(self._info.values())

    def search_local(self, query: str, limit: int = 20) -> List[MemeInfo]:
        """在本地索引里按关键词/标签/题材模糊搜（不联网）。"""
        q = (query or "").strip().lower()
        if not q:
            return []
        hits: List[Tuple[int, MemeInfo]] = []
        for info in self._info.values():
            score = 0
            for kw in info.keywords:
                if q == kw.lower():
                    score = max(score, 100)
                elif q in kw.lower():
                    score = max(score, 60)
            if q in info.key.lower():
                score = max(score, 40)
            for tag in info.tags:
                if q in tag.lower():
                    score = max(score, 30)
            if score:
                hits.append((score, info))
        hits.sort(key=lambda x: (-x[0], x[1].key))
        return [info for _, info in hits[:limit]]

    async def refresh_all(self) -> Tuple[int, int]:
        """全量拉取 keys + 所有 info。返回 (成功数, 失败数)。"""
        async with self._lock:
            backend = await self.detect_backend(force=True)
            keys = await self.get_keys(force=True)
            self._info.clear()

            if backend == _BACKEND_RS:
                try:
                    async with new_client(self.timeout) as client:
                        resp = await client.get(self._url("/meme/infos"))
                    self._raise_for_error(resp)
                    raw_list = resp.json()
                    if isinstance(raw_list, list):
                        ok = 0
                        for raw in raw_list:
                            try:
                                info = MemeInfo.from_rs(raw)
                                self._info[info.key] = info
                                ok += 1
                            except Exception:  # noqa: BLE001
                                pass
                        self._last_refresh = time.time()
                        return ok, len(raw_list) - ok
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"[meme_lite] /meme/infos 失败，回退逐条拉取：{e}")

            # Python 版（或 Rust 兜底）：并发逐条拉
            sem = asyncio.Semaphore(32)
            ok = 0
            fail = 0

            async with new_client(
                self.timeout,
                limits=httpx.Limits(max_keepalive_connections=32, max_connections=64),
            ) as client:

                async def _one(key: str) -> None:
                    nonlocal ok, fail
                    async with sem:
                        try:
                            resp = await client.get(self._url(f"/memes/{key}/info"))
                            if resp.status_code >= 400:
                                fail += 1
                                return
                            info = MemeInfo.from_py(resp.json())
                            if info.key:
                                self._info[info.key] = info
                                ok += 1
                            else:
                                fail += 1
                        except Exception:  # noqa: BLE001
                            fail += 1

                await asyncio.gather(*[_one(k) for k in keys])

            self._last_refresh = time.time()
            return ok, fail

    # ------------------------------------------------------------------
    # 索引磁盘缓存
    # ------------------------------------------------------------------
    def load_cache(self) -> bool:
        """从磁盘加载索引缓存。命中返回 True。"""
        if not self.cache_path or not self.cache_path.exists():
            return False
        try:
            raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[meme_lite] 索引缓存读取失败：{e}")
            return False

        ts = float(raw.get("timestamp") or 0)
        if self.cache_hours > 0 and (time.time() - ts) > self.cache_hours * 3600:
            logger.info("[meme_lite] 索引缓存已过期，将重新拉取")
            return False

        infos = raw.get("infos") or []
        for d in infos:
            try:
                info = MemeInfo.from_dict(d)
                if info.key:
                    self._info[info.key] = info
            except Exception:  # noqa: BLE001
                continue
        self._keys = sorted(self._info.keys())
        self._backend = str(raw.get("backend") or _BACKEND_PY)
        self._last_refresh = ts
        logger.info(f"[meme_lite] 已从缓存加载 {len(self._info)} 个表情索引")
        return bool(self._info)

    def save_cache(self) -> None:
        if not self.cache_path:
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "timestamp": self._last_refresh or time.time(),
                "backend": self._backend or _BACKEND_PY,
                "infos": [i.to_dict() for i in self._info.values()],
            }
            self.cache_path.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
            logger.info(f"[meme_lite] 索引已缓存（{len(self._info)} 条）")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[meme_lite] 索引缓存写入失败：{e}")

    # ------------------------------------------------------------------
    # 生成
    # ------------------------------------------------------------------
    async def generate(
        self,
        key: str,
        images: List[Tuple[str, bytes]],
        texts: List[str],
        options: Dict[str, Any],
        user_infos: Optional[List[Dict[str, Any]]] = None,
    ) -> bytes:
        """调用后端生成表情，返回图片字节。"""
        backend = await self.detect_backend()
        async with new_client(self.timeout) as client:
            if backend == _BACKEND_PY:
                args: Dict[str, Any] = dict(options or {})
                if user_infos is not None:
                    args["user_infos"] = user_infos
                files = [("images", (name, data)) for name, data in images]
                form: Dict[str, Any] = {
                    "texts": texts,
                    "args": json.dumps(args, ensure_ascii=False),
                }
                resp = await client.post(
                    self._url(f"/memes/{key}/"), files=files or None, data=form
                )
                self._raise_for_error(resp)
                return resp.content

            # Rust 版：先上传图片拿 id，再按 json 生成，最后取图
            image_ids: List[Dict[str, str]] = []
            for name, data in images:
                up = await client.post(
                    self._url("/image/upload"),
                    json={"image": __import__("base64").b64encode(data).decode()},
                )
                self._raise_for_error(up)
                image_ids.append({"name": name, "id": str(up.json().get("image_id"))})

            payload = {"images": image_ids, "texts": texts, "options": options or {}}
            resp = await client.post(self._url(f"/memes/{key}"), json=payload)
            self._raise_for_error(resp)
            image_id = resp.json().get("image_id")
            got = await client.get(self._url(f"/image/{image_id}"))
            self._raise_for_error(got)
            return got.content
