"""Central palette + spacing tokens for Tom's Lab.

Every UI module that hardcodes colors (``#1E1F22`` / ``#FFC857`` / etc.)
should import these names instead. Makes a future light-theme toggle
or contrast tweak a single-file change rather than a codebase-wide
search-and-replace.

The values reflect the Discord-inspired dark theme already in use —
no visual change if modules migrate carefully. Additions:
  * ``MUTED_BG``, ``OK``, ``WARN``, ``DANGER`` for semantic accents
  * ``RADIUS_*`` / ``SPACE_*`` so cards line up across tabs
  * ``FONT_SIZE_*`` for consistent type scale
"""
from __future__ import annotations

# --- surface colors -------------------------------------------------------
BG         = "#1E1F22"   # app background (main canvas)
BG_ALT     = "#2B2D31"   # raised surface (cards, popovers, menus)
BG_DEEP    = "#17181A"   # sunken area (code blocks, disabled fields)
BORDER     = "#3F4147"   # 1px separators, input borders
BORDER_SOFT = "#313338"  # hairline separators

# --- text -----------------------------------------------------------------
TEXT       = "#DBDEE1"   # primary text
TEXT_DIM   = "#949BA4"   # secondary / captions
TEXT_MUTED = "#6B6E74"   # placeholders / disabled

# --- accents --------------------------------------------------------------
PRIMARY    = "#5865F2"   # action accent (Ask button, primary links)
TOM_GOLD   = "#FFC857"   # Tom B featured-speaker accent, primary CTAs
TOM_GOLD_HOVER = "#FFD87A"

# --- semantic -------------------------------------------------------------
OK         = "#43B581"   # success, "done"
WARN       = "#FAA61A"
DANGER     = "#ED4245"   # errors, destructive actions

# --- spacing --------------------------------------------------------------
SPACE_XS = 4
SPACE_SM = 8
SPACE_MD = 12
SPACE_LG = 16
SPACE_XL = 24

# --- radius ---------------------------------------------------------------
RADIUS_SM = 4
RADIUS_MD = 6
RADIUS_LG = 10
RADIUS_PILL = 12    # header chips / toggles

# --- type scale (px) ------------------------------------------------------
FONT_SIZE_CAPTION = 10
FONT_SIZE_SMALL   = 11
FONT_SIZE_BODY    = 12
FONT_SIZE_BODY_LG = 13
FONT_SIZE_H3      = 15
FONT_SIZE_H2      = 17
FONT_SIZE_H1      = 22


# --- reusable style snippets ---------------------------------------------
def pill_button(color: str = TEXT_DIM, border: str = BORDER) -> str:
    """Small dim header pill — used for toggles, recent, sort."""
    return (
        f"QPushButton {{ background: transparent; color: {color};"
        f" padding: 4px 10px; border: 1px solid {border};"
        f" border-radius: {RADIUS_PILL}px; font-size: {FONT_SIZE_SMALL}px; }}"
        f"QPushButton:hover {{ color: {TEXT};"
        f" border-color: {TEXT}; }}"
    )


def primary_button() -> str:
    """The gold 'Ask' / 'Save' / 'I agree' style."""
    return (
        f"QPushButton {{ background: {TOM_GOLD}; color: {BG};"
        f" padding: 10px 22px; border: none; border-radius: {RADIUS_MD}px;"
        f" font-weight: 600; font-size: {FONT_SIZE_BODY}px; }}"
        f"QPushButton:hover:enabled {{ background: {TOM_GOLD_HOVER}; }}"
        f"QPushButton:disabled {{ background: {BORDER}; color: {TEXT_MUTED}; }}"
    )


def secondary_button() -> str:
    """Outline / ghost button — Decline, Clear, Close."""
    return (
        f"QPushButton {{ background: transparent; color: {TEXT_DIM};"
        f" padding: 10px 16px; border: 1px solid {BORDER};"
        f" border-radius: {RADIUS_MD}px; font-size: {FONT_SIZE_BODY}px; }}"
        f"QPushButton:hover {{ color: {TEXT}; border-color: {TEXT}; }}"
    )


def card_frame() -> str:
    """Raised card surface — sections inside tabs."""
    return (
        f"background: {BG_ALT}; border: 1px solid {BORDER};"
        f" border-radius: {RADIUS_LG}px;"
    )
