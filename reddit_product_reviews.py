#!/usr/bin/env python3
"""Collect Reddit posts and comments that mention product/tool names.

The script uses Reddit's public JSON endpoints, so it does not require OAuth for
lightweight research. Respect Reddit's terms, keep rates low, and avoid using it
for high-volume collection without an approved API workflow.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

DEFAULT_USER_AGENT = "reddit-product-review-research/1.0 (contact: local-script)"
REDDIT_BASE = "https://www.reddit.com"
WORD_RE = re.compile(r"[A-Za-z0-9_']+")

PAIN_POINT_TERMS = {
    "price": [
        "expensive",
        "overpriced",
        "cost",
        "costly",
        "pricing",
        "subscription",
        "贵",
        "太贵",
    ],
    "bugs": ["bug", "bugs", "broken", "crash", "crashes", "error", "glitch", "报错"],
    "usability": [
        "confusing",
        "hard to use",
        "difficult",
        "learning curve",
        "clunky",
        "难用",
        "不好用",
    ],
    "performance": ["slow", "lag", "lags", "latency", "freezes", "卡", "很慢"],
    "support": [
        "support",
        "customer service",
        "refund",
        "unresponsive",
        "客服",
        "退款",
    ],
    "missing_features": [
        "missing",
        "lack",
        "lacks",
        "wish it had",
        "feature request",
        "缺少",
    ],
}
DEFAULT_REVIEW_TERMS = [
    "review",
    "pros",
    "cons",
    "recommend",
    "alternative",
    "vs",
    *dict.fromkeys(term for terms in PAIN_POINT_TERMS.values() for term in terms),
]


@dataclass(slots=True)
class ReviewRecord:
    """A normalized Reddit post or comment mentioning a requested product."""

    product: str
    source_type: str
    id: str
    subreddit: str
    author: str
    created_utc: int | None
    score: int | None
    permalink: str
    title: str
    body: str
    matched_terms: str
    pain_points: str
    sentiment: str
    url: str


def load_products(values: Sequence[str], products_file: str | None) -> list[str]:
    """Load product/tool names from CLI values and an optional newline file."""

    products: list[str] = []
    products.extend(values)
    if products_file:
        for line in Path(products_file).read_text(encoding="utf-8").splitlines():
            cleaned = line.strip()
            if cleaned and not cleaned.startswith("#"):
                products.append(cleaned)
    deduped = list(
        dict.fromkeys(product.strip() for product in products if product.strip())
    )
    if not deduped:
        raise SystemExit(
            "请通过 --product 或 --products-file 至少提供一个产品/工具名称。"
        )
    return deduped


def reddit_get_json(url: str, user_agent: str, timeout: int, retries: int) -> Any:
    """GET JSON from Reddit with simple retry/backoff handling."""

    request = urllib.request.Request(url, headers={"User-Agent": user_agent})
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt >= retries:
                break
            sleep_for = min(2**attempt, 10)
            time.sleep(sleep_for)
    raise RuntimeError(f"请求 Reddit 失败: {url} ({last_error})")


def build_search_url(
    product: str, subreddit: str | None, sort: str, time_filter: str, limit: int
) -> str:
    """Build a Reddit search URL for one product and optional subreddit."""

    base = (
        f"{REDDIT_BASE}/r/{subreddit}/search.json"
        if subreddit
        else f"{REDDIT_BASE}/search.json"
    )
    params = {
        "q": f'"{product}"',
        "restrict_sr": "1" if subreddit else "0",
        "sort": sort,
        "t": time_filter,
        "limit": str(limit),
    }
    return f"{base}?{urllib.parse.urlencode(params)}"


def iter_search_posts(payload: Any) -> Iterator[dict[str, Any]]:
    """Yield listing post dictionaries from a Reddit search response."""

    for child in payload.get("data", {}).get("children", []):
        if child.get("kind") == "t3":
            yield child.get("data", {})


def flatten_comments(node: Any) -> Iterator[dict[str, Any]]:
    """Recursively yield comment dictionaries from a Reddit comment tree."""

    if not isinstance(node, dict):
        return
    kind = node.get("kind")
    data = node.get("data", {})
    if kind == "t1":
        yield data
    replies = data.get("replies")
    if isinstance(replies, dict):
        for child in replies.get("data", {}).get("children", []):
            yield from flatten_comments(child)


def extract_comments(payload: Any, max_comments: int) -> list[dict[str, Any]]:
    """Extract up to max_comments comments from a Reddit comments response."""

    if not isinstance(payload, list) or len(payload) < 2:
        return []
    comments: list[dict[str, Any]] = []
    for child in payload[1].get("data", {}).get("children", []):
        for comment in flatten_comments(child):
            body = comment.get("body")
            if body and body not in ("[deleted]", "[removed]"):
                comments.append(comment)
                if len(comments) >= max_comments:
                    return comments
    return comments


def find_matched_terms(
    text: str, product: str, extra_terms: Sequence[str]
) -> list[str]:
    """Return product and review terms present in a text block."""

    terms = [product, *extra_terms]
    matches: list[str] = []
    lowered = text.lower()
    for term in terms:
        if term.lower() in lowered:
            matches.append(term)
    return matches


def classify_pain_points(text: str) -> list[str]:
    """Return pain-point categories detected in a text block."""

    lowered = text.lower()
    categories: list[str] = []
    for category, terms in PAIN_POINT_TERMS.items():
        if any(term.lower() in lowered for term in terms):
            categories.append(category)
    return categories


def simple_sentiment(text: str) -> str:
    """Classify sentiment with a small transparent keyword heuristic."""

    positive = {
        "good",
        "great",
        "love",
        "loved",
        "best",
        "useful",
        "recommend",
        "solid",
        "works",
        "awesome",
        "fast",
        "便宜",
        "好用",
        "推荐",
    }
    negative = {
        "bad",
        "bug",
        "bugs",
        "broken",
        "hate",
        "expensive",
        "slow",
        "issue",
        "issues",
        "problem",
        "terrible",
        "worse",
        "worst",
        "垃圾",
        "难用",
        "贵",
    }
    words = {word.lower() for word in WORD_RE.findall(text)}
    pos = len(words & positive)
    neg = len(words & negative)
    if pos > neg:
        return "positive"
    if neg > pos:
        return "negative"
    return "neutral"


def make_post_record(
    product: str, post: dict[str, Any], matched_terms: Sequence[str]
) -> ReviewRecord:
    """Convert a Reddit post payload into a ReviewRecord."""

    permalink = post.get("permalink") or ""
    return ReviewRecord(
        product=product,
        source_type="post",
        id=post.get("id", ""),
        subreddit=post.get("subreddit", ""),
        author=post.get("author", ""),
        created_utc=as_int(post.get("created_utc")),
        score=as_int(post.get("score")),
        permalink=permalink,
        title=post.get("title", ""),
        body=post.get("selftext", ""),
        matched_terms=", ".join(matched_terms),
        pain_points=", ".join(
            classify_pain_points(f"{post.get('title', '')}\n{post.get('selftext', '')}")
        ),
        sentiment=simple_sentiment(
            f"{post.get('title', '')}\n{post.get('selftext', '')}"
        ),
        url=f"{REDDIT_BASE}{permalink}" if permalink else post.get("url", ""),
    )


def make_comment_record(
    product: str,
    post: dict[str, Any],
    comment: dict[str, Any],
    matched_terms: Sequence[str],
) -> ReviewRecord:
    """Convert a Reddit comment payload into a ReviewRecord."""

    permalink = comment.get("permalink") or post.get("permalink") or ""
    return ReviewRecord(
        product=product,
        source_type="comment",
        id=comment.get("id", ""),
        subreddit=comment.get("subreddit", post.get("subreddit", "")),
        author=comment.get("author", ""),
        created_utc=as_int(comment.get("created_utc")),
        score=as_int(comment.get("score")),
        permalink=permalink,
        title=post.get("title", ""),
        body=comment.get("body", ""),
        matched_terms=", ".join(matched_terms),
        pain_points=", ".join(classify_pain_points(comment.get("body", ""))),
        sentiment=simple_sentiment(comment.get("body", "")),
        url=f"{REDDIT_BASE}{permalink}" if permalink else "",
    )


def as_int(value: Any) -> int | None:
    """Safely convert Reddit numeric values to int."""

    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def collect_reviews(args: argparse.Namespace) -> list[ReviewRecord]:
    """Collect matching post/comment records for all configured products."""

    products = load_products(args.product, args.products_file)
    subreddits = args.subreddit or [None]
    records: list[ReviewRecord] = []
    seen: set[tuple[str, str, str]] = set()

    for product in products:
        for subreddit in subreddits:
            search_url = build_search_url(
                product, subreddit, args.sort, args.time, args.limit
            )
            payload = reddit_get_json(
                search_url, args.user_agent, args.timeout, args.retries
            )
            time.sleep(args.delay)
            for post in iter_search_posts(payload):
                text = f"{post.get('title', '')}\n{post.get('selftext', '')}"
                matched = find_matched_terms(text, product, args.review_term)
                if matched:
                    add_record(records, seen, make_post_record(product, post, matched))

                if args.comments_per_post <= 0:
                    continue
                permalink = post.get("permalink")
                if not permalink:
                    continue
                comments_url = f"{REDDIT_BASE}{permalink}.json?limit={args.comments_per_post}&sort={args.comment_sort}"
                comments_payload = reddit_get_json(
                    comments_url, args.user_agent, args.timeout, args.retries
                )
                time.sleep(args.delay)
                for comment in extract_comments(
                    comments_payload, args.comments_per_post
                ):
                    comment_text = comment.get("body", "")
                    matched = find_matched_terms(
                        comment_text, product, args.review_term
                    )
                    if matched:
                        add_record(
                            records,
                            seen,
                            make_comment_record(product, post, comment, matched),
                        )
    return records


def add_record(
    records: list[ReviewRecord], seen: set[tuple[str, str, str]], record: ReviewRecord
) -> None:
    """Append a record once per product/source/id."""

    key = (record.product.lower(), record.source_type, record.id)
    if key not in seen:
        records.append(record)
        seen.add(key)


def write_records(records: Iterable[ReviewRecord], output: str, fmt: str) -> None:
    """Write records as JSON Lines, JSON, or CSV."""

    rows = [asdict(record) for record in records]
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "jsonl":
        with output_path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    elif fmt == "json":
        output_path.write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    elif fmt == "csv":
        with output_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=list(ReviewRecord.__dataclass_fields__.keys())
            )
            writer.writeheader()
            writer.writerows(rows)
    else:
        raise ValueError(f"不支持的输出格式: {fmt}")


INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Reddit 产品痛点抓取器</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 2rem; color: #1f2937; }
    label { display: block; font-weight: 600; margin-top: 1rem; }
    input, textarea, select { width: min(760px, 100%); padding: .65rem; margin-top: .35rem; border: 1px solid #cbd5e1; border-radius: .5rem; }
    button { margin-top: 1rem; padding: .8rem 1.2rem; border: 0; border-radius: .6rem; background: #ff4500; color: white; font-weight: 700; cursor: pointer; }
    button:disabled { opacity: .6; cursor: wait; }
    .hint { color: #64748b; font-size: .92rem; }
    .status { margin: 1rem 0; font-weight: 700; }
    table { border-collapse: collapse; width: 100%; margin-top: 1rem; font-size: .9rem; }
    th, td { border: 1px solid #e2e8f0; padding: .45rem; text-align: left; vertical-align: top; }
    th { background: #f8fafc; }
  </style>
</head>
<body>
  <h1>Reddit 产品痛点/评价一键抓取器</h1>
  <p class="hint">输入产品名，点击按钮后会抓取 Reddit 帖子和评论，并自动标记价格、Bug、易用性、性能、客服、缺失功能等痛点。</p>
  <form id="collector">
    <label>产品名（每行一个，必填）</label>
    <textarea name="products" rows="4" placeholder="Notion\nLinear\nObsidian" required></textarea>
    <label>Subreddit（可选，每行一个；留空为全站搜索）</label>
    <textarea name="subreddits" rows="3" placeholder="SaaS\nproductivity"></textarea>
    <label>额外关键词（可选，每行一个）</label>
    <textarea name="review_terms" rows="3" placeholder="pricing\nalternative\nbug"></textarea>
    <label>每个产品/社区搜索帖子数</label>
    <input name="limit" type="number" min="1" max="100" value="25">
    <label>每帖最多评论数</label>
    <input name="comments_per_post" type="number" min="0" max="500" value="50">
    <label>时间范围</label>
    <select name="time"><option value="year">过去一年</option><option value="month">过去一月</option><option value="week">过去一周</option><option value="all">全部</option></select>
    <label>输出格式</label>
    <select name="format"><option value="jsonl">JSONL</option><option value="csv">CSV</option><option value="json">JSON</option></select>
    <button id="run" type="submit">开始抓取 Reddit 数据</button>
  </form>
  <div id="status" class="status"></div>
  <div id="result"></div>
<script>
const form = document.getElementById('collector');
const statusBox = document.getElementById('status');
const resultBox = document.getElementById('result');
const button = document.getElementById('run');
form.addEventListener('submit', async (event) => {
  event.preventDefault();
  button.disabled = true;
  statusBox.textContent = '正在抓取，请稍等……';
  resultBox.innerHTML = '';
  try {
    const response = await fetch('/api/collect', { method: 'POST', body: new URLSearchParams(new FormData(form)) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '抓取失败');
    statusBox.innerHTML = `已抓取 ${data.count} 条记录，文件：<code>${data.output}</code>，<a href="/download?file=${encodeURIComponent(data.output)}">下载</a>`;
    renderTable(data.records);
  } catch (error) {
    statusBox.textContent = `错误：${error.message}`;
  } finally {
    button.disabled = false;
  }
});
function renderTable(records) {
  const rows = records.slice(0, 100).map((r) => `<tr><td>${esc(r.product)}</td><td>${esc(r.source_type)}</td><td>${esc(r.subreddit)}</td><td>${esc(r.sentiment)}</td><td>${esc(r.pain_points)}</td><td>${esc(r.title)}</td><td>${esc(r.body).slice(0, 400)}</td><td><a href="${esc(r.url)}" target="_blank">打开</a></td></tr>`).join('');
  resultBox.innerHTML = `<p class="hint">下方最多预览前 100 条。</p><table><thead><tr><th>产品</th><th>来源</th><th>社区</th><th>情感</th><th>痛点</th><th>标题</th><th>内容</th><th>链接</th></tr></thead><tbody>${rows}</tbody></table>`;
}
function esc(value) { return String(value || '').replace(/[&<>"]/g, (ch) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[ch])); }
</script>
</body>
</html>
"""


