"""Small building blocks shared by every screen.

Each is a thin wrapper over a Qt widget that carries a Lattice style class, so the screens
read as layout and words rather than widget plumbing.
"""

from __future__ import annotations

from html import escape as esc
from typing import Callable, Iterable, List, Optional, Sequence

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from vcf_ops_telegraf_helper.workflow.plan import quote_note_for

__all__ = [
    "esc", "adopt", "set_class", "label", "button", "link", "hbox", "vbox", "Banner", "Headline", "Card", "ListBox",
    "Row", "Pill", "Tags", "Progress", "Field", "OptionCard", "CommandBox", "Page", "FlowLayout", "Dialog",
    "stretch", "busy_bar", "mark", "sentence", "grid", "EqualGrid", "reserve_bold", "plain",
]

stretch = object()  # placed between widgets in hbox()/vbox()/Page.footer() to push the rest apart

TICK = "✓"
CROSS = "✕"
DASH = "—"
DOTS = "…"
RING = "○"


def adopt(root: Optional[QWidget]) -> None:
    """Hand every widget under `root` to Qt, which already is its parent.

    PySide leaves a widget owned by Python when it sits in a layout that had no parent at the time
    and that layout is then added to one that has. Qt reparents the widget; PySide is not told.
    Such a widget is deleted twice when the program exits (a crash on PySide6 6.12). Setting the
    parent it already has tells PySide who owns it, and changes nothing else.
    """
    if root is None:
        return
    import shiboken6

    if not shiboken6.isValid(root):
        return
    for child in root.findChildren(QWidget):
        if shiboken6.ownedByPython(child) and child.parentWidget() is not None:
            child.setParent(child.parentWidget())


def set_class(widget: QWidget, cls: str) -> QWidget:
    widget.setProperty("class", cls)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    return widget


def plain(text: str) -> str:
    """Text for a button: Qt reads a single ampersand as "underline the next letter", so double it."""
    return text.replace("&", "&&")


def label(text: str = "", cls: str = "plain", wrap: bool = True, name: Optional[str] = None) -> QLabel:
    """A text label. Text is rich text: callers escape anything that came from outside."""
    widget = QLabel(text)
    widget.setProperty("class", cls)
    widget.setWordWrap(wrap)
    widget.setTextFormat(Qt.RichText)
    widget.setTextInteractionFlags(Qt.NoTextInteraction)
    if name:
        widget.setObjectName(name)
    return widget


def button(text: str, on_click: Optional[Callable[[], None]] = None, kind: str = "", name: Optional[str] = None,
           tip: str = "") -> QPushButton:
    widget = QPushButton(plain(text))
    if kind:
        widget.setProperty("class", kind)
    if name:
        widget.setObjectName(name)
    if tip:
        widget.setToolTip(tip)
    widget.setCursor(QCursor(Qt.PointingHandCursor))
    widget.setAutoDefault(False)
    if not any(word in kind.split() for word in ("nav", "conn")):
        # A button is as wide as its words, wherever it sits; it never stretches to fill a row
        widget.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    if any(word in kind.split() for word in ("chip", "tab", "seg")):
        reserve_bold(widget)
    if on_click:
        widget.clicked.connect(lambda _checked=False: on_click())
    return widget


def reserve_bold(widget: QPushButton, text: Optional[str] = None) -> None:
    """Keep room for the bold text a chip or tab shows when it is the chosen one, so it is never clipped."""
    font = widget.font()
    font.setBold(True)
    font.setPixelSize(13)
    from PySide6.QtGui import QFontMetrics

    widget.setMinimumWidth(QFontMetrics(font).horizontalAdvance(text if text is not None else widget.text()) + 34)


def link(text: str, on_click: Optional[Callable[[], None]] = None, name: Optional[str] = None) -> QPushButton:
    widget = button(text, on_click, "link", name)
    widget.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    return widget


