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
import pathlib
import hashlib
from typing import Optional, Tuple, List, Dict, Any

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

# Security constants
_MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB max file size
_ALLOWED_IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.bmp', '.gif'}
_MAX_COLOR_PAIRS = 4096  # Soft cap for dynamic color pairs

# Curses color pair IDs (keep stable; also used by render functions)
PAIR_HEADING_1 = 2
PAIR_HEADING_2 = 3
PAIR_HEADING_3 = 4
PAIR_BOLD = 5
PAIR_ITALIC = 6
PAIR_CODE = 7
PAIR_TABLE = 8
PAIR_BLOCKQUOTE = 9
PAIR_BULLET = 10
PAIR_CHECKBOX_CHECKED = 11
PAIR_LINK = 12


def _resolve_theme_color(color: Any) -> int:
    """Resolve a theme color value to a curses color index.

    Supported formats:
    - int: curses color index (0-255)
    - (r, g, b) tuple: mapped to nearest 256-color index
    - "default": -1 (terminal default)

    Note: `rgb_to_ansi256` is defined later in the file, so we avoid calling it
    at import-time.
    """
    if color is None:
        return -1
    if color == "default":
        return -1
    if isinstance(color, int):
        return color
    if isinstance(color, tuple) and len(color) == 3:
        r, g, b = color
        # Defer to runtime: rgb_to_ansi256 exists by the time themes are applied.
        return rgb_to_ansi256(int(r), int(g), int(b))
    return -1


def _apply_theme_colors(theme: Dict[str, Any]) -> None:
    """Initialize curses color pairs from theme."""
    colors = theme.get("colors", {})

    def pair(pid: int, role: str, default_fg: int, default_bg: int = -1) -> None:
        cfg = colors.get(role, {}) if isinstance(colors.get(role, {}), dict) else {}
        fg = _resolve_theme_color(cfg.get("fg", default_fg))
        bg = _resolve_theme_color(cfg.get("bg", default_bg))
        try:
            curses.init_pair(pid, fg, bg)
        except curses.error:
            # If a terminal doesn't like specific indices, fall back to default.
            curses.init_pair(pid, -1, -1)

    # Headings: allow "bright" variants when 16+ colors available.
    if getattr(curses, "COLORS", 0) >= 16:
        pair(PAIR_HEADING_1, "heading1", 11)
        pair(PAIR_HEADING_2, "heading2", 14)
        pair(PAIR_HEADING_3, "heading3", 13)
    else:
        pair(PAIR_HEADING_1, "heading1", curses.COLOR_YELLOW)
        pair(PAIR_HEADING_2, "heading2", curses.COLOR_CYAN)
        pair(PAIR_HEADING_3, "heading3", curses.COLOR_MAGENTA)

    pair(PAIR_BOLD, "bold", curses.COLOR_RED)
    pair(PAIR_ITALIC, "italic", curses.COLOR_YELLOW)
    pair(PAIR_CODE, "code", curses.COLOR_GREEN)
    pair(PAIR_TABLE, "table", curses.COLOR_WHITE)
    pair(PAIR_BLOCKQUOTE, "blockquote", curses.COLOR_WHITE)
    pair(PAIR_BULLET, "bullet", curses.COLOR_CYAN)
    pair(PAIR_CHECKBOX_CHECKED, "checkbox_checked", curses.COLOR_GREEN)
    pair(PAIR_LINK, "link", curses.COLOR_BLUE)


_BUILTIN_THEMES: Dict[str, Dict[str, Any]] = {
    # Defaults preserve the existing look, but can be overridden.
    "dark": {
        "figlet": {"title": "mono12", "slide": "smblock"},
        "colors": {
            "heading1": {"fg": 11, "bg": "default"},
            "heading2": {"fg": 14, "bg": "default"},
            "heading3": {"fg": 13, "bg": "default"},
            "bold": {"fg": "default", "bg": "default"},
            "italic": {"fg": "default", "bg": "default"},
            "code": {"fg": "default", "bg": "default"},
            "table": {"fg": "default", "bg": "default"},
            "blockquote": {"fg": "default", "bg": "default"},
            "bullet": {"fg": "default", "bg": "default"},
            "checkbox_checked": {"fg": "default", "bg": "default"},
            "link": {"fg": "default", "bg": "default"},
        },
    },
    "light": {
        "figlet": {"title": "mono12", "slide": "smblock"},
        "colors": {
            # Slightly calmer palette that tends to work on light backgrounds.
            "heading1": {"fg": curses.COLOR_BLUE, "bg": "default"},
            "heading2": {"fg": curses.COLOR_MAGENTA, "bg": "default"},
            "heading3": {"fg": curses.COLOR_CYAN, "bg": "default"},
            "bold": {"fg": curses.COLOR_RED, "bg": "default"},
            "italic": {"fg": curses.COLOR_YELLOW, "bg": "default"},
            "code": {"fg": curses.COLOR_GREEN, "bg": "default"},
            "table": {"fg": curses.COLOR_BLACK, "bg": "default"},
            "blockquote": {"fg": curses.COLOR_BLACK, "bg": "default"},
            "bullet": {"fg": curses.COLOR_BLUE, "bg": "default"},
            "checkbox_checked": {"fg": curses.COLOR_GREEN, "bg": "default"},
            "link": {"fg": curses.COLOR_BLUE, "bg": "default"},
        },
    },
    "nord": {
        "figlet": {"title": "mono12", "slide": "smblock"},
        "colors": {
            # Nord palette (approx): https://www.nordtheme.com/docs/colors-and-palettes
            # Use RGB tuples so we can map to the nearest 256-color index.
            "heading1": {"fg": (136, 192, 208), "bg": "default"},  # nord8
            "heading2": {"fg": (129, 161, 193), "bg": "default"},  # nord9
            "heading3": {"fg": (180, 142, 173), "bg": "default"},  # nord15
            "bold": {"fg": (191, 97, 106), "bg": "default"},       # nord11
            "italic": {"fg": (235, 203, 139), "bg": "default"},    # nord13
            "code": {"fg": (163, 190, 140), "bg": "default"},      # nord14
            "table": {"fg": (216, 222, 233), "bg": "default"},     # nord4
            "blockquote": {"fg": (229, 233, 240), "bg": "default"},# nord5
            "bullet": {"fg": (143, 188, 187), "bg": "default"},    # nord7
            "checkbox_checked": {"fg": (163, 190, 140), "bg": "default"},
            "link": {"fg": (94, 129, 172), "bg": "default"},       # nord10
        },
    },
    "github": {
        "figlet": {"title": "mono12", "slide": "smblock"},
        "colors": {
            # GitHub-ish accents (approx). These are not official terminal mappings,
            # but should feel familiar.
            "heading1": {"fg": (9, 105, 218), "bg": "default"},
            "heading2": {"fg": (130, 80, 223), "bg": "default"},
            "heading3": {"fg": (31, 136, 61), "bg": "default"},
            "bold": {"fg": (209, 36, 47), "bg": "default"},
            "italic": {"fg": (191, 135, 0), "bg": "default"},
            "code": {"fg": (31, 136, 61), "bg": "default"},
            "table": {"fg": (87, 96, 106), "bg": "default"},
            "blockquote": {"fg": (87, 96, 106), "bg": "default"},
            "bullet": {"fg": (9, 105, 218), "bg": "default"},
            "checkbox_checked": {"fg": (31, 136, 61), "bg": "default"},
            "link": {"fg": (9, 105, 218), "bg": "default"},
        },
    },
}


