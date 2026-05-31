from reddit_product_reviews import (
    build_args_from_form,
    build_search_url,
    classify_pain_points,
    extract_comments,
    find_matched_terms,
    iter_search_posts,
    load_products,
    parse_args,
    simple_sentiment,
    split_form_list,
)


def test_build_search_url_restricts_subreddit():
    url = build_search_url("Notion", "productivity", "relevance", "year", 25)

    assert url.startswith("https://www.reddit.com/r/productivity/search.json?")
    assert "restrict_sr=1" in url
    assert "q=%22Notion%22" in url


def test_iter_search_posts_ignores_non_posts():
    payload = {
        "data": {
            "children": [
                {"kind": "t3", "data": {"id": "abc"}},
                {"kind": "t1", "data": {"id": "comment"}},
            ]
        }
    }

    assert list(iter_search_posts(payload)) == [{"id": "abc"}]


def test_extract_comments_flattens_nested_replies():
    payload = [
        {},
        {
            "data": {
                "children": [
                    {
                        "kind": "t1",
                        "data": {
                            "id": "c1",
                            "body": "Great tool",
                            "replies": {
                                "data": {
                                    "children": [
                                        {
                                            "kind": "t1",
                                            "data": {
                                                "id": "c2",
                                                "body": "Too expensive",
                                            },
                                        }
                                    ]
                                }
                            },
                        },
                    }
                ]
            }
        },
    ]

    assert [comment["id"] for comment in extract_comments(payload, 10)] == ["c1", "c2"]


def test_find_matched_terms_includes_product_and_review_terms():
    matches = find_matched_terms(
        "I recommend Linear pricing for teams", "Linear", ["pricing"]
    )

    assert matches == ["Linear", "pricing"]


def test_simple_sentiment_keyword_heuristic():
    assert simple_sentiment("great useful fast") == "positive"
    assert simple_sentiment("bad slow bugs") == "negative"
    assert simple_sentiment("plain mention") == "neutral"


def test_classify_pain_points_detects_customer_complaints():
    assert classify_pain_points("Too expensive and the app is slow") == [
        "price",
        "performance",
    ]


def test_split_form_list_accepts_newlines_and_commas():
    assert split_form_list("Notion, Linear\n\nObsidian") == [
        "Notion",
        "Linear",
        "Obsidian",
    ]


def test_build_args_from_form_creates_collector_namespace():
    base_args = parse_args(["--serve", "--no-open", "--delay", "0", "--timeout", "5"])
    collector_args = build_args_from_form(
        {
            "products": ["Notion\nLinear"],
            "subreddits": ["productivity"],
            "review_terms": ["pricing\nbug"],
            "limit": ["10"],
            "comments_per_post": ["3"],
            "time": ["month"],
            "format": ["csv"],
        },
        base_args,
    )

    assert collector_args.product == ["Notion", "Linear"]
    assert collector_args.subreddit == ["productivity"]
    assert "review" in collector_args.review_term
    assert "pricing" in collector_args.review_term
    assert "bug" in collector_args.review_term
    assert collector_args.limit == 10
    assert collector_args.comments_per_post == 3
    assert collector_args.time == "month"
    assert collector_args.format == "csv"
    assert collector_args.delay == 0
    assert collector_args.timeout == 5


def test_load_products_dedupes_cli_and_file(tmp_path):
    products_file = tmp_path / "products.txt"
    products_file.write_text("Notion\n# comment\nLinear\n", encoding="utf-8")

    assert load_products(["Notion", "Obsidian"], str(products_file)) == [
        "Notion",
        "Obsidian",
        "Linear",
    ]
