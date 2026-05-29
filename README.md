# Reddit 产品评论抓取工具

这个仓库提供一个轻量 Python CLI，用于抓取 Reddit 上提到指定工具/产品的帖子和评论，并导出为 JSONL、JSON 或 CSV。

> 注意：脚本使用 Reddit 公共 JSON 端点，适合低频研究和原型验证。请遵守 Reddit 的服务条款、robots/平台规则和适用隐私法规；不要抓取或再发布敏感个人信息。

## 快速开始

```bash
python3 reddit_product_reviews.py \
  --product "Notion" \
  --product "Obsidian" \
  --subreddit productivity \
  --subreddit SaaS \
  --limit 20 \
  --comments-per-post 30 \
  --format csv \
  --output data/reddit_tool_reviews.csv
```

也可以把产品名放到文件中：

```text
Notion
Obsidian
Linear
```

然后运行：

```bash
python3 reddit_product_reviews.py --products-file products.txt --time year --format jsonl
```

## 输出字段

| 字段 | 说明 |
| --- | --- |
| `product` | 命中的产品/工具名称 |
| `source_type` | `post` 或 `comment` |
| `id` | Reddit 对象 ID |
| `subreddit` | 来源社区 |
| `author` | 作者名 |
| `created_utc` | UTC 时间戳 |
| `score` | Reddit 分数 |
| `permalink` | Reddit 相对链接 |
| `title` | 帖子标题 |
| `body` | 帖子正文或评论内容 |
| `matched_terms` | 命中的产品名/评论意图关键词 |
| `sentiment` | 简单关键词情感：`positive`、`negative`、`neutral` |
| `url` | 完整 Reddit 链接 |

## 常用参数

- `--product`：指定产品名，可重复传入。
- `--products-file`：从文件读取产品名，每行一个。
- `--subreddit`：限定社区，可重复传入；不传则全站搜索。
- `--review-term`：额外评论意图关键词，例如 `pricing`、`alternative`、`bug`。
- `--limit`：每个产品/社区搜索帖子数，范围 1-100。
- `--comments-per-post`：每个帖子抓取评论数；设为 `0` 时只抓帖子。
- `--time`：搜索时间范围，可选 `hour/day/week/month/year/all`。
- `--format`：输出格式，支持 `jsonl/json/csv`。
- `--delay`：请求间隔秒数，默认 1 秒，建议保持低频。

## 示例分析思路

1. 先用 `--format csv` 导出。
2. 在表格或 BI 工具里按 `product`、`subreddit`、`sentiment` 聚合。
3. 根据 `matched_terms` 过滤 `pricing`、`alternative`、`bug` 等主题。
4. 打开 `url` 人工复核高分评论，避免只依赖关键词情感。

# 邮件机器人智能体

`email_agent.py` 是一个轻量、零第三方依赖的邮件智能体，可以根据你的目标自动起草邮件，或渲染模板后通过 SMTP 发送。为了避免误发，它默认只预览；只有显式添加 `--send` 才会真正发送。

## 邮件智能体快速开始

只预览一封由目标生成的邮件：

```bash
python3 email_agent.py \
  --from me@example.com \
  --from-name "小王" \
  --to client@example.com \
  --goal "提醒客户明天下午三点参加项目进度会" \
  --tone professional \
  --signature "小王"
```

确认预览无误后，通过 SMTP 发送：

```bash
export SMTP_HOST=smtp.example.com
export SMTP_USERNAME=me@example.com
export SMTP_PASSWORD='your-app-password'

python3 email_agent.py \
  --from me@example.com \
  --to client@example.com \
  --subject "项目进度会提醒" \
  --body "您好，提醒您明天下午三点参加项目进度会。谢谢。" \
  --send
```

## 使用模板批量复用

模板文件 `invite.txt`：

```text
$name 您好，

邀请您参加 $date 的产品演示会，会议链接为：$link

谢谢，
小王
```

运行：

```bash
python3 email_agent.py \
  --from me@example.com \
  --to ada@example.com \
  --subject "产品演示会邀请" \
  --template-file invite.txt \
  --var name=Ada \
  --var date=周三 \
  --var link=https://example.com/meeting
```

## 邮件智能体常用参数

- `--to` / `--cc` / `--bcc`：收件人、抄送、密送；都可重复传入。
- `--from` / `--from-name`：发件邮箱与显示名。
- `--goal`：描述邮件目的，由智能体生成正文。
- `--body`：直接提供完整正文。
- `--template-file`：读取模板文件，支持 `$name` 这类变量。
- `--var KEY=VALUE` / `--variables-file`：提供模板变量；命令行变量会覆盖 JSON 文件中的同名变量。
- `--tone`：起草语气，支持 `professional`、`friendly`、`concise`、`formal`。
- `--send`：真正发送邮件；不加时只预览。
- `--smtp-host` / `--smtp-username` / `--smtp-password`：SMTP 配置，也可用 `SMTP_HOST`、`SMTP_USERNAME`、`SMTP_PASSWORD` 环境变量。
