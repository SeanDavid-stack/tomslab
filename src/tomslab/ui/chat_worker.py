"""Runs an Ask Tom turn on a QThread so the UI stays responsive."""
from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal

from tomslab import chat as chatmod
from tomslab import db as dbmod


class ChatWorker(QThread):
    answered = pyqtSignal(object)      # AnswerResult
    failed = pyqtSignal(str)

    def __init__(
        self,
        question: str,
        history: list[chatmod.ChatTurn],
        attachment_path: str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._question = question
        self._history = list(history)
        self._attachment_path = attachment_path

    def run(self) -> None:
        try:
            conn = dbmod.connect()
            dbmod.initialise(conn)
            try:
                result = chatmod.ask(
                    conn, self._question, self._history,
                    attachment_path=self._attachment_path,
                )
            finally:
                conn.close()
            self.answered.emit(result)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
