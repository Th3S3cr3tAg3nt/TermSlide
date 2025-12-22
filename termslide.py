#!/usr/bin/env python3
"""TermSlide: terminal-based Markdown slideshow.

This is a curses-based presenter that renders a subset of Markdown features
(headings, inline formatting, lists, tables, images) and supports Mermaid
diagrams via the optional `mermaid-ascii-diagrams` Python package.

Mermaid support
---------------
If `mermaid-ascii-diagrams` is installed, fenced code blocks tagged as
```mermaid``` are rendered into an ASCII/Unicode diagram. If the package is not
installed (or rendering fails), TermSlide falls back to showing the Mermaid
source as a normal code block. When exiting a presentation that contains Mermaid
blocks without the library installed, TermSlide prints a reminder command to
install it.

Environment variables
---------------------
- TERMSLIDE_MERMAID_ASCII_ONLY=1:
  Force ASCII-only output for Mermaid diagrams (disable box-drawing characters).
"""

from __future__ import annotations

import curses
import locale
import os
import re
import sys
import unicodedata

from pyfiglet import Figlet

# Optional Mermaid support (pip install mermaid-ascii-diagrams).
# The `mermaid-ascii-diagrams` project installs the `mermaid_ascii` module.
try:
    from mermaid_ascii import parse_mermaid as _parse_mermaid
    from mermaid_ascii import render_ascii as _render_ascii

    _MERMAID_LIB_AVAILABLE = True
except Exception:
    _parse_mermaid = None
    _render_ascii = None
    _MERMAID_LIB_AVAILABLE = False

# Tracks whether the current presentation contains any Mermaid blocks.
_ENCOUNTERED_MERMAID_BLOCK = False


def _terminal_encoding() -> str:
    """Return the terminal encoding (best effort)."""
    return (getattr(sys.stdout, "encoding", None) or locale.getpreferredencoding(False) or "").lower()


def _utf8_probably_supported() -> bool:
    """Heuristic for whether Unicode box drawing is likely to render correctly."""
    enc = _terminal_encoding()
    if "utf" not in enc:
        return False

    # Ensure the chosen glyphs can be encoded in the terminal encoding.
    try:
        "┌─│┐└┘┼►".encode(enc, errors="strict")
    except Exception:
        return False

    # Box-drawing characters should not be combining marks.
    return not any(unicodedata.combining(ch) for ch in "┌─│┐└┘┼►")


# Prefer Unicode box drawing when UTF-8 looks supported; otherwise fall back to ASCII.
_USE_ASCII_MERMAID_FALLBACK = not _utf8_probably_supported()

# Manual override: force ASCII output for Mermaid diagrams.
if os.environ.get("TERMSLIDE_MERMAID_ASCII_ONLY"):
    _USE_ASCII_MERMAID_FALLBACK = True

# Unicode -> ASCII fallback map (helps terminals/fonts that don't render box-drawing cleanly).
_UNICODE_TO_ASCII = str.maketrans(
    {
        "─": "-",
        "│": "|",
        "┌": "+",
        "┐": "+",
        "└": "+",
        "┘": "+",
        "┬": "+",
        "┴": "+",
        "├": "+",
        "┤": "+",
        "┼": "+",
        "╭": "+",
        "╮": "+",
        "╰": "+",
        "╯": "+",
        "►": ">",
        "◄": "<",
        "▼": "v",
        "▲": "^",
    }
)

try:
    from PIL import Image
except ImportError:
    Image = None


def parse_markdown(md_text):
    """Split Markdown into slides using '---' separators and simple title rules."""
    slides = []
    raw_slides = re.split(r'^\-{3,}\s*$', md_text, flags=re.MULTILINE)
    for raw in raw_slides:
        lines = [line.rstrip() for line in raw.strip().splitlines()]
        if not any(l.strip() for l in lines):
            continue
        if len(lines) > 1 and re.match(r"^=+$", lines[1].strip()):
            title = lines[0].strip()
            content = "\n".join(lines[2:])
            slides.append(("title", title, content))
        else:
            title = None
            if lines[0].startswith("#"):
                title = lines[0].lstrip("# ").strip()
                content = "\n".join(lines[1:])
            else:
                content = "\n".join(lines)
            slides.append(("content", title, content))
    return slides


