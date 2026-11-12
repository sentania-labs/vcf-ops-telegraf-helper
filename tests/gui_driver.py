"""Drive the real window offscreen: click and type on real widgets by name, and save screenshots."""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QLabel, QLineEdit, QPlainTextEdit, QWidget  # noqa: E402


class Driver:
    def __init__(self, window, shots: Path):
        self.app = QApplication.instance()
        self.window = window
        self.fit_problems: list = []
        self.shots = Path(shots)
        self.shots.mkdir(parents=True, exist_ok=True)
        self.count = 0
        self.log = []

    # ---------------------------------------------------------------- waiting
    def pump(self, ms: int = 30) -> None:
        end = time.time() + ms / 1000
        while time.time() < end:
            self.app.processEvents()
            time.sleep(0.005)

    def settle(self, timeout: float = 60.0) -> None:
        """Wait until no background work is running and nothing is waiting to be delivered."""
        end = time.time() + timeout
        quiet = 0
        while time.time() < end:
            self.app.processEvents()
            if self.window.tasks.busy:
                quiet = 0
            else:
                quiet += 1
                if quiet > 12:
                    return
            time.sleep(0.01)
        raise TimeoutError("the app did not settle")

    def wait(self, condition, timeout: float = 60.0, what: str = "condition") -> None:
        end = time.time() + timeout
        while time.time() < end:
            self.app.processEvents()
            if condition():
                return
            time.sleep(0.01)
        raise TimeoutError(f"timed out waiting for {what}")

    # ---------------------------------------------------------------- finding
    def top(self) -> QWidget:
        modal = QApplication.activeModalWidget()
        return modal if modal is not None else self.window

    def find(self, name: str, kind=QWidget, root=None):
        root = root or self.top()
        found = [w for w in root.findChildren(kind, name) if w.isVisible()]
        if not found and root is not self.window:
            found = [w for w in self.window.findChildren(kind, name) if w.isVisible()]
        if not found:
            raise LookupError(f"no visible widget named {name!r}; page is {self.page()}")
        return found[0]

    def has(self, name: str) -> bool:
        try:
            self.find(name)
            return True
        except LookupError:
            return False

    def page(self) -> str:
        return self.window.page.name if self.window.page is not None else "?"

    def text(self, name: str) -> str:
        widget = self.find(name)
        if isinstance(widget, QPlainTextEdit):
            return widget.toPlainText()
        return widget.text()

    def title(self) -> str:
        return self.text("page-title")

    # ---------------------------------------------------------------- acting
    def click(self, name: str, settle: bool = True) -> None:
        widget = self.find(name)
        assert widget.isEnabled(), f"{name} is disabled"
        self.log.append(f"click {name}")
        QTest.mouseClick(widget, Qt.LeftButton, Qt.NoModifier, self._inside(widget))
        self.pump(40)
        if settle:
            self.settle()

    def _inside(self, widget) -> QPoint:
        if isinstance(widget, QCheckBox):
            return QPoint(8, widget.height() // 2)
        return widget.rect().center()

    def type(self, name: str, text: str) -> None:
        widget = self.find(name, QLineEdit)
        widget.setFocus()
        widget.selectAll()
        QTest.keyClicks(widget, text) if text else QTest.keyClick(widget, Qt.Key_Delete)
        self.log.append(f"type {name}")
        self.pump(20)

    def paste(self, name: str, text: str) -> None:
        widget = self.find(name, QPlainTextEdit)
        widget.setPlainText(text)
        self.pump(20)

    def choose(self, name: str, text: str) -> None:
        combo = self.find(name, QComboBox)
        index = next(i for i in range(combo.count()) if text.lower() in combo.itemText(i).lower())
        combo.setCurrentIndex(index)
        combo.activated.emit(index)
        self.pump(40)
        self.settle()

    def key(self, key, name: str = None) -> None:
        widget = self.find(name) if name else QApplication.focusWidget()
        QTest.keyClick(widget, key)
        self.pump(40)

    def in_dialog(self, action, open_by) -> None:
        """Run `action` once a modal dialog opened by `open_by` is on screen (exec() blocks until it closes)."""
        from PySide6.QtCore import QTimer

        state = {"done": False, "error": None}

        def run() -> None:
            if QApplication.activeModalWidget() is None:
                QTimer.singleShot(30, run)
                return
            try:
                action()
            except Exception as exc:  # close the dialog so the run does not hang, then report
                state["error"] = exc
                modal = QApplication.activeModalWidget()
                while modal is not None:
                    modal.reject()
                    self.app.processEvents()
                    modal = QApplication.activeModalWidget()
            state["done"] = True

        QTimer.singleShot(30, run)
        open_by()
        self.wait(lambda: state["done"], what="dialog action")
        if state["error"]:
            raise state["error"]
        self.settle()

    # ---------------------------------------------------------------- screenshots
    def shot(self, name: str) -> Path:
        self.pump(60)
        self.count += 1
        path = self.shots / f"{self.count:02d}-{name}.png"
        modal = QApplication.activeModalWidget()
        image = self.window.grab()
        if modal is not None:
            from PySide6.QtGui import QColor, QPainter

            painter = QPainter(image)
            painter.fillRect(image.rect(), QColor(0, 0, 0, 128))
            shot = modal.grab()
            x = (image.width() / image.devicePixelRatio() - shot.width() / shot.devicePixelRatio()) / 2
            y = (image.height() / image.devicePixelRatio() - shot.height() / shot.devicePixelRatio()) / 2
            painter.drawPixmap(int(x), int(y), shot)
            painter.end()
        image.save(str(path))
        self.log.append(f"shot {path.name}")
        # Every screen that is looked at is also checked for anything cut off or squeezed
        self.fit_problems.extend(f"{path.name}: {line}" for line in self.overflow())
        return path

    def overflow(self) -> list:
        """Everything on screen that is cut off, squeezed or needs scrolling to reach.

        The page must fit the window both ways without scrolling, and nothing in the window or in an
        open dialog may be shorter than its words or its box needs.
        """
        page = self.window.page
        problems = []
        if page is not None:
            hint = page.body_widget.minimumSizeHint()
            have = page.scroll.viewport().size()
            if hint.height() > have.height() + 1 or page.scroll.verticalScrollBar().maximum() > 0:
                problems.append(f"page {page.name}: needs {hint.height()}px, has {have.height()}px (the page would scroll)")
            if hint.width() > have.width() + 1 or page.scroll.horizontalScrollBar().maximum() > 0:
                problems.append(f"page {page.name}: needs {hint.width()}px across, has {have.width()}px (cut off at the side)")
        top = self.top()
        for box in top.findChildren(QLineEdit):
            if box.isVisible() and box.height() < box.minimumSizeHint().height():
                problems.append(f"{box.objectName() or 'a box'} is squeezed to {box.height()}px, needs "
                                f"{box.minimumSizeHint().height()}px")
        for words in top.findChildren(QLabel):
            if not words.isVisible() or not words.text():
                continue
            said = " ".join(re.sub(r"<[^>]+>", " ", words.text()).split())[:48]
            if words.wordWrap() and words.height() < words.heightForWidth(words.width()) - 1:
                problems.append(f"cut off at the bottom: {said!r} ({words.height()}px of "
                                f"{words.heightForWidth(words.width())}px)")
        return problems
        hint = page.body_widget.minimumSizeHint().height()
        have = page.scroll.viewport().height()
        if hint > have + 1 or page.scroll.verticalScrollBar().maximum() > 0:
            problems.append(f"page {page.name}: needs {hint}px, has {have}px (the page would scroll)")
        return problems
