import ctypes
import ctypes.util
import math
import re
import sys
import time
from difflib import SequenceMatcher

from PySide6.QtCore import Qt, Signal, QPoint, QTimer, QRect, QRectF
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QFontMetrics, QGuiApplication
from PySide6.QtWidgets import QWidget


class Overlay(QWidget):
    geometry_changed = Signal(object)
    visibility_changed = Signal(bool)
    position_moved = Signal(int, int)
    size_changed = Signal(int, int)

    def __init__(self, fade_timeout=4.5, fade_duration=1.0):
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        if sys.platform == "win32":
            flags |= Qt.WindowType.Tool
        super().__init__(None, flags)
        self._is_win32 = (sys.platform == "win32")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setGeometry(0, 0, 860, 95)

        # Rolling subtitle entries: each entry is {"text": str, "time": float, "opacity": float}
        self.lines = []
        self.fade_timeout = float(fade_timeout)  # seconds before fade begins
        self.fade_duration = float(fade_duration)  # seconds to fade to 0
        self.anchor = None
        self._current_font = None

        # User-customizable appearance attributes
        self.position_mode = "above_subtitle"  # "above_subtitle", "screen_bottom", "screen_top", "custom"
        self.opacity_pct = 40                  # 0 to 100
        self.font_family = "Ubuntu Sans"
        self.font_size = 17                    # 12 to 36
        self.bbox_width = 860                  # 250 to 2500
        self.bbox_height = 95                  # 45 to 800
        self.custom_pos = None                 # (x, y)
        self._draggable = False
        self._preview_mode = False
        self._drag_start = None
        self._resize_mode = None
        self._resize_start_geo = None

        # Smooth timer for ~60 FPS delta-time interpolated scrolling and floating fade-out
        self._last_tick = time.perf_counter()
        self._last_push_time = 0.0
        self._pending_pushes = []
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._on_tick)

        self._x11 = None
        self._x11_display = None
        try:
            lib = ctypes.util.find_library("X11")
            if lib:
                self._x11 = ctypes.CDLL(lib)
                self._x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
                self._x11.XOpenDisplay.restype = ctypes.c_void_p
                self._x11.XRaiseWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
                self._x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
                self._x11_display = self._x11.XOpenDisplay(None)
        except Exception:
            pass

        self.hide()

    def _apply_win32_overlay_style(self, click_through=True):
        """Enforces Win32 WS_EX_TOPMOST / NOACTIVATE and toggles click-through WS_EX_TRANSPARENT."""
        if not self._is_win32:
            return
        try:
            hwnd = int(self.winId())
            if not hwnd:
                return
            user32 = ctypes.windll.user32
            GWL_EXSTYLE = -20
            WS_EX_TRANSPARENT = 0x00000020
            WS_EX_LAYERED = 0x00080000
            WS_EX_NOACTIVATE = 0x08000000
            HWND_TOPMOST = -1
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_NOACTIVATE = 0x0010
            SWP_FRAMECHANGED = 0x0020

            get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
            set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
            get_long.argtypes = [ctypes.c_void_p, ctypes.c_int]
            get_long.restype = ctypes.c_ssize_t
            set_long.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
            set_long.restype = ctypes.c_ssize_t

            style = get_long(ctypes.c_void_p(hwnd), GWL_EXSTYLE)
            style |= WS_EX_LAYERED | WS_EX_NOACTIVATE
            if click_through:
                style |= WS_EX_TRANSPARENT
            else:
                style &= ~WS_EX_TRANSPARENT
            set_long(ctypes.c_void_p(hwnd), GWL_EXSTYLE, style)
            user32.SetWindowPos(
                ctypes.c_void_p(hwnd),
                ctypes.c_void_p(HWND_TOPMOST),
                0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_FRAMECHANGED,
            )
        except Exception:
            pass

    def raise_above(self):
        self.raise_()
        if self._is_win32:
            self._apply_win32_overlay_style(click_through=not getattr(self, "_draggable", False))
            return
        if self._x11 and self._x11_display and self.winId():
            try:
                self._x11.XRaiseWindow(self._x11_display, int(self.winId()))
                self._x11.XSync(self._x11_display, 0)
            except Exception:
                pass

    def set_appearance(self, position_mode=None, opacity_pct=None, font_family=None, font_size=None,
                       custom_x=None, custom_y=None, bbox_width=None, bbox_height=None):
        """Allows user to dynamically customize overlay placement, size, opacity, and typography."""
        if position_mode is not None:
            self.position_mode = str(position_mode)
        if opacity_pct is not None:
            self.opacity_pct = max(0, min(100, int(opacity_pct)))
        if font_family is not None and str(font_family).strip():
            self.font_family = str(font_family).strip()
        if font_size is not None:
            self.font_size = max(10, min(48, int(font_size)))
        if bbox_width is not None:
            self.bbox_width = max(250, min(2500, int(bbox_width)))
        if bbox_height is not None:
            self.bbox_height = max(45, min(800, int(bbox_height)))
        if custom_x is not None and custom_y is not None:
            self.custom_pos = (int(custom_x), int(custom_y))
        self._place()
        if self.lines:
            self._compute_metrics_and_layout()
        self.update()

    def start_preview_mode(self, text="CanlıAltyazı (LiveSub): Altyazı görünüm ve konum önizlemesi (Fareyle sürükleyin / köşeden boyutlandırın)"):
        """Enables interactive positioning/resizing and displays a live preview subtitle."""
        self._preview_mode = True
        self._draggable = True
        self._resize_mode = None
        self._resize_start_geo = None
        self._drag_start = None
        self._pending_pushes.clear()
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, False)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        preview_line = {
            "text": text,
            "time": time.time() + 999999.0,
            "render_y": None,
            "target_y": 0.0,
            "lh": self.font_size + 8,
            "opacity": 1.0,
            "target_opacity": 1.0,
            "exiting": False,
            "is_extension": False,
        }
        self.lines = [preview_line]
        self._place()
        self._compute_metrics_and_layout()
        preview_line["render_y"] = preview_line["target_y"]
        self.show()
        self._apply_win32_overlay_style(click_through=False)
        self.raise_above()
        self.update()
        self.visibility_changed.emit(True)

    def stop_preview_mode(self):
        """Exits preview mode, locks position/size, restores click-through pass, and hides preview."""
        self._preview_mode = False
        self._draggable = False
        self._drag_start = None
        self._resize_mode = None
        self._resize_start_geo = None
        self.setMouseTracking(False)
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, True)
        self._apply_win32_overlay_style(click_through=True)
        self.unsetCursor()
        self.lines.clear()
        self.hide()
        self.update()
        self.visibility_changed.emit(False)

    def _hit_test_resize(self, pos):
        w, h = self.width(), self.height()
        on_right = pos.x() >= w - 14
        on_bottom = pos.y() >= h - 14
        if on_right and on_bottom:
            return "rb"
        if on_right:
            return "r"
        if on_bottom:
            return "b"
        return None

    def mousePressEvent(self, event):
        if self._draggable and event.button() == Qt.MouseButton.LeftButton:
            mode = self._hit_test_resize(event.position().toPoint())
            if mode:
                self._resize_mode = mode
                self._drag_start = event.globalPosition().toPoint()
                self._resize_start_geo = self.geometry()
            else:
                self._resize_mode = None
                self._resize_start_geo = None
                self._drag_start = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._draggable:
            if self._drag_start is not None and (event.buttons() & Qt.MouseButton.LeftButton):
                if self._resize_mode and self._resize_start_geo is not None:
                    delta = event.globalPosition().toPoint() - self._drag_start
                    new_w = self._resize_start_geo.width()
                    new_h = self._resize_start_geo.height()
                    if "r" in self._resize_mode:
                        new_w = max(250, min(2500, self._resize_start_geo.width() + delta.x()))
                    if "b" in self._resize_mode:
                        new_h = max(45, min(800, self._resize_start_geo.height() + delta.y()))
                    self.bbox_width = new_w
                    self.bbox_height = new_h
                    self.resize(new_w, new_h)
                    self._compute_metrics_and_layout()
                    self.update()
                    self.size_changed.emit(new_w, new_h)
                    self.geometry_changed.emit(self.geometry())
                    event.accept()
                    return
                new_pos = event.globalPosition().toPoint() - self._drag_start
                self.move(new_pos)
                self.custom_pos = (new_pos.x(), new_pos.y())
                self.position_mode = "custom"
                self.position_moved.emit(new_pos.x(), new_pos.y())
                self.geometry_changed.emit(self.geometry())
                event.accept()
                return
            mode = self._hit_test_resize(event.position().toPoint())
            if mode == "rb":
                self.setCursor(Qt.CursorShape.SizeFDiagCursor)
            elif mode == "r":
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            elif mode == "b":
                self.setCursor(Qt.CursorShape.SizeVerCursor)
            else:
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._draggable and event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = None
            self._resize_mode = None
            self._resize_start_geo = None
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def set_anchor(self, region):
        self.anchor = dict(region) if region else None
        if (self.lines or self._preview_mode) and self.anchor:
            self._place()

    def clear_now(self):
        """Immediately wipes all subtitles and hides the overlay."""
        self._pending_pushes.clear()
        if self._preview_mode:
            self.stop_preview_mode()
            return
        self.timer.stop()
        self.lines.clear()
        self.update()
        self.hide()
        self.visibility_changed.emit(False)

    def _compute_metrics_and_layout(self, now=None):
        """Computes wrapped line heights, evicts overflowing history, and sets continuous target_y slots."""
        if not self.lines:
            return
        if now is None:
            now = time.time()

        font_family = getattr(self, "font_family", "Ubuntu Sans")
        font_size = getattr(self, "font_size", 17)
        font = QFont(font_family, font_size, QFont.Weight.Bold)
        font.setStyleHint(QFont.StyleHint.SansSerif)
        metrics = QFontMetrics(font)

        avail_rect = self.rect().adjusted(16, 6, -16, -6)
        avail_w = max(80, avail_rect.width())
        avail_h = max(32, avail_rect.height())
        spacing = 4

        for l in self.lines:
            br = metrics.boundingRect(0, 0, avail_w, 2000,
                                      Qt.TextFlag.TextWordWrap, l["text"])
            l["lh"] = max(br.height(), font_size + 8)

        active_lines = [l for l in self.lines if not l.get("exiting")]

        # Automatically glide oldest active lines out if combined height exceeds avail_h or max 2 slots
        while len(active_lines) > 1:
            total_active_h = sum(l["lh"] for l in active_lines) + spacing * (len(active_lines) - 1)
            if len(active_lines) > 2 or total_active_h > avail_h:
                oldest = active_lines.pop(0)
                oldest["exiting"] = True
                oldest["target_opacity"] = 0.0
                cur_ry = oldest.get("render_y")
                if cur_ry is None:
                    cur_ry = oldest.get("target_y", float(avail_rect.top()))
                    oldest["render_y"] = cur_ry
                oldest["target_y"] = cur_ry - max(24.0, oldest.get("lh", 28) * 0.85)
            else:
                break

        if not active_lines:
            return

        for l in active_lines[:-1]:
            l["target_opacity"] = 0.60
        active_lines[-1]["target_opacity"] = 1.0

        # Long overflowing single active line: dwell at top briefly, then auto-scroll bottom into view
        if len(active_lines) == 1 and active_lines[0]["lh"] > avail_h:
            l = active_lines[0]
            age = now - l["time"]
            if age < 0.65 and not l.get("is_extension", False):
                l["target_y"] = float(avail_rect.top())
            else:
                l["target_y"] = float(avail_rect.bottom() - l["lh"])
            if self._preview_mode or l.get("render_y") is None:
                l["render_y"] = l["target_y"]
        else:
            total_h = sum(l["lh"] for l in active_lines) + spacing * max(0, len(active_lines) - 1)
            if len(active_lines) >= 2 and total_h > avail_h * 0.72:
                base_y = avail_rect.bottom() - total_h
            else:
                base_y = avail_rect.top() + max(0, (avail_h - total_h) // 2)
            curr_y = float(base_y)
            for l in active_lines:
                l["target_y"] = curr_y
                if self._preview_mode or l.get("render_y") is None:
                    l["render_y"] = curr_y
                curr_y += l["lh"] + spacing

    def _commit_line(self, clean, region=None, now=None, now_mono=None):
        """Appends a new active line and smoothly launches it from below without snapping existing lines."""
        if region:
            self.anchor = dict(region)
        if now is None:
            now = time.time()
        if now_mono is None:
            now_mono = time.perf_counter()

        self._place()

        # Soften existing active lines; _compute_metrics_and_layout will transition excess lines smoothly
        active_lines = [l for l in self.lines if not l.get("exiting")]
        while len(active_lines) >= 2:
            oldest = active_lines.pop(0)
            oldest["exiting"] = True
            oldest["target_opacity"] = 0.0
            cur_ry = oldest.get("render_y")
            if cur_ry is None:
                cur_ry = oldest.get("target_y", float(self.rect().top() + 6))
                oldest["render_y"] = cur_ry
            oldest["target_y"] = cur_ry - max(24.0, oldest.get("lh", 28) * 0.85)

        for l in active_lines:
            l["target_opacity"] = 0.60

        new_entry = {
            "text": clean,
            "time": now,
            "render_y": None,
            "target_y": 0.0,
            "lh": self.font_size + 8,
            "opacity": 0.0,
            "target_opacity": 1.0,
            "exiting": False,
            "is_extension": False,
        }
        self.lines.append(new_entry)

        self._compute_metrics_and_layout(now)
        # Slide new line smoothly upward from +22px below its slot
        new_entry["render_y"] = new_entry["target_y"] + 22.0
        self._last_push_time = now_mono

        self.show()
        self.raise_above()
        if not self.timer.isActive():
            self._last_tick = now_mono
            self.timer.start()
        self.update()
        self.visibility_changed.emit(True)

    def push_translation(self, text, region=None):
        """Adds a new subtitle at the bottom, smoothly gliding older lines upward with fading."""
        clean = text.strip()
        if not clean:
            return

        if self._preview_mode:
            self.stop_preview_mode()

        now = time.time()
        now_mono = time.perf_counter()
        c_clean = re.sub(r"[^\w\s]", "", clean).strip().casefold()

        # If a rapid burst item is already pending, coalesce multi-word duplicates/extensions there
        if self._pending_pushes:
            last_p = self._pending_pushes[-1]
            c_pend = re.sub(r"[^\w\s]", "", last_p["text"]).strip().casefold()
            if c_clean == c_pend:
                if region:
                    last_p["region"] = dict(region)
                return
            if (
                len(c_pend.split()) >= 2
                and (
                    (c_clean.startswith(c_pend + " ") and len(c_clean) > len(c_pend))
                    or (len(c_clean.split()) >= 2 and SequenceMatcher(None, c_clean, c_pend).ratio() >= 0.90)
                )
            ):
                last_p["text"] = clean
                if region:
                    last_p["region"] = dict(region)
                return
        else:
            # Check against all active lines on screen to prevent duplicate / jitter / multi-word subset lines
            active_lines = [l for l in self.lines if not l.get("exiting")]
            if active_lines:
                last_line = active_lines[-1]
                c_last = re.sub(r"[^\w\s]", "", last_line["text"].strip()).strip().casefold()

                if (
                    len(c_last.split()) >= 2
                    and (
                        (c_clean.startswith(c_last + " ") and len(c_clean) > len(c_last))
                        or (len(c_clean.split()) >= 2 and SequenceMatcher(None, c_clean, c_last).ratio() >= 0.90)
                    )
                ):
                    if region:
                        self.anchor = dict(region)
                    last_line["text"] = clean
                    last_line["time"] = now
                    last_line["is_extension"] = True
                    self._place()
                    self._compute_metrics_and_layout(now)
                    self.update()
                    return

                clean_words = len(c_clean.split())
                for line in reversed(active_lines):
                    if clean_words == 1 and line is not last_line:
                        continue
                    c_line = re.sub(r"[^\w\s]", "", line["text"].strip()).strip().casefold()
                    line_words = len(c_line.split())
                    is_ext = line_words >= 2 and c_clean.startswith(c_line + " ") and (len(c_clean) - len(c_line) >= 3)
                    is_multi_dup = (
                        clean_words >= 2
                        and line_words >= 2
                        and not is_ext
                        and (
                            SequenceMatcher(None, c_clean, c_line).ratio() >= 0.84
                            or (len(c_clean) >= 12 and len(c_clean) < len(c_line) and c_clean in c_line)
                        )
                    )
                    if c_clean == c_line or is_multi_dup:
                        line["time"] = now
                        if region and line is last_line:
                            self.anchor = dict(region)
                            self._place()
                        return

        # Stagger bursts of rapid distinct lines by ~110ms so each line visibly scrolls in
        if self.lines and (now_mono - self._last_push_time < 0.11):
            if len(self._pending_pushes) >= 6:
                self._pending_pushes.pop(0)
            self._pending_pushes.append({"text": clean, "region": dict(region) if region else None})
            if not self.timer.isActive():
                self._last_tick = now_mono
                self.timer.start()
            return

        self._commit_line(clean, region, now, now_mono)

    def set_text(self, text, region=None):
        """Compatibility wrapper for single text calls."""
        if text:
            self.push_translation(text, region)
        else:
            if not self.lines and not self._pending_pushes:
                self.clear_now()

    def _on_tick(self):
        """Timer callback that smoothly animates upward glide and line opacity decay."""
        if self._preview_mode:
            return

        now = time.time()
        now_mono = time.perf_counter()
        dt = max(0.004, min(0.050, now_mono - self._last_tick))
        self._last_tick = now_mono

        # Release one queued rapid-burst subtitle every ~110ms
        if self._pending_pushes and (now_mono - self._last_push_time >= 0.11):
            item = self._pending_pushes.pop(0)
            self._commit_line(item["text"], item.get("region"), now, now_mono)

        self._compute_metrics_and_layout(now)

        active_pos_factor = 1.0 - math.exp(-12.5 * dt)
        active_op_factor = 1.0 - math.exp(-14.0 * dt)
        exit_pos_factor = 1.0 - math.exp(-9.5 * dt)
        exit_op_factor = 1.0 - math.exp(-5.5 * dt)

        alive = []
        for line in self.lines:
            age = now - line["time"]
            exiting = line.get("exiting", False)

            if not exiting and age >= self.fade_timeout:
                line["exiting"] = True
                line["target_opacity"] = 0.0
                cur_ry = line.get("render_y")
                if cur_ry is None:
                    cur_ry = line.get("target_y", 0.0)
                    line["render_y"] = cur_ry
                line["target_y"] = cur_ry - max(24.0, line.get("lh", 28) * 0.85)
                exiting = True

            if line.get("render_y") is None:
                line["render_y"] = line.get("target_y", 0.0)

            if exiting:
                line["target_y"] -= 26.0 * dt
                line["render_y"] += (line["target_y"] - line["render_y"]) * exit_pos_factor
                line["opacity"] += (0.0 - line["opacity"]) * exit_op_factor
                if line["opacity"] > 0.02:
                    alive.append(line)
            else:
                line["render_y"] += (line["target_y"] - line["render_y"]) * active_pos_factor
                line["opacity"] += (line["target_opacity"] - line["opacity"]) * active_op_factor
                alive.append(line)

        self.lines = alive
        if not self.lines and not self._pending_pushes:
            self.timer.stop()
            self.hide()
            self.visibility_changed.emit(False)
        else:
            self.update()

    def _place(self):
        anchor = self.anchor
        mode = getattr(self, "position_mode", "above_subtitle")
        screen = None
        if mode == "custom" and self.custom_pos:
            screen = QGuiApplication.screenAt(QPoint(int(self.custom_pos[0]), int(self.custom_pos[1])))
        if anchor:
            region_limit = anchor.get("region_limit") or anchor
            reg_left = region_limit.get("left", anchor["left"])
            reg_top = region_limit.get("top", anchor["top"])
            reg_w = region_limit.get("width", anchor["width"])
            reg_h = region_limit.get("height", anchor["height"])
            anchor_cx = reg_left + reg_w // 2
            if not screen:
                screen = QGuiApplication.screenAt(QPoint(int(anchor_cx), int(reg_top + reg_h // 2)))
        elif not screen and self.custom_pos:
            screen = QGuiApplication.screenAt(QPoint(int(self.custom_pos[0]), int(self.custom_pos[1])))

        if not screen:
            screen = self.screen() or QGuiApplication.primaryScreen()

        screen_geo = screen.geometry() if screen else None
        max_screen_w = screen_geo.width() if screen_geo else 1920
        screen_left = screen_geo.left() if screen_geo else 0
        screen_right = screen_geo.right() if screen_geo else (screen_left + max_screen_w)
        screen_top = screen_geo.top() if screen_geo else 0
        screen_bottom = screen_geo.bottom() if screen_geo else 1080

        # User-defined bbox dimensions safely clamped to screen bounds
        stable_w = min(max(250, int(getattr(self, "bbox_width", 860))), max_screen_w - 20)
        stable_h = min(max(45, int(getattr(self, "bbox_height", 95))),
                       max(45, (screen_geo.height() - 20) if screen_geo else 800))

        if mode == "custom" and self.custom_pos:
            left, top = self.custom_pos
        elif mode == "screen_bottom":
            left = screen_left + (max_screen_w - stable_w) // 2
            top = screen_bottom - stable_h - 40
        elif mode == "screen_top":
            left = screen_left + (max_screen_w - stable_w) // 2
            top = screen_top + 40
        else:  # "above_subtitle" (default)
            if anchor:
                left = anchor_cx - stable_w // 2
                left = max(screen_left + 10, min(left, screen_right - stable_w - 10))
                top = reg_top - stable_h - 10
                if top < screen_top + 10:
                    top = reg_top + reg_h + 10
            else:
                left = screen_left + (max_screen_w - stable_w) // 2
                top = screen_top + int(screen_geo.height() * 0.76 if screen_geo else 700)

        new_geo = QRect(int(left), int(top), int(stable_w), int(stable_h))
        if self.geometry() != new_geo:
            self.setGeometry(new_geo)
            self.geometry_changed.emit(self.geometry())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)

        if not self.lines and not self._preview_mode:
            return

        max_op = max((line.get("opacity", 1.0) for line in self.lines), default=0.0)
        if max_op <= 0.01 and not self._preview_mode:
            return

        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        # Translucent background customized by user
        opacity_pct = getattr(self, "opacity_pct", 40)
        bg_alpha = int(2.55 * opacity_pct * max_op)
        border_alpha = int(min(60, opacity_pct * 0.6) * max_op)

        if opacity_pct > 0:
            painter.setPen(QPen(QColor(255, 255, 255, border_alpha), 1))
            painter.setBrush(QColor(10, 12, 16, bg_alpha))
            painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 10, 10)

        # Visual indicator when draggable preview is active
        if getattr(self, "_draggable", False):
            guide_pen = QPen(QColor(80, 200, 255, 220), 1.5, Qt.PenStyle.DashLine)
            painter.setPen(guide_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(self.rect().adjusted(2, 2, -2, -2), 10, 10)
            # Bottom-right resize grip indicator
            grip_pen = QPen(QColor(80, 200, 255, 240), 2.0)
            painter.setPen(grip_pen)
            rw, rh = self.width(), self.height()
            painter.drawLine(rw - 12, rh - 5, rw - 5, rh - 12)
            painter.drawLine(rw - 8, rh - 5, rw - 5, rh - 8)

        # Typography customized by user
        font_family = getattr(self, "font_family", "Ubuntu Sans")
        font_size = getattr(self, "font_size", 17)
        font = QFont(font_family, font_size, QFont.Weight.Bold)
        font.setStyleHint(QFont.StyleHint.SansSerif)
        painter.setFont(font)
        metrics = QFontMetrics(font)

        # Smoothly clip subtitles inside pill bounds
        painter.setClipRect(self.rect().adjusted(4, 3, -4, -3))

        avail_rect = self.rect().adjusted(16, 6, -16, -6)
        active_lines = [l for l in self.lines if not l.get("exiting")] or self.lines
        flags = Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap

        for l in self.lines:
            op = max(0.0, min(1.0, l.get("opacity", 1.0)))
            if op <= 0.02 and not self._preview_mode:
                continue

            lh = l.get("lh")
            if not lh:
                br = metrics.boundingRect(0, 0, max(80, avail_rect.width()), 2000,
                                          Qt.TextFlag.TextWordWrap, l["text"])
                lh = max(br.height(), font_size + 8)
                l["lh"] = lh

            render_y = l.get("render_y")
            if render_y is None:
                render_y = l.get("target_y", float(avail_rect.top()))

            # Smooth top-edge fade dissolve as exiting lines glide above the box
            if l.get("exiting") and render_y < avail_rect.top():
                edge_dist = avail_rect.top() - render_y
                op *= max(0.0, 1.0 - edge_dist / max(20.0, lh * 0.75))
                if op <= 0.02:
                    continue

            line_box = QRectF(float(avail_rect.left()), float(render_y), float(avail_rect.width()), float(lh))

            # 4-direction outline shadow for natural readability on any game scene even with 0% opacity
            shadow_col = QColor(0, 0, 0, int(230 * op))
            painter.setPen(shadow_col)
            d = 1 if font_size <= 18 else 2
            for dx, dy in ((-d, -d), (d, -d), (-d, d), (d, d), (0, d + 1)):
                painter.drawText(line_box.adjusted(dx, dy, dx, dy), flags, l["text"])

            # Text color (newest line crisp pure white, older line softer silver)
            if not l.get("exiting") and (not active_lines or l is active_lines[-1]):
                text_col = QColor(255, 255, 255, int(255 * op))
            else:
                text_col = QColor(215, 222, 235, int(190 * op))

            painter.setPen(text_col)
            painter.drawText(line_box, flags, l["text"])