def parse_image_only(content):
    """Detect slides that contain only an image and return (path, alt) if present."""
    content = content.strip()
    if not content:
        return None
    m = re.fullmatch(r'!\[([^\]]*)\]\((.*?)\)\s*', content)
    if m:
        alt, path = m.groups()
        if os.path.exists(path):
            return path, alt.strip()
        return None
    if "\n" not in content and (
        "/" in content or content.lower().endswith((".png", ".jpg", ".jpeg", ".bmp", ".gif"))
    ):
        if os.path.exists(content):
            return content, ""
    return None


def rgb_to_ansi256(r, g, b):
    """Map an RGB tuple (0-255) to the nearest 256-color ANSI index."""
    r_ = int(round(r / 255 * 5))
    g_ = int(round(g / 255 * 5))
    b_ = int(round(b / 255 * 5))
    return 16 + 36 * r_ + 6 * g_ + b_


def render_image_in_curses(stdscr, img_path, alt):
    """Render an image slide using half-block characters (requires Pillow)."""
    if Image is None:
        stdscr.addstr(2, 2, "Pillow required for image slides.")
        return

    h, w = stdscr.getmaxyx()
    tgt_w = w
    tgt_h = (h - 1) * 2

    img = Image.open(img_path).convert("RGB")
    img_w, img_h = img.size
    ratio = min(tgt_w / img_w, tgt_h / img_h)
    new_w = max(1, int(img_w * ratio))
    new_h = max(1, int(img_h * ratio))
    img = img.resize((new_w, new_h), Image.LANCZOS)

    canvas = Image.new("RGB", (tgt_w, tgt_h), (0, 0, 0))
    off_x = (tgt_w - new_w) // 2
    off_y = (tgt_h - new_h) // 2
    canvas.paste(img, (off_x, off_y))
    color_cache = {}
    next_pair = 50
    for y in range(h - 1):
        for x in range(w):
            top = canvas.getpixel((x, y * 2))
            bot = canvas.getpixel((x, y * 2 + 1))
            top_idx = rgb_to_ansi256(*top)
            bot_idx = rgb_to_ansi256(*bot)
            key = (bot_idx, top_idx)
            if key not in color_cache:
                if next_pair < curses.COLOR_PAIRS:
                    curses.init_pair(next_pair, bot_idx, top_idx)
                    color_cache[key] = next_pair
                    next_pair += 1
                else:
                    color_cache[key] = 0
            pair_id = color_cache[key]
            if pair_id > 0:
                stdscr.attron(curses.color_pair(pair_id))
            stdscr.addstr(y, x, "▄")
            if pair_id > 0:
                stdscr.attroff(curses.color_pair(pair_id))

    stdscr.addstr(h - 1, 2, "←/→ to navigate, q to quit")
    if alt:
        stdscr.addstr(h - 1, w - len(alt) - 2, alt)


def render_links(line, stdscr, y, x, maxw):
    """Render Markdown links/images inline with basic styling."""
    pos = 0
    pattern = re.compile(r"(!?\[[^\]]*\]\([^)]*\))")
    for match in pattern.finditer(line):
        start, end = match.span()
        stdscr.addstr(y, x + pos, line[pos:start])
        text = match.group(0)
        img_match = re.match(r"!\[([^\]]*)\]\(([^)]*)\)", text)
        link_match = re.match(r"\[([^\]]*)\]\(([^)]*)\)", text)
        if img_match:
            alt, url = img_match.groups()
            stdscr.addstr(y, x + start, f"Image: {alt} ")
            stdscr.attron(curses.color_pair(12))
            stdscr.addstr(y, x + start + len(f"Image: {alt} "), f"({url})"[: maxw - (x + start)])
            stdscr.attroff(curses.color_pair(12))
        elif link_match:
            label, url = link_match.groups()
            stdscr.addstr(y, x + start, label + " ")
            stdscr.attron(curses.color_pair(12))
            stdscr.addstr(y, x + start + len(label) + 1, f"({url})"[: maxw - (x + start + len(label) + 1)])
            stdscr.attroff(curses.color_pair(12))
        pos = end
    if pos < len(line):
        stdscr.addstr(y, x + pos, line[pos:])