def _fill(layout, items: Iterable) -> None:
    for item in items:
        if item is stretch:
            layout.addStretch(1)
        elif isinstance(item, tuple):
            layout.addWidget(item[0], item[1])
        elif isinstance(item, QLayout):
            layout.addLayout(item)
        elif isinstance(item, int):
            layout.addSpacing(item)
        elif item is not None:
            layout.addWidget(item)


def hbox(*items, spacing: int = 10, margins: Sequence[int] = (0, 0, 0, 0), align=None) -> QHBoxLayout:
    layout = QHBoxLayout()
    layout.setSpacing(spacing)
    layout.setContentsMargins(*margins)
    _fill(layout, items)
    if align is not None:
        layout.setAlignment(align)
    return layout


def vbox(*items, spacing: int = 6, margins: Sequence[int] = (0, 0, 0, 0)) -> QVBoxLayout:
    layout = QVBoxLayout()
    layout.setSpacing(spacing)
    layout.setContentsMargins(*margins)
    _fill(layout, items)
    return layout


def box(layout: QLayout, cls: str = "clear") -> QWidget:
    """A plain container for a layout, transparent unless given a class."""
    widget = QWidget()
    widget.setProperty("class", cls)
    widget.setLayout(layout)
    return widget


def mark(kind: str) -> str:
    """A tick, cross, dash or dots as rich text, coloured by the label it sits in."""
    return {"ok": TICK, "bad": CROSS, "skip": DASH, "wait": DOTS, "idle": RING}[kind]


class Progress(QProgressBar):
    """A thin progress bar; busy() makes it run without an end."""

    def __init__(self, busy: bool = False, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setTextVisible(False)
        if busy:
            self.setRange(0, 0)
        else:
            self.setRange(0, 100)
            self.setValue(0)

    def set_fraction(self, fraction: float) -> None:
        self.setRange(0, 100)
        self.setValue(int(max(0.0, min(1.0, fraction)) * 100))


def busy_bar(width: Optional[int] = None) -> Progress:
    bar = Progress(busy=True)
    if width:
        bar.setMaximumWidth(width)
    return bar


class Banner(QFrame):
    """A coloured line of text with an optional button: ok, warn, bad, info or neutral."""

    def __init__(self, kind: str, html: str, *actions: QWidget, name: Optional[str] = None):
        super().__init__()
        self.setProperty("class", f"banner banner-{kind}" if kind else "banner")
        if name:
            self.setObjectName(name)
        self.text = label(html, "banner-text")
        layout = hbox((self.text, 1), *actions, spacing=10, margins=(12, 9, 12, 9))
        self.setLayout(layout)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)


class Headline(QFrame):
    """The one-sentence outcome at the top of a result or a machine's page."""

    def __init__(self, kind: str, icon: str, title: str, small: str = "", name: Optional[str] = None):
        super().__init__()
        self.setProperty("class", f"headline headline-{kind}")
        if name:
            self.setObjectName(name)
        self.kind = kind
        icon_label = label(icon, "headline-icon", wrap=False)
        icon_label.setFixedWidth(26)
        icon_label.setAlignment(Qt.AlignCenter)
        self.title = label(title, "headline-title")
        text = vbox(self.title, spacing=1)
        self.small = None
        if small:
            self.small = label(small, "headline-small")
            text.addWidget(self.small)
        self.setLayout(hbox(icon_label, (box(text), 1), spacing=12, margins=(14, 11, 14, 11)))
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)


