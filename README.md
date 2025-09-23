# TermSlide

**Turn boring markdown into epic terminal slides… because who needs PowerPoint when you have ANSI escape codes?**

---

## What is this?

**TermSlide** is a **terminal-based slideshow generator** for Markdown files.  
It reads your slides from a Markdown document and renders them beautifully in your terminal with:  

- **Title slides** in giant ASCII art (figlet style, because size matters).  
- **Colored headings, bold, italics, inline code**, all the emphasis you could dream of.  
- **Blockquotes and bullets** in fancy colors for maximum nerd credibility.  
- **Code blocks** with green text on dark gray, indented like a pro.  
- **Links and image references** formatted nicely, because your terminal deserves style.  
- **Local image slides** rendered as ASCII art with truecolor or 256-color approximation - yes, you can put pictures in your terminal (sort of).  

Basically, it’s **PowerPoint for keyboard warriors**.

---

## Installation

1. Clone this repo (or download `termslide.py` directly).  
2. Make sure you have Python 3.8+ installed.  
3. Install dependencies:

```bash
pip install -r requirements.txt
```

> **Windows users:** You also need `windows-curses` for terminal magic:

```bash
pip install windows-curses
```

---

## Usage

Create a Markdown file with your slides. Slides are separated by:

```markdown
---
```

Title slides are a line of text followed by a line of `===`:

```markdown
Welcome to Nerdvana
==================
```

Regular slides use standard Markdown:

```markdown
# Heading 1
## Heading 2
### Heading 3

- Bullet point
- Another bullet

> This is a blockquote
**Bold text** and *italic* text and `inline code`.

![Alt text](image.png)
```

Run your slideshow:

```bash
python termslide.py your_slides.md
```

Navigate with:  

- **→ / l / space** → next slide  
- **← / h** → previous slide  
- **q / Esc** → quit  

Enjoy the **pure ASCII glory**.

---

## Features in a nutshell

| Feature | Terminal Awesomeness |
|---------|--------------------|
| Title slides | Big figlet ASCII, centered, yellow |
| Headings | Colored per level |
| Bold | Bright red |
| Italics | Bright yellow |
| Code | Green on dark gray, indented |
| Links & image paths | Muted blue in brackets |
| Bullets | Cyan |
| Blockquotes | White with a left pipe |
| Local images | Full-page ASCII with truecolor or 256-color fallback |

---

## Why?

Because sometimes you just want to **present code, text, and nerdy memes** without leaving your terminal.  
Because real developers don’t need PowerPoint… they need **ANSI escape sequences**.  

---

## Nerd Disclaimer

- Works best in **xterm, kitty, alacritty, wezterm, or any truecolor-compatible terminal**.  
- On Windows, use a terminal that supports ANSI (like Windows Terminal).  
- If your terminal is too basic, it will gracefully fall back to 256 colors.  

---

TermSlide: **where ASCII meets ANSI and your slides are terminal-famous.**

