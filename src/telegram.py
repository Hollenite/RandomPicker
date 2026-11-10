"""Thin Telegram Bot API client. No bot framework; jobs are short-lived."""

from __future__ import annotations

import html
import json
import os
import re
from typing import Any, Callable

import requests

TELEGRAM_MAX_LEN = 4096
EMPTY_KEYBOARD: dict[str, list] = {"inline_keyboard": []}

CALLBACK_DONE = "done"
CALLBACK_REROLL = "reroll"
CALLBACK_EXPLAIN = "explain"
CALLBACK_HINT = "hint"
CALLBACK_SOLUTION = "solution"
CALLBACK_SOLUTION_CONFIRM = "solution_confirm"
CALLBACK_SOLUTION_CANCEL = "solution_cancel"


class TelegramError(RuntimeError):
    pass


def pending_keyboard() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": "Explain 🧠", "callback_data": CALLBACK_EXPLAIN}],
            [{"text": "Hint 💡", "callback_data": CALLBACK_HINT}],
            [{"text": "Solution 🔓", "callback_data": CALLBACK_SOLUTION}],
            [{"text": "Done ✅", "callback_data": CALLBACK_DONE}],
            [{"text": "Reroll 🎲", "callback_data": CALLBACK_REROLL}],
        ]
    }


def solution_confirm_keyboard() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": "Show solution", "callback_data": CALLBACK_SOLUTION_CONFIRM}],
            [{"text": "Cancel", "callback_data": CALLBACK_SOLUTION_CANCEL}],
        ]
    }


def question_keyboard(status: str, *, solution_confirm: bool = False) -> dict[str, Any] | None:
    if status != "pending":
        return None
    if solution_confirm:
        return solution_confirm_keyboard()
    return pending_keyboard()


def keyboard_labels(markup: dict[str, Any] | None) -> list[str]:
    if not markup:
        return []
    return [btn["text"] for row in markup.get("inline_keyboard", []) for btn in row]


def question_label(qid: int, difficulty: str, title: str | None = None) -> str:
    if title:
        return f"#{qid} {title} ({difficulty})"
    return f"#{qid} ({difficulty})"


def morning_text(
    qid: int,
    difficulty: str,
    title: str | None = None,
    statement: str | None = None,
    greeting: str | None = None,
) -> str:
    return _question_body(
        question_label(qid, difficulty, title),
        statement,
        greeting=greeting or "Good morning.",
    )


def reroll_text(
    qid: int,
    difficulty: str,
    title: str | None = None,
    statement: str | None = None,
) -> str:
    return _question_body(question_label(qid, difficulty, title), statement)


def _question_body(label: str, statement: str | None, greeting: str | None = None) -> str:
    prefix_parts = []
    if greeting:
        prefix_parts.append(greeting.strip())
    prefix_parts.append(f"Today's revisit: {label}")
    prefix = "\n\n".join(prefix_parts)
    footer = "Open it, solve it, then tap Done."
    body = (statement or "").strip() or "No statement in the catalog for this question."
    text = f"{prefix}\n\n{body}\n\n{footer}"
    if len(text) <= TELEGRAM_MAX_LEN:
        return text
    room = max(0, TELEGRAM_MAX_LEN - len(prefix) - len(footer) - 20)
    return f"{prefix}\n\n{body[:room].rstrip()}\n\n[truncated]\n\n{footer}"


def done_text(qid: int, title: str | None = None) -> str:
    if title:
        return f"Done ✅ #{qid} {title}"
    return f"Done ✅ #{qid}"


def empty_green_text() -> str:
    return "No questions in the green set yet."


def reminder_text(qid: int, reminder_number: int, title: str | None = None) -> str:
    label = f"#{qid} {title}" if title else f"#{qid}"
    if reminder_number <= 2:
        return f"Still pending: {label}. Tap Done when finished."
    if reminder_number <= 5:
        return f"Reminder {reminder_number}: {label} is still open. Even 15 minutes counts."
    return f"Last stretch: {label}. Do it or tap Reroll for something lighter."


