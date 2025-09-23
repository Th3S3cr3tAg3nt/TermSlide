#!/usr/bin/env python3
import curses
import sys
import re
import os
from pyfiglet import Figlet

try:
    from PIL import Image
except ImportError:
    Image = None


def parse_markdown(md_text):
    slides = []
    # Split on lines starting with three or more dashes
    raw_slides = re.split(r'^\-{3,}\s*$', md_text, flags=re.MULTILINE)
    for raw in raw_slides:
        lines = [line.rstrip() for line in raw.strip().splitlines()]
        if not any(l.strip() for l in lines):
            continue
        # Title slide with === underline
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
    r_ = int(round(r / 255 * 5))
    g_ = int(round(g / 255 * 5))
    b_ = int(round(b / 255 * 5))
    return 16 + 36 * r_ + 6 * g_ + b_


def render_image_in_curses(stdscr, img_path, alt):
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
            stdscr.addstr(y, x + start + len(f"Image: {alt} ") , f"({url})"[: maxw - (x + start)])
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
    cursor = 0
    pattern = re.compile(r"(\*\*([^\*]+)\*\*|\*([^\*]+)\*|`([^`]+)`)")
    last_end = 0
    for match in pattern.finditer(line):
        start, end = match.span()
        if start > last_end:
            text = line[last_end:start]
            stdscr.addstr(y, x + cursor, text)
            cursor += len(text)
        if match.group(2):  # bold
            inner = match.group(2)
            stdscr.attron(curses.color_pair(5))
            stdscr.addstr(y, x + cursor, inner)
            stdscr.attroff(curses.color_pair(5))
            cursor += len(inner)
        elif match.group(3):  # italic
            inner = match.group(3)
            stdscr.attron(curses.color_pair(6))
            stdscr.addstr(y, x + cursor, inner)
            stdscr.attroff(curses.color_pair(6))
            cursor += len(inner)
        elif match.group(4):  # inline code
            inner = match.group(4)
            stdscr.attron(curses.color_pair(7))
            stdscr.addstr(y, x + cursor, inner)
            stdscr.attroff(curses.color_pair(7))
            cursor += len(inner)
        last_end = end
    if last_end < len(line):
        text = line[last_end:]
        stdscr.addstr(y, x + cursor, text)


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
        pad_len = col_widths[i] - rl + 1  # +1 for right padding space
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


def format_text(line, stdscr, y, x, maxw, fig_slide, lines=None, line_idx=0):
    if lines and line.strip().startswith("|"):
        table_info = parse_table(lines, line_idx)
        if table_info[0] is not None:  # Check if table parsing succeeded
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
            stdscr.attron(curses.color_pair(2))
            title_lines = ascii_title.splitlines()
            for i, l in enumerate(title_lines):
                stdscr.addstr(y + i, x, l[: maxw - x])
            stdscr.attroff(curses.color_pair(2))
            return len(title_lines), 1
        elif level == 2:
            stdscr.attron(curses.color_pair(3))
            format_inline(text, stdscr, y, x, maxw)
            stdscr.attroff(curses.color_pair(3))
            return 1, 1
        else:
            stdscr.attron(curses.color_pair(4))
            format_inline(text, stdscr, y, x, maxw)
            stdscr.attroff(curses.color_pair(4))
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
    img_info = parse_image_only(content)
    if img_info:
        path, alt = img_info
        render_image_in_curses(stdscr, path, alt)
        return
    lines = content.split("\n")
    in_code = False
    y = start_y
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            in_code = not in_code
            i += 1
            continue
        if in_code:
            stdscr.attron(curses.color_pair(7))
            stdscr.addstr(y, start_x, "│ " + line[: maxw - (start_x + 2)])
            stdscr.attroff(curses.color_pair(7))
            y += 1
            i += 1
            continue
        rendered_consumed, source_consumed = format_text(line, stdscr, y, start_x, maxw, fig_slide, lines, i)
        y += rendered_consumed
        i += source_consumed


def run_slideshow(stdscr, slides):
    curses.curs_set(0)
    curses.start_color()
    curses.use_default_colors()

    # Brightest possible unique colors
    curses.init_pair(2, curses.COLOR_YELLOW, -1)   # Heading 1
    curses.init_pair(3, curses.COLOR_CYAN, -1)     # Heading 2
    curses.init_pair(4, curses.COLOR_MAGENTA, -1)  # Heading 3+
    curses.init_pair(5, curses.COLOR_RED, -1)      # Bold
    curses.init_pair(6, curses.COLOR_YELLOW, -1)   # Italic
    curses.init_pair(7, curses.COLOR_GREEN, -1)    # Code
    curses.init_pair(8, curses.COLOR_WHITE, -1)    # Table
    curses.init_pair(9, curses.COLOR_WHITE, -1)    # Blockquote
    curses.init_pair(10, curses.COLOR_CYAN, -1)   # Bullets
    curses.init_pair(12, curses.COLOR_BLUE, -1)    # Links & image paths

    fig_title = Figlet(font="standard")
    fig_slide = Figlet(font="small")
    idx = 0

    while True:
        stdscr.clear()
        h, w = stdscr.getmaxyx()
        slide_type, title, content = slides[idx]
        if slide_type == "title":
            ascii_title = fig_title.renderText(title)
            lines = ascii_title.splitlines()
            start_y = max(0, (h - len(lines)) // 2)
            stdscr.attron(curses.color_pair(2))
            for i, line in enumerate(lines):
                stdscr.addstr(start_y + i, max(0, (w - len(line)) // 2), line)
            stdscr.attroff(curses.color_pair(2))
            if content:
                render_content(stdscr, content, start_y + len(lines) + 2, max(0, w // 4), w, fig_slide)
        else:
            if title:
                ascii_title = fig_slide.renderText(title)
                stdscr.attron(curses.color_pair(2))
                for i, line in enumerate(ascii_title.splitlines()):
                    stdscr.addstr(i + 1, 2, line)
                stdscr.attroff(curses.color_pair(2))
                offset = len(ascii_title.splitlines()) + 2
            else:
                offset = 1
            render_content(stdscr, content, offset, 4, w, fig_slide)
            stdscr.addstr(h - 1, 2, f"Slide {idx+1}/{len(slides)}  ←/→ to navigate, q to quit")
        stdscr.refresh()
        key = stdscr.getch()
        if key in (ord("q"), 27):
            break
        elif key in (curses.KEY_RIGHT, ord("l")) and idx < len(slides) - 1:
            idx += 1
        elif key in (curses.KEY_LEFT, ord("h")) and idx > 0:
            idx -= 1


def main():
    if len(sys.argv) < 2:
        print("Usage: python slideshow.py file.md")
        sys.exit(1)
    if Image is None:
        print("Warning: Pillow not installed, image slides disabled.")
    with open(sys.argv[1], "r", encoding="utf-8") as f:
        slides = parse_markdown(f.read())
    curses.wrapper(run_slideshow, slides)


if __name__ == "__main__":
    main()