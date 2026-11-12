"""Run slow work off the screen's thread and bring the answers back to it.

Everything that talks to VCF Operations or to a machine runs on a plain background thread.
What it reports (a result, a progress step) is put on a queue, and a timer on the screen's
thread takes it off and calls the screen. No widget is ever touched from another thread, and
no thread object is owned by Qt, so closing a window can never tear down a running thread.
"""

from __future__ import annotations

import threading
from queue import Empty, Queue
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, QTimer

from vcf_ops_telegraf_helper.logger import get_logger

logger = get_logger("gui.tasks")


class Tasks(QObject):
    """Background work for one window."""

    def __init__(self, parent: Optional[QObject] = None, interval_ms: int = 20):
        super().__init__(parent)
        self._queue: "Queue[Callable[[], None]]" = Queue()
        self._running = 0
        self._lock = threading.Lock()
        self._closed = False
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self.drain)
        self._timer.start()

    @property
    def busy(self) -> bool:
        """True while any work is running or an answer is waiting to be delivered."""
        return self._running > 0 or not self._queue.empty()

    def post(self, call: Callable[[], None]) -> None:
        """Ask for `call` to run on the screen's thread. Safe from any thread."""
        if not self._closed:
            self._queue.put(call)

    def run(
        self,
        work: Callable[[], Any],
        done: Optional[Callable[[Any], None]] = None,
        failed: Optional[Callable[[Exception], None]] = None,
        name: str = "task",
    ) -> None:
        """Run `work` in the background; then call done(result) or failed(error) on the screen's thread."""
        with self._lock:
            self._running += 1

        def body() -> None:
            try:
                result = work()
            except Exception as exc:  # delivered to the screen, never lost on a thread
                logger.exception("Background %s failed", name)
                error = exc
                self.post(lambda: failed(error) if failed else None)
            else:
                self.post(lambda: done(result) if done else None)
            finally:
                with self._lock:
                    self._running -= 1

        threading.Thread(target=body, name=name, daemon=True).start()

    def drain(self) -> None:
        """Deliver everything waiting. Called by the timer; tests may call it directly."""
        while True:
            try:
                call = self._queue.get_nowait()
            except Empty:
                return
            try:
                call()
            except Exception:
                logger.exception("A screen callback raised")

    def close(self) -> None:
        """Stop delivering: the window is going away."""
        self._closed = True
        self._timer.stop()
