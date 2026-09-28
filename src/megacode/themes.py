"""Цветовые схемы (темы) MegaCode.

Каждая схема — один словарь со слотами двух видов:

* «хром» приложения — фон окна, карточки, рамки, акцент, текст — из них
  :func:`build_qss` собирает весь QSS (правило за правилом, как раньше
  выглядел константный ``app.QSS``);
* терминальная палитра — fg/bg и 16 цветов ANSI (имена pyte: ``brown`` —
  это SGR 33, «жёлтый»), их читает :mod:`megacode.terminal_widget`;
* палитра теговых чипов ``tag_colors`` — классы цветов групп синхронизации
  (:data:`megacode.tags.TAG_COLORS` / ``TAG_COLORS_LIGHT``): тёмная плашка
  со светлым текстом в тёмных темах, пастельная с тёмным — в светлых.

Схема ``claude-dark`` повторяет прежние константы один в один: смена
темы по умолчанию на неё — нулевая визуальная разница. Активная схема —
глобальное состояние процесса (у окна она одна; смена — через меню
тулбара), читается виджетами на каждом paint'е, поэтому переключение
сводится к ``set_active`` + перераздаче QSS/палитры + repaint.
"""

from __future__ import annotations

from typing import Dict

from .tags import TAG_COLORS, TAG_COLORS_LIGHT

#: Имя схемы по умолчанию (обязана существовать в SCHEMES).
DEFAULT_SCHEME = "claude-dark"

#: pyte-имена 16 ANSI-цветов, общие для всех схем (проверка полноты в тестах).
_ANSI_NAMES = (
    "black", "red", "green", "brown", "yellow", "blue", "magenta", "cyan",
    "white", "brightblack", "brightred", "brightgreen", "brightyellow",
    "brightblue", "brightmagenta", "brightwhite", "brightcyan",
)

# Терминальная палитра классического Windows Terminal (текущая «как была»).
_TERM_DARK = {
    "black": "#0c0c0c", "red": "#c50f1f", "green": "#13a10e",
    "brown": "#c19c00", "yellow": "#c19c00", "blue": "#0037da",
    "magenta": "#881798", "cyan": "#3a96dd", "white": "#cccccc",
    "brightblack": "#767676", "brightred": "#e74856",
    "brightgreen": "#16c60c", "brightyellow": "#f9f1a5",
    "brightblue": "#3b78ff", "brightmagenta": "#b4009e",
    "brightcyan": "#61d6d6", "brightwhite": "#f2f2f2",
}