def _get_active_theme() -> Dict[str, Any]:
    name = (os.environ.get("TERMSLIDE_THEME") or "dark").strip().lower()
    return _BUILTIN_THEMES.get(name, _BUILTIN_THEMES["dark"])


# Defer theme activation until curses is initialized.
_ACTIVE_THEME: Dict[str, Any] | None = None

# Image processing constants
_MAX_IMAGE_DIMENSION = 8192  # Maximum width/height in pixels
_MAX_IMAGE_MEMORY = 200 * 1024 * 1024  # 200MB max memory for image processing
_MAX_CANVAS_SIZE = 1000 * 1000  # Maximum canvas size in pixels
_IMAGE_PROCESSING_TIMEOUT = 30  # Seconds

# Enhanced block character rendering constants
# Characters with different fill patterns for better detail
BLOCK_CHARS = [
    ' ',     # Space - empty
    '▀',     # Upper half block
    '▄',     # Lower half block  
    '█',     # Full block
    '▌',     # Left half block
    '▐',     # Right half block
    '░',     # Light shade
    '▒',     # Medium shade
    '▓',     # Dark shade
    '▔',     # upper one eighth block
    '▕',     # right one eighth block
    '▖',     # left lower eighth block
    '▗',     # right lower eighth block
    '▘',     # left upper eighth block
    '▙',     # right upper eighth block
    '▚',     # left upper right lower eighth block
    '▛',     # right upper left lower eighth block
    '▜',     # left upper left lower eighth block
    '▝',     # right upper right lower eighth block
    '▞',     # left lower right upper eighth block
    '▟',     # right lower left upper eighth block
    '▚',     # diagonal quarter block
]

# Character patterns for different brightness levels
CHAR_PATTERNS = {
    # Brightness range: (min, max) -> character
    (0.0, 0.1): ' ',
    (0.1, 0.25): '░',
    (0.25, 0.4): '▒',
    (0.4, 0.55): '▓',
    (0.55, 0.7): '▄',
    (0.7, 0.85): '▀',
    (0.85, 1.0): '█',
}

# Quarter block patterns for 2x2 pixel detail
QUARTER_BLOCKS = {

    (True, False, False, False): '▘',  # Top-left
    (False, True, False, False): '▝',  # Top-right
    (False, False, True, False): '▖',  # Bottom-left
    (False, False, False, True): '▗',  # Bottom-right
    (True, True, False, False): '▀',  # Top half
    (False, False, True, True): '▄',   # Bottom half
    (True, False, True, False): '▌',   # Left half
    (False, True, False, True): '▐',   # Right half
    (True, True, True, False): '▚',    # Three quarters
    (True, False, True, True): '▚',    # Three quarters (same char)
    (False, True, True, True): '▚',    # Three quarters (same char)
    (True, True, False, True): '▚',    # Three quarters (same char)
    (True, True, True, True): '█',     # Full block
    (False, False, False, False): ' ',  # Empty
}


def validate_file_path(file_path: str, base_dir: Optional[str] = None) -> str:
    """Validate file path to prevent path traversal attacks.
    
    Args:
        file_path: The file path to validate
        base_dir: Base directory for relative paths (defaults to current directory)
        
    Returns:
        Absolute, validated file path
        
    Raises:
        ValueError: If path is invalid or potentially dangerous
    """
    if not file_path or not isinstance(file_path, str):
        raise ValueError("Invalid file path")
    
    # Check for null bytes
    if '\x00' in file_path:
        raise ValueError("Null bytes not allowed in file path")
    
    # Check for dangerous shell patterns
    dangerous_patterns = ['`', '$', '${', '~']
    for pattern in dangerous_patterns:
        if pattern in file_path:
            raise ValueError(f"Dangerous pattern detected: {pattern}")
    
    # Convert to Path object for safe handling
    try:
        path = pathlib.Path(file_path)
    except Exception as e:
        raise ValueError(f"Invalid path format: {e}")
    
    # Resolve to absolute path
    if base_dir:
        base = pathlib.Path(base_dir).resolve()
        target_path = (base / path).resolve()
        
        # Ensure the path is within the base directory
        if not str(target_path).startswith(str(base)):
            raise ValueError("Path traversal detected - access denied")
    else:
        target_path = path.resolve()
        
        # Additional check for path traversal when no base_dir specified
        if '..' in str(path):
            raise ValueError("Path traversal not allowed")
    
    # Additional security check: ensure no dangerous URL patterns
    path_str = str(target_path).lower()
    dangerous_url_patterns = ['javascript:', 'data:', 'vbscript:', 'file://', 'file:', 'ftp://']
    for pattern in dangerous_url_patterns:
        if pattern in path_str:
            raise ValueError(f"Dangerous URL pattern detected: {pattern}")
    
    return str(target_path)