class Card(QFrame):
    def __init__(self, title: str = "", margins: Sequence[int] = (16, 13, 16, 13), spacing: int = 8):
        super().__init__()
        self.setProperty("class", "lattice-card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(*margins)
        self.body.setSpacing(spacing)
        if title:
            self.body.addWidget(label(title.upper(), "card-title", wrap=False))

    def add(self, *items) -> "Card":
        _fill(self.body, items)
        return self


class Row(QFrame):
    """One line of a list: who (name with a small line under it), what, and the buttons for it."""

    def __init__(self, name: str = "", sub: str = "", kind: str = "", name_width: int = 190,
                 object_name: Optional[str] = None, margins: Sequence[int] = (14, 9, 14, 9)):
        super().__init__()
        self.setProperty("class", f"mrow mrow-{kind}" if kind else "mrow")
        if object_name:
            self.setObjectName(object_name)
        self.grid = QHBoxLayout(self)
        self.grid.setContentsMargins(*margins)
        self.grid.setSpacing(12)
        if name or sub:
            who = vbox(label(esc(name), "nm", wrap=False), spacing=0)
            if sub:
                who.addWidget(label(esc(sub), "nm-sub", wrap=False))
            holder = box(who)
            holder.setFixedWidth(name_width)
            self.grid.addWidget(holder, 0, Qt.AlignVCenter)
        self.middle = QVBoxLayout()
        self.middle.setSpacing(4)
        self.middle.setContentsMargins(0, 0, 0, 0)
        self.grid.addLayout(self.middle, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(6)
        self.actions.setContentsMargins(0, 0, 0, 0)
        self.grid.addLayout(self.actions, 0)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

    def say(self, html: str, cls: str = "msg") -> QLabel:
        text = label(html, cls)
        self.middle.addWidget(text)
        return text

    def add_middle(self, *items) -> "Row":
        _fill(self.middle, items)
        return self

    def add_actions(self, *buttons: QWidget) -> "Row":
        for item in buttons:
            self.actions.addWidget(item, 0, Qt.AlignVCenter)
        return self

    def clear(self) -> None:
        for layout in (self.middle, self.actions):
            _empty(layout)


def _empty(layout: QLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout() is not None:
            _empty(item.layout())


class ListBox(QFrame):
    """A bordered box of rows that scrolls inside itself when the rows do not fit.

    The page never scrolls; only a genuinely long list does, and then a line at the bottom
    says so.
    """

    def __init__(self, max_height: Optional[int] = None, name: Optional[str] = None):
        super().__init__()
        self.setProperty("class", "listbox")
        if name:
            self.setObjectName(name)
        # The frame itself has no layout: Qt would then ask that layout, not this class, how tall the
        # box wants to be. A holder fills the frame and carries the layout instead.
        self.holder = QWidget(self)
        self.holder.setProperty("class", "clear")
        outer = QVBoxLayout(self.holder)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.outer = outer
        self.header: Optional[QWidget] = None
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.inner = QWidget()
        self.rows = QVBoxLayout(self.inner)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(0)
        self.rows.addStretch(1)
        self.scroll.setWidget(self.inner)
        outer.addWidget(self.scroll, 1)
        self.more = label("More in this list: scroll it to see the rest", "more", wrap=False)
        self.more.setAlignment(Qt.AlignCenter)
        self.more.hide()
        outer.addWidget(self.more)
        self._max_height = max_height
        self.scroll.verticalScrollBar().rangeChanged.connect(self._range_changed)
        policy = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred if max_height is None else QSizePolicy.Maximum)
        policy.setHeightForWidth(max_height is not None)
        self.setSizePolicy(policy)

    def set_header(self, widget: QWidget) -> None:
        self.header = widget
        self.outer.insertWidget(0, widget)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        self.holder.setGeometry(1, 1, max(self.width() - 2, 0), max(self.height() - 2, 0))

    def add_row(self, row: QWidget) -> QWidget:
        self.rows.insertWidget(self.rows.count() - 1, row)
        adopt(row)
        return row

    def clear(self) -> None:
        while self.rows.count() > 1:
            widget = self.rows.takeAt(0).widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _range_changed(self, _low: int, high: int) -> None:
        self.more.setVisible(high > 4)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 (Qt override)
        return self._max_height is not None

    def heightForWidth(self, width: int) -> int:  # noqa: N802 (Qt override)
        inner = max(width - 2, 50)
        rows = self.rows.heightForWidth(inner) if self.rows.hasHeightForWidth() else self.inner.sizeHint().height()
        height = rows + 2 + (self.header.sizeHint().height() if self.header else 0)
        if self._max_height is not None and height > self._max_height:
            return self._max_height
        return height

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        width = self.width() if self.width() > 100 else 700
        if self._max_height is None:
            return QSize(300, 200)
        return QSize(300, self.heightForWidth(width))

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        if self._max_height is None:
            return QSize(200, 90)
        return QSize(200, min(self.heightForWidth(self.width() if self.width() > 100 else 700), 90))


class Pill(QLabel):
    """A status in a rounded pill with a dot. tone: '', ok, acc, warn, bad."""

    def __init__(self, text: str, tone: str = ""):
        super().__init__(f"●&nbsp;{esc(text)}")
        self.status = text
        self.setAccessibleName(text)
        self.setTextFormat(Qt.RichText)
        self.setProperty("class", f"pill pill-{tone}" if tone else "pill")
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)


class FlowLayout(QLayout):
    """Lays children left to right and wraps to the next line, like words in a paragraph."""

    def __init__(self, parent: Optional[QWidget] = None, spacing: int = 5):
        super().__init__(parent)
        self._items: List = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:  # noqa: N802
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):  # noqa: N802
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):  # noqa: N802
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._layout(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._layout(rect, apply=True)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _layout(self, rect: QRect, apply: bool) -> int:
        x, y, line = rect.x(), rect.y(), 0
        for item in self._items:
            hint = item.sizeHint()
            if x + hint.width() > rect.right() + 1 and line > 0:
                x, y, line = rect.x(), y + line + self._spacing, 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._spacing
            line = max(line, hint.height())
        return y + line - rect.y()


class Tags(QWidget):
    """A wrapping run of small labels. Each item is text, or (text, 'add' | 'rem')."""

    def __init__(self, items: Iterable):
        super().__init__()
        self.setProperty("class", "clear")
        flow = FlowLayout(self)
        for item in items:
            text, kind = (item, "") if isinstance(item, str) else item
            tag = label(esc(text), f"tag tag-{kind}" if kind else "tag", wrap=False)
            tag.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
            flow.addWidget(tag)
        policy = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)


