from app.services.brief_v2.article_fetcher import fetch_article


def test_fetch_article_returns_text_date_source():
    def fake_fetch(url):
        return "<html>raw</html>"

    def fake_extract(html):
        return {"text": "Full body text.", "date": "2026-07-01", "sitename": "Reuters"}

    art = fetch_article("https://www.reuters.com/x", fetch_fn=fake_fetch, extract_fn=fake_extract)
    assert art == {
        "full_text": "Full body text.",
        "publication_time": "2026-07-01",
        "source_name": "Reuters",
    }


def test_fetch_article_none_when_download_fails():
    art = fetch_article("https://x.com/y", fetch_fn=lambda u: None, extract_fn=lambda h: {"text": "x"})
    assert art is None


def test_fetch_article_none_when_no_text():
    art = fetch_article(
        "https://x.com/y",
        fetch_fn=lambda u: "<html>",
        extract_fn=lambda h: {"text": "", "date": None, "sitename": None},
    )
    assert art is None


def test_fetch_article_source_name_falls_back_to_domain():
    art = fetch_article(
        "https://www.example-news.com/story",
        fetch_fn=lambda u: "<html>",
        extract_fn=lambda h: {"text": "Body.", "date": "2026-07-01", "sitename": None},
    )
    assert art["source_name"] == "example-news.com"
