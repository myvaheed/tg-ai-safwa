"""The text of a message, on the way out and on the way back.

Out: render the Markdown subset a model writes as safe Telegram HTML, and cut a message
Telegram would refuse into ones it takes without leaving formatting open across the cut.
Back: the chat is kept as Telegram HTML and a model reads words, a citation written back
from the link it became. All three are pure.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlparse

# Telegram accepts 4096 characters. The rest is headroom for what a host appends to a part
# after it is cut — a marker, a heading — and it is the number a scenario names.
TELEGRAM_TEXT_LIMIT = 3_900

_CITATION_HOSTS = frozenset({"t.me", "www.t.me", "telegram.me"})
_MARKDOWN_ESCAPE = re.compile(r"\\([\\`*_[\]()~])")
_TAG_RE = re.compile(r"<(/?)([a-zA-Z][\w-]*)(?:\s[^>]*)?>")
# Room kept at the end of a part for the closing tags it has to carry.  Bot messages nest
# three or four deep at most, and `</blockquote>` is the longest closer there is.
_CLOSING_RESERVE = 80


def markdown_to_telegram_html(text: str) -> str:
    """Render the Markdown subset a model writes as safe Telegram HTML.

    Escape the model's text first, then translate only the small subset promised in its
    prompt.  Completed fragments are protected from later passes so mixed delimiters cannot
    create crossing HTML tags.  Markdown links are left alone: a host that resolves them
    from live data needs them still in Markdown form.
    """
    rendered = html.escape(text)
    protected: list[str] = []
    token_prefix = "md-"
    while token_prefix in rendered:
        token_prefix += "x"

    def protect(fragment: str) -> str:
        token = f"{token_prefix}{len(protected)}"
        protected.append(fragment)
        return token

    rendered = _MARKDOWN_ESCAPE.sub(lambda match: protect(match[1]), rendered)

    def fenced_code(match: re.Match[str]) -> str:
        body = match[1]
        if "\n" in body:
            first, rest = body.split("\n", 1)
            if re.fullmatch(r"[A-Za-z0-9_+.-]*", first):
                body = rest
        return protect(f"<pre><code>{body}</code></pre>")

    rendered = re.sub(r"```(.*?)```", fenced_code, rendered, flags=re.DOTALL)
    rendered = re.sub(
        r"`([^`\n]+)`", lambda match: protect(f"<code>{match[1]}</code>"), rendered
    )

    inline_patterns = (
        (r"\*\*(?=\S)([^*\n]+?)(?<=\S)\*\*", "b"),
        (r"__(?=\S)([^_\n]+?)(?<=\S)__", "b"),
        (r"~~(?=\S)([^~\n]+?)(?<=\S)~~", "s"),
        (r"(?<!\*)\*(?=\S)([^*\n]+?)(?<=\S)\*(?!\*)", "i"),
        (r"(?<![\w_])_(?=\S)([^_\n]+?)(?<=\S)_(?![\w_])", "i"),
    )
    for pattern, tag in inline_patterns:
        rendered = re.sub(
            pattern,
            lambda match, tag=tag: protect(f"<{tag}>{match[1]}</{tag}>"),
            rendered,
        )

    for index, fragment in reversed(list(enumerate(protected))):
        rendered = rendered.replace(f"{token_prefix}{index}", fragment)
    return rendered


def split_telegram_text(text: str, limit: int = TELEGRAM_TEXT_LIMIT) -> list[str]:
    """Split text into messages Telegram accepts, leaving no formatting open across a cut.

    The cut prefers a line break, then a space, then the limit itself, and is moved back
    out of any tag or entity it lands in.  Whatever is still open at the cut is closed at
    the end of the part and opened again at the start of the next one.
    """
    text = text.strip()
    if not text:
        return []
    chunks: list[str] = []
    carried = ""
    while len(carried) + len(text) > limit:
        room = max(limit - len(carried) - _CLOSING_RESERVE, limit // 2)
        cut = text.rfind("\n", 0, room)
        if cut < room // 2:
            cut = text.rfind(" ", 0, room)
        if cut < room // 2:
            cut = room
        cut = _outside_markup(text, cut)
        head, text = text[:cut].rstrip(), text[cut:].lstrip()
        opened = _open_tags(carried + head)
        chunks.append(carried + head + "".join(f"</{name}>" for name, _ in reversed(opened)))
        carried = "".join(markup for _, markup in opened)
    chunks.append(carried + text)
    return chunks


def _open_tags(text: str) -> list[tuple[str, str]]:
    """The tags still open at the end of `text`, outermost first, with their own markup."""
    stack: list[tuple[str, str]] = []
    for match in _TAG_RE.finditer(text):
        name = match.group(2).lower()
        if match.group(1):
            for index in range(len(stack) - 1, -1, -1):
                if stack[index][0] == name:
                    del stack[index]
                    break
        else:
            stack.append((name, match.group(0)))
    return stack


def _outside_markup(text: str, cut: int) -> int:
    """Move a cut back out of the tag or HTML entity it would otherwise land inside."""
    for opener, closer in (("<", ">"), ("&", ";")):
        start = text.rfind(opener, 0, cut)
        if start != -1 and text.find(closer, start, cut) == -1:
            cut = start
    return cut


def telegram_html_to_text(text: str, citation_types: tuple[str, ...]) -> str:
    """Read a message kept as Telegram HTML back as the words it shows.

    The tags go and the entities are read, which is what the person sees. A link to one of
    the bot's own items is written back as the `[text](card:12)` citation it was rendered
    from, so a model reading its own answer sees that citing is how it names an item; any
    other link reads as its words.
    """
    reader = _Words(citation_types)
    reader.feed(text)
    reader.close()
    return "".join(reader.parts)


class _Words(HTMLParser):
    def __init__(self, citation_types: tuple[str, ...]) -> None:
        super().__init__(convert_charrefs=True)
        self.citation_types = citation_types
        self.parts: list[str] = []
        # Each open link: the citation it stands for, or None, and where its words begin.
        self.links: list[tuple[str | None, int]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            target = _citation_from_url(dict(attrs).get("href"), self.citation_types)
            self.links.append((target, len(self.parts)))

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or not self.links:
            return
        target, start = self.links.pop()
        if target is not None:
            label = "".join(self.parts[start:])
            self.parts[start:] = [f"[{label}]({target})"]

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _citation_from_url(url: str | None, citation_types: tuple[str, ...]) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.netloc.casefold() not in _CITATION_HOSTS:
        return None
    item_type, _, item_id = parse_qs(parsed.query).get("start", [""])[0].strip().partition("-")
    if item_type not in citation_types or not item_id.isdigit() or len(item_id) > 9:
        return None
    return f"{item_type}:{int(item_id)}"