class Field(QWidget):
    """A labelled input with a hint under it; an error replaces the hint and clears as you type."""

    def __init__(self, text: str, hint: str = "", password: bool = False, placeholder: str = "",
                 name: Optional[str] = None, value: str = ""):
        super().__init__()
        self.setProperty("class", "clear")
        self.caption = label(esc(text), "field-label", wrap=False)
        self.input = QLineEdit()
        if password:
            self.input.setEchoMode(QLineEdit.Password)
        if placeholder:
            self.input.setPlaceholderText(placeholder)
        if name:
            self.input.setObjectName(name)
        if value:
            self.input.setText(value)
        self.caption.setBuddy(self.input)
        self.input.setAccessibleName(text)
        self._hint_text = hint
        self.note = label(hint, "hint")
        self.note.setVisible(bool(hint))
        self.setLayout(vbox(self.caption, self.input, self.note, spacing=4))
        self.input.textEdited.connect(self._edited)

    def text(self) -> str:
        return self.input.text()

    def set_error(self, message: Optional[str]) -> None:
        if message:
            set_class(self.input, "bad")
            self.note.setText("! " + esc(message))
            set_class(self.note, "hint-bad")
            self.note.setVisible(True)
        else:
            set_class(self.input, "")
            self.note.setText(self._hint_text)
            set_class(self.note, "hint")
            self.note.setVisible(bool(self._hint_text))

    def _edited(self, text: str) -> None:
        if text and self.input.property("class") == "bad":
            self.set_error(None)


