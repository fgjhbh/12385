#!/usr/bin/env python3
"""Collect Reddit posts and comments that mention product/tool names.

The script uses Reddit's public JSON endpoints, so it does not require OAuth for
lightweight research. Respect Reddit's terms, keep rates low, and avoid using it
for high-volume collection without an approved API workflow.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

DEFAULT_USER_AGENT = "reddit-product-review-research/1.0 (contact: local-script)"
REDDIT_BASE = "https://www.reddit.com"
WORD_RE = re.compile(r"[A-Za-z0-9_']+")


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
        default=["review", "pros", "cons", "recommend", "alternative", "vs", "pricing"],
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
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint."""

    args = parse_args(argv or sys.argv[1:])
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