def rendered_length(text):
    """Calculate the rendered length of text after removing markdown inline formatting delimiters."""
    cursor = 0
    pattern = re.compile(r"(\*\*([^\*]+)\*\*|\*([^\*]+)\*|`([^`]+)`)")
    last_end = 0
    for match in pattern.finditer(text):
        start, end = match.span()
        if start > last_end:
            cursor += (start - last_end)
        if match.group(2):  # bold
            cursor += len(match.group(2))
        elif match.group(3):  # italic
            cursor += len(match.group(3))
        elif match.group(4):  # inline code
            cursor += len(match.group(4))
        last_end = end
    if last_end < len(text):
        cursor += (len(text) - last_end)
    return cursor


def format_inline(line, stdscr, y, x, maxw):
    """Render inline Markdown emphasis (bold/italic/code) onto the curses screen.

    This function is intentionally conservative about screen bounds to avoid
    `_curses.error: addwstr() returned ERR` when text would overflow the window.
    """
    cursor = 0
    pattern = re.compile(r"(\*\*([^\*]+)\*\*|\*([^\*]+)\*|`([^`]+)`)")
    last_end = 0
    max_y, max_x = stdscr.getmaxyx()

    def _add(text: str, attr: int | None = None) -> None:
        nonlocal cursor
        if not text:
            return
        if y < 0 or y >= max_y:
            return
        start_x = x + cursor
        if start_x < 0 or start_x >= max_x:
            return
        avail = min(maxw, max_x) - start_x
        if avail <= 0:
            return
        chunk = text[:avail]
        try:
            if attr is not None:
                stdscr.attron(attr)
            stdscr.addstr(y, start_x, chunk)
        except curses.error:
            # Ignore draw errors caused by terminal/window edge cases.
            pass
        finally:
            if attr is not None:
                try:
                    stdscr.attroff(attr)
                except curses.error:
                    pass
        cursor += len(chunk)

    for match in pattern.finditer(line):
        start, end = match.span()
        if start > last_end:
            _add(line[last_end:start])

        if match.group(2):  # bold
            _add(match.group(2), curses.color_pair(5))
        elif match.group(3):  # italic
            _add(match.group(3), curses.color_pair(6))
        elif match.group(4):  # inline code
            _add(match.group(4), curses.color_pair(7))

        last_end = end

    if last_end < len(line):
        _add(line[last_end:])


def parse_table(lines, start_idx):
    """Parse a markdown table starting from start_idx, return table data and number of lines consumed."""
    if start_idx >= len(lines) or not lines[start_idx].strip().startswith("|"):
        return None, 0
    
    table_data = []
    col_widths = []
    current_line = start_idx
    
    # Parse header
    header = [cell.strip() for cell in lines[current_line].strip("| \t").split("|")]
    if not header:
        return None, 0
    table_data.append(header)
    col_widths = [rendered_length(cell) for cell in header]
    current_line += 1
    
    # Parse separator line
    if current_line >= len(lines) or not lines[current_line].strip().startswith("|"):
        return None, 0
    separator = lines[current_line].strip("| \t").split("|")
    if len(separator) != len(header) or not all(re.match(r"^-+:?-*$", cell.strip()) for cell in separator):
        return None, 0
    current_line += 1
    
    # Parse rows
    while current_line < len(lines) and lines[current_line].strip().startswith("|"):
        row = [cell.strip() for cell in lines[current_line].strip("| \t").split("|")]
        if len(row) != len(header):
            break
        table_data.append(row)
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], rendered_length(cell))
        current_line += 1
    
    return table_data, col_widths, current_line - start_idx


def render_row(stdscr, y, row, col_widths, x, maxw):
    """Render a single table row with breathing room (space on both sides of content)."""
    stdscr.addstr(y, x, "│")
    current_x = x + 1
    for i, cell in enumerate(row):
        # Left padding space
        stdscr.addstr(y, current_x, " ")
        content_x = current_x + 1
        # Render cell content with inline formatting
        format_inline(cell, stdscr, y, content_x, maxw)
        rl = rendered_length(cell)
        # Right padding: fill up to col_widths[i] (content width) + 1 extra space
        pad_x = content_x + rl
        pad_len = col_widths[i] - rl + 1
        if pad_len > 0:
            stdscr.addstr(y, pad_x, " " * pad_len)
        border_x = content_x + col_widths[i] + 1
        stdscr.addstr(y, border_x, "│")
        current_x = border_x + 1
    return 1