class OptionCard(QFrame):
    """One thing to collect: a tick box, its plain name, a line of explanation, and a 'found' marker."""

    toggled = Signal(str, bool)

    def __init__(self, key: str, name: str, hint: str, checked: bool, found: str = "",
                 change: Optional[Callable[[], None]] = None):
        super().__init__()
        self.key = key
        self.setObjectName(f"opt-{key}")
        cls = "opt opt-on" if checked else ("opt opt-sug" if found else "opt")
        self.setProperty("class", cls)
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self.setMinimumHeight(44)
        self.check = QCheckBox()
        self.check.setChecked(checked)
        self.check.setAccessibleName(name)
        self.check.setObjectName(f"tick-{key}")
        self.check.clicked.connect(lambda state: self.toggled.emit(self.key, bool(state)))
        text = vbox(label(esc(name), "opt-name"), spacing=1)
        hint_line = hbox((label(hint, "opt-hint"), 1), spacing=6)
        if change is not None:
            hint_line.addWidget(link("Change", change, name=f"change-{key}"), 0, Qt.AlignTop)
        text.addLayout(hint_line)
        if found:
            marker = label(esc(found), "found" if checked else "found found-off")
            marker.setToolTip("Found, and will be watched" if checked else "Found, but not watched unless you tick it")
            text.addWidget(marker)
        self.setLayout(hbox(self.check, (box(text), 1), spacing=8, margins=(8, 4, 8, 4), align=Qt.AlignTop))

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.toggled.emit(self.key, not self.check.isChecked())
        super().mouseReleaseEvent(event)


class FitText(QPlainTextEdit):
    """Read-only text that is exactly as tall as its wrapped lines, up to a limit, so nothing is cut off."""

    def __init__(self, max_lines: int = 14):
        super().__init__()
        self.setReadOnly(True)
        self.setProperty("class", "code-block")
        self.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._max_lines = max_lines

    def fit(self) -> None:
        document = self.document()
        lines = max(1, int(document.size().height()))  # a plain-text document measures its height in lines
        shown = min(lines, self._max_lines)
        # Everything around the text itself: the frame, the padding from the style sheet
        chrome = self.height() - self.viewport().height() if self.viewport().height() > 0 else 14
        height = int(shown * self.fontMetrics().lineSpacing() + 2 * document.documentMargin() + max(chrome, 2) + 2)
        if height != self.height():
            self.setFixedHeight(height)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        self.fit()

    def set_text(self, text: str) -> None:
        self.setPlainText(text)
        self.fit()


class CommandBox(QFrame):
    """'Do this again from the command line': the whole command, never cut off, with Copy.

    A run of several machines has one command per machine: a drop-down chooses which is shown,
    Copy copies that one and Copy all copies every one.
    """

    def __init__(self, commands: Sequence[tuple], toast: Optional[Callable[[str], None]] = None):
        super().__init__()
        self.setProperty("class", "lattice-card")
        self.setObjectName("command-box")
        self.commands = list(commands)  # (machine name, command)
        self._toast = toast or (lambda _text: None)
        many = len(self.commands) > 1
        title = label(
            '<b style="font-size:13px">Do this again from the command line</b>'
            + ("" if many else "&nbsp; The same thing as a command, for a script or a terminal."), "small", wrap=False)
        head = hbox(title, stretch, button("Copy", self.copy, "sm", name="command-copy"), spacing=8)
        self.picker: Optional[QComboBox] = None
        which = None
        if many:
            head.addWidget(button(f"Copy all {len(self.commands)}", self.copy_all, "sm", name="command-copy-all"))
            self.picker = QComboBox()
            self.picker.setObjectName("command-picker")
            self.picker.setAccessibleName("Show the command for this machine")
            self.picker.addItems([name for name, _cmd in self.commands])
            self.picker.setProperty("class", "sm")
            self.picker.setMinimumWidth(170)
            self.picker.currentIndexChanged.connect(self._show)
            which = hbox(label("One command per machine. Showing the one for", "small", wrap=False), self.picker, stretch,
                         spacing=8)
        self.text = FitText()
        self.text.setObjectName("command-text")
        note = label("Passwords are never written out. Put your own in place of each &lt;password&gt; before you run it.",
                     "small")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(6)
        layout.addLayout(head)
        if which is not None:
            layout.addLayout(which)
        layout.addWidget(self.text)
        layout.addWidget(note)
        extra = quote_note_for([cmd for _name, cmd in self.commands])
        if extra:
            layout.addWidget(label(esc(extra), "small", name="command-quote-note"))
        self._show(0)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

    def current(self) -> str:
        index = self.picker.currentIndex() if self.picker else 0
        return self.commands[index][1] if self.commands else ""

    def _show(self, index: int) -> None:
        self.text.set_text(self.commands[index][1] if self.commands else "")

    def copy(self) -> None:
        QApplication.clipboard().setText(self.current())
        self._toast("Copied the command.")

    def copy_all(self) -> None:
        QApplication.clipboard().setText("\n\n".join(cmd for _name, cmd in self.commands))
        self._toast(f"Copied all {len(self.commands)} commands.")


