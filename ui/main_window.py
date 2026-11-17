import ctypes
import json
import os
import re
import sys
import time
from collections import deque
from difflib import SequenceMatcher
from pathlib import Path

import requests
from PySide6.QtCore import Qt, QTimer, Signal, QObject, QThread
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFontComboBox, QFormLayout, QGridLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton,
    QPlainTextEdit, QSlider, QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from core.cache import TranslationCache
from core.capture_source import X11WindowCapture
from core.pipeline import CaptureWorker, ModelWorker, TranslationWorker, is_translation_echo, is_english_text, is_dialogue_subtitle
from translation.ollama_provider import OllamaProvider
from ui.overlay import Overlay
from ui.region_selector import RegionSelector, ScreenPicker
from utils.system_info import gpu_memory_mb, session_info

SETTINGS = Path(__file__).resolve().parents[1] / "config/settings.json"


class Bus(QObject):
    translate = Signal(str, str, str, str, int, object)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CanlıAltyazı (LiveSub) · Gerçek Zamanlı Altyazı Çevirmeni")
        self.resize(1080, 720)
        self.settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
        self.region = self.settings.get("region")
        self.selected_screen = self.settings.get("selected_screen") or (self.region or {}).get("screen")
        self.cache = TranslationCache()
        self.history = deque(maxlen=4)
        self.recent_translations = deque(maxlen=40)
        self.recent_sources = deque(maxlen=8)
        self.generation = 0
        self._stopped_generation = -1
        self.workers = []
        self.capture = None
        self.active_translation = None
        self.active_source_text = ""
        self.pending_translation = None
        self.translation_queue = deque()
        self.overlay_texts = []
        self.overlay_anchors = []
        self.overlay_sources = []
        self.overlay = Overlay()
        self.overlays = [self.overlay]
        self.overlay.visibility_changed.connect(lambda _: self._sync_capture_masks())
        self.overlay.geometry_changed.connect(lambda _: self._sync_capture_masks())
        self.capture_source = None
        self.model_worker = None
        self._loading_settings = False
        self._build()
        self.load_settings()
        QTimer.singleShot(200, self.refresh_models)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_gpu)
        self.timer.start(2000)

    def _build(self):
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.start_btn = QPushButton("▶  Çeviriyi Başlat")
        self.start_btn.setObjectName("startBtn")
        self.stop_btn = QPushButton("■  Durdur")
        self.stop_btn.setObjectName("stopBtn")
        self.stop_btn.setEnabled(False)
        self.region_btn = QPushButton("▧  Bölge Seç")
        self.clear_btn = QPushButton("🧹 Temizle")
        self.clear_btn.setToolTip("Aktif kutu ve metin panellerini temizler")
        self.overlay_toggle = QCheckBox("Overlay açık")
        self.region_badge = QLabel("📍 Bölge seçilmedi")
        self.region_badge.setObjectName("regionBadge")
        for widget in (self.start_btn, self.stop_btn, self.region_btn, self.clear_btn, self.overlay_toggle):
            bar.addWidget(widget)
        bar.addStretch(1)
        bar.addWidget(self.region_badge)
        root.addLayout(bar)

        columns = QHBoxLayout()
        columns.setSpacing(12)
        self.tabs = QTabWidget()
        self.tabs.setMinimumWidth(360)

        # Tab 1: Motor & Model Ayarları
        tab_engine = QWidget()
        form_engine = QFormLayout(tab_engine)
        form_engine.setContentsMargins(12, 12, 12, 12)
        form_engine.setSpacing(8)
        self.source = QComboBox(); self.source.addItems(["English"])
        self.target = QComboBox(); self.target.addItems(["Turkish", "German", "French", "Spanish", "Japanese", "Polish"])
        self.ocr_device = QComboBox(); self.ocr_device.addItems(["Otomatik", "CPU", "GPU"])
        self.subtitle_filter = QComboBox(); self.subtitle_filter.addItems(["Açık (Renk & Kontur)", "Kapalı"])
        self.profile = QComboBox(); self.profile.addItems(["Ekonomik", "Dengeli", "Performans"])
        self.hz = QSpinBox(); self.hz.setRange(1, 15); self.hz.setSuffix(" Hz")
        self.provider = QComboBox(); self.provider.addItems(["Ollama"])
        self.server = QLineEdit()
        self.refresh_models_btn = QPushButton("↻")
        self.refresh_models_btn.setFixedWidth(34)
        self.refresh_models_btn.setToolTip("Ollama modellerini yenile")
        server_row = QHBoxLayout()
        server_row.setSpacing(6)
        server_row.addWidget(self.server, 1)
        server_row.addWidget(self.refresh_models_btn)
        self.model = QComboBox(); self.model.setEditable(True)
        self.models_status = QLabel("Sunucu kontrol ediliyor…")
        form_engine.addRow("Kaynak dil", self.source); form_engine.addRow("Hedef dil", self.target)
        form_engine.addRow("OCR motoru", QLabel("PaddleOCR · İngilizce")); form_engine.addRow("OCR cihazı", self.ocr_device)
        form_engine.addRow("Altyazı filtresi", self.subtitle_filter)
        form_engine.addRow("LLM sağlayıcısı", self.provider)
        form_engine.addRow("LLM sunucusu", server_row); form_engine.addRow("Model", self.model)
        form_engine.addRow("Bağlantı", self.models_status); form_engine.addRow("Profil", self.profile)
        form_engine.addRow("Tarama sıklığı", self.hz)
        self.scan_hint = QLabel("Profiller: Eko 4 Hz · Dengeli 7 Hz · Performans 10 Hz")
        self.scan_hint.setStyleSheet("color:#8ea0b8;font-size:12px")
        form_engine.addRow(self.scan_hint)
        self.tabs.addTab(tab_engine, "⚙️ Motor & Model")

        # Tab 2: Altyazı Kutusu (BBox) Görünümü & Konum
        tab_overlay = QWidget()
        form_ov = QFormLayout(tab_overlay)
        form_ov.setContentsMargins(12, 12, 12, 12)
        form_ov.setSpacing(8)
        self.overlay_pos_combo = QComboBox()
        self.overlay_pos_combo.addItems([
            "Altyazı Üstü (Akıllı Otomatik)",
            "Ekran Altı",
            "Ekran Üstü",
            "Özel Konum (X, Y)",
        ])
        form_ov.addRow("Kutu konumu", self.overlay_pos_combo)

        coords_row = QHBoxLayout()
        self.overlay_x_spin = QSpinBox(); self.overlay_x_spin.setRange(-5000, 25000); self.overlay_x_spin.setPrefix("X: ")
        self.overlay_y_spin = QSpinBox(); self.overlay_y_spin.setRange(-5000, 25000); self.overlay_y_spin.setPrefix("Y: ")
        coords_row.addWidget(self.overlay_x_spin); coords_row.addWidget(self.overlay_y_spin)
        form_ov.addRow("Özel koordinat", coords_row)

        size_row = QHBoxLayout()
        self.overlay_width_spin = QSpinBox()
        self.overlay_width_spin.setRange(250, 2500); self.overlay_width_spin.setSingleStep(20)
        self.overlay_width_spin.setValue(860); self.overlay_width_spin.setPrefix("G: "); self.overlay_width_spin.setSuffix(" px")
        self.overlay_height_spin = QSpinBox()
        self.overlay_height_spin.setRange(45, 800); self.overlay_height_spin.setSingleStep(10)
        self.overlay_height_spin.setValue(95); self.overlay_height_spin.setPrefix("Y: "); self.overlay_height_spin.setSuffix(" px")
        size_row.addWidget(self.overlay_width_spin); size_row.addWidget(self.overlay_height_spin)
        form_ov.addRow("Kutu boyutu", size_row)

        slider_row = QHBoxLayout()
        self.overlay_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.overlay_opacity_slider.setRange(0, 100); self.overlay_opacity_slider.setValue(40)
        self.overlay_opacity_label = QLabel("%40")
        self.overlay_opacity_label.setFixedWidth(36)
        slider_row.addWidget(self.overlay_opacity_slider); slider_row.addWidget(self.overlay_opacity_label)
        form_ov.addRow("Kutu şeffaflığı", slider_row)

        self.overlay_font_combo = QFontComboBox()
        form_ov.addRow("Yazı fontu", self.overlay_font_combo)

        self.overlay_font_size_spin = QSpinBox()
        self.overlay_font_size_spin.setRange(12, 36); self.overlay_font_size_spin.setValue(17); self.overlay_font_size_spin.setSuffix(" pt")
        form_ov.addRow("Yazı boyutu", self.overlay_font_size_spin)

        self.preview_btn = QPushButton("🎯 Konumu Ekranda Taşı / Önizle")
        form_ov.addRow(self.preview_btn)

        self.reset_bbox_btn = QPushButton("↺ Kutu Ayarlarını Varsayılana Sıfırla")
        form_ov.addRow(self.reset_bbox_btn)

        self.preview_hint = QLabel("💡 'Konumu Ekranda Taşı' butonuna basarak altyazı kutusunu fareyle ekranda sürükleyip sağ/alt köşeden boyutlandırabilirsiniz.")
        self.preview_hint.setWordWrap(True)
        self.preview_hint.setStyleSheet("color:#8ea0b8;font-size:12px")
        form_ov.addRow(self.preview_hint)
        self.tabs.addTab(tab_overlay, "🎨 Altyazı Kutusu (BBox)")

        columns.addWidget(self.tabs, 2)

        center = QVBoxLayout()
        center.setSpacing(6)
        lbl_ocr = QLabel("ALGILANAN METİN (OCR)"); lbl_ocr.setObjectName("sectionTitle")
        lbl_tr = QLabel("AKTİF ÇEVİRİ"); lbl_tr.setObjectName("sectionTitle")
        lbl_hist = QLabel("SON ÇEVİRİ GEÇMİŞİ"); lbl_hist.setObjectName("sectionTitle")

        self.ocr_text = QPlainTextEdit(); self.ocr_text.setReadOnly(True)
        self.ocr_text.setPlaceholderText("Ekrandan okunan İngilizce altyazı")
        self.ocr_text.setMinimumHeight(74); self.ocr_text.setMaximumHeight(115)

        self.translated = QPlainTextEdit(); self.translated.setReadOnly(True)
        self.translated.setObjectName("translatedBox")
        self.translated.setPlaceholderText("Aktif Türkçe çeviri")
        self.translated.setMinimumHeight(74); self.translated.setMaximumHeight(115)

        self.history_view = QPlainTextEdit(); self.history_view.setReadOnly(True)
        self.history_view.setObjectName("historyBox")
        self.history_view.setMaximumBlockCount(120)
        self.history_view.setPlaceholderText("Oturum boyunca çevrilen altyazılar burada birikir…")

        center.addWidget(lbl_ocr); center.addWidget(self.ocr_text, 1)
        center.addWidget(lbl_tr); center.addWidget(self.translated, 1)
        center.addWidget(lbl_hist); center.addWidget(self.history_view, 2)
        columns.addLayout(center, 3)
        root.addLayout(columns, 1)

        stats = QGroupBox("Performans ve Durum")
        grid = QGridLayout(stats)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        self.status = QLabel("Hazır"); self.status.setObjectName("statusBadge")
        self.capture_metric = QLabel("Yakalama: —")
        self.change_metric = QLabel("Değişim: —"); self.ocr_metric = QLabel("OCR: —")
        self.first_metric = QLabel("İlk token: —"); self.total_metric = QLabel("Çeviri: —")
        self.overlay_metric = QLabel("Overlay: —")
        self.cache_metric = QLabel("Önbellek: 0% (0/0)"); self.gpu_metric = QLabel("GPU belleği: ölçülemiyor")
        self.error = QLabel(""); self.error.setWordWrap(True); self.error.setStyleSheet("color:#ff7878")
        metric_widgets = (
            self.status, self.capture_metric, self.change_metric,
            self.ocr_metric, self.first_metric, self.total_metric,
            self.overlay_metric, self.cache_metric, self.gpu_metric,
        )
        for i, w in enumerate(metric_widgets):
            if w is not self.status:
                w.setObjectName("metricBadge")
            grid.addWidget(w, i // 3, i % 3)
        grid.addWidget(self.error, 3, 0, 1, 3)
        root.addWidget(stats)
        self.setCentralWidget(central)
        self.setStyleSheet(
            "QMainWindow,QWidget{background:#171a20;color:#e8eaf0} "
            "QGroupBox{border:1px solid #353b47;border-radius:8px;margin-top:8px;padding:12px 10px 10px 10px;font-weight:600;color:#cfd6e3} "
            "QGroupBox::title{subcontrol-origin:margin;left:10px;padding:0 6px} "
            "QTabWidget::pane{border:1px solid #353b47;border-radius:8px;background:#171a20;padding:4px} "
            "QTabBar::tab{background:#222731;color:#b0b8c4;padding:8px 14px;border-top-left-radius:6px;border-top-right-radius:6px;margin-right:2px} "
            "QTabBar::tab:selected{background:#303947;color:#ffffff;font-weight:bold;border-bottom:2px solid #5090ff} "
            "QLineEdit,QComboBox,QFontComboBox,QSpinBox,QPlainTextEdit{background:#222731;border:1px solid #3b4350;border-radius:6px;padding:6px;color:#f4f5f8} "
            "QPlainTextEdit#translatedBox{border:1px solid #44689c;background:#1d2430;font-size:16px;font-weight:600} "
            "QPlainTextEdit#historyBox{font-size:13px;color:#cfd6e3;background:#1b1f27} "
            "QLabel#sectionTitle{font-size:11px;font-weight:bold;color:#8ea0b8;letter-spacing:0.6px;margin-top:2px} "
            "QLabel#regionBadge{background:#202734;border:1px solid #3b4a62;border-radius:6px;padding:6px 10px;color:#bfe3ff;font-size:12px;font-weight:600} "
            "QLabel#statusBadge{background:#1f2c27;border:1px solid #335c4a;border-radius:5px;padding:4px 8px;color:#b7f0d2;font-size:12px;font-weight:600} "
            "QLabel#metricBadge{background:#20252f;border:1px solid #303846;border-radius:5px;padding:4px 8px;color:#d6dce6;font-size:12px} "
            "QSlider::groove:horizontal{height:6px;background:#222731;border:1px solid #3b4350;border-radius:3px} "
            "QSlider::handle:horizontal{background:#5090ff;border:1px solid #6ba0ff;width:14px;margin:-5px 0;border-radius:7px} "
            "QPushButton{background:#303947;border:0;border-radius:6px;padding:9px 14px;font-weight:600} "
            "QPushButton:hover{background:#435269} "
            "QPushButton:disabled{background:#252a33;color:#6c7585} "
            "QPushButton#startBtn{background:#247a53;color:#ffffff} "
            "QPushButton#startBtn:hover{background:#2d9465} "
            "QPushButton#startBtn:disabled{background:#1e3a2d;color:#6c8f7d} "
            "QPushButton#stopBtn{background:#8c343a;color:#ffffff} "
            "QPushButton#stopBtn:hover{background:#a64047} "
            "QPushButton#stopBtn:disabled{background:#252a33;color:#6c7585} "
            "QPlainTextEdit{font-size:15px}"
        )
        self.start_btn.clicked.connect(self.start_capture)
        self.stop_btn.clicked.connect(self.stop_capture)
        self.region_btn.clicked.connect(self.select_region)
        self.clear_btn.clicked.connect(self.clear_all_subtitles)
        self.overlay_toggle.toggled.connect(self.toggle_overlay)
        self.server.editingFinished.connect(self.refresh_models)
        self.refresh_models_btn.clicked.connect(self.refresh_models)
        self.profile.currentTextChanged.connect(self.apply_profile)

        # Connect appearance & general setting persistence
        self.target.currentTextChanged.connect(lambda *_: self.save_settings())
        self.ocr_device.currentTextChanged.connect(lambda *_: self.save_settings())
        self.subtitle_filter.currentTextChanged.connect(self._on_runtime_engine_changed)
        self.hz.valueChanged.connect(self._on_runtime_engine_changed)
        self.model.currentTextChanged.connect(lambda *_: self.save_settings())

        self.overlay_pos_combo.currentTextChanged.connect(self._on_overlay_pos_changed)
        self.overlay_x_spin.valueChanged.connect(self._on_overlay_custom_coords_changed)
        self.overlay_y_spin.valueChanged.connect(self._on_overlay_custom_coords_changed)
        self.overlay_width_spin.valueChanged.connect(self._on_overlay_size_changed)
        self.overlay_height_spin.valueChanged.connect(self._on_overlay_size_changed)
        self.overlay_opacity_slider.valueChanged.connect(self._on_overlay_opacity_changed)
        self.overlay_font_combo.currentFontChanged.connect(self._on_overlay_font_changed)
        self.overlay_font_size_spin.valueChanged.connect(self._on_overlay_font_size_changed)
        self.preview_btn.clicked.connect(self.toggle_overlay_preview)
        self.reset_bbox_btn.clicked.connect(self.reset_bbox_defaults)
        self.overlay.position_moved.connect(self._on_overlay_dragged)
        self.overlay.size_changed.connect(self._on_overlay_resized)

    def _on_overlay_pos_changed(self, text):
        if getattr(self, "_loading_settings", False):
            return
        mode_map = {
            "Altyazı Üstü (Akıllı Otomatik)": "above_subtitle",
            "Ekran Altı": "screen_bottom",
            "Ekran Üstü": "screen_top",
            "Özel Konum (X, Y)": "custom",
        }
        mode = mode_map.get(text, "above_subtitle")
        if mode == "custom" and (not self.overlay.custom_pos or self.overlay.custom_pos == (0, 0)):
            geo = self.overlay.geometry()
            cx, cy = geo.x(), geo.y()
            self.overlay_x_spin.blockSignals(True)
            self.overlay_y_spin.blockSignals(True)
            self.overlay_x_spin.setValue(cx)
            self.overlay_y_spin.setValue(cy)
            self.overlay_x_spin.blockSignals(False)
            self.overlay_y_spin.blockSignals(False)
        self.overlay.set_appearance(
            position_mode=mode,
            custom_x=self.overlay_x_spin.value(),
            custom_y=self.overlay_y_spin.value(),
        )
        self.save_settings()

    def _on_overlay_custom_coords_changed(self):
        if getattr(self, "_loading_settings", False):
            return
        x = self.overlay_x_spin.value()
        y = self.overlay_y_spin.value()
        self.overlay.set_appearance(position_mode="custom", custom_x=x, custom_y=y)
        if self.overlay_pos_combo.currentText() != "Özel Konum (X, Y)":
            self.overlay_pos_combo.blockSignals(True)
            self.overlay_pos_combo.setCurrentText("Özel Konum (X, Y)")
            self.overlay_pos_combo.blockSignals(False)
        self.save_settings()

    def _on_overlay_size_changed(self):
        if getattr(self, "_loading_settings", False):
            return
        self.overlay.set_appearance(
            bbox_width=self.overlay_width_spin.value(),
            bbox_height=self.overlay_height_spin.value(),
        )
        self.save_settings()

    def _on_overlay_resized(self, w, h):
        self.overlay_width_spin.blockSignals(True)
        self.overlay_height_spin.blockSignals(True)
        self.overlay_width_spin.setValue(w)
        self.overlay_height_spin.setValue(h)
        self.overlay_width_spin.blockSignals(False)
        self.overlay_height_spin.blockSignals(False)
        self.save_settings()

    def _on_overlay_opacity_changed(self, val):
        if getattr(self, "_loading_settings", False):
            return
        self.overlay_opacity_label.setText(f"%{val}")
        self.overlay.set_appearance(opacity_pct=val)
        self.save_settings()

    def _on_overlay_font_changed(self, font):
        if getattr(self, "_loading_settings", False):
            return
        self.overlay.set_appearance(font_family=font.family())
        self.save_settings()

    def _on_overlay_font_size_changed(self, val):
        if getattr(self, "_loading_settings", False):
            return
        self.overlay.set_appearance(font_size=val)
        self.save_settings()

    def toggle_overlay_preview(self):
        if getattr(self.overlay, "_preview_mode", False):
            self.overlay.stop_preview_mode()
            self.preview_btn.setText("🎯 Konumu Ekranda Taşı / Önizle")
            self.status.setText("Altyazı konumu kilitlendi.")
            self.save_settings()
        else:
            self.overlay.start_preview_mode()
            self.preview_btn.setText("✓ Konumu Kilitle ve Tamamla")
            self.status.setText("Önizleme aktif: Kutuyu sürükleyerek taşıyın veya köşeden boyutlandırın.")

    def _on_overlay_dragged(self, x, y):
        self.overlay_x_spin.blockSignals(True)
        self.overlay_y_spin.blockSignals(True)
        self.overlay_x_spin.setValue(x)
        self.overlay_y_spin.setValue(y)
        self.overlay_x_spin.blockSignals(False)
        self.overlay_y_spin.blockSignals(False)

        if self.overlay_pos_combo.currentText() != "Özel Konum (X, Y)":
            self.overlay_pos_combo.blockSignals(True)
            self.overlay_pos_combo.setCurrentText("Özel Konum (X, Y)")
            self.overlay_pos_combo.blockSignals(False)
        self.save_settings()

    def load_settings(self):
        self._loading_settings = True
        try:
            s = dict(self.settings)
            self.provider.setCurrentText("Ollama")
            self.target.setCurrentText(s.get("target_language", "Turkish"))
            self.ocr_device.setCurrentText({"auto": "Otomatik", "cpu": "CPU", "gpu": "GPU"}.get(s.get("ocr_device"), "Otomatik"))
            filter_val = s.get("subtitle_filter", "color_contour")
            self.subtitle_filter.setCurrentText("Açık (Renk & Kontur)" if filter_val == "color_contour" else "Kapalı")
            self.server.setText(s.get("server_url", "http://127.0.0.1:11434"))
            if not self.model.currentText():
                self.model.setEditText(s.get("model", "ali6parmak/hy-mt1.5:1.8b"))
            self.profile.setCurrentText(s.get("profile", "Dengeli"))
            self.hz.setValue(s.get("scan_hz", 7))
            self.overlay_toggle.setChecked(s.get("overlay_enabled", True))
            s_info = session_info()
            if s_info.get("session") == "windows":
                self.models_status.setText("Windows oturumu")
            elif s_info.get("global_overlay_supported"):
                self.models_status.setText("X11 oturumu")
            else:
                self.models_status.setText("Global overlay kısıtlı · ayrı pencere")

            # Overlay appearance, size & position
            pos_mode = s.get("overlay_position", "above_subtitle")
            pos_map = {
                "above_subtitle": "Altyazı Üstü (Akıllı Otomatik)",
                "screen_bottom": "Ekran Altı",
                "screen_top": "Ekran Üstü",
                "custom": "Özel Konum (X, Y)",
            }
            self.overlay_pos_combo.setCurrentText(pos_map.get(pos_mode, "Altyazı Üstü (Akıllı Otomatik)"))

            bbox_w = int(s.get("overlay_width", 860))
            bbox_h = int(s.get("overlay_height", 95))
            self.overlay_width_spin.setValue(bbox_w)
            self.overlay_height_spin.setValue(bbox_h)

            custom_x = int(s.get("overlay_custom_x", 0))
            custom_y = int(s.get("overlay_custom_y", 0))
            if custom_x == 0 and custom_y == 0:
                if self.region:
                    reg_left = self.region.get("left", 0)
                    reg_top = self.region.get("top", 0)
                    reg_w = self.region.get("width", 860)
                    custom_x = reg_left + (reg_w - bbox_w) // 2
                    custom_y = max(10, reg_top - bbox_h - 10)
                else:
                    custom_x, custom_y = 1500, 700
            self.overlay_x_spin.setValue(custom_x)
            self.overlay_y_spin.setValue(custom_y)

            op_val = int(s.get("overlay_opacity", 40))
            self.overlay_opacity_slider.setValue(op_val)
            self.overlay_opacity_label.setText(f"%{op_val}")

            font_fam = s.get("overlay_font_family", "Ubuntu Sans")
            if font_fam:
                self.overlay_font_combo.setCurrentFont(QFont(font_fam))

            font_sz = int(s.get("overlay_font_size", 17))
            self.overlay_font_size_spin.setValue(font_sz)

            if self.region:
                self.overlay.set_anchor({
                    "left": self.region["left"],
                    "top": self.region["top"],
                    "width": self.region["width"],
                    "height": self.region["height"],
                    "region_limit": dict(self.region),
                })

            self.overlay.set_appearance(
                position_mode=pos_mode,
                opacity_pct=op_val,
                font_family=self.overlay_font_combo.currentFont().family(),
                font_size=font_sz,
                custom_x=custom_x,
                custom_y=custom_y,
                bbox_width=bbox_w,
                bbox_height=bbox_h,
            )
        finally:
            self._loading_settings = False
        self._update_region_badge()

    def _update_region_badge(self):
        if self.region:
            scr = (self.region.get("screen") or {}).get("name") or (self.selected_screen or {}).get("name") or "Ekran"
            w = self.region.get("width", 0)
            h = self.region.get("height", 0)
            l = self.region.get("left", 0)
            t = self.region.get("top", 0)
            self.region_badge.setText(f"📍 {scr} · {w}×{h}")
            self.region_badge.setToolTip(f"Aktif yakalama bölgesi: {scr}\nKonum: ({l}, {t}) · Boyut: {w}×{h} px")
        else:
            self.region_badge.setText("📍 Bölge seçilmedi")
            self.region_badge.setToolTip("Henüz altyazı bölgesi seçilmedi")

    def _on_runtime_engine_changed(self, *_):
        if getattr(self, "_loading_settings", False):
            return
        self.save_settings()
        if self.capture and self.capture.isRunning():
            self.capture.hz = max(1, min(15, int(self.hz.value())))
            new_filter = "color_contour" if self.subtitle_filter.currentText().startswith("Açık") else "none"
            if getattr(self.capture, "filter_mode", None) != new_filter:
                self.capture.filter_mode = new_filter
                if getattr(self.capture, "detector", None):
                    self.capture.detector.previous = None

    def clear_all_subtitles(self):
        self.ocr_text.clear()
        self.translated.clear()
        self.overlay_texts = []
        self.overlay.clear_now()
        self._sync_capture_masks()
        self.status.setText("Ekran ve kutu temizlendi")

    def reset_bbox_defaults(self):
        default_w, default_h = 860, 95
        if self.region:
            scr = self.region.get("screen") or {}
            scr_top = scr.get("y", 0)
            cx = self.region.get("left", 0) + (self.region.get("width", default_w) - default_w) // 2
            cy = max(scr_top + 10, self.region.get("top", 0) - default_h - 10)
        else:
            cx, cy = 1500, 700

        self._loading_settings = True
        try:
            self.overlay_pos_combo.setCurrentText("Altyazı Üstü (Akıllı Otomatik)")
            self.overlay_width_spin.setValue(default_w)
            self.overlay_height_spin.setValue(default_h)
            self.overlay_x_spin.setValue(cx)
            self.overlay_y_spin.setValue(cy)
            self.overlay_opacity_slider.setValue(40)
            self.overlay_opacity_label.setText("%40")
            self.overlay_font_combo.setCurrentFont(QFont("Ubuntu Sans"))
            self.overlay_font_size_spin.setValue(17)
        finally:
            self._loading_settings = False

        self.overlay.set_appearance(
            position_mode="above_subtitle",
            opacity_pct=40,
            font_family=self.overlay_font_combo.currentFont().family(),
            font_size=17,
            custom_x=cx,
            custom_y=cy,
            bbox_width=default_w,
            bbox_height=default_h,
        )
        self.save_settings()
        self.status.setText("Altyazı kutusu ayarları varsayılana sıfırlandı.")

    def save_settings(self):
        if getattr(self, "_loading_settings", False):
            return
        pos_mode_map = {
            "Altyazı Üstü (Akıllı Otomatik)": "above_subtitle",
            "Ekran Altı": "screen_bottom",
            "Ekran Üstü": "screen_top",
            "Özel Konum (X, Y)": "custom",
        }
        pos_mode = pos_mode_map.get(self.overlay_pos_combo.currentText(), "above_subtitle")
        self.settings.update(
            provider="ollama", source_language=self.source.currentText(), temperature=0, streaming=True,
            keep_alive=-1, target_language=self.target.currentText(),
            ocr_device={"Otomatik":"auto","CPU":"cpu","GPU":"gpu"}.get(self.ocr_device.currentText(), "auto"),
            subtitle_filter="color_contour" if self.subtitle_filter.currentText().startswith("Açık") else "none",
            server_url=self.server.text().rstrip("/"), model=self.model.currentText(),
            scan_hz=self.hz.value(), profile=self.profile.currentText(),
            overlay_enabled=self.overlay_toggle.isChecked(),
            overlay_position=pos_mode,
            overlay_custom_x=self.overlay_x_spin.value(),
            overlay_custom_y=self.overlay_y_spin.value(),
            overlay_width=self.overlay_width_spin.value(),
            overlay_height=self.overlay_height_spin.value(),
            overlay_opacity=self.overlay_opacity_slider.value(),
            overlay_font_family=self.overlay_font_combo.currentFont().family(),
            overlay_font_size=self.overlay_font_size_spin.value()
        )
        if self.selected_screen:
            self.settings["selected_screen"] = self.selected_screen
        SETTINGS.write_text(json.dumps(self.settings, ensure_ascii=False, indent=2), encoding="utf-8")

    def apply_profile(self, name):
        value = {"Ekonomik": 4, "Dengeli": 7, "Performans": 10}.get(name)
        if value:
            self.hz.setValue(value)

    def select_region(self):
        screens = QApplication.screens()
        if not screens:
            self.show_error("Ekran bulunamadı. X11 DISPLAY erişimini kontrol edin.")
            return
        if len(screens) > 1:
            picker = ScreenPicker(screens, self)
            picker.select_saved_screen(self.selected_screen)
            if picker.exec() != ScreenPicker.DialogCode.Accepted:
                return
            screen = picker.selected_screen()
        else:
            screen = screens[0]
        if screen is not None:
            geo = screen.geometry()
            self.selected_screen = {"name": screen.name(), "x": geo.x(), "y": geo.y(),
                                    "width": geo.width(), "height": geo.height()}
            self.settings["selected_screen"] = self.selected_screen
            self.save_settings()
        self.selector = RegionSelector(screen)
        self.selector.selected.connect(self.region_selected)
        self.selector.show()
        self.selector.activateWindow()

    def region_selected(self, region):
        self.region = dict(region)
        self.selected_screen = region.get("screen", self.selected_screen)
        self.settings["region"] = self.region

        bbox_w = self.overlay_width_spin.value()
        bbox_h = self.overlay_height_spin.value()
        scr = region.get("screen") or {}
        scr_top = scr.get("y", 0)
        new_cx = region["left"] + (region["width"] - bbox_w) // 2
        new_cy = max(scr_top + 10, region["top"] - bbox_h - 10)
        self.overlay_x_spin.blockSignals(True)
        self.overlay_y_spin.blockSignals(True)
        self.overlay_x_spin.setValue(new_cx)
        self.overlay_y_spin.setValue(new_cy)
        self.overlay_x_spin.blockSignals(False)
        self.overlay_y_spin.blockSignals(False)
        self.overlay.set_appearance(custom_x=new_cx, custom_y=new_cy)

        self.overlay.set_anchor({
            "left": region["left"],
            "top": region["top"],
            "width": region["width"],
            "height": region["height"],
            "region_limit": dict(region),
        })

        screen_name = scr.get("name", "Ekran")
        self._update_region_badge()
        if self.capture and self.capture.isRunning():
            self.capture.set_region(region)
            self.translation_queue.clear()
            self.recent_sources.clear()
            self.ocr_text.clear()
            self.overlay.clear_now()
            self.status.setText(f"Bölge güncellendi ({screen_name}): {region['width']}×{region['height']} — okuma yeni ekrana taşındı")
        else:
            self.status.setText(f"Bölge seçildi ({screen_name}): {region['width']}×{region['height']}")
        self.save_settings()

    def refresh_models(self):
        self.server.setText(self.server.text().strip())
        try:
            ids = OllamaProvider(self.server.text()).models()
            selected = self.model.currentText() or self.settings.get("model", "")
            self.model.blockSignals(True)
            self.model.clear()
            self.model.addItems(ids)
            if selected:
                self.model.setCurrentText(selected)
            self.model.blockSignals(False)
            self.models_status.setText(f"Ollama bağlı · {len(ids)} model" if ids else "Bağlı · model listesi boş")
            self.error.setText("")
        except Exception as exc:
            self.model.blockSignals(False)
            self.models_status.setText("Bağlantı yok")
            self.error.setText(f"LLM sunucusuna erişilemiyor: {exc}")

    def start_capture(self):
        if getattr(self.overlay, "_preview_mode", False):
            self.overlay.stop_preview_mode()
            self.preview_btn.setText("🎯 Konumu Ekranda Taşı / Önizle")
        self.save_settings()
        region = self.settings.get("region")
        if not region:
            self.show_error("Önce altyazı bölgesini seçin.")
            return
        if self.capture and self.capture.isRunning():
            self.capture.set_region(region)
            self.capture.hz = max(1, min(15, int(self.hz.value())))
            return
        model = self.model.currentText().strip()
        if not model:
            self.show_error("Ollama model adı girin veya model listesinden seçin.")
            return
        self.status.setText(f"Ollama {model} belleğe alınıyor…")
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.warm_worker = WarmWorker(self.server.text(), model)
        self.warm_worker.ready.connect(lambda: (self.workers.remove(self.warm_worker) if self.warm_worker in self.workers else None, self._start_ocr(region)))
        self.warm_worker.failed.connect(lambda msg: (self.workers.remove(self.warm_worker) if self.warm_worker in self.workers else None, self.start_btn.setEnabled(True), self.stop_btn.setEnabled(False), self.show_error(msg)))
        self.warm_worker.start(); self.workers.append(self.warm_worker)

    def _start_ocr(self, region):
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.error.setText(""); self.status.setText("PaddleOCR hazırlanıyor…")
        self.model_worker = ModelWorker(self.settings.get("ocr_device", "auto"))
        self.model_worker.ready.connect(lambda reader: (self.workers.remove(self.model_worker) if self.model_worker in self.workers else None, self._start_capture_with_reader(reader, region)))
        self.model_worker.failed.connect(lambda msg: (self.workers.remove(self.model_worker) if self.model_worker in self.workers else None, self.start_btn.setEnabled(True), self.stop_btn.setEnabled(False), self.show_error(msg)))
        self.model_worker.start(); self.workers.append(self.model_worker)

    def _sync_capture_masks(self):
        if not self.capture:
            return
        rects = []
        for o in self.overlays:
            if o.isVisible():
                g = o.geometry()
                rects.append((g.x(), g.y(), g.width(), g.height()))
        self.capture.set_overlay_rects(rects)
        self.capture.set_active_translations(list(self.recent_translations))

    def _start_capture_with_reader(self, reader, region):
        if sys.platform.startswith("linux"):
            try:
                self.capture_source = X11WindowCapture()
                self.capture_source.set_overlays([int(self.overlay.winId())])
            except Exception:
                self.capture_source = None
        else:
            self.capture_source = None
        filter_mode = self.settings.get("subtitle_filter", "color_contour")
        self.capture = CaptureWorker(region, self.hz.value(), self.settings.get("stability_ms", 120),
                                     source_lang=self.source.currentText(), filter_mode=filter_mode)
        self.capture.set_reader(reader)
        self.capture.set_capture_source(self.capture_source)
        self._sync_capture_masks()
        self.capture.recognized.connect(self.on_recognized)
        self.capture.cleared.connect(self.clear_subtitle)
        self.capture.failed.connect(self.show_error)
        self.capture.failed.connect(lambda _message: (self.start_btn.setEnabled(True), self.stop_btn.setEnabled(False)))
        self.capture.start(); self.workers.append(self.capture)
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.status.setText("Ekran taranıyor")

    def on_recognized(self, text, capture_ms, change_ms, ocr_ms, gpu_mb, text_bounds, is_new, blocks):
        if not text or not is_english_text(text):
            return
        self.ocr_text.setPlainText(text)
        if text_bounds and self.region:
            self.overlay.set_anchor({
                "left": self.region["left"] + int(text_bounds[0]),
                "top": self.region["top"] + int(text_bounds[1]),
                "width": max(1, int(text_bounds[2] - text_bounds[0])),
                "height": max(1, int(text_bounds[3] - text_bounds[1])),
                "region_limit": dict(self.region),
            })
        self.capture_metric.setText(f"Yakalama: {capture_ms:.1f} ms")
        self.change_metric.setText(f"Değişim: {change_ms:.1f} ms")
        self.ocr_metric.setText(f"OCR: {ocr_ms:.0f} ms")
        current_blocks = []
        rw = self.region.get("width", 0) if self.region else 0
        rh = self.region.get("height", 0) if self.region else 0
        for block in blocks:
            block_text = block.get("text", "").strip()
            box = block.get("bounds")
            if (
                block_text
                and box
                and is_english_text(block_text)
                and is_dialogue_subtitle(block_text, box, rw, rh)
                and not is_translation_echo(block_text, self.recent_translations, self.source.currentText())
            ):
                current_blocks.append((block_text, {
                    "left": self.region["left"] + int(box[0]),
                    "top": self.region["top"] + int(box[1]),
                    "width": max(1, int(box[2] - box[0])),
                    "height": max(1, int(box[3] - box[1])),
                    "region_limit": dict(self.region),
                }))
        # Refresh source positions every OCR frame, even when the text is unchanged.
        # This makes overlays follow moving captions instead of freezing at their first box.
        unmatched = set(range(len(current_blocks)))
        for index, source in enumerate(self.overlay_sources):
            if index >= len(self.overlays) or not self.overlays[index].isVisible():
                continue
            match_index, match_score = None, 0.0
            for candidate_index in unmatched:
                candidate_text, _ = current_blocks[candidate_index]
                score = SequenceMatcher(None, source.casefold(), candidate_text.casefold()).ratio()
                if score > match_score:
                    match_index, match_score = candidate_index, score
            if match_index is not None and match_score >= .58:
                unmatched.remove(match_index)
                _, anchor = current_blocks[match_index]
                self.overlay_anchors[index] = anchor
                self.overlays[index].set_anchor(anchor)
                self.overlays[index].raise_()
        if not is_new:
            return
        if not current_blocks:
            return

        # Replace stale source geometry on each new subtitle generation.
        self.current_subtitle_anchor = None
        if text_bounds and self.region:
            self.current_subtitle_anchor = {
                "left": self.region["left"] + int(text_bounds[0]),
                "top": self.region["top"] + int(text_bounds[1]),
                "width": max(1, int(text_bounds[2] - text_bounds[0])),
                "height": max(1, int(text_bounds[3] - text_bounds[1])),
                "region_limit": dict(self.region),
            }

        primary_source = current_blocks[0][0]
        c_new = " ".join(re.findall(r"[a-z0-9]+", primary_source.casefold()))
        c_new_words = len(c_new.split())
        now_ts = time.monotonic()
        matched_recent = False
        for i in range(len(self.recent_sources) - 1, -1, -1):
            c_prev, prev_ts = self.recent_sources[i]
            if c_new_words == 1:
                if i != len(self.recent_sources) - 1 or now_ts - prev_ts > 1.5:
                    continue
            elif now_ts - prev_ts > 6.0:
                continue
            c_prev_words = len(c_prev.split())
            is_genuine_ext = (
                i == len(self.recent_sources) - 1
                and c_prev_words >= 2
                and c_new.startswith(c_prev + " ")
                and (len(c_new) - len(c_prev) >= 3)
            )
            if is_genuine_ext:
                self.recent_sources[i][0] = c_new
                self.recent_sources[i][1] = now_ts
                matched_recent = True
                break
            if (
                c_new == c_prev
                or (
                    c_new_words >= 2
                    and c_prev_words >= 2
                    and not (c_new.startswith(c_prev + " ") and len(c_new) - len(c_prev) >= 3)
                    and (
                        SequenceMatcher(None, c_new, c_prev).ratio() >= 0.85
                        or (len(c_new) >= 12 and len(c_new) < len(c_prev) and c_new in c_prev)
                    )
                )
            ):
                self.recent_sources[i][1] = now_ts
                return
        if not matched_recent and c_new:
            self.recent_sources.append([c_new, now_ts])

        if self.active_translation and self.active_translation.isRunning():
            c_act = " ".join(re.findall(r"[a-z0-9]+", self.active_source_text.casefold()))
            if self.translation_queue:
                _, last_q_src = self.translation_queue[-1]
                c_last_q = " ".join(re.findall(r"[a-z0-9]+", last_q_src.casefold()))
                if c_new == c_last_q:
                    return
                if (
                    len(c_last_q.split()) >= 2
                    and (
                        c_new.startswith(c_last_q + " ")
                        or (c_new_words >= 2 and SequenceMatcher(None, c_new, c_last_q).ratio() >= 0.82)
                    )
                ):
                    self.translation_queue[-1] = (current_blocks, primary_source)
                    return
            elif c_new == c_act:
                return

            self.translation_queue.append((current_blocks, primary_source))
            while len(self.translation_queue) > 5:
                self.translation_queue.popleft()
            return

        self._launch_translation(current_blocks, primary_source)

    def _launch_translation(self, blocks, source_text):
        self.generation += 1
        self.active_source_text = source_text
        self.overlay_sources = [b[0] for b in blocks]
        self.overlay_anchors = [b[1] for b in blocks]

        worker = TranslationWorker(
            self.generation, blocks, self.target.currentText(),
            self.server.text(), self.model.currentText(), (), self.cache,
            source=self.source.currentText()
        )
        worker.completed.connect(self.on_translation)
        worker.failed.connect(self.translation_failed)
        worker.finished.connect(lambda w=worker: self.translation_finished(w))
        self.active_translation = worker
        worker.start()
        self.workers.append(worker)

    def translation_finished(self, worker):
        if worker in self.workers:
            self.workers.remove(worker)
        if self.active_translation is worker:
            self.active_translation = None
            if self.translation_queue and self.capture and self.capture.isRunning():
                next_blocks, next_source = self.translation_queue.popleft()
                self._launch_translation(next_blocks, next_source)

    def on_translation(self, generation, results, first_ms, total_ms, cache_hit):
        if generation <= self._stopped_generation:
            return
        overlay_started = time.perf_counter()

        self.overlay_texts = [r[0] for r in results]
        self.overlay_anchors = [r[1] for r in results]

        if results and self.overlay_toggle.isChecked():
            pushed = False
            for text, anchor in results:
                if text:
                    self.overlay.push_translation(text, anchor)
                    self.recent_translations.append(text)
                    pushed = True
            if pushed:
                self.overlay.raise_above()

        self._sync_capture_masks()
        full_translation = "\n\n".join(t for t, _ in results if t)
        self.translated.setPlainText(full_translation)
        src_txt = (self.active_source_text or self.ocr_text.toPlainText() or "").strip().replace("\n", " ")
        tr_txt = full_translation.strip().replace("\n", " ")
        if tr_txt:
            ts = time.strftime("%H:%M:%S")
            entry = f"[{ts}] {src_txt}  ➔  {tr_txt}" if src_txt else f"[{ts}] {tr_txt}"
            self.history_view.appendPlainText(entry)
            sb = self.history_view.verticalScrollBar()
            if sb:
                sb.setValue(sb.maximum())
        self.overlay_metric.setText(f"Overlay: {(time.perf_counter()-overlay_started)*1000:.1f} ms")
        self.first_metric.setText("İlk token: önbellek" if cache_hit else f"İlk token: {first_ms:.0f} ms" if first_ms >= 0 else "İlk token: —")
        self.total_metric.setText("Çeviri: önbellek" if cache_hit else f"Çeviri: {total_ms:.0f} ms")
        if self.ocr_text.toPlainText():
            self.history.append(self.ocr_text.toPlainText())
        self.cache_hits = getattr(self, "cache_hits", 0) + int(cache_hit)
        self.cache_misses = getattr(self, "cache_misses", 0) + int(not cache_hit)
        total = self.cache_hits + self.cache_misses
        self.cache_metric.setText(f"Önbellek: {100*self.cache_hits/total:.0f}% ({self.cache_hits}/{total})")

    def translation_failed(self, generation, message):
        if generation > self._stopped_generation:
            self.show_error(f"Çeviri başarısız: {message}")

    def clear_subtitle(self):
        # Clear OCR input monitor, but let the overlay fade gracefully on its inactivity timer
        self.ocr_text.clear()
        self._sync_capture_masks()

    def stop_capture(self):
        if self.capture:
            self.capture.stop()
            self.capture.wait(3000)
            if self.capture in self.workers:
                self.workers.remove(self.capture)
            self.capture = None
        self._stopped_generation = self.generation
        self.generation += 1
        self.pending_translation = None
        self.translation_queue.clear()
        self.recent_sources.clear()
        self.active_source_text = ""
        if self.active_translation:
            self.active_translation.cancel()
        self.overlay_texts = []
        for overlay in self.overlays:
            overlay.clear_now()
        self._sync_capture_masks()
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.status.setText("Durduruldu")

    def toggle_overlay(self, checked):
        for overlay in self.overlays:
            if not checked:
                overlay.hide()
            else:
                if overlay.lines or getattr(overlay, "_pending_pushes", None):
                    overlay.show()
                    overlay.raise_above()
        self._sync_capture_masks()
        self.save_settings()

    def update_gpu(self):
        value = gpu_memory_mb()
        self.gpu_metric.setText(f"GPU belleği: {value} MB" if value is not None else "GPU belleği: erişilemiyor")

    def show_error(self, message):
        self.error.setText(message)
        self.status.setText("Hata")

    def closeEvent(self, event):
        self.stop_capture()
        for worker in list(self.workers):
            if worker.isRunning():
                worker.wait(3000)
        self.save_settings()
        event.accept()


class WarmWorker(QThread):
    ready = Signal()
    failed = Signal(str)

    def __init__(self, url, model):
        super().__init__()
        self.url, self.model = url, model

    def run(self):
        try:
            OllamaProvider(self.url).warm(self.model)
            self.ready.emit()
        except Exception as exc:
            self.failed.emit(f"Ollama modeli hazır edilemedi: {exc}")


def run():
    if sys.platform == "win32":
        os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
    app = QApplication([])
    app.setFont(QFont("Segoe UI" if sys.platform == "win32" else "Sans", 10))
    window = MainWindow(); window.show()
    app.exec()
