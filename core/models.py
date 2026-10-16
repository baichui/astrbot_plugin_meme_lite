"""数据模型：表情元信息与可选参数。

来源与致谢：
    `MemeInfo.from_py()` / `from_rs()` 的字段映射口径参考
    Loping151/core_plugin_memes (GPL-3.0-or-later) 的
    `utils/client.py::_normalize_py_info`；
    元信息本身来自 MemeCrafters/meme-generator 后端 (MIT)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class MemeOption:
    """表情的一个可选参数（如 `--circle true`）。

    由后端 `params_type.args_type.parser_options` 解析而来。
    """

    dest: str
    aliases: List[str] = field(default_factory=list)
    help_text: str = ""
    default: Any = None
    takes_value: bool = True
    value_type: str = "string"

    @property
    def display(self) -> str:
        if self.aliases:
            return " / ".join(self.aliases)
        return f"--{self.dest}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dest": self.dest,
            "aliases": list(self.aliases),
            "help_text": self.help_text,
            "default": self.default,
            "takes_value": self.takes_value,
            "value_type": self.value_type,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MemeOption":
        return cls(
            dest=str(d.get("dest") or ""),
            aliases=[str(a) for a in (d.get("aliases") or [])],
            help_text=str(d.get("help_text") or ""),
            default=d.get("default"),
            takes_value=bool(d.get("takes_value", True)),
            value_type=str(d.get("value_type") or "string"),
        )


@dataclass
class MemeInfo:
    """单个表情的元信息（跨 Python / Rust 后端归一化）。"""

    key: str
    keywords: List[str] = field(default_factory=list)
    min_images: int = 0
    max_images: int = 0
    min_texts: int = 0
    max_texts: int = 0
    default_texts: List[str] = field(default_factory=list)
    options: List[MemeOption] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # 构造
    # ------------------------------------------------------------------
    @classmethod
    def from_py(cls, raw: Dict[str, Any]) -> "MemeInfo":
        """Python 版 meme-generator 的 `/memes/{key}/info`。

        结构：{key, params_type: {min_images, ..., args_type: {...}}, keywords, tags}
        """
        params = raw.get("params_type") or {}
        args_type = params.get("args_type") or {}
        return cls(
            key=str(raw.get("key") or ""),
            keywords=[str(k) for k in (raw.get("keywords") or [])],
            min_images=int(params.get("min_images") or 0),
            max_images=int(params.get("max_images") or 0),
            min_texts=int(params.get("min_texts") or 0),
            max_texts=int(params.get("max_texts") or 0),
            default_texts=[str(t) for t in (params.get("default_texts") or [])],
            options=cls._parse_options(args_type),
            tags=[str(t) for t in (raw.get("tags") or [])],
        )

    @classmethod
    def from_rs(cls, raw: Dict[str, Any]) -> "MemeInfo":
        """Rust 版 meme-generator 的 info（字段大多在顶层）。"""
        params = raw.get("params") or raw.get("params_type") or {}
        args_type = params.get("args_type") or {}
        return cls(
            key=str(raw.get("key") or ""),
            keywords=[str(k) for k in (raw.get("keywords") or [])],
            min_images=int(params.get("min_images") or raw.get("min_images") or 0),
            max_images=int(params.get("max_images") or raw.get("max_images") or 0),
            min_texts=int(params.get("min_texts") or raw.get("min_texts") or 0),
            max_texts=int(params.get("max_texts") or raw.get("max_texts") or 0),
            default_texts=[
                str(t) for t in (params.get("default_texts") or raw.get("default_texts") or [])
            ],
            options=cls._parse_options(args_type),
            tags=[str(t) for t in (raw.get("tags") or [])],
        )

    @staticmethod
    def _parse_options(args_type: Dict[str, Any]) -> List[MemeOption]:
        """把后端 args_type 里的 parser_options 转成 MemeOption 列表。"""
        if not isinstance(args_type, dict):
            return []
        raw_opts = args_type.get("parser_options") or []
        if not isinstance(raw_opts, list):
            return []

        args_model = args_type.get("args_model") or {}
        props = args_model.get("properties", {}) if isinstance(args_model, dict) else {}

        result: List[MemeOption] = []
        for opt in raw_opts:
            if not isinstance(opt, dict):
                continue
            names = [str(n) for n in (opt.get("names") or []) if n]
            short_aliases = [n for n in names if not n.startswith("--")]
            long_aliases = [n[2:] for n in names if n.startswith("--")]
            dest = str(opt.get("dest") or (long_aliases[0] if long_aliases else "") or (
                short_aliases[0] if short_aliases else ""
            ))
            if not dest:
                continue

            opt_args = opt.get("args") or []
            takes_value = bool(opt_args)
            field_meta = props.get(dest, {}) if isinstance(props, dict) else {}
            value_type = "string"
            if takes_value:
                value_type = str((opt_args[0] or {}).get("value") or "string")
            else:
                value_type = str(field_meta.get("type") or "boolean")

            result.append(
                MemeOption(
                    dest=dest,
                    aliases=names,
                    help_text=str(opt.get("help_text") or field_meta.get("description") or ""),
                    default=opt.get("default"),
                    takes_value=takes_value,
                    value_type=value_type,
                )
            )
        return result

    # ------------------------------------------------------------------
    # 序列化（用于磁盘索引缓存）
    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "keywords": list(self.keywords),
            "min_images": self.min_images,
            "max_images": self.max_images,
            "min_texts": self.min_texts,
            "max_texts": self.max_texts,
            "default_texts": list(self.default_texts),
            "options": [o.to_dict() for o in self.options],
            "tags": list(self.tags),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MemeInfo":
        return cls(
            key=str(d.get("key") or ""),
            keywords=[str(k) for k in (d.get("keywords") or [])],
            min_images=int(d.get("min_images") or 0),
            max_images=int(d.get("max_images") or 0),
            min_texts=int(d.get("min_texts") or 0),
            max_texts=int(d.get("max_texts") or 0),
            default_texts=[str(t) for t in (d.get("default_texts") or [])],
            options=[MemeOption.from_dict(o) for o in (d.get("options") or [])],
            tags=[str(t) for t in (d.get("tags") or [])],
        )

    # ------------------------------------------------------------------
    # 能力判断
    # ------------------------------------------------------------------
    @property
    def needs_image(self) -> bool:
        return self.min_images > 0

    def image_requirement_text(self) -> str:
        if self.min_images == self.max_images:
            return str(self.min_images)
        return f"{self.min_images} ~ {self.max_images}"

    def text_requirement_text(self) -> str:
        if self.min_texts == self.max_texts:
            return str(self.min_texts)
        return f"{self.min_texts} ~ {self.max_texts}"

    def accepts(self, n_images: int, n_texts: int) -> bool:
        """给定的图片/文字数量能否满足该表情。"""
        return (
            self.min_images <= n_images <= self.max_images
            and self.min_texts <= n_texts <= self.max_texts
        )

    def auto_fillable(self, n_images: int) -> bool:
        """在只给 n_images 张图、且不另给文字时，能否靠默认值凑齐。"""
        if not (self.min_images <= n_images <= self.max_images):
            return False
        if self.min_texts <= 0:
            return True
        return bool(self.default_texts)

    def matched_keyword(self, text: str) -> Optional[str]:
        """返回该表情在 text 开头命中的关键词（长度优先）。"""
        best: Optional[str] = None
        for kw in sorted(self.keywords, key=len, reverse=True):
            if kw and text.startswith(kw):
                best = kw
                break
        return best
