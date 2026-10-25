"""重新生成内置字体子集（NotoSerifSC-Subset.ttf / NotoSansSC-Subset.ttf）。

用途：emote 关键词里出现生僻字、渲染成方块时，重跑一次把新字补进子集。
字符集 = GB2312 全字 + 指定表情索引里的全部关键词用字 + ASCII 可见字符。

依赖 fontTools（`pip install fonttools`），仅在生成时需要，运行插件不需要。

用法：
    python build_subset.py --index <meme_index.json> --out .
    python build_subset.py --index <meme_index.json> --out . --serif-src D:/fonts/NotoSerifSC-VF.ttf

源字体取系统的 Noto Sans/Serif SC（SIL OFL 1.1，允许随插件分发）。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont

DEFAULT_SERIF = r"C:\Windows\Fonts\NotoSerifSC-VF.ttf"
DEFAULT_SANS = r"C:\Windows\Fonts\NotoSansSC-VF.ttf"


def gb2312_chars() -> set[str]:
    chars: set[str] = set()
    for hi in range(0xA1, 0xF8):
        for lo in range(0xA1, 0xFF):
            try:
                chars.add(bytes([hi, lo]).decode("gb2312"))
            except UnicodeDecodeError:
                continue
    return chars


def index_chars(path: Path | None) -> set[str]:
    """表情索引里的关键词 / 标签 / key 用字。"""
    if not path or not path.exists():
        return set()
    raw = json.loads(path.read_text(encoding="utf-8"))
    chars: set[str] = set()
    for info in raw.get("infos") or []:
        for kw in info.get("keywords") or []:
            chars.update(str(kw))
        chars.update(str(info.get("key") or ""))
        for tag in info.get("tags") or []:
            chars.update(str(tag))
    return chars


def build(src: Path, dst: Path, chars: set[str]) -> None:
    text = "".join(sorted(c for c in chars if c.isprintable()))
    opts = subset.Options()
    opts.layout_features = ["*"]
    opts.drop_tables = []
    opts.hinting = False
    opts.desubroutinize = True
    # 保留可变字体轴（fvar/gvar/avar），否则运行时 set_variation_by_axes 失效
    opts.recalc_bounds = True
    opts.name_IDs = ["*"]
    opts.name_legacy = True

    font = TTFont(str(src))
    subsetter = subset.Subsetter(options=opts)
    subsetter.populate(text=text)
    subsetter.subset(font)
    font.save(str(dst))
    font.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", help="meme_index.json 路径（可省略）")
    ap.add_argument("--out", default=".", help="输出目录")
    ap.add_argument("--serif-src", default=DEFAULT_SERIF)
    ap.add_argument("--sans-src", default=DEFAULT_SANS)
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    idx = Path(args.index) if args.index else None
    chars = gb2312_chars() | {chr(i) for i in range(0x20, 0x7F)} | index_chars(idx)
    print(f"字符数 {len(chars)}（含索引用字 {len(index_chars(idx))}）")

    for src, name in (
        (Path(args.serif_src), "NotoSerifSC-Subset.ttf"),
        (Path(args.sans_src), "NotoSansSC-Subset.ttf"),
    ):
        if not src.exists():
            print(f"跳过 {name}：源字体不存在 {src}")
            continue
        dst = out / name
        build(src, dst, chars)
        print(f"{name}: {src.stat().st_size / 1048576:.1f}MB -> {dst.stat().st_size / 1024:.0f}KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
