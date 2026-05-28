from reddit_product_reviews import (
    build_search_url,
    extract_comments,
    find_matched_terms,
    iter_search_posts,
    load_products,
    simple_sentiment,
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


def test_load_products_dedupes_cli_and_file(tmp_path):
    products_file = tmp_path / "products.txt"
    products_file.write_text("Notion\n# comment\nLinear\n", encoding="utf-8")

    assert load_products(["Notion", "Obsidian"], str(products_file)) == [
        "Notion",
        "Obsidian",
        "Linear",
    ]
