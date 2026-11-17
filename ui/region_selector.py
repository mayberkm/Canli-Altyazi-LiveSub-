from PySide6.QtCore import Qt, QRect, Signal
from PySide6.QtGui import QPainter, QPen, QColor, QGuiApplication
from PySide6.QtWidgets import (
    QApplication, QWidget, QDialog, QVBoxLayout, QLabel, QComboBox, QPushButton, QHBoxLayout
)


class ScreenPicker(QDialog):
    def __init__(self, screens, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Ekran seç")
        self.setModal(True)
        self.screens = list(screens)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Altyazı bulunan ekranı seç:"))
        self.combo = QComboBox()
        self.combo.addItem("Tüm Ekranlar (Herhangi bir monitörde doğrudan seç)", None)
        for index, screen in enumerate(screens, 1):
            geo = screen.geometry()
            name = screen.name() or f"Ekran {index}"
            self.combo.addItem(f"{index}. {name} · {geo.width()}×{geo.height()} · ({geo.x()}, {geo.y()})",
                               {"name": name, "x": geo.x(), "y": geo.y(),
                                "width": geo.width(), "height": geo.height()})
        layout.addWidget(self.combo)
        buttons = QHBoxLayout()
        cancel = QPushButton("İptal")
        choose = QPushButton("Bu ekranda bölge seç")
        buttons.addWidget(cancel); buttons.addWidget(choose)
        layout.addLayout(buttons)
        cancel.clicked.connect(self.reject)
        choose.clicked.connect(self.accept)
        self.setStyleSheet(
            "QDialog,QWidget{background:#171a20;color:#e8eaf0} "
            "QComboBox{background:#222731;border:1px solid #3b4350;border-radius:6px;padding:6px;color:#f4f5f8} "
            "QPushButton{background:#303947;border:0;border-radius:6px;padding:8px 14px;color:#e8eaf0;font-weight:600} "
            "QPushButton:hover{background:#435269}"
        )

    def selected_screen(self):
        idx = self.combo.currentIndex()
        if idx <= 0:
            return None
        screen_idx = idx - 1
        return self.screens[screen_idx] if 0 <= screen_idx < len(self.screens) else None

    def selected_screen_info(self):
        return self.combo.currentData()

    def select_saved_screen(self, saved):
        if not saved:
            return
        for i in range(self.combo.count()):
            current = self.combo.itemData(i)
            if current and current["name"] == saved.get("name") and \
                    current["width"] == saved.get("width") and current["height"] == saved.get("height"):
                self.combo.setCurrentIndex(i)
                return
            if current and current["x"] == saved.get("x") and current["y"] == saved.get("y") and \
                    current["width"] == saved.get("width") and current["height"] == saved.get("height"):
                self.combo.setCurrentIndex(i)
                return


class RegionSelector(QWidget):
    selected = Signal(dict)
    cancelled = Signal()

    def __init__(self, screen=None):
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint)
        self.screen_ref = screen
        if screen is not None:
            geo = screen.geometry()
            self.screen_info = {"name": screen.name(), "x": geo.x(), "y": geo.y(),
                                "width": geo.width(), "height": geo.height()}
        else:
            geo = QRect()
            for s in QApplication.screens():
                geo = geo.united(s.geometry())
            self.screen_info = None
        self.origin = None
        self.current = None
        self.global_origin = None
        self.setGeometry(geo)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setWindowOpacity(.88)

    def mousePressEvent(self, event):
        self.origin = event.position().toPoint()
        self.current = self.origin
        self.global_origin = event.globalPosition().toPoint()
        self.update()

    def mouseMoveEvent(self, event):
        if self.origin:
            self.current = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event):
        if not self.origin or not self.global_origin:
            return
        vgeo = QRect()
        for s in QApplication.screens():
            vgeo = vgeo.united(s.geometry())
        global_end = event.globalPosition().toPoint()
        global_rect = QRect(self.global_origin, global_end).normalized()
        if not vgeo.isEmpty():
            global_rect = global_rect.intersected(vgeo)
        if global_rect.width() < 15 or global_rect.height() < 15:
            self.close()
            return

        target_screen = QGuiApplication.screenAt(global_rect.center()) or self.screen_ref or QGuiApplication.primaryScreen()
        sgeo = target_screen.geometry() if target_screen else global_rect
        sname = target_screen.name() if target_screen else "Ekran"

        self.selected.emit({
            "left": int(global_rect.x()),
            "top": int(global_rect.y()),
            "width": int(global_rect.width()),
            "height": int(global_rect.height()),
            "screen": {
                "name": sname,
                "x": sgeo.x(),
                "y": sgeo.y(),
                "width": sgeo.width(),
                "height": sgeo.height(),
            },
        })
        self.close()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            self.close()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.origin and self.current:
            rect = QRect(self.origin, self.current).normalized()
            # Dim the area outside the selection cutout so the subtitle region stays crystal clear
            dim = QColor(0, 0, 0, 120)
            w, h = self.width(), self.height()
            p.fillRect(0, 0, w, rect.top(), dim)
            p.fillRect(0, rect.bottom() + 1, w, max(0, h - rect.bottom() - 1), dim)
            p.fillRect(0, rect.top(), rect.left(), rect.height(), dim)
            p.fillRect(rect.right() + 1, rect.top(), max(0, w - rect.right() - 1), rect.height(), dim)

            p.fillRect(rect, QColor(255, 255, 255, 18))
            p.setPen(QPen(QColor("#55e7c1"), 2))
            p.drawRect(rect)

            dim_text = f"{rect.width()} × {rect.height()} px"
            badge_w = p.fontMetrics().horizontalAdvance(dim_text) + 16
            badge_h = 24
            badge_x = max(8, min(rect.left(), w - badge_w - 8))
            badge_y = rect.top() - badge_h - 6 if rect.top() >= badge_h + 10 else rect.bottom() + 6
            badge_rect = QRect(badge_x, badge_y, badge_w, badge_h)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(15, 20, 28, 220))
            p.drawRoundedRect(badge_rect, 5, 5)
            p.setPen(QColor("#55e7c1"))
            p.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, dim_text)
        else:
            p.fillRect(self.rect(), QColor(0, 0, 0, 110))

        hint_text = "Altyazı alanını fareyle sürükleyip seçin · İptal: Esc"
        hw = p.fontMetrics().horizontalAdvance(hint_text) + 24
        hint_rect = QRect(16, 16, hw, 30)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(15, 20, 28, 210))
        p.drawRoundedRect(hint_rect, 6, 6)
        p.setPen(QColor("white"))
        p.drawText(hint_rect, Qt.AlignmentFlag.AlignCenter, hint_text)
