from telegram import TelegramClient, callback_data, format_telegram_html, parse_callback


def test_parse_callback_binds_question_id() -> None:
    assert callback_data("explain", 242) == "explain:242"
    assert parse_callback("explain:242") == ("explain", 242)
    assert parse_callback("done") == ("done", None)
    assert parse_callback("solution_confirm:7") == ("solution_confirm", 7)


def test_format_bold_italic_code_and_lists() -> None:
    html = format_telegram_html(
        "**Hint (level 1):**\nThink about a *string* named `s`.\n- track counts\n- compare them"
    )
    assert html.startswith("<b>Hint (level 1):</b>")
    assert "<i>string</i>" in html
    assert "<code>s</code>" in html
    assert "• track counts" in html
    assert "• compare them" in html


def test_format_fenced_code_and_escapes() -> None:
    html = format_telegram_html(
        "Use `vector<int>`.\n\n```python\nprint(1 < 2)\n```"
    )
    assert "<code>vector&lt;int&gt;</code>" in html
    assert '<pre><code class="language-python">print(1 &lt; 2)</code></pre>' in html


def test_format_links_and_headings() -> None:
    html = format_telegram_html("## Title\nSee [docs](https://example.com/a).")
    assert "<b>Title</b>" in html
    assert '<a href="https://example.com/a">docs</a>' in html


def test_send_message_uses_telegram_html(capsys) -> None:
    bot = TelegramClient("token", 1, dry_run=True)
    bot.send_message("Return **true** if `s` matches.")
    payload = bot.sent[0]
    assert payload["parse_mode"] == "HTML"
    assert payload["text"] == "Return <b>true</b> if <code>s</code> matches."
    out = capsys.readouterr().out
    assert "parse_mode" in out