def render_table(table_data, col_widths, stdscr, y, x, maxw):
    """Render a table using ASCII box-drawing characters with inline formatting and breathing room."""
    if not table_data:
        return 0
        
    stdscr.attron(curses.color_pair(8))  # Table color
    lines_used = 0
    
    # Draw top border: +2 for spaces on both sides of content
    top_border = "┌" + "┬".join("─" * (w + 2) for w in col_widths) + "┐"
    stdscr.addstr(y, x, top_border[:maxw - x])
    lines_used += 1
    
    # Draw header row
    header = table_data[0]
    lines_used += render_row(stdscr, y + lines_used, header, col_widths, x, maxw)
    
    # Draw separator
    sep_border = "├" + "┼".join("─" * (w + 2) for w in col_widths) + "┤"
    stdscr.addstr(y + lines_used, x, sep_border[:maxw - x])
    lines_used += 1
    
    # Draw data rows
    for row in table_data[1:]:
        lines_used += render_row(stdscr, y + lines_used, row, col_widths, x, maxw)
    
    # Draw bottom border
    bot_border = "└" + "┴".join("─" * (w + 2) for w in col_widths) + "┘"
    stdscr.addstr(y + lines_used, x, bot_border[:maxw - x])
    lines_used += 1
    
    stdscr.attroff(curses.color_pair(8))
    return lines_used

def render_mermaid(diagram_content, stdscr, y, x, maxw, color_attr):
    """Render a Mermaid fenced block.

    If Mermaid support is available, render the diagram via `mermaid-ascii-diagrams`.
    Otherwise (or on render errors), display the raw Mermaid text as a normal code block.

    Returns the number of terminal lines consumed.
    """
    global _ENCOUNTERED_MERMAID_BLOCK
    _ENCOUNTERED_MERMAID_BLOCK = True

    prefix = "│ "
    max_y, max_x = stdscr.getmaxyx()
    avail = max(0, min(maxw, max_x) - x)

    def _draw_lines(lines):
        lines_used = 0
        for line in lines:
            if y + lines_used >= max_y:
                break
            # Preserve the diagram's layout by not wrapping; just truncate to width.
            text = (prefix + line)[:avail]
            stdscr.attron(color_attr)
            stdscr.addstr(y + lines_used, x, text)
            stdscr.attroff(color_attr)
            lines_used += 1
        return lines_used

    if not _MERMAID_LIB_AVAILABLE or _parse_mermaid is None or _render_ascii is None:
        return _draw_lines(diagram_content.splitlines())

    try:
        diagram = _parse_mermaid(diagram_content)
        out = _render_ascii(diagram)
        if _USE_ASCII_MERMAID_FALLBACK:
            out = out.translate(_UNICODE_TO_ASCII)
        rendered_lines = out.rstrip("\n").splitlines() if out else []
        return _draw_lines(rendered_lines or diagram_content.splitlines())
    except Exception:
        # Rendering should never break the slideshow: fall back to raw Mermaid.
        return _draw_lines(diagram_content.splitlines())

    # Render via mermaid-ascii-diagrams
    try:
        out = _mermaid_render(diagram_content)
        if _USE_ASCII_MERMAID_FALLBACK:
            out = out.translate(_UNICODE_TO_ASCII)
        lines = out.rstrip("\n").splitlines() if out else []
    except Exception as e:
        # If rendering fails, show a short error line + raw input (so the slide still works)
        err = f"{type(e).__name__}: {e}"
        lines = ["[mermaid render failed] " + err, ""] + diagram_content.splitlines()

    lines_used = 0
    max_y, max_x = stdscr.getmaxyx()
    for line in lines:
        if y + lines_used >= max_y:
            break
        avail = max(0, min(maxw, max_x) - x)
        stdscr.attron(color_attr)
        stdscr.addstr(y + lines_used, x, line[:avail])
        stdscr.attroff(color_attr)
        lines_used += 1
    return lines_used