def split_form_list(value: str) -> list[str]:
    """Split textarea values by newlines or commas and drop blanks."""

    return [item.strip() for item in re.split(r"[\n,]+", value) if item.strip()]


def build_args_from_form(
    form: dict[str, list[str]], base_args: argparse.Namespace
) -> argparse.Namespace:
    """Create collector args from a submitted web form."""

    products = split_form_list(form.get("products", [""])[0])
    subreddits = split_form_list(form.get("subreddits", [""])[0])
    custom_review_terms = split_form_list(form.get("review_terms", [""])[0])
    review_terms = list(dict.fromkeys([*DEFAULT_REVIEW_TERMS, *custom_review_terms]))
    fmt = form.get("format", ["jsonl"])[0]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    return argparse.Namespace(
        product=products,
        products_file=None,
        subreddit=subreddits,
        review_term=review_terms,
        limit=int(form.get("limit", ["25"])[0]),
        comments_per_post=int(form.get("comments_per_post", ["50"])[0]),
        sort="relevance",
        time=form.get("time", ["year"])[0],
        comment_sort="top",
        format=fmt,
        output=f"reddit_reviews_{now}.{fmt}",
        delay=base_args.delay,
        timeout=base_args.timeout,
        retries=base_args.retries,
        user_agent=base_args.user_agent,
    )