class Page(QWidget):
    """One screen: a body that never scrolls, and a bar along the bottom for its buttons.

    The blue button is always at the right end of the bar.
    """

    def __init__(self, name: str, margins: Sequence[int] = (22, 18, 22, 12), spacing: int = 12):
        super().__init__()
        self.setObjectName(f"page-{name}")
        self.name = name
        self.setProperty("class", "page")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.body_widget = QWidget()
        self.body_widget.setProperty("class", "page")
        self.body = QVBoxLayout(self.body_widget)
        self.body.setContentsMargins(*margins)
        self.body.setSpacing(spacing)
        # Every screen is designed to fit the window without scrolling. The scroll area only comes into
        # play when the window is made smaller than the screen needs, so nothing is ever cut off.
        self.scroll = QScrollArea()
        self.scroll.setProperty("class", "page-scroll")
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFocusPolicy(Qt.NoFocus)
        self.scroll.setWidget(self.body_widget)
        outer.addWidget(self.scroll, 1)
        self.foot = QFrame()
        self.foot.setProperty("class", "foot")
        self.foot.setMinimumHeight(52)
        self.foot_layout = QHBoxLayout(self.foot)
        self.foot_layout.setContentsMargins(22, 10, 22, 10)
        self.foot_layout.setSpacing(10)
        self.foot.hide()
        outer.addWidget(self.foot)
        self.primary: Optional[QPushButton] = None
        self.on_enter: Optional[Callable[[], None]] = None
        self.on_leave: Optional[Callable[[], None]] = None

    def add(self, *items, stretch_last: bool = False) -> "Page":
        _fill(self.body, items)
        return self

    def title(self, text: str, sub: str = "", name: str = "page-title") -> QLabel:
        heading = label(text, "h1", name=name)
        self.body.addWidget(heading)
        if sub:
            self.body.addWidget(label(sub, "sub", name="page-sub"))
            self.body.setSpacing(self.body.spacing())
        return heading

    def end(self) -> "Page":
        self.body.addStretch(1)
        return self

    def footer(self, *items) -> "Page":
        _empty(self.foot_layout)
        _fill(self.foot_layout, items)
        if items and not any(item is stretch or isinstance(item, tuple) for item in items):
            self.foot_layout.addStretch(1)
        self.foot.setVisible(bool(items))
        for item in items:
            if isinstance(item, QPushButton) and "primary" in str(item.property("class") or ""):
                self.primary = item
        adopt(self.foot)
        return self

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and self.primary is not None and self.primary.isEnabled() \
                and isinstance(QApplication.focusWidget(), QLineEdit):
            self.primary.click()
            return
        super().keyPressEvent(event)