def validate_image_file(file_path: str) -> Tuple[str, bool]:
    """Validate image file for security and size limits.
    
    Args:
        file_path: Path to the image file
        
    Returns:
        Tuple of (validated_path, is_valid)
        
    Raises:
        ValueError: If file is invalid or too large
    """
    try:
        # Validate path first
        validated_path = validate_file_path(file_path)
        
        # Check if file exists
        if not os.path.exists(validated_path):
            return validated_path, False
        
        # Check file size
        file_size = os.path.getsize(validated_path)
        if file_size > _MAX_FILE_SIZE:
            raise ValueError(f"File too large: {file_size} bytes (max: {_MAX_FILE_SIZE})")
        
        # Check file extension
        path_obj = pathlib.Path(validated_path)
        if path_obj.suffix.lower() not in _ALLOWED_IMAGE_EXTENSIONS:
            return validated_path, False
        
        # Additional validation: check file header/magic bytes
        with open(validated_path, 'rb') as f:
            header = f.read(8)
        
        # Basic image format validation
        image_signatures = {
            b'\x89PNG': 'png',
            b'\xff\xd8\xff': 'jpg',
            b'GIF87a': 'gif',
            b'GIF89a': 'gif',
            b'BM': 'bmp'
        }
        
        is_valid_image = any(header.startswith(sig) for sig in image_signatures.keys())
        
        return validated_path, is_valid_image
        
    except Exception as e:
        raise ValueError(f"Image validation failed: {e}")


def sanitize_markdown_content(content: str) -> str:
    """Sanitize markdown content to prevent injection attacks.
    
    Args:
        content: Raw markdown content
        
    Returns:
        Sanitized content
    """
    if not content or not isinstance(content, str):
        return ""
    
    # Remove null bytes and control characters except newlines and tabs
    content = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', content)
    
    # Limit content length to prevent memory issues
    max_content_length = 10 * 1024 * 1024  # 10MB
    if len(content) > max_content_length:
        content = content[:max_content_length] + "\n[Content truncated due to length]"
    
    # Sanitize potentially dangerous patterns in URLs
    def sanitize_url(match):
        full_match = match.group(0)
        if len(match.groups()) >= 2:
            text, url = match.group(1), match.group(2)
        else:
            text, url = match.group(1), match.group(1)
        
        # Remove javascript: and data: URLs
        if url.lower().startswith(('javascript:', 'data:', 'vbscript:')):
            # Return safe placeholder
            if full_match.startswith('!'):
                return f"![Dangerous URL blocked]"
            else:
                return f"[Dangerous URL blocked]"
        return full_match
    
    # Sanitize link URLs: [text](url)
    content = re.sub(r'\[([^\]]*)\]\(([^)]+)\)', lambda m: sanitize_url(m), content)
    
    # Sanitize image URLs: ![alt](url)
    content = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', lambda m: sanitize_url(m), content)
    
    # Limit nested code blocks to prevent stack overflow
    code_block_count = content.count('```')
    if code_block_count > 100:  # Arbitrary reasonable limit
        # Remove excess code blocks
        parts = content.split('```')
        content = '```'.join(parts[:101])  # Keep first 100 blocks (101 parts)
    
    return content


def safe_terminal_encoding() -> str:
    """Get terminal encoding safely with fallback."""
    try:
        # Try stdout encoding first
        enc = getattr(sys.stdout, "encoding", None)
        if enc and enc.lower():
            return enc.lower()
    except Exception:
        pass
    
    try:
        # Fallback to locale
        enc = locale.getpreferredencoding(False)
        if enc and enc.lower():
            return enc.lower()
    except Exception:
        pass
    
    # Ultimate fallback
    return "utf-8"


def _color_pair_capacity() -> int:
    """Compute a safe usable color-pair capacity.

    We keep a buffer because TermSlide also allocates pairs for headings/links,
    and some curses implementations are picky about high pair ids.
    """
    try:
        total = int(getattr(curses, "COLOR_PAIRS", 0) or 0)
    except Exception:
        total = 0

    if total <= 0:
        total = 64

    # Reserve some low ids for non-image UI and keep within our soft cap.
    return max(0, min(_MAX_COLOR_PAIRS, total - 64))


def validate_color_pair_allocation(pair_id: int) -> bool:
    """Validate color pair ID to prevent overflow."""
    return 0 <= pair_id < _color_pair_capacity()


def check_memory_availability(required_bytes: int) -> bool:
    """Check if enough memory is available for image processing.
    
    Args:
        required_bytes: Amount of memory needed in bytes
        
    Returns:
        True if memory is available, False otherwise
    """
    try:
        import psutil
        available = psutil.virtual_memory().available
        return available > required_bytes + (100 * 1024 * 1024)  # 100MB buffer
    except ImportError:
        # If psutil is not available, make a reasonable estimate
        return required_bytes < _MAX_IMAGE_MEMORY


