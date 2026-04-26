"""Shared empty-state card builder.

Every tab in Tom's Lab gets the same visual treatment when it has
nothing to show: a centered card with an optional big glyph, a title,
a one-sentence explanation, and an optional call-to-action button.

Use `build_empty_state_widget()` for tabs that own their own empty
QWidget placeholder (Docs, TomTube, Gallery), or `empty_state_html()`
for tabs that render HTML directly into a QTextBrowser (Bookmarks,
Ask Tom).
"""
from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget


_CARD_BG = "#2B2D31"
_CARD_BORDER = "#3F4147"
_TEXT = "#DBDEE1"
_DIM = "#949BA4"
_ACCENT = "#FFC857"


def empty_state_html(
    *, glyph: str, title: str, sub: str, cta_text: str = "", cta_url: str = "",
) -> str:
    """HTML card for QTextBrowser-based views (Bookmarks, Ask Tom).

    ``cta_url`` may be any href scheme the host's anchorClicked handler
    understands — e.g. ``tomslab:import-dce`` — so the button triggers
    the right menu action.
    """
    import html as _h
    cta_html = ""
    if cta_text and cta_url:
        cta_html = (
            f'<div style="margin-top: 18px;">'
            f'<a href="{_h.escape(cta_url)}" style="display: inline-block;'
            f' background: {_ACCENT}; color: #1E1F22; padding: 10px 22px;'
            f' border-radius: 6px; text-decoration: none; font-weight: 600;">'
            f'{_h.escape(cta_text)}</a></div>'
        )
    return (
        f'<div style="margin: 48px auto; max-width: 560px; text-align: center;'
        f' color: {_DIM};">'
        f'<div style="font-size: 46px; margin-bottom: 14px; color: {_ACCENT};">'
        f'{_h.escape(glyph)}</div>'
        f'<div style="font-size: 17px; color: {_TEXT}; font-weight: 600;'
        f' margin-bottom: 6px;">{_h.escape(title)}</div>'
        f'<div style="font-size: 13px; line-height: 1.5;">{_h.escape(sub)}</div>'
        f'{cta_html}</div>'
    )


def build_empty_state_widget(
    *,
    glyph: str,
    title: str,
    sub: str,
    cta_text: str = "",
    cta_callback: Callable[[], None] | None = None,
    parent: QWidget | None = None,
) -> QWidget:
    """QWidget card for tabs that embed the empty state as a widget.

    Returns a self-contained QWidget suitable for `layout.addWidget(...)`.
    Pass an optional ``cta_callback`` to wire a primary action button.
    """
    host = QWidget(parent)
    host.setStyleSheet(
        f"QWidget {{ background: transparent; }}"
    )
    v = QVBoxLayout(host)
    v.setContentsMargins(24, 48, 24, 48)
    v.setSpacing(8)
    v.addStretch(1)

    # centered card
    card = QWidget()
    card.setStyleSheet(
        f"QWidget {{ background: {_CARD_BG};"
        f" border: 1px solid {_CARD_BORDER};"
        f" border-radius: 10px; }}"
    )
    card.setFixedWidth(560)
    cv = QVBoxLayout(card)
    cv.setContentsMargins(28, 28, 28, 28)
    cv.setSpacing(10)

    glyph_lbl = QLabel(glyph)
    glyph_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    glyph_lbl.setStyleSheet(
        f"color: {_ACCENT}; font-size: 42px; padding: 4px 0;"
    )
    cv.addWidget(glyph_lbl)

    title_lbl = QLabel(title)
    title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    title_lbl.setWordWrap(True)
    title_lbl.setStyleSheet(
        f"color: {_TEXT}; font-size: 15px; font-weight: 600;"
    )
    cv.addWidget(title_lbl)

    sub_lbl = QLabel(sub)
    sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    sub_lbl.setWordWrap(True)
    sub_lbl.setStyleSheet(
        f"color: {_DIM}; font-size: 12px; line-height: 1.5;"
    )
    cv.addWidget(sub_lbl)

    if cta_text and cta_callback is not None:
        btn = QPushButton(cta_text)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(
            f"QPushButton {{ background: {_ACCENT}; color: #1E1F22;"
            f" padding: 10px 22px; border: none; border-radius: 6px;"
            f" font-weight: 600; font-size: 12px; }}"
            f"QPushButton:hover {{ background: #FFD87A; }}"
        )
        btn.clicked.connect(cta_callback)
        cv.addWidget(btn, alignment=Qt.AlignmentFlag.AlignCenter)

    # hbox wrapper so the card is horizontally centered within the host
    from PyQt6.QtWidgets import QHBoxLayout
    hwrap = QHBoxLayout()
    hwrap.addStretch(1)
    hwrap.addWidget(card)
    hwrap.addStretch(1)
    v.addLayout(hwrap)
    v.addStretch(2)
    return host
