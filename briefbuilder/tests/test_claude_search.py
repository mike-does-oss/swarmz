from app.services.brief_v2.claude_search import ClaudeSearchClient


def test_results_come_back_in_the_search_clients_shape_and_only_real_links():
    seen = {}

    def ask(system, user, **kw):
        seen.update(kw, user=user)
        return 'Here are the results:\n[{"url": "https://a.example/1", "title": "A", "description": "d", "page_age": "2026-11-09"},' \
               ' {"url": "javascript:alert(1)", "title": "bad"}, {"title": "no url"}, "junk", {"url": "https://b.example/2"}]'
    out = ClaudeSearchClient(model="m", ask=ask).search("fed december decision", count=5, freshness="pw")
    assert out == [{"url": "https://a.example/1", "title": "A", "description": "d", "page_age": "2026-11-09"},
                   {"url": "https://b.example/2", "title": "", "description": "", "page_age": None}]
    assert seen["tools"] == ("WebSearch",) and "past week" in seen["user"] and "up to 5" in seen["user"]


def test_an_answer_with_no_list_is_no_results():
    assert ClaudeSearchClient(model="m", ask=lambda *a, **k: "I could not search.").search("q") == []