def format_text(line, stdscr, y, x, maxw, fig_slide, lines=None, line_idx=0):
    if lines and line.strip().startswith("|"):
        table_info = parse_table(lines, line_idx)
        if table_info[0] is not None:
            table_data, col_widths, lines_consumed = table_info
            rendered = render_table(table_data, col_widths, stdscr, y, x, maxw)
            return rendered, lines_consumed
    
    if re.search(r"!\[[^\]]*\]\([^)]*\)|\[[^\]]*\]\([^)]*\)", line):
        render_links(line, stdscr, y, x, maxw)
        return 1, 1
    if line.strip().startswith(">"):
        text = line.lstrip("> ").strip()
        stdscr.attron(curses.color_pair(9))
        stdscr.addstr(y, x, "│ ")
        format_inline(text, stdscr, y, x + 2, maxw)
        stdscr.attroff(curses.color_pair(9))
        return 1, 1
    m = re.match(r"^(#+) (.*)$", line)
    if m:
        level = len(m.group(1))
        text = m.group(2).strip()
        if level == 1:
            ascii_title = fig_slide.renderText(text)
            stdscr.attron(curses.color_pair(2) | curses.A_BOLD)
            title_lines = ascii_title.splitlines()
            for i, l in enumerate(title_lines):
                if y + i < stdscr.getmaxyx()[0]:
                    stdscr.addstr(y + i, x, l[:maxw - x])
            stdscr.attroff(curses.color_pair(2) | curses.A_BOLD)
            return len(title_lines), 1
        elif level == 2:
            stdscr.attron(curses.color_pair(3) | curses.A_BOLD)
            format_inline(text, stdscr, y, x, maxw)
            stdscr.attroff(curses.color_pair(3) | curses.A_BOLD)
            return 1, 1
        else:
            stdscr.attron(curses.color_pair(4) | curses.A_BOLD)
            format_inline(text, stdscr, y, x, maxw)
            stdscr.attroff(curses.color_pair(4) | curses.A_BOLD)
            return 1, 1
    if line.strip().startswith("- "):
        line = "• " + line.strip()[2:]
        stdscr.attron(curses.color_pair(10))
        format_inline(line, stdscr, y, x, maxw)
        stdscr.attroff(curses.color_pair(10))
        return 1, 1
    format_inline(line, stdscr, y, x, maxw)
    return 1, 1


def render_content(stdscr, content, start_y, start_x, maxw, fig_slide):
    """Render a slide's body content, including fenced code blocks and tables."""
    img_info = parse_image_only(content)
    if img_info:
        path, alt = img_info
        render_image_in_curses(stdscr, path, alt)
        return
    lines = content.split("\n")
    y = start_y
    i = 0
    in_code = False
    language = None
    code_lines = []
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            if not in_code:
                lang = line.strip()[3:].strip()
                language = lang if lang else "text"
                in_code = True
                code_lines = []
                i += 1
                continue
            else:
                in_code = False
                if language == "mermaid":
                    diagram_content = "\n".join(code_lines)
                    consumed = render_mermaid(diagram_content, stdscr, y, start_x, maxw, curses.color_pair(8))
                    y += consumed
                else:
                    for code_line in code_lines:
                        stdscr.attron(curses.color_pair(7))
                        if y < stdscr.getmaxyx()[0] and start_x < stdscr.getmaxyx()[1]:
                            stdscr.addstr(y, start_x, "│ " + code_line[:maxw - (start_x + 2)])
                        stdscr.attroff(curses.color_pair(7))
                        y += 1
                language = None
                code_lines = []
                i += 1
                continue
        if in_code:
            code_lines.append(line)
            i += 1
            continue
        consumed = format_text(line, stdscr, y, start_x, maxw, fig_slide, lines, i)
        y += consumed[0]  # Use rendered_consumed
        i += consumed[1]  # Use source_consumed