SCHEMES: Dict[str, Dict[str, str]] = {
    # --- текущая тёмная «Клод»-тема: значения = прежние константы app.py ---
    "claude-dark": {
        "label": "Claude dark",
        "bg": "#0e1014", "card": "#161a21", "border": "#232830",
        "border_hi": "#3a4150", "accent": "#d97757", "accent_hi": "#e08866",
        "text": "#e6e6e6", "muted": "#8a93a3", "cell": "#3a3f4b",
        "hover": "#1b2029", "checked_bg": "#3a2a22", "checked_fg": "#f3e3dc",
        "sel_bg": "#2a2f3a",
        "disabled_bg": "#3a3a3a", "disabled_fg": "#777777",
        "launch_text": "#1a120e", "send_pressed": "#c4684a",
        "tile_bg": "#1e1e1e", "tile_border": "#2a2a2a",
        "tile_header": "#252526", "tile_title": "#cccccc",
        "tile_grip": "#6a6a6a", "tile_close": "#9a9a9a",
        "close_hover": "#e74856",
        "header_sync_peer": "#2e2118", "header_sync_source": "#33241a",
        "header_remote": "#1c2733",
        "chat_bg": "#12151a", "chat_border": "#1c212a",
        "chat_user_bg": "#3a2a22", "chat_user_border": "#613d2f",
        "chat_user_fg": "#f3e3dc", "chat_ai_bg": "#1b2027",
        "chat_ai_border": "#262c36",
        "chat_err_bg": "#2e1c1e", "chat_err_border": "#5a2f34",
        "chat_err_fg": "#f0989e",
        "term_fg": "#d4d4d4", "term_bg": "#1e1e1e",
        "term_colors": dict(_TERM_DARK),
        "tag_colors": TAG_COLORS,
    },
    # --- светлая: бумажный фон, тёмный текст, тот же терракотовый акцент ---
    "paper-light": {
        "label": "Paper light",
        "bg": "#f4f2ee", "card": "#ffffff", "border": "#d8d3ca",
        "border_hi": "#b8b2a6", "accent": "#c2583a", "accent_hi": "#a94d33",
        "text": "#33302b", "muted": "#837d72", "cell": "#d9d4cb",
        "hover": "#efece6", "checked_bg": "#f3ddcd", "checked_fg": "#5c3a26",
        "sel_bg": "#d7d2c7",
        "disabled_bg": "#dcd8d0", "disabled_fg": "#9a948a",
        "launch_text": "#fff6f0", "send_pressed": "#a94d33",
        "tile_bg": "#ffffff", "tile_border": "#d8d3ca",
        "tile_header": "#ecebe7", "tile_title": "#55524c",
        "tile_grip": "#a09a8e", "tile_close": "#8a8478",
        "close_hover": "#c92f3f",
        "header_sync_peer": "#f5ecdc", "header_sync_source": "#f0e2cb",
        "header_remote": "#dfe9f6",
        "chat_bg": "#f7f5f2", "chat_border": "#e5e1d9",
        "chat_user_bg": "#f3ddcd", "chat_user_border": "#d9b39a",
        "chat_user_fg": "#5c3a26", "chat_ai_bg": "#ffffff",
        "chat_ai_border": "#e0dcd3",
        "chat_err_bg": "#fbe9ea", "chat_err_border": "#e5b3b8",
        "chat_err_fg": "#a33d44",
        "term_fg": "#383a42", "term_bg": "#fafafa",
        "term_colors": {
            "black": "#383a42", "red": "#e45649", "green": "#50a14f",
            "brown": "#c18401", "yellow": "#c18401", "blue": "#0184bc",
            "magenta": "#a626a4", "cyan": "#0997b3", "white": "#fafafa",
            "brightblack": "#4f525e", "brightred": "#e06c75",
            "brightgreen": "#6bc178", "brightyellow": "#dcb02f",
            "brightblue": "#61afef", "brightmagenta": "#c678dd",
            "brightcyan": "#56b6c2", "brightwhite": "#ffffff",
        },
        "tag_colors": TAG_COLORS_LIGHT,
    },
    # --- тёмно-синяя «ночная»: холодный хром, Tokyo-Night-подобный терминал ---
    "marine-night": {
        "label": "Marine night",
        "bg": "#0a0f1e", "card": "#101830", "border": "#1d2947",
        "border_hi": "#2d3f6b", "accent": "#7aa2f7", "accent_hi": "#89b4fa",
        "text": "#c0caf5", "muted": "#565f89", "cell": "#273352",
        "hover": "#141d33", "checked_bg": "#1c2740", "checked_fg": "#dbe6ff",
        "sel_bg": "#243047",
        "disabled_bg": "#262b3d", "disabled_fg": "#5b6178",
        "launch_text": "#0b1222", "send_pressed": "#5d84cf",
        "tile_bg": "#10141f", "tile_border": "#232b40",
        "tile_header": "#161d2d", "tile_title": "#a9b1d6",
        "tile_grip": "#565f89", "tile_close": "#8a93a3",
        "close_hover": "#f7768e",
        "header_sync_peer": "#1d2a3a", "header_sync_source": "#223248",
        "header_remote": "#172033",
        "chat_bg": "#0c1220", "chat_border": "#1a2338",
        "chat_user_bg": "#22304d", "chat_user_border": "#33456e",
        "chat_user_fg": "#dbe6ff", "chat_ai_bg": "#141c2e",
        "chat_ai_border": "#1e2a44",
        "chat_err_bg": "#2e1c1e", "chat_err_border": "#5a2f34",
        "chat_err_fg": "#f0989e",
        "term_fg": "#c0caf5", "term_bg": "#10141f",
        "term_colors": {
            "black": "#151a2b", "red": "#f7768e", "green": "#9ece6a",
            "brown": "#e0af68", "yellow": "#e0af68", "blue": "#7aa2f7",
            "magenta": "#bb9af7", "cyan": "#7dcfff", "white": "#a9b1d6",
            "brightblack": "#414868", "brightred": "#ff7a93",
            "brightgreen": "#b9f28f", "brightyellow": "#ffdf9b",
            "brightblue": "#89b4fa", "brightmagenta": "#c8a9f9",
            "brightcyan": "#a6dcff", "brightwhite": "#e6edff",
        },
        "tag_colors": TAG_COLORS,
    },
}

