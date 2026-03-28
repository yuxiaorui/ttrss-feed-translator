# TT-RSS Feed Translator

一个不改 TT-RSS 插件代码的外挂翻译方案：

- 直接连 PostgreSQL 读新文章
- `title` 按纯文本翻译
- `content` 先解析 HTML，只翻译文本节点，再回写原结构
- 用独立追踪表记录原文快照、译文快照、原文 hash、翻译时间
- 支持按 `owner_uid`、`feed_id`、`lang`、最近入库时间过滤

这个仓库同时给了两种跑法：

- `Python + PostgreSQL` 脚本版
- `Docker sidecar + while loop` 版

## 为什么追踪表不只存 hash

如果你直接把译文写回 `ttrss_entries.title/content`，下一次脚本再扫库时，库里看到的已经是译文了。

为了避免把译文再翻一遍，这里会额外保存：

- 原始标题 / 原始正文
- 译后标题 / 译后正文
- 原文 hash

这样脚本可以区分三种状态：

1. 当前库里已经是我们写进去的译文：跳过
2. 当前库里回到了同一份原文：直接重放已保存译文，不再请求翻译 API
3. 当前库里变成了新的原文：重新翻译并覆盖追踪记录

## 目录

- `src/ttrss_feed_translator/`：核心代码
- `sql/001_translation_tracking.sql`：追踪表 DDL
- `docker/entrypoint.sh`：sidecar 的 while loop 入口
- `docker-compose.example.yml`：带 translator service 的 compose 示例
- `translator.env.example`：环境变量模板

## 快速开始

### 1. 初始化环境

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp translator.env.example translator.env
```

### 2. 配置环境变量

至少需要改这些：

- `TRANSLATOR_DATABASE_URL`
- `TRANSLATOR_FEED_IDS`
- `TRANSLATOR_API_BASE_URL`
- `TRANSLATOR_API_KEY`
- `TRANSLATOR_MODEL`
- `TRANSLATOR_TARGET_LANGUAGE`

默认会：

- 只处理 `owner_uid=1`
- 只处理你白名单里的 `feed_id`
- 只扫最近 `48` 小时入库文章
- 每轮最多处理 `10` 篇
- 遇到多用户共享文章时跳过

推荐做法：

- 把 `TRANSLATOR_FEED_IDS` 设成你确认是英文源的 feed id 白名单
- `TRANSLATOR_SOURCE_LANGS` 默认留空，不依赖 `ttrss_entries.lang`

这样即使 RSSHub 或上游 feed 把中文内容错误标成 `en`，也不会被误翻。

### 3. 先 dry-run

```bash
set -a
source translator.env
set +a
TRANSLATOR_DRY_RUN=true python -m ttrss_feed_translator --once
```

### 4. 正式执行

```bash
set -a
source translator.env
set +a
python -m ttrss_feed_translator --once
```

## Docker Sidecar

`docker-compose.example.yml` 里已经带了一个 `translator` service，运行逻辑是：

```sh
while true; do
  python -m ttrss_feed_translator --once
  sleep "$TRANSLATOR_LOOP_INTERVAL_SECONDS"
done
```

常见用法：

```bash
cp translator.env.example translator.env
docker compose -f docker-compose.example.yml up -d translator
docker compose -f docker-compose.example.yml logs -f translator
```

如果你已经有自己的 TT-RSS compose，只需要把 `translator` 这个 service 抄进去，并保证：

- 它能访问 PostgreSQL
- 它和 DB 在同一个 compose network
- `TRANSLATOR_DATABASE_URL` 指向 TT-RSS 的库

## 配置项

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `TRANSLATOR_DATABASE_URL` | 无 | PostgreSQL 连接串 |
| `TRANSLATOR_OWNER_UID` | `1` | 只处理这个 TT-RSS 用户的订阅视图 |
| `TRANSLATOR_TARGET_LANGUAGE` | `zh-CN` | 目标语言 |
| `TRANSLATOR_FEED_IDS` | 无 | feed 白名单，至少填一个；只处理这些指定 feed id |
| `TRANSLATOR_SOURCE_LANGS` | 空 | 可选附加过滤：只处理这些源语言，逗号分隔；默认不依赖 `ttrss_entries.lang` |
| `TRANSLATOR_LOOKBACK_HOURS` | `48` | 只扫最近入库文章 |
| `TRANSLATOR_BATCH_SIZE` | `10` | 每轮最多处理多少篇 |
| `TRANSLATOR_LOOP_INTERVAL_SECONDS` | `300` | sidecar 每轮间隔秒数 |
| `TRANSLATOR_REQUIRE_SINGLE_OWNER` | `true` | 如果文章被多个 owner 共享则跳过 |
| `TRANSLATOR_DRY_RUN` | `false` | 只打印动作，不写库 |
| `TRANSLATOR_API_BASE_URL` | `https://api.openai.com/v1` | OpenAI-compatible 接口地址 |
| `TRANSLATOR_API_KEY` | 无 | API key |
| `TRANSLATOR_MODEL` | 无 | 模型名 |
| `TRANSLATOR_REQUEST_TIMEOUT_SECONDS` | `120` | 接口超时 |
| `TRANSLATOR_MAX_TEXTS_PER_REQUEST` | `40` | 单次请求最多多少个文本块 |
| `TRANSLATOR_MAX_CHARS_PER_REQUEST` | `8000` | 单次请求最大字符数 |

## 行为说明

### 标题

- 作为纯文本单独翻译

### 正文

- 用 BeautifulSoup 解析 HTML
- 只提取文本节点翻译
- `script/style/noscript/code/pre/textarea/svg/math` 默认跳过
- 标签结构、属性、链接地址不会送去翻译

### 不会改的字段

默认只写：

- `ttrss_entries.title`
- `ttrss_entries.content`

不会动：

- `ttrss_entries.content_hash`
- `ttrss_entries.guid`
- `ttrss_user_entries`

保留 `content_hash` 的原因是，尽量不干扰 TT-RSS 对上游原文是否变化的判断。

## 追踪表

脚本启动时会自动创建追踪表，DDL 也单独放在 `sql/001_translation_tracking.sql`：

- 表名：`ttrss_entry_translations`
- 主键：`entry_id`
- 记录：原文快照、译文快照、原文 hash、目标语言、翻译时间、重放时间

## 风险和注意事项

- 如果你的 TT-RSS 不是单用户环境，直接改 `ttrss_entries` 会影响所有引用这篇文章的用户。默认配置会跳过共享文章。
- 上游文章更新后，TT-RSS 很可能会把原文重新写回 `ttrss_entries`。这正是追踪表存在的原因。
- BeautifulSoup 会尽量保留 HTML 结构，但重新序列化后，空白和实体编码形式可能与原始 HTML 略有差异。
- 正式跑之前，建议先备份数据库。

## 与 TT-RSS 库表的对应

当前实现基于 PostgreSQL 版 TT-RSS 常见结构：

- `ttrss_entries`
- `ttrss_user_entries`
- `ttrss_feeds`

查询策略是用 `ttrss_user_entries.owner_uid + feed_id` 限定目标文章，再回写对应的 `ttrss_entries`。
