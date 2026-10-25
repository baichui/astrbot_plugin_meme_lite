# fonts/ —— 内置中文字体子集

渲染帮助图 / 表情总览图用的字体，随插件一起分发，**不依赖宿主机是否装了中文字体**。

| 文件 | 用途 | 来源 | 许可 |
|---|---|---|---|
| `NotoSerifSC-Subset.ttf` | 标题（serif，weight 700） | Noto Serif SC | SIL OFL 1.1 |
| `NotoSansSC-Subset.ttf` | 正文 / 命令 / 数字（weight 400/500/700） | Noto Sans SC | SIL OFL 1.1 |
| `build_subset.py` | 重新生成子集（仅开发时需要 fontTools） | — | — |

- 字符集 = GB2312 全字 + 表情索引里的关键词用字 + ASCII 可见字符，共约 7.5k 字
- 保留了可变字体轴（fvar/gvar），所以 400/700 字重是从同一个文件里取，不用多份字体
- 体积：serif ≈ 5.5MB、sans ≈ 3.9MB
- emoji 不在子集里（Noto SC 本身没有 emoji 字形），列表渲染时会被 `_strip_symbols()` 过滤掉

## 什么时候需要重新生成

新增表情后，如果列表图里出现**方块**（说明该关键词用了子集里没有的生僻字），重跑：

```bash
python build_subset.py --index <path/to/meme_index.json> --out .
```

`--index` 可以省略，那就只保证 GB2312 覆盖。