def serve_web_app(args: argparse.Namespace) -> int:
    """Start the one-click Reddit collector web UI."""

    class RedditCollectorHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/":
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(INDEX_HTML.encode("utf-8"))
                return
            if parsed.path == "/download":
                params = urllib.parse.parse_qs(parsed.query)
                filename = params.get("file", [""])[0]
                path = Path(filename).resolve()
                if (
                    not filename
                    or path.parent != Path.cwd().resolve()
                    or not path.exists()
                ):
                    self.send_error(404, "file not found")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header(
                    "Content-Disposition",
                    f"attachment; filename={html.escape(path.name)}",
                )
                self.end_headers()
                self.wfile.write(path.read_bytes())
                return
            self.send_error(404)

        def do_POST(self) -> None:
            if self.path != "/api/collect":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8")
            form = urllib.parse.parse_qs(body)
            try:
                collector_args = build_args_from_form(form, args)
                if collector_args.limit < 1 or collector_args.limit > 100:
                    raise ValueError("limit 必须在 1 到 100 之间")
                if collector_args.comments_per_post < 0:
                    raise ValueError("comments_per_post 不能小于 0")
                records = collect_reviews(collector_args)
                write_records(records, collector_args.output, collector_args.format)
                payload = {
                    "count": len(records),
                    "output": collector_args.output,
                    "records": [asdict(record) for record in records],
                }
                self.send_json(200, payload)
            except (RuntimeError, ValueError, SystemExit) as exc:
                self.send_json(400, {"error": str(exc)})

        def send_json(self, status: int, payload: dict[str, Any]) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

        def log_message(self, format: str, *log_args: Any) -> None:
            print(f"{self.address_string()} - {format % log_args}")

    server = ThreadingHTTPServer((args.host, args.port), RedditCollectorHandler)
    url = f"http://{args.host}:{args.port}"
    print(f"Reddit 产品痛点抓取器已启动: {url}")
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止网页服务。")
    return 0


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description="抓取 Reddit 上提到指定工具/产品的帖子和评论，并导出结构化评论数据。"
    )
    parser.add_argument(
        "--product", action="append", default=[], help="产品/工具名称，可重复传入。"
    )
    parser.add_argument("--products-file", help="产品/工具名称列表文件，每行一个。")
    parser.add_argument(
        "--subreddit",
        action="append",
        help="限定 subreddit，可重复传入；默认全站搜索。",
    )
    parser.add_argument(
        "--review-term",
        action="append",
        default=DEFAULT_REVIEW_TERMS,
        help="额外评论意图关键词，可重复传入。",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=25,
        help="每个产品/社区搜索的帖子数，最大建议不超过 100。",
    )
    parser.add_argument(
        "--comments-per-post",
        type=int,
        default=50,
        help="每个帖子最多抓取的评论数；设为 0 只抓帖子。",
    )
    parser.add_argument(
        "--sort",
        choices=["relevance", "hot", "top", "new", "comments"],
        default="relevance",
    )
    parser.add_argument(
        "--time",
        choices=["hour", "day", "week", "month", "year", "all"],
        default="year",
    )
    parser.add_argument(
        "--comment-sort",
        choices=["confidence", "top", "new", "controversial", "old", "qa"],
        default="top",
    )
    parser.add_argument("--format", choices=["jsonl", "json", "csv"], default="jsonl")
    parser.add_argument(
        "--output",
        default=f"reddit_reviews_{datetime.now(timezone.utc).date().isoformat()}.jsonl",
    )
    parser.add_argument(
        "--delay", type=float, default=1.0, help="请求间隔秒数，用于降低请求频率。"
    )
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument(
        "--user-agent", default=DEFAULT_USER_AGENT, help="Reddit 请求 User-Agent。"
    )
    parser.add_argument("--serve", action="store_true", help="启动一键抓取网页界面。")
    parser.add_argument("--host", default="127.0.0.1", help="网页界面监听地址。")
    parser.add_argument("--port", type=int, default=8765, help="网页界面监听端口。")
    parser.add_argument(
        "--no-open", action="store_true", help="启动网页界面后不自动打开浏览器。"
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint."""

    args = parse_args(argv or sys.argv[1:])
    if args.serve:
        return serve_web_app(args)
    if args.limit < 1 or args.limit > 100:
        raise SystemExit("--limit 必须在 1 到 100 之间。")
    if args.comments_per_post < 0:
        raise SystemExit("--comments-per-post 不能小于 0。")

    records = collect_reviews(args)
    write_records(records, args.output, args.format)
    print(f"已写入 {len(records)} 条记录到 {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
