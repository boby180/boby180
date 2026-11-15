"""החלון הראשי: שלוש לשוניות (חזרה, כרטיסים, הגדרות) ושורת מצב."""
from __future__ import annotations

from PySide6.QtWidgets import QMainWindow, QTabWidget

from ..config import Config
from ..storage import Storage
from .audio import AudioService
from .browse import BrowsePage
from .review import ReviewPage
from .settings import SettingsPage


class MainWindow(QMainWindow):
    def __init__(self, cfg: Config):
        super().__init__()
        self.setWindowTitle("Arabic Cards – ערבית מדוברת")
        self.resize(900, 680)
        self.cfg = cfg
        self.db = Storage(cfg.db_path)
        self.audio = AudioService(cfg, self)
        self.audio.failed.connect(lambda msg: self.statusBar().showMessage(msg, 10000))

        self.review = ReviewPage(cfg, self.db, self.audio)
        self.browse = BrowsePage(cfg, self.db, self.audio)
        self.settings = SettingsPage(cfg, self.audio)

        self.tabs = QTabWidget()
        self.tabs.addTab(self.review, "חזרה")
        self.tabs.addTab(self.browse, "כרטיסים")
        self.tabs.addTab(self.settings, "הגדרות")
        self.setCentralWidget(self.tabs)
        self.statusBar()

        self.tabs.currentChanged.connect(self._tab_changed)
        self.review.cards_changed.connect(self.browse.refresh)
        self.browse.cards_changed.connect(self._cards_changed)
        self.settings.saved.connect(self._settings_saved)

    def _tab_changed(self, index: int) -> None:
        self.audio.stop()
        page = self.tabs.widget(index)
        if page is self.review:
            if self.review.is_idle():       # אולי בינתיים הגיע זמנם של כרטיסים
                self.review.restart()
        elif page is self.browse:
            self.browse.refresh()

    def _cards_changed(self) -> None:
        """יובאו, נוספו, נערכו או נמחקו כרטיסים: בונים מחדש את תור החזרה."""
        self.review.restart()
        self.audio.stop()

    def _settings_saved(self) -> None:
        self.review.restart()
        self.audio.stop()

    def closeEvent(self, event) -> None:
        self.audio.stop()
        self.db.close()
        super().closeEvent(event)
