"""A small Markdown subset for the agent's answers: bold, inline code, bullet lists, headings, and tables.

The text is escaped first and only these patterns become tags, so the agent's text can never inject markup.
"""

from __future__ import annotations

import re

from markupsafe import Markup, escape

BOLD = re.compile(r"\*\*(.+?)\*\*")
CODE = re.compile(r"`([^`]+)`")
BULLET = re.compile(r"^\s*[-*]\s+(.*)$")
HEADING = re.compile(r"^(#{1,4})\s+(.*)$")
TABLE_SEPARATOR = re.compile(r"^\|?[\s:|-]+\|?$")


def _inline(text: str) -> str:
    html = str(escape(text))
    html = CODE.sub(r"<code>\1</code>", html)
    return BOLD.sub(r"<b>\1</b>", html)


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def render(text: str) -> Markup:
    blocks: list[str] = []
    paragraph: list[str] = []
    lines = text.splitlines()

    def flush() -> None:
        if paragraph:
            blocks.append("<p>" + "<br>".join(_inline(line) for line in paragraph) + "</p>")
            paragraph.clear()

    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            flush()
            index += 1
        elif BULLET.match(line):
            flush()
            items = []
            while index < len(lines) and BULLET.match(lines[index]):
                items.append(f"<li>{_inline(BULLET.match(lines[index]).group(1))}</li>")
                index += 1
            blocks.append("<ul>" + "".join(items) + "</ul>")
        elif line.lstrip().startswith("|"):
            flush()
            rows = []
            while index < len(lines) and lines[index].lstrip().startswith("|"):
                if not TABLE_SEPARATOR.match(lines[index].strip()):
                    rows.append(_cells(lines[index]))
                index += 1
            head, *body = rows
            html = "<tr>" + "".join(f"<th>{_inline(cell)}</th>" for cell in head) + "</tr>"
            html += "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in row) + "</tr>" for row in body)
            blocks.append(f"<table>{html}</table>")
        elif heading := HEADING.match(line):
            flush()
            blocks.append(f"<p><b>{_inline(heading.group(2))}</b></p>")
            index += 1
        else:
            paragraph.append(line)
            index += 1
    flush()
    return Markup("".join(blocks))