def estimate_image_memory_usage(width: int, height: int, channels: int = 3) -> int:
    """Estimate memory usage for an image.
    
    Args:
        width: Image width in pixels
        height: Image height in pixels
        channels: Number of color channels (default 3 for RGB)
        
    Returns:
        Estimated memory usage in bytes
    """
    # Base image data
    base_memory = width * height * channels
    
    # Add overhead for Pillow processing (rough estimate)
    processing_overhead = base_memory * 2
    
    # Add canvas memory if we're creating one
    canvas_memory = width * height * channels * 2  # RGB canvas
    
    total_memory = base_memory + processing_overhead + canvas_memory
    
    return total_memory


def validate_image_dimensions(img_path: str) -> Tuple[bool, Optional[tuple], Optional[str]]:
    """Validate image dimensions and memory requirements.
    
    Args:
        img_path: Path to the image file
        
    Returns:
        Tuple of (is_valid, dimensions, error_message)
    """
    if Image is None:
        return False, None, "Pillow not available"
    
    try:
        with Image.open(img_path) as img:
            width, height = img.size
            
            # Check dimension limits
            if width > _MAX_IMAGE_DIMENSION or height > _MAX_IMAGE_DIMENSION:
                return False, (width, height), f"Image too large: {width}x{height} (max: {_MAX_IMAGE_DIMENSION})"
            
            # Check memory requirements
            estimated_memory = estimate_image_memory_usage(width, height)
            if not check_memory_availability(estimated_memory):
                return False, (width, height), f"Insufficient memory: need {estimated_memory // (1024*1024)}MB"
            
            return True, (width, height), None
            
    except Exception as e:
        return False, None, f"Failed to validate image: {e}"


def safe_load_image(img_path: str) -> Optional[object]:
    """Safely load an image with comprehensive error handling.
    
    Args:
        img_path: Path to the image file
        
    Returns:
        PIL Image object or None if loading failed
    """
    if Image is None:
        return None
    
    try:
        # First validate the image
        is_valid, dimensions, error = validate_image_dimensions(img_path)
        if not is_valid:
            print(f"Image validation failed: {error}", file=sys.stderr)
            return None
        
        # Load the image with error handling
        with Image.open(img_path) as img:
            # Verify the image can be converted to RGB
            img = img.convert("RGB")
            
            # Double-check dimensions after conversion
            if img.size != dimensions:
                print(f"Warning: Image size changed after conversion", file=sys.stderr)
            
            return img.copy()  # Return a copy to avoid file handle issues
            
    except Exception as e:
        # Handle specific PIL errors
        if "cannot identify" in str(e).lower():
            print(f"Error: Cannot identify image file: {img_path}", file=sys.stderr)
        elif "decompression bomb" in str(e).lower():
            print(f"Error: Image decompression bomb detected: {img_path}", file=sys.stderr)
        elif "out of memory" in str(e).lower():
            print(f"Error: Out of memory loading image: {img_path}", file=sys.stderr)
        else:
            print(f"Error loading image {img_path}: {e}", file=sys.stderr)
        return None


