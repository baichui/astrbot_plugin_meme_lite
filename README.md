# astrbot_plugin_meme_lite

表情包制作（轻量版）—— **AstrBot 原生插件**，直连 meme-generator 后端，
可与 GsCore 侧的 `core_plugin_memes` **共用同一个服务**（同一个 `MemeApiUrl`）。

## 来源与致谢

> 本插件为**独立实现** —— 自行编写代码、直接对接 meme-generator 的 HTTP 接口，
> **未复制**下列任何项目的源码；但功能设计、命令体系与接口用法都参考了它们，
> 在此明确署名并致谢。

| 项目 | 作者 / 组织 | 许可 | 与本插件的关系 |
|---|---|---|---|
| [**meme-generator**](https://github.com/MemeCrafters/meme-generator) | **MemeCrafters** | MIT | **后端能力来源**（Python 版，`/memes/*` 接口） |
| [**meme-generator-rs**](https://github.com/MemeCrafters/meme-generator-rs) | **MemeCrafters** | MIT | 后端能力来源（Rust 版，`/meme/*` 接口） |
| [**core_plugin_memes**](https://github.com/Loping151/core_plugin_memes) | **Loping151** &lt;wangkailing151@gmail.com&gt; | **GPL-3.0-or-later** | **功能设计 / 命令体系的参考来源**（GsCore 侧同款插件） |
| [astrbot_plugin_pokepro](https://github.com/Zhalslar/astrbot_plugin_pokepro) | Zhalslar | — | 戳一戳联动的接入方式参考 |
| [AstrBot](https://github.com/AstrBotDevs/AstrBot) | AstrBotDevs | AGPL-3.0 | 插件运行时框架 |

**特别致谢**

- **MemeCrafters** —— 维护 meme-generator 后端与整套表情仓库。本插件的**全部出图能力**都来自这个后端。
- **Loping151** —— GsCore 侧 `core_plugin_memes` 的作者。本插件的**命令命名、交互设计与素材解析口径**
  基本沿袭自该项目（这是它能与 GsCore 侧共用同一套后端与命令习惯的原因）。

本插件不包含上述项目的源码副本，仅通过 HTTP 调用其后端接口；此处保留完整署名与致谢。

## 为什么叫「轻量版」

GsCore 的 `core_plugin_memes` 有 **30 个文件 / 5052 行**（搜索、管理、统计、昵称、门控、
AI 知识库等一应俱全）。本插件保留**核心能力**，砍掉与 AstrBot 自身重复的部分：

- 代码量约 **1600 行 / 6 个模块**
- **不下载任何本地模板资源**（原生 GsCore 方案要下 2000+ 张图，这里直接用远程后端）
- 索引可缓存到本地，二次启动秒加载

## 安装

1. 把整个 `astrbot_plugin_meme_lite` 目录放进 `AstrBot/data/plugins/`
2. 重启 AstrBot，或在插件管理页点「重载」
3. 依赖：`httpx`、`Pillow`（AstrBot 环境一般已自带；缺失时用插件管理页的依赖安装）

首次启动会自动拉取表情索引（841 个约 15 秒），结果缓存到
`data/plugin_data/astrbot_plugin_meme_lite/meme_index.json`。

## 配置（插件管理页）

| 键 | 默认 | 说明 |
|---|---|---|
| `enable` | `true` | 总开关 |
| `api_url` | `https://meme.xlinxc.cn` | 后端地址；填 GsCore 侧同一个即可共用 |
| `request_timeout` | `60` | 请求超时（秒） |
| `trigger_prefix` | 空 | 生成表情的触发前缀（**本机当前配的是 `bq`**）。详见下方说明 |
| `cooldown_seconds` | `5` | 同会话生成冷却 |
| `auto_refresh_index` | `true` | 启动时刷新索引 |
| `index_cache_hours` | `24` | 索引缓存有效期 |
| `max_image_size_mb` | `5` | 素材图片上限，超出自动压缩 |
| `enable_stats` | `true` | 记录调用统计 |

> ⚠️ **`trigger_prefix` 一定要填**
>
> 留空时，**任何消息只要以某个表情关键词开头就会触发**。而 841 个表情里有大量**单字/双字**关键词
> （`搓` `哈` `摸` `亲` …），群聊随口一句话就可能撞上：
>
> | 群友说的话 | 实际命中 | 后果 |
> |---|---|---|
> | 「**搓**破过好几次了」 | `twist`（关键词「搓」） | 素材不符 → 回一句提示到群里 |
> | 「**哈哈**哈哈哈」 | `kurogames_phoebe_haha` | 直接出图 |
>
> 而且素材不匹配时还会往群里回一句提示，比出图更扰民。
>
> **填了前缀（本机 = `bq`）后**，只有 `bq摸头` / `bq 摸头` / `bq亲` 这样**主动发**的消息才会出图：
>
> ```
> bq摸头          → petpet     ✅
> bq 摸头         → petpet     ✅（带空格也行）
> bq亲            → kiss       ✅
> 搓破过好几次了   → 不触发      ✅（无前缀，忽略）
> ```
>
> `/随机表情` 等命令**不受影响**，戳一戳联动（pokepro 命令位）也不受影响。

## 命令

**出图（核心）**
```
摸头                  ← 直接用发送者头像
摸头 你好             ← 带台词
摸头 自己             ← 显式指定用自己头像
摸头 @某人            ← 用被 @ 的人的头像
摸头 --circle true    ← 表情专属选项（用 /表情详情 查可用选项）
```

**随机**
```
/随机表情
/随机表情 @某人
```

**查询**
```
/表情列表                     ← 全部可用表情（分「可用 / 个人禁用 / 全局禁用」）
/表情搜索 <词>                ← 按关键词/标签搜
/表情详情 <关键词>            ← 图片/文字需求、可选参数
```

**管理**
```
/禁用表情 <词>      /启用表情 <词>                      ← 本会话
/全局禁用表情 <词>  /全局启用表情 <词>                   ← 管理员
/表情黑名单                                              ← 查看全局禁用
/屏蔽表情 <词>      /解除屏蔽 <词>                       ← 不让别人拿我当素材
```

**统计 / 群开关 / 维护**
```
/表情统计 [n]                 ← 当日调用排行
/开启表情包 /关闭表情包 /表情包状态
/更新表情                     ← 管理员，刷新索引
/表情帮助
```

## 与 pokepro 联动（戳一戳出图）

pokepro（专业戳一戳）的「**命令**」响应位可以填 `随机表情`：

```
戳一下 → pokepro 按权重选中「命令」响应 → 伪造「@机器人 随机表情」事件入队
       → 本插件响应 → 出图
```

这样**不需要 GsCore 适配器**，也**不会多出一条回复**（完全融入 pokepro 的加权响应池）。

> ⚠️ 注意：pokepro 伪造的消息是 `@机器人 + 命令`，所以出图用的是**机器人头像**。
> 若想用「戳的人」的头像，需要改 pokepro 的 `respond_cmd`（把 `At(qq=evt.get_self_id())` 换成发起者）。

## 实现要点

| 模块 | 职责 |
|---|---|
| `core/models.py` | `MemeInfo` / `MemeOption`，含 Python 版与 Rust 版 info 的归一化 |
| `core/client.py` | 后端探测、索引拉取与缓存、生成 |
| `core/store.py` | 个人/全局禁用、屏蔽、群开关、统计（JSON 持久化） |
| `core/parser.py` | 解析 `自己` / `@某人` / `--选项 值` / 台词 |
| `core/utils.py` | 头像与图片获取、超限压缩 |
| `main.py` | 命令注册与调度 |

- **后端双版兼容**：`/memes/keys` 200 判为 Python 版，`/meme/keys` 200 判为 Rust 版；
  info 结构差异（Python 版参数藏在 `params_type` 里）已在 `models.py` 归一化。
- **索引缓存**：`refresh_all()` 并发 32 路拉取（841 条约 15 秒），结果落盘，二次启动直接命中。
- **关键词匹配**：长关键词优先的前缀匹配（`摸摸你的头` 会命中 `摸摸` 而不是 `摸`）。

## 已知限制

- 只实现 aiocqhttp 平台的图片发送（其它平台未测试）。
- 统计只有"当日"维度，保留最近 7 天。
- 不含 GsCore 版的「昵称记录」「AI 知识库注册」「NSFW 门控」等模块。