class Dialog(QDialog):
    """A small dialog: a title, a sentence, a body and a row of buttons with the blue one last."""

    def __init__(self, parent: QWidget, title: str, intro: str = "", width: int = 480, name: str = "dialog"):
        super().__init__(parent)
        self.setObjectName(name)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(width)
        self.setMaximumWidth(max(width, 640))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        head = vbox(label(esc(title), "dlg-title", name="dialog-title"), spacing=4, margins=(18, 14, 18, 0))
        if intro:
            head.addWidget(label(intro, "sub"))
        outer.addLayout(head)
        self.body = QVBoxLayout()
        self.body.setContentsMargins(18, 12, 18, 12)
        self.body.setSpacing(8)
        outer.addLayout(self.body, 1)
        foot = QFrame()
        foot.setProperty("class", "dlg-foot")
        self.buttons = QHBoxLayout(foot)
        self.buttons.setContentsMargins(18, 12, 18, 12)
        self.buttons.setSpacing(8)
        self.buttons.addStretch(1)
        outer.addWidget(foot)
        self.primary: Optional[QPushButton] = None

    def add(self, *items) -> "Dialog":
        _fill(self.body, items)
        return self

    def exec(self) -> int:
        adopt(self)
        return super().exec()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        adopt(self)
        super().showEvent(event)

    def add_button(self, text: str, on_click: Callable[[], None], kind: str = "", name: Optional[str] = None) -> QPushButton:
        widget = button(text, on_click, kind, name)
        self.buttons.addWidget(widget)
        if "primary" in kind or "danger" in kind:
            self.primary = widget
            widget.setDefault(True)
        return widget


def sentence(html: str, *links: QWidget, width: int = 730, cls: str = "small", name: Optional[str] = None) -> QHBoxLayout:
    """Words followed by a link. The words wrap when they need to, and are never cut off."""
    import re

    words = label(html, cls, wrap=False, name=name)
    plain = re.sub(r"<[^>]+>", "", html).replace("&nbsp;", " ").replace("&middot;", ".")
    needed = words.fontMetrics().horizontalAdvance(plain) + sum(item.sizeHint().width() + 8 for item in links) + 24
    if needed <= width:
        return hbox(words, *links, stretch, spacing=6)
    words.setWordWrap(True)
    layout = hbox((words, 1), spacing=8)
    for item in links:
        layout.addWidget(item, 0, Qt.AlignBottom)
    return layout


class EqualGrid(QWidget):
    """Children in equal columns, each row as tall as its tallest child needs at that width.

    The widget lays its children out itself (it has no Qt layout), so that its height for a
    given width is exactly what the wrapped text inside the children needs.
    """

    def __init__(self, columns: int, widgets: Sequence[QWidget], spacing: int = 8, min_row: int = 0):
        super().__init__()
        self.setProperty("class", "clear")
        self._columns = columns
        self._spacing = spacing
        self._min_row = min_row
        self._items = list(widgets)
        for widget in self._items:
            widget.setParent(self)
        policy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def _column_width(self, width: int) -> int:
        return max((width - self._spacing * (self._columns - 1)) // self._columns, 40)

    def _row_heights(self, width: int) -> List[int]:
        column = self._column_width(width)
        heights = []
        for start in range(0, len(self._items), self._columns):
            row = self._items[start:start + self._columns]
            needed = [w.heightForWidth(column) if w.hasHeightForWidth() else w.sizeHint().height() for w in row]
            heights.append(max([self._min_row] + [h for h in needed if h > 0] + [w.minimumHeight() for w in row]))
        return heights

    def hasHeightForWidth(self) -> bool:  # noqa: N802 (Qt override)
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 (Qt override)
        heights = self._row_heights(width)
        return sum(heights) + self._spacing * max(len(heights) - 1, 0)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        width = self.width() if self.width() > 100 else 720
        return QSize(width, self.heightForWidth(width))

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        return QSize(self._columns * 60, self.heightForWidth(self.width() if self.width() > 100 else 720))

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        column = self._column_width(self.width())
        y = 0
        for index, height in enumerate(self._row_heights(self.width())):
            for position, widget in enumerate(self._items[index * self._columns:(index + 1) * self._columns]):
                widget.setGeometry(position * (column + self._spacing), y, column, height)
            y += height + self._spacing


def grid(columns: int, widgets: Sequence[QWidget], spacing: int = 8) -> EqualGrid:
    """Widgets in equal columns."""
    return EqualGrid(columns, widgets, spacing)