def safe_resize_image(img, target_width: int, target_height: int):
    """Safely resize an image with memory checks.
    
    Args:
        img: PIL Image object
        target_width: Target width in pixels
        target_height: Target height in pixels
        
    Returns:
        Resized PIL Image or None if resizing failed
    """
    try:
        # Check memory requirements for resize
        estimated_memory = estimate_image_memory_usage(target_width, target_height)
        if not check_memory_availability(estimated_memory):
            print(f"Insufficient memory for image resize", file=sys.stderr)
            return None
        
        # Progressive resizing for very large images to save memory
        original_width, original_height = getattr(img, 'size', (0, 0))
        if max(original_width, original_height) > 4096:
            # Multi-step downscaling for large images
            intermediate_width = max(original_width // 2, target_width * 2)
            intermediate_height = max(original_height // 2, target_height * 2)
            
            # First step: resize to intermediate size
            try:
                lanczos = getattr(Image, 'LANCZOS', Image.BILINEAR if Image else None)
                intermediate = img.resize((intermediate_width, intermediate_height), lanczos)
                # Second step: resize to target size
                resized = intermediate.resize((target_width, target_height), lanczos)
                intermediate.close()
            except Exception:
                # Fallback to single-step resize
                resized = img.resize((target_width, target_height))
        else:
            # Direct resize for smaller images
            try:
                lanczos = getattr(Image, 'LANCZOS', Image.BILINEAR if Image else None)
                resized = img.resize((target_width, target_height), lanczos)
            except Exception:
                # Fallback to default resampling
                resized = img.resize((target_width, target_height))
        
        return resized
        
    except MemoryError:
        print(f"Error: Out of memory resizing image", file=sys.stderr)
        return None
    except Exception as e:
        print(f"Error resizing image: {e}", file=sys.stderr)
        return None


def create_image_canvas_safely(target_width: int, target_height: int):
    """Create an image canvas with memory monitoring.
    
    Args:
        target_width: Canvas width in pixels
        target_height: Canvas height in pixels
        
    Returns:
        PIL Image canvas or None if creation failed
    """
    try:
        # Check memory requirements
        canvas_memory = target_width * target_height * 3  # RGB
        if not check_memory_availability(canvas_memory):
            print(f"Insufficient memory for canvas creation", file=sys.stderr)
            return None
        
        # Create canvas
        if Image:
            canvas = Image.new("RGB", (target_width, target_height), (0, 0, 0))
        else:
            return None
        return canvas
        
    except MemoryError:
        print(f"Error: Out of memory creating canvas", file=sys.stderr)
        return None
    except Exception as e:
        print(f"Error creating canvas: {e}", file=sys.stderr)
        return None


def calculate_brightness(color):
    """Calculate perceived brightness of an RGB color."""
    r, g, b = color
    # Weighted luminance formula
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255.0


def select_optimal_char(top_brightness, bottom_brightness, top_color, bottom_color):
    """Select the best block character based on pixel values."""
    # Calculate difference for contrast detection
    brightness_diff = abs(top_brightness - bottom_brightness)
    
    # If colors are very different, use half blocks
    if brightness_diff > 0.3:
        if top_brightness > bottom_brightness:
            return '▀'  # Upper half block
        else:
            return '▄'  # Lower half block
    else:
        # Colors are similar, use density patterns
        avg_brightness = (top_brightness + bottom_brightness) / 2
        return select_char_by_brightness(avg_brightness)


def select_char_by_brightness(brightness):
    """Select character based on brightness value."""
    for (min_b, max_b), char in CHAR_PATTERNS.items():
        if min_b <= brightness < max_b:
            return char
    return '█'  # Default to full block


def get_optimal_color_pair(fg_color, bg_color):
    """Create optimal color key for foreground/background combination.

    Returns (fg_idx, bg_idx) after quantization.
    """
    fg_q = quantize_rgb(fg_color)
    bg_q = quantize_rgb(bg_color)
    fg_idx = rgb_to_ansi256(*fg_q)
    bg_idx = rgb_to_ansi256(*bg_q)
    return (fg_idx, bg_idx)


def analyze_2x2_pixels(canvas, x, y, width, height):
    """Analyze a 2x2 pixel area for detailed character selection."""
    pixels = []
    
    # Collect up to 4 pixels (handle edges)
    for dy in range(2):
        for dx in range(2):
            px, py = x + dx, y + dy
            if 0 <= px < width and 0 <= py < height:
                try:
                    pixels.append(canvas.getpixel((px, py)))
                except Exception:
                    pixels.append((0, 0, 0))  # Default to black on error
            else:
                pixels.append((0, 0, 0))  # Out of bounds = black
    
    # Calculate average color and brightness
    if len(pixels) == 4:
        avg_color = tuple(sum(p[i] for p in pixels) // 4 for i in range(3))
        brightnesses = [calculate_brightness(p) for p in pixels]
        avg_brightness = sum(brightnesses) / 4
        
        # Determine which quadrants are "filled" (above average brightness)
        threshold = avg_brightness
        filled = [b > threshold for b in brightnesses]
        
        # Select character based on pattern
        pattern_key = tuple(filled)
        char = QUARTER_BLOCKS.get(pattern_key, select_char_by_brightness(avg_brightness))
        
        return char, avg_color
    else:
        # Fallback for edge cases
        if pixels:
            avg_color = tuple(sum(p[i] for p in pixels) // len(pixels) for i in range(3))
            brightnesses = [calculate_brightness(p) for p in pixels]
            avg_brightness = sum(brightnesses) / len(brightnesses)
            char = select_char_by_brightness(avg_brightness)
            return char, avg_color
        else:
            return ' ', (0, 0, 0)


def render_image_enhanced(stdscr, canvas, width, height, use_advanced=True):
    """Enhanced image rendering using multiple block characters while maintaining same dimensions."""
    h, w = stdscr.getmaxyx()
    color_cache: Dict[tuple, int] = {}
    next_pair_ref = [50]
    render_errors = 0
    
    # Use same dimensions as original simple rendering
    # Each terminal character represents 1 horizontal pixel, 2 vertical pixels
    # But we'll choose better characters based on pixel analysis
    
    # Center the image like original
    off_x = (w - width) // 2 if width < w else 0
    off_y = ((h - 1) - height // 2) // 2 if height // 2 < h - 1 else 0
    
    # Pre-calculate color cache for better performance
    # (Capacity logic handled in _get_or_create_color_pair).
    
    for y in range(h - 1):
        for x in range(w):
            try:
                # Check bounds like original
                if y * 2 >= height or x >= width:
                    continue
                
                # Get the two pixels for this character position
                top = canvas.getpixel((x, y * 2))
                if y * 2 + 1 < height:
                    bot = canvas.getpixel((x, y * 2 + 1))
                else:
                    bot = (0, 0, 0)
                
                # Enhanced character selection based on pixel analysis
                top_b = calculate_brightness(top)
                bot_b = calculate_brightness(bot)
                
                # Use 2x2 pixel analysis if we have neighboring pixels
                if use_advanced and x + 1 < width and y * 2 + 1 < height:
                    # Look at a 2x2 area for better character selection
                    try:
                        next_right_top = canvas.getpixel((x + 1, y * 2))
                        next_right_bot = canvas.getpixel((x + 1, y * 2 + 1))
                        
                        # Analyze all 4 pixels for optimal character
                        avg_color = tuple((top[i] + bot[i] + next_right_top[i] + next_right_bot[i]) // 4 for i in range(3))
                        pixels = [top, bot, next_right_top, next_right_bot]
                        brightnesses = [calculate_brightness(p) for p in pixels]
                        avg_brightness = sum(brightnesses) / 4
                        
                        # Determine filled quadrants
                        threshold = avg_brightness
                        tl = brightnesses[0] > threshold  # top-left
                        tr = brightnesses[2] > threshold  # top-right  
                        bl = brightnesses[1] > threshold  # bottom-left
                        br = brightnesses[3] > threshold  # bottom-right
                        
                        # Select character based on quadrant pattern
                        pattern_key = (tl, tr, bl, br)
                        char = QUARTER_BLOCKS.get(pattern_key, '▄')
                        color = avg_color
                        
                    except Exception:
                        # Fall back to simple analysis
                        char = select_optimal_char(top_b, bot_b, top, bot)
                        color = top if top_b > bot_b else bot
                else:
                    # Simple analysis for edge pixels
                    char = select_optimal_char(top_b, bot_b, top, bot)
                    color = top if top_b > bot_b else bot
                
                # Get background color for color pair
                bg_color = top  # Use top pixel as background
                
                # Get or allocate a color pair (hybrid strategy)
                pair_id = _get_or_create_color_pair(color_cache, next_pair_ref, fg_color=color, bg_color=bg_color)

                # Render character
                if pair_id > 0:
                    stdscr.attron(curses.color_pair(pair_id))
                stdscr.addstr(y, x, char)
                if pair_id > 0:
                    stdscr.attroff(curses.color_pair(pair_id))
                    
            except Exception:
                render_errors += 1
                if render_errors > 100:
                    break
        if render_errors > 100:
            break
    
    return render_errors


def render_image_simple(stdscr, canvas, width, height):
    """Simple image rendering using half-block characters."""
    h, w = stdscr.getmaxyx()
    color_cache: Dict[tuple, int] = {}
    next_pair_ref = [50]
    render_errors = 0
    
    for y in range(h - 1):
        for x in range(w):
            try:
                # Get pixels with bounds checking
                if y * 2 >= height or x >= width:
                    continue
                    
                top = canvas.getpixel((x, y * 2))
                if y * 2 + 1 < height:
                    bot = canvas.getpixel((x, y * 2 + 1))
                else:
                    bot = (0, 0, 0)  # Black for out-of-bounds
                    
                # Get or allocate a color pair (hybrid strategy)
                pair_id = _get_or_create_color_pair(color_cache, next_pair_ref, fg_color=bot, bg_color=top)

                # Render pixel
                if pair_id > 0:
                    stdscr.attron(curses.color_pair(pair_id))
                stdscr.addstr(y, x, "▄")
                if pair_id > 0:
                    stdscr.attroff(curses.color_pair(pair_id))
                    
            except Exception:
                render_errors += 1
                if render_errors > 100:
                    break
        if render_errors > 100:
            break
    
    return render_errors


def _terminal_encoding() -> str:
    """Return the terminal encoding (best effort)."""
    return safe_terminal_encoding()


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

# Image rendering mode control
_USE_ENHANCED_RENDERING = not os.environ.get("TERMSLIDE_SIMPLE_RENDERING")

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
    # Sanitize input first
    md_text = sanitize_markdown_content(md_text)
    
    slides = []
    raw_slides = re.split(r'^\-{3,}\s*$', md_text, flags=re.MULTILINE)
    for raw in raw_slides:
        lines = [line.rstrip() for line in raw.strip().splitlines()]
        if not any(l.strip() for l in lines):
            continue
        if len(lines) > 1 and re.match(r"^=+$", lines[1].strip()):
            title = sanitize_markdown_content(lines[0].strip())
            content = sanitize_markdown_content("\n".join(lines[2:]))
            slides.append(("title", title, content))
        else:
            title = None
            if lines[0].startswith("#"):
                title = sanitize_markdown_content(lines[0].lstrip("# ").strip())
                content = sanitize_markdown_content("\n".join(lines[1:]))
            else:
                content = sanitize_markdown_content("\n".join(lines))
            slides.append(("content", title, content))
    return slides


def parse_image_only(content):
    """Detect slides that contain only an image and return (path, alt) if present."""
    content = sanitize_markdown_content(content.strip())
    if not content:
        return None
    m = re.fullmatch(r'!\[([^\]]*)\]\((.*?)\)\s*', content)
    if m:
        alt, path = m.groups()
        # Validate and check image file
        try:
            validated_path, is_valid = validate_image_file(path)
            if is_valid:
                return validated_path, alt.strip()
        except ValueError:
            pass
        return None
    if "\n" not in content and (
        "/" in content or content.lower().endswith((".png", ".jpg", ".jpeg", ".bmp", ".gif"))
    ):
        try:
            validated_path, is_valid = validate_image_file(content)
            if is_valid:
                return validated_path, ""
        except ValueError:
            pass
    return None


def rgb_to_ansi256(r, g, b):
    """Map an RGB tuple (0-255) to the nearest 256-color ANSI index."""
    r_ = int(round(r / 255 * 5))
    g_ = int(round(g / 255 * 5))
    b_ = int(round(b / 255 * 5))
    return 16 + 36 * r_ + 6 * g_ + b_


def quantize_rgb(color, levels: int = 6):
    """Quantize an RGB color to reduce distinct fg/bg pairs.

    levels=6 matches the 256-color cube (0..5), but quantizing early helps reduce
    the number of distinct (fg,bg) pairs we attempt to allocate.
    """
    r, g, b = color
    step = 255 / max(1, (levels - 1))

    def q(v: int) -> int:
        return int(round(v / step) * step)

    return (q(r), q(g), q(b))


def _get_or_create_color_pair(color_cache: Dict[tuple, int], next_pair_ref: List[int], fg_color, bg_color) -> int:
    """Get (or allocate) a curses color pair for a fg/bg combination.

    Hybrid strategy:
    - Quantize RGB to reduce unique combinations.
    - Allocate pairs up to a safe capacity.
    - When capacity is reached, reuse a stable hash bucket of existing pairs
      instead of falling back to 0 (default colors).
    """
    fg_q = quantize_rgb(fg_color)
    bg_q = quantize_rgb(bg_color)

    fg_idx = rgb_to_ansi256(*fg_q)
    bg_idx = rgb_to_ansi256(*bg_q)
    key = (fg_idx, bg_idx)

    if key in color_cache:
        return color_cache[key]

    cap = _color_pair_capacity()
    pair_id = next_pair_ref[0]

    if pair_id < cap:
        try:
            curses.init_pair(pair_id, fg_idx, bg_idx)
            color_cache[key] = pair_id
            next_pair_ref[0] += 1
            return pair_id
        except curses.error:
            # Fall through to reuse mode.
            pass

    # Reuse mode: map to an existing pair id range if possible.
    # If we have any allocated pairs, pick one deterministically.
    allocated = next_pair_ref[0] - 50  # 50 is our starting point
    if allocated > 0:
        # Stable bucket within allocated range.
        bucket = (hash(key) % allocated)
        return 50 + bucket

    return 0


def render_image_fallback(stdscr, img_path, alt):
    """Fallback image rendering that shows basic image info."""
    try:
        # Show basic file info
        stdscr.addstr(2, 2, f"Image: {os.path.basename(img_path)}")
        
        # Try to get basic file info
        file_size = os.path.getsize(img_path)
        size_mb = file_size / (1024 * 1024)
        stdscr.addstr(3, 2, f"Size: {size_mb:.1f} MB")
        
        # Try to get image dimensions without fully loading
        try:
            if Image:
                with Image.open(img_path) as img:
                    width, height = getattr(img, 'size', (0, 0))
                    stdscr.addstr(4, 2, f"Dimensions: {width}x{height}")
            else:
                stdscr.addstr(4, 2, "Dimensions: Unknown (Pillow unavailable)")
        except Exception:
            stdscr.addstr(4, 2, "Dimensions: Unknown")
        
        stdscr.addstr(6, 2, "Full image rendering failed.")
        stdscr.addstr(7, 2, "Possible causes:")
        stdscr.addstr(8, 4, "- Insufficient memory")
        stdscr.addstr(9, 4, "- Corrupted image file")
        stdscr.addstr(10, 4, "- Unsupported image format")
        stdscr.addstr(11, 4, "- Image too large for terminal")
        
    except Exception as e:
        stdscr.addstr(2, 2, f"Cannot display image: {e}")


def render_image_in_curses(stdscr, img_path, alt):
    """Render an image slide using half-block characters (requires Pillow)."""
    if Image is None:
        stdscr.addstr(2, 2, "Pillow required for image slides.")
        return

    # Get terminal dimensions safely
    try:
        h, w = stdscr.getmaxyx()
        if h < 3 or w < 10:  # Minimum viable dimensions
            stdscr.addstr(2, 2, "Terminal too small for image display.")
            return
    except Exception as e:
        stdscr.addstr(2, 2, f"Error getting terminal size: {e}")
        return

    tgt_w = w
    tgt_h = (h - 1) * 2

    # Validate canvas size
    if tgt_w * tgt_h > _MAX_CANVAS_SIZE:
        stdscr.addstr(2, 2, "Terminal too large for safe image rendering.")
        return

    # Load and validate image safely
    img = safe_load_image(img_path)
    if img is None:
        stdscr.addstr(2, 2, f"Failed to load image: {os.path.basename(img_path)}")
        return

    try:
        img_w, img_h = getattr(img, 'size', (0, 0))
        
        # Calculate target dimensions with bounds checking
        if img_w == 0 or img_h == 0:
            stdscr.addstr(2, 2, "Invalid image dimensions.")
            return
            
        ratio = min(tgt_w / img_w, tgt_h / img_h)
        new_w = max(1, min(tgt_w, int(img_w * ratio)))
        new_h = max(1, min(tgt_h, int(img_h * ratio)))

        # Resize image safely
        resized_img = safe_resize_image(img, new_w, new_h)
        if resized_img is None:
            stdscr.addstr(2, 2, "Failed to resize image.")
            return

        # Create canvas safely
        canvas = create_image_canvas_safely(tgt_w, tgt_h)
        if canvas is None:
            return
            
        try:
            off_x = (tgt_w - new_w) // 2
            off_y = (tgt_h - new_h) // 2
            canvas.paste(resized_img, (off_x, off_y))
        except Exception as e:
            stdscr.addstr(2, 2, f"Error pasting image to canvas: {e}")
            canvas.close()
            return

        # Render image with enhanced block character rendering
        render_errors = render_image_enhanced(stdscr, canvas, tgt_w, tgt_h, _USE_ENHANCED_RENDERING)

        # Cleanup
        try:
            if hasattr(canvas, 'close'):
                canvas.close()
            if hasattr(resized_img, 'close'):
                resized_img.close()
        except Exception:
            pass

    except Exception as e:
        # Fallback to info display on any major error
        render_image_fallback(stdscr, img_path, alt)
        return
    finally:
        try:
            if hasattr(img, 'close'):
                img.close()
        except Exception:
            pass

    # Show navigation and status
    try:
        stdscr.addstr(h - 1, 2, "←/→ to navigate, q to quit")
        if alt and len(alt) < w - 4:
            stdscr.addstr(h - 1, w - len(alt) - 2, alt)
    except Exception:
        pass


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
            stdscr.attron(curses.color_pair(PAIR_LINK))
            stdscr.addstr(y, x + start + len(f"Image: {alt} "), f"({url})"[: maxw - (x + start)])
            stdscr.attroff(curses.color_pair(PAIR_LINK))
        elif link_match:
            label, url = link_match.groups()
            stdscr.addstr(y, x + start, label + " ")
            stdscr.attron(curses.color_pair(PAIR_LINK))
            stdscr.addstr(y, x + start + len(label) + 1, f"({url})"[: maxw - (x + start + len(label) + 1)])
            stdscr.attroff(curses.color_pair(PAIR_LINK))
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
            _add(match.group(2), curses.color_pair(PAIR_BOLD))
        elif match.group(3):  # italic
            _add(match.group(3), curses.color_pair(PAIR_ITALIC))
        elif match.group(4):  # inline code
            _add(match.group(4), curses.color_pair(PAIR_CODE))

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
        
    stdscr.attron(curses.color_pair(PAIR_TABLE))  # Table color
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
    
    stdscr.attroff(curses.color_pair(PAIR_TABLE))
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
        stdscr.attron(curses.color_pair(PAIR_BLOCKQUOTE))
        stdscr.addstr(y, x, "│ ")
        format_inline(text, stdscr, y, x + 2, maxw)
        stdscr.attroff(curses.color_pair(PAIR_BLOCKQUOTE))
        return 1, 1
    m = re.match(r"^(#+) (.*)$", line)
    if m:
        level = len(m.group(1))
        text = m.group(2).strip()
        if level == 1:
            ascii_title = fig_slide.renderText(text)
            stdscr.attron(curses.color_pair(PAIR_HEADING_1) | curses.A_BOLD)
            title_lines = ascii_title.splitlines()
            for i, l in enumerate(title_lines):
                if y + i < stdscr.getmaxyx()[0]:
                    stdscr.addstr(y + i, x, l[:maxw - x])
            stdscr.attroff(curses.color_pair(PAIR_HEADING_1) | curses.A_BOLD)
            return len(title_lines), 1
        elif level == 2:
            stdscr.attron(curses.color_pair(PAIR_HEADING_2) | curses.A_BOLD)
            format_inline(text, stdscr, y, x, maxw)
            stdscr.attroff(curses.color_pair(PAIR_HEADING_2) | curses.A_BOLD)
            return 1, 1
        else:
            stdscr.attron(curses.color_pair(PAIR_HEADING_3) | curses.A_BOLD)
            format_inline(text, stdscr, y, x, maxw)
            stdscr.attroff(curses.color_pair(PAIR_HEADING_3) | curses.A_BOLD)
            return 1, 1
    stripped = line.strip()

    # Task list items (checkboxes)
    # Supports:
    # - [ ] unchecked
    # - [x] checked
    # - [X] checked
    # * [ ] unchecked
    # + [ ] unchecked
    m_task = re.match(r"^[-*+]\s+\[( |x|X)\]\s+(.*)$", stripped)
    if m_task:
        state, text = m_task.group(1), m_task.group(2)

        use_ascii = bool(os.environ.get("TERMSLIDE_ASCII_CHECKBOXES")) or not _utf8_probably_supported()
        if state in ("x", "X"):
            box = "[x]" if use_ascii else "☑"  # U+2611
            color = curses.color_pair(PAIR_CHECKBOX_CHECKED)
        else:
            box = "[ ]" if use_ascii else "☐"  # U+2610
            color = curses.color_pair(PAIR_BULLET)

        stdscr.attron(color)
        format_inline(f"{box} {text}", stdscr, y, x, maxw)
        stdscr.attroff(color)
        return 1, 1

    # Normal unordered list items
    if stripped.startswith("- "):
        line = "• " + stripped[2:]
        stdscr.attron(curses.color_pair(PAIR_BULLET))
        format_inline(line, stdscr, y, x, maxw)
        stdscr.attroff(curses.color_pair(PAIR_BULLET))
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
                    consumed = render_mermaid(diagram_content, stdscr, y, start_x, maxw, curses.color_pair(PAIR_TABLE))
                    y += consumed
                else:
                    for code_line in code_lines:
                        stdscr.attron(curses.color_pair(PAIR_CODE))
                        if y < stdscr.getmaxyx()[0] and start_x < stdscr.getmaxyx()[1]:
                            stdscr.addstr(y, start_x, "│ " + code_line[:maxw - (start_x + 2)])
                        stdscr.attroff(curses.color_pair(PAIR_CODE))
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
    global _ACTIVE_THEME

    curses.curs_set(0)
    curses.start_color()
    curses.use_default_colors()

    # Load and apply theme (colors + fonts)
    _ACTIVE_THEME = _get_active_theme()
    _apply_theme_colors(_ACTIVE_THEME)

    h, w = stdscr.getmaxyx()
    figlet_cfg = (_ACTIVE_THEME or {}).get("figlet", {})
    fig_title = Figlet(font=figlet_cfg.get("title", "mono12"), width=w)
    fig_slide = Figlet(font=figlet_cfg.get("slide", "smblock"), width=w)
    idx = 0

    while True:
        stdscr.clear()
        h, w = stdscr.getmaxyx()
        slide_type, title, content = slides[idx]
        if slide_type == "title":
            ascii_title = fig_title.renderText(title)
            lines = ascii_title.splitlines()
            start_y = max(0, (h - len(lines)) // 2)
            stdscr.attron(curses.color_pair(PAIR_HEADING_1) | curses.A_BOLD)
            for i, line in enumerate(lines):
                if start_y + i < h:
                    stdscr.addstr(start_y + i, max(0, (w - len(line)) // 2), line)
            stdscr.attroff(curses.color_pair(PAIR_HEADING_1) | curses.A_BOLD)
            if content:
                render_content(stdscr, content, start_y + len(lines) + 2, max(0, w // 4), w, fig_slide)
        else:
            if title:
                ascii_title = fig_slide.renderText(title)
                stdscr.attron(curses.color_pair(PAIR_HEADING_1) | curses.A_BOLD)
                for i, line in enumerate(ascii_title.splitlines()):
                    if i + 1 < h:
                        stdscr.addstr(i + 1, 2, line)
                stdscr.attroff(curses.color_pair(PAIR_HEADING_1) | curses.A_BOLD)
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

    # Validate input file path
    try:
        validated_file = validate_file_path(sys.argv[1])
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        raise SystemExit(1)

    if Image is None:
        print("Warning: Pillow not installed, image slides disabled.", file=sys.stderr)

    try:
        with open(validated_file, "r", encoding="utf-8") as f:
            content = f.read()
            slides = parse_markdown(content)
    except UnicodeDecodeError:
        print(f"Error: Unable to read file {validated_file} as UTF-8", file=sys.stderr)
        raise SystemExit(1)
    except IOError as e:
        print(f"Error reading file: {e}", file=sys.stderr)
        raise SystemExit(1)

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
