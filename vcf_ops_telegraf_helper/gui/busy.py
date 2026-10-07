"""Keep the GUI responsive while a bounded network operation runs."""
from PySide6.QtCore import QObject, QThread, Signal, Slot, Qt
from PySide6.QtWidgets import QDialog, QLabel, QProgressBar, QVBoxLayout


class _Query(QObject):
    done = Signal(object)

    def __init__(self, operation):
        super().__init__()
        self.operation = operation

    def run(self):
        try:
            result = (self.operation(), None)
        except Exception as exc:
            result = (None, exc)
        self.done.emit(result)


class BusyDialog(QDialog):
    """Network requests have timeouts; closing cannot abandon their worker."""

    @Slot(object)
    def completed(self, value):
        self.value = value
        self.accept()

    def reject(self):
        pass


def run_busy(parent, title, operation):
    dialog = BusyDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setWindowFlag(Qt.WindowCloseButtonHint, False)
    dialog.setMinimumWidth(340)
    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel(title))
    bar = QProgressBar()
    bar.setRange(0, 0)
    layout.addWidget(bar)
    thread = QThread()
    query = _Query(operation)
    query.moveToThread(thread)
    query.done.connect(dialog.completed)
    query.done.connect(thread.quit, Qt.DirectConnection)
    thread.finished.connect(query.deleteLater)
    thread.started.connect(query.run)
    # Start only once the modal event loop exists, including very fast responses.
    from PySide6.QtCore import QTimer
    QTimer.singleShot(0, thread.start)
    dialog.exec()
    thread.wait()
    thread.deleteLater()
    dialog.deleteLater()
    value, error = dialog.value
    if error:
        raise error
    return value