def run_slideshow(stdscr, slides):
    """Curses main loop: draw slides and handle navigation keys."""
    curses.curs_set(0)
    curses.start_color()
    curses.use_default_colors()
    # Brightest possible unique colors
    # If 16+ colors are available, explicitly use the bright 16-color ANSI indices
    # for headings so they render brighter even when A_BOLD doesn't increase intensity.
    if getattr(curses, "COLORS", 0) >= 16:
        curses.init_pair(2, 11, -1)  # Heading 1 (bright yellow)
        curses.init_pair(3, 14, -1)  # Heading 2 (bright cyan)
        curses.init_pair(4, 13, -1)  # Heading 3+ (bright magenta)
    else:
        curses.init_pair(2, curses.COLOR_YELLOW, -1)   # Heading 1
        curses.init_pair(3, curses.COLOR_CYAN, -1)     # Heading 2
        curses.init_pair(4, curses.COLOR_MAGENTA, -1)  # Heading 3+
    curses.init_pair(5, curses.COLOR_RED, -1)      # Bold
    curses.init_pair(6, curses.COLOR_YELLOW, -1)   # Italic
    curses.init_pair(7, curses.COLOR_GREEN, -1)    # Code
    curses.init_pair(8, curses.COLOR_WHITE, -1)    # Table
    curses.init_pair(9, curses.COLOR_WHITE, -1)    # Blockquote
    curses.init_pair(10, curses.COLOR_CYAN, -1)    # Bullets
    curses.init_pair(12, curses.COLOR_BLUE, -1)    # Links & image paths

    h, w = stdscr.getmaxyx()
    fig_title = Figlet(font="mono12", width=w)
    #fig_title = Figlet(font="standard", width=w)
    #fig_slide = Figlet(font="small", width=w)
    fig_slide = Figlet(font="smblock", width=w)
    idx = 0

    while True:
        stdscr.clear()
        h, w = stdscr.getmaxyx()
        slide_type, title, content = slides[idx]
        if slide_type == "title":
            ascii_title = fig_title.renderText(title)
            lines = ascii_title.splitlines()
            start_y = max(0, (h - len(lines)) // 2)
            stdscr.attron(curses.color_pair(2) | curses.A_BOLD)
            for i, line in enumerate(lines):
                if start_y + i < h:
                    stdscr.addstr(start_y + i, max(0, (w - len(line)) // 2), line)
            stdscr.attroff(curses.color_pair(2) | curses.A_BOLD)
            if content:
                render_content(stdscr, content, start_y + len(lines) + 2, max(0, w // 4), w, fig_slide)
        else:
            if title:
                ascii_title = fig_slide.renderText(title)
                stdscr.attron(curses.color_pair(2) | curses.A_BOLD)
                for i, line in enumerate(ascii_title.splitlines()):
                    if i + 1 < h:
                        stdscr.addstr(i + 1, 2, line)
                stdscr.attroff(curses.color_pair(2) | curses.A_BOLD)
                offset = len(ascii_title.splitlines()) + 2
            else:
                offset = 1
            render_content(stdscr, content, offset, 4, w, fig_slide)
            if h - 1 < h and 2 < w:
                stdscr.addstr(h - 1, 2, f"Slide {idx+1}/{len(slides)}  ←/→ to navigate, q to quit")
        stdscr.refresh()
        key = stdscr.getch()
        if key in (ord("q"), 27):
            break
        elif key in (curses.KEY_RIGHT, ord("l")) and idx < len(slides) - 1:
            idx += 1
        elif key in (curses.KEY_LEFT, ord("h")) and idx > 0:
            idx -= 1



def main() -> None:
    """Entry point."""
    if len(sys.argv) < 2:
        print("Usage: python termslide.py file.md")
        raise SystemExit(1)

    if Image is None:
        print("Warning: Pillow not installed, image slides disabled.", file=sys.stderr)

    with open(sys.argv[1], "r", encoding="utf-8") as f:
        slides = parse_markdown(f.read())

    try:
        curses.wrapper(run_slideshow, slides)
    finally:
        # After curses exits, print a one-time reminder if Mermaid blocks were present but
        # Mermaid rendering support is missing.
        if _ENCOUNTERED_MERMAID_BLOCK and not _MERMAID_LIB_AVAILABLE:
            msg = (
                "Note: This presentation contains Mermaid diagrams.\n"
                "Install support with:\n"
                "  pip install mermaid-ascii-diagrams\n"
            )
            # Prefer showing the message even if stderr/stdout are redirected.
            for stream in (getattr(sys, "__stderr__", None), getattr(sys, "__stdout__", None)):
                try:
                    if stream:
                        print(msg, file=stream, flush=True)
                except Exception:
                    pass
            try:
                with open("/dev/tty", "w", encoding="utf-8", errors="ignore") as tty:
                    tty.write(msg)
                    tty.flush()
            except Exception:
                pass


if __name__ == "__main__":
    main()