def split_message(text: str, limit: int = TELEGRAM_MAX_LEN) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        window = remaining[:limit]
        cut = window.rfind("\n\n")
        if cut < limit // 2:
            cut = window.rfind("\n")
        if cut < limit // 2:
            cut = limit
        piece = remaining[:cut]
        if piece.count("```") % 2 == 1:
            last_fence = piece.rfind("```")
            if last_fence > limit // 4:
                piece = remaining[:last_fence]
                cut = last_fence
        chunks.append(piece)
        remaining = remaining[cut:].lstrip("\n")
    return [chunk for chunk in chunks if chunk]


_FENCE_RE = re.compile(r"```([a-zA-Z0-9_+-]*)\n?(.*?)```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_BULLET_RE = re.compile(r"^(\s*)[-*] +", re.MULTILINE)


def format_telegram_html(text: str) -> str:
    """Turn GitHub-style Markdown into Telegram HTML."""
    fences: list[str] = []

    def _keep_fence(match: re.Match[str]) -> str:
        lang = (match.group(1) or "").strip()
        body = html.escape(match.group(2).strip("\n"))
        if lang:
            lang_attr = html.escape(lang, quote=True)
            rendered = f'<pre><code class="language-{lang_attr}">{body}</code></pre>'
        else:
            rendered = f"<pre>{body}</pre>"
        fences.append(rendered)
        return f"%%%FENCE{len(fences) - 1}%%%"

    converted = _FENCE_RE.sub(_keep_fence, text)

    codes: list[str] = []

    def _keep_code(match: re.Match[str]) -> str:
        codes.append(f"<code>{html.escape(match.group(1))}</code>")
        return f"%%%CODE{len(codes) - 1}%%%"

    converted = _INLINE_CODE_RE.sub(_keep_code, converted)
    converted = html.escape(converted, quote=False)
    converted = _HEADING_RE.sub(lambda m: f"<b>{m.group(2).strip()}</b>", converted)
    converted = _BOLD_RE.sub(r"<b>\1</b>", converted)
    converted = _ITALIC_RE.sub(r"<i>\1</i>", converted)

    def _link(match: re.Match[str]) -> str:
        label, url = match.group(1), match.group(2)
        return f'<a href="{html.escape(url, quote=True)}">{label}</a>'

    converted = _LINK_RE.sub(_link, converted)
    converted = _BULLET_RE.sub(r"\1• ", converted)
    converted = re.sub(r" +\n", "\n", converted)

    for index, snippet in enumerate(codes):
        converted = converted.replace(f"%%%CODE{index}%%%", snippet)
    for index, snippet in enumerate(fences):
        converted = converted.replace(f"%%%FENCE{index}%%%", snippet)
    return converted


def parse_command(text: str | None) -> tuple[str, list[str]] | None:
    if not text:
        return None
    parts = text.strip().split()
    if not parts or not parts[0].startswith("/"):
        return None
    name = parts[0][1:].split("@", 1)[0].lower()
    return name, parts[1:]


def extract_ids(update: dict[str, Any]) -> tuple[int | None, int | None]:
    if "message" in update:
        message = update["message"]
        user = message.get("from") or {}
        chat = message.get("chat") or {}
        return _as_int(user.get("id")), _as_int(chat.get("id"))
    if "callback_query" in update:
        callback = update["callback_query"]
        user = callback.get("from") or {}
        message = callback.get("message") or {}
        chat = message.get("chat") or {}
        return _as_int(user.get("id")), _as_int(chat.get("id"))
    return None, None


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def is_authorized(update: dict[str, Any], expected_chat_id: int) -> bool:
    user_id, chat_id = extract_ids(update)
    if user_id is None or chat_id is None:
        return False
    return user_id == expected_chat_id and chat_id == expected_chat_id


class TelegramClient:
    def __init__(
        self,
        token: str,
        chat_id: str | int,
        *,
        dry_run: bool = False,
        session: requests.Session | None = None,
        injected_updates: list[dict[str, Any]] | None = None,
        printer: Callable[[str], None] = print,
    ) -> None:
        self.token = token
        self.chat_id = str(chat_id)
        self.dry_run = dry_run
        self.session = session or requests.Session()
        self.injected_updates = injected_updates or []
        self.printer = printer
        self.sent: list[dict[str, Any]] = []
        self.edits: list[dict[str, Any]] = []
        self.callback_answers: list[dict[str, Any]] = []
        self._next_message_id = 1

    def send_message(
        self, text: str, reply_markup: dict[str, Any] | None = None
    ) -> int:
        payload = self._text_payload(text)
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        if self.dry_run:
            self._log("sendMessage", payload)
            message_id = self._next_message_id
            self._next_message_id += 1
            self.sent.append({"message_id": message_id, **payload})
            return message_id
        result = self._api_text("sendMessage", payload, text)
        message_id = int(result["message_id"])
        self.sent.append({"message_id": message_id, **payload})
        return message_id

    def edit_message(
        self,
        message_id: int,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> None:
        payload = self._text_payload(text)
        payload["message_id"] = message_id
        payload["reply_markup"] = reply_markup if reply_markup is not None else EMPTY_KEYBOARD
        if self.dry_run:
            self._log("editMessageText", payload)
            self.edits.append(payload)
            return
        self._api_text("editMessageText", payload, text)
        self.edits.append(payload)

    def _text_payload(self, text: str) -> dict[str, Any]:
        return {
            "chat_id": self.chat_id,
            "text": format_telegram_html(text),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

    def _api_text(self, method: str, payload: dict[str, Any], original: str) -> Any:
        try:
            return self._api(method, payload)
        except TelegramError:
            payload["text"] = original
            payload.pop("parse_mode", None)
            return self._api(method, payload)

    def edit_reply_markup(self, message_id: int, reply_markup: dict[str, Any] | None) -> None:
        payload = {
            "chat_id": self.chat_id,
            "message_id": message_id,
            "reply_markup": reply_markup if reply_markup is not None else EMPTY_KEYBOARD,
        }
        if self.dry_run:
            self._log("editMessageReplyMarkup", payload)
            self.edits.append(payload)
            return
        try:
            self._api("editMessageReplyMarkup", payload)
        except TelegramError:
            return
        self.edits.append(payload)

    def answer_callback(self, callback_id: str, text: str | None = None) -> None:
        payload: dict[str, Any] = {"callback_query_id": callback_id}
        if text:
            payload["text"] = text
        if self.dry_run:
            self._log("answerCallbackQuery", payload)
            self.callback_answers.append(payload)
            return
        try:
            self._api("answerCallbackQuery", payload)
        except TelegramError:
            return
        self.callback_answers.append(payload)

    def get_updates(self, offset: int, timeout: int = 0) -> list[dict[str, Any]]:
        payload = {
            "offset": offset,
            "timeout": timeout,
            "allowed_updates": ["message", "callback_query"],
        }
        if self.dry_run:
            self._log("getUpdates", payload)
            return [u for u in self.injected_updates if int(u.get("update_id", 0)) >= offset]
        result = self._api("getUpdates", payload, http_timeout=max(30, timeout + 10))
        return list(result)

    def _api(self, method: str, payload: dict[str, Any], http_timeout: int = 30) -> Any:
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        try:
            response = self.session.post(url, json=payload, timeout=http_timeout)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            raise TelegramError(str(exc)) from exc
        if not data.get("ok"):
            raise TelegramError(str(data.get("description", "telegram error")))
        return data["result"]

    def _log(self, method: str, payload: dict[str, Any]) -> None:
        self.printer(f"[dry-run] {method} {json.dumps(payload, ensure_ascii=False)}")


def expected_chat_id() -> int:
    raw = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not raw:
        raise TelegramError("TELEGRAM_CHAT_ID is not set")
    return int(raw)