# --- активная схема (процесс-глобально; меняет только set_active) ------------
_current: Dict[str, str] = {"name": DEFAULT_SCHEME}


def active() -> Dict[str, str]:
    return SCHEMES[_current["name"]]


def active_name() -> str:
    return _current["name"]


def tag_colors():
    """Цветовые классы тегов АКТИВНОЙ схемы (см. ``tags.TAG_COLORS*``).

    Единая точка входа для кода, которому нужна палитра «сейчас»
    (иконки меню тегов, деколлизия классов в заголовке): чипы красит сам
    QSS активной схемы, и эти потребители обязаны расходиться с ним не
    сильнее, чем на один switch темы.
    """
    return active()["tag_colors"]


def set_active(name: str) -> None:
    """Сделать схему активной; неизвестное имя — ошибка (значит, рассинхрон
    между сохранённой настройкой и кодом, молчаливый откат скрыл бы это)."""
    if name not in SCHEMES:
        raise KeyError(f"unknown theme: {name}")
    _current["name"] = name


def build_qss(scheme: Dict[str, str]) -> str:
    """Собрать QSS приложения из схемы (см. класс app.QSS до тематизации)."""
    # Tag-chip QSS, по правилу на цветовой класс — из палитры самой схемы
    # (tags.TAG_COLORS / TAG_COLORS_LIGHT), чтобы чипы, иконки меню и QSS
    # не расходились в цвете тега. Палитра выбирается по теме: тёмные схемы
    # красят чип тёмным со светлым текстом, светлые — пастельным с тёмным
    # (прежний общий тёмный чип в paper-light выглядел чужеродной плашкой,
    # а светлый текст схемы на нём исчезал). Hover-правило для (x) обязательно
    # дублируется на классный селектор: два id в нём перебивают базовый
    # QToolButton#tileTagX:hover по специфичности CSS, и без дубля подсветка
    # наведения умерла бы (проверено офлайн-рендером в ревью).
    hover = scheme["close_hover"]
    tag_chip_qss = "".join(
        f'QFrame#tileTag[tagClass="{i}"] '
        f'{{ background: {bg}; border: 1px solid {bd}; }}\n'
        f'QFrame#tileTag[tagClass="{i}"] QLabel#tileTagName,\n'
        f'QFrame#tileTag[tagClass="{i}"] QToolButton#tileTagX '
        f'{{ color: {fg}; }}\n'
        f'QFrame#tileTag[tagClass="{i}"] QToolButton#tileTagX:hover '
        f'{{ color: {hover}; }}\n'
        for i, (bg, bd, fg) in enumerate(scheme["tag_colors"])
    )
    return f"""
QWidget#root, QWidget#workspace {{ background: {scheme['bg']}; }}
QLabel {{ color: {scheme['text']}; }}
QLabel#title {{ font-size: 22px; font-weight: 600; }}
QLabel#subtitle {{ color: {scheme['muted']}; font-size: 12px; }}
QLabel#section {{ color: {scheme['muted']}; font-size: 11px; }}
QLabel#status {{ color: {scheme['muted']}; font-size: 11px; }}

QLineEdit {{
    background: {scheme['card']}; border: 1px solid {scheme['border']};
    border-radius: 8px; padding: 8px 10px; color: {scheme['text']};
}}
QLineEdit:focus {{ border: 1px solid {scheme['border_hi']}; }}
QSpinBox {{
    background: {scheme['card']}; border: 1px solid {scheme['border']};
    border-radius: 8px; padding: 5px 6px; color: {scheme['text']};
}}
/* Явные правила подсекции ::up/::down-button — не косметика: без них
   QStyleSheetStyle на xcb оставляет незакрытые save()-состояния художника
   и при каждом первом запуске сыплет "QPainter::end: Painter ended with
   2 saved states" (воспроизведено в xvfb-стенде, docker/xvfb-testbed). */
QSpinBox::up-button {{ border: none; }}
QSpinBox::down-button {{ border: none; }}
QSpinBox:focus {{ border: 1px solid {scheme['border_hi']}; }}

QPushButton#count {{
    background: {scheme['card']}; border: 1px solid {scheme['border']};
    border-radius: 12px; padding: 16px; color: {scheme['text']};
    font-size: 18px; font-weight: 600;
}}
QPushButton#count:hover {{
    border: 1px solid {scheme['border_hi']}; background: {scheme['hover']};
}}
QPushButton#count:checked {{
    background: {scheme['checked_bg']}; border: 1px solid {scheme['accent']};
    color: {scheme['checked_fg']};
}}
QPushButton#secondary, QPushButton#toolbarBtn {{
    background: {scheme['card']}; border: 1px solid {scheme['border']};
    border-radius: 8px; padding: 8px 14px; color: {scheme['text']};
}}
QPushButton#secondary:hover, QPushButton#toolbarBtn:hover {{
    border: 1px solid {scheme['border_hi']};
}}
QComboBox {{
    background: {scheme['card']}; border: 1px solid {scheme['border']};
    border-radius: 8px; padding: 6px 10px; color: {scheme['text']};
}}
QComboBox:hover {{ border: 1px solid {scheme['border_hi']}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {scheme['card']}; border: 1px solid {scheme['border']};
    color: {scheme['text']};
    selection-background-color: {scheme['sel_bg']}; outline: 0;
}}
QToolButton#toolbarBtn {{
    background: {scheme['card']}; border: 1px solid {scheme['border']};
    border-radius: 8px; padding: 6px 12px; color: {scheme['text']};
}}
QToolButton#toolbarBtn:hover {{ border: 1px solid {scheme['border_hi']}; }}
QToolButton#toolbarBtn:disabled {{
    color: {scheme['muted']}; border: 1px solid {scheme['border']};
}}
/* the sync-input toggle reads as "armed" while mirroring is live */
QToolButton#toolbarBtn:checked {{
    background: {scheme['checked_bg']}; border: 1px solid {scheme['accent']};
    color: {scheme['checked_fg']};
}}
/* the run-pasted button lights up while some pane holds pasted input */
QToolButton#toolbarBtn[armed="true"] {{
    border: 1px solid {scheme['accent']}; color: {scheme['accent_hi']};
}}
QToolButton#toolbarBtn::menu-button {{ border: none; width: 16px; }}
QPushButton#launch {{
    background: {scheme['accent']}; border: none; border-radius: 10px;
    padding: 14px; color: {scheme['launch_text']}; font-size: 14px;
    font-weight: 700;
}}
QPushButton#launch:hover {{ background: {scheme['accent_hi']}; }}
QPushButton#launch:disabled {{
    background: {scheme['disabled_bg']}; color: {scheme['disabled_fg']};
}}

/* workspace */
QFrame#toolbar {{
    background: {scheme['card']}; border: 1px solid {scheme['border']};
    border-radius: 10px;
}}
QLabel#toolbarTitle {{
    color: {scheme['text']}; font-size: 13px; font-weight: 600;
}}
/* the broadcast bar's command box: slimmer than the launcher fields so it
   matches the toolbar buttons' height, darker to read as an input */
QLineEdit#broadcastInput {{
    background: {scheme['bg']}; border: 1px solid {scheme['border']};
    border-radius: 8px; padding: 5px 10px; color: {scheme['text']};
}}
QLineEdit#broadcastInput:focus {{ border: 1px solid {scheme['accent']}; }}
QFrame#tile {{
    background: {scheme['tile_bg']}; border: 1px solid {scheme['tile_border']};
    border-radius: 6px;
}}
QFrame#tile[drop="true"] {{ border: 2px solid {scheme['accent']}; }}
QFrame#tileHeader {{
    background: {scheme['tile_header']};
    border-top-left-radius: 6px; border-top-right-radius: 6px;
}}
QLabel#tileGrip {{ color: {scheme['tile_grip']}; font-size: 14px; }}
QLabel#tileTitle {{ color: {scheme['tile_title']}; font-size: 12px; }}
QPushButton#tileClose {{
    background: transparent; border: none; color: {scheme['tile_close']};
    font-size: 16px; padding: 0 6px;
}}
QPushButton#tileClose:hover {{ color: {scheme['close_hover']}; }}
/* sync-group tag chips (see tags.py / workspace.py): a colored container
   holding the tag name and, for real tags, the explicit (x) that removes
   the tag from this pane */
QWidget#tileChips {{ background: transparent; }}
QFrame#tileTag {{ border-radius: 7px; }}
QLabel#tileTagName {{
    background: transparent; font-size: 10px; padding: 0 0 0 6px;
    color: {scheme['text']};
}}
QToolButton#tileTagX {{
    background: transparent; border: none; font-size: 10px;
    color: {scheme['muted']}; padding: 0 4px 0 2px;
}}
QToolButton#tileTagX:hover {{ color: {scheme['close_hover']}; }}
{tag_chip_qss}
/* sync-domain tint: WHERE a keystroke will go while sync is armed. On the
   header, not the tile frame -- that channel belongs to drop="true". */
QFrame#tileHeader[sync="peer"] {{ background: {scheme['header_sync_peer']}; }}
QFrame#tileHeader[sync="source"] {{ background: {scheme['header_sync_source']}; }}
QFrame#tileHeader[sync="source"] QLabel#tileTitle {{
    color: {scheme['accent_hi']};
}}
/* cross-window linking (see sync_bus.py): a cool-blue pulse on panes that
   just received REMOTE keys, and the degraded link chip. Both selectors
   match nothing while a window is unlinked. */
QFrame#tileHeader[remotePulse="true"] {{ background: {scheme['header_remote']}; }}
QToolButton#toolbarBtn[link="degraded"] {{
    border: 1px dashed {scheme['muted']}; color: {scheme['muted']};
}}
/* pane resizing: the gaps between tiles are draggable splitter handles */
QSplitter::handle {{ background: transparent; border-radius: 3px; }}
QSplitter::handle:hover, QSplitter::handle:pressed {{
    background: {scheme['border_hi']};
}}

/* AI chat tiles (web-chatbot look; see chat_widget.py) */
QWidget#chatRoot {{
    background: {scheme['chat_bg']};
    border-bottom-left-radius: 6px; border-bottom-right-radius: 6px;
}}
QScrollArea#chatScroll {{ background: transparent; border: none; }}
QWidget#chatMessages {{ background: transparent; }}
QLabel#chatErrorBubble {{
    background: {scheme['chat_err_bg']}; border: 1px solid
    {scheme['chat_err_border']}; border-radius: 12px;
    border-bottom-left-radius: 4px; padding: 8px 12px;
    color: {scheme['chat_err_fg']}; font-size: 12px;
}}
QTextBrowser#chatUserBubble {{
    background: {scheme['chat_user_bg']}; border: 1px solid
    {scheme['chat_user_border']}; border-radius: 12px;
    border-bottom-right-radius: 4px; padding: 8px 12px;
    color: {scheme['chat_user_fg']}; font-size: 13px;
    selection-background-color: {scheme['sel_bg']};
}}
QTextBrowser#chatAssistantBubble {{
    background: {scheme['chat_ai_bg']}; border: 1px solid
    {scheme['chat_ai_border']}; border-radius: 12px;
    border-bottom-left-radius: 4px; padding: 8px 12px;
    color: {scheme['text']}; font-size: 13px;
    selection-background-color: {scheme['sel_bg']};
}}
QLabel#chatThinking {{
    background: transparent; color: {scheme['muted']}; font-size: 12px;
    font-style: italic;
}}
QToolButton#chatCopyBtn {{
    background: {scheme['card']}; border: 1px solid {scheme['border']};
    border-radius: 6px; color: {scheme['muted']}; font-size: 11px;
    padding: 2px 8px;
}}
QToolButton#chatCopyBtn:hover {{
    color: {scheme['text']}; border: 1px solid {scheme['border_hi']};
    background: {scheme['hover']};
}}
QLabel#chatEmptyGlyph {{ color: {scheme['accent']}; font-size: 26px; }}
QLabel#chatEmptyTitle {{
    color: {scheme['text']}; font-size: 15px; font-weight: 600;
}}
QLabel#chatEmptyHint {{ color: {scheme['muted']}; font-size: 12px; }}
QToolButton#chatJumpBtn {{
    background: {scheme['card']}; border: 1px solid {scheme['border_hi']};
    border-radius: 13px; color: {scheme['text']}; font-size: 11px;
    padding: 4px 12px;
}}
QToolButton#chatJumpBtn:hover {{
    border: 1px solid {scheme['accent']}; color: {scheme['accent_hi']};
    background: {scheme['hover']};
}}
QFrame#chatInputBar {{
    background: {scheme['chat_bg']}; border-top: 1px solid {scheme['chat_border']};
}}
QFrame#chatModelBar {{
    background: {scheme['chat_bg']}; border-top: 1px solid {scheme['chat_border']};
}}
QComboBox#chatModelCombo, QComboBox#chatEffortCombo {{
    background: {scheme['card']}; border: 1px solid {scheme['border']};
    border-radius: 6px; padding: 2px 8px; color: {scheme['text']};
    font-size: 11px;
}}
QComboBox#chatModelCombo:hover, QComboBox#chatEffortCombo:hover {{
    border: 1px solid {scheme['border_hi']};
}}
QComboBox#chatModelCombo::drop-down, QComboBox#chatEffortCombo::drop-down {{
    border: none; width: 16px;
}}
QPlainTextEdit#chatInput {{
    background: {scheme['card']}; border: 1px solid {scheme['border']};
    border-radius: 12px; padding: 8px 12px; color: {scheme['text']};
    font-size: 13px; selection-background-color: {scheme['sel_bg']};
}}
QPlainTextEdit#chatInput:focus {{
    border: 1px solid {scheme['border_hi']};
}}
QToolButton#chatSendBtn {{
    background: {scheme['accent']}; border: none; border-radius: 16px;
    color: {scheme['launch_text']}; font-size: 15px; font-weight: 700;
    min-width: 32px; max-width: 32px; min-height: 32px; max-height: 32px;
}}
QToolButton#chatSendBtn:hover {{ background: {scheme['accent_hi']}; }}
QToolButton#chatSendBtn:pressed {{ background: {scheme['send_pressed']}; }}
QToolButton#chatSendBtn:disabled {{
    background: {scheme['disabled_bg']}; color: {scheme['disabled_fg']};
    border: none;
}}
QToolButton#chatSendBtn[streaming="true"] {{
    background: {scheme['card']}; border: 1px solid {scheme['border_hi']};
    color: {scheme['text']};
}}
QToolButton#chatSendBtn[streaming="true"]:hover {{
    border: 1px solid {scheme['accent']}; color: {scheme['accent_hi']};
    background: {scheme['hover']};
}}
QScrollArea#chatScroll QScrollBar:vertical,
QPlainTextEdit#chatInput QScrollBar:vertical {{
    background: transparent; width: 8px; margin: 2px;
}}
QScrollArea#chatScroll QScrollBar::handle:vertical,
QPlainTextEdit#chatInput QScrollBar::handle:vertical {{
    background: {scheme['border']}; border-radius: 3px; min-height: 24px;
}}
QScrollArea#chatScroll QScrollBar::handle:vertical:hover {{
    background: {scheme['border_hi']};
}}
QScrollArea#chatScroll QScrollBar::handle:vertical:pressed {{
    background: {scheme['accent']};
}}
QScrollArea#chatScroll QScrollBar::add-line:vertical,
QScrollArea#chatScroll QScrollBar::sub-line:vertical,
QPlainTextEdit#chatInput QScrollBar::add-line:vertical,
QPlainTextEdit#chatInput QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollArea#chatScroll QScrollBar::add-page:vertical,
QScrollArea#chatScroll QScrollBar::sub-page:vertical {{
    background: transparent;
}}
"""
