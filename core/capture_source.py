import ctypes
import ctypes.util
import time


class X11WindowCapture:
    """Capture a desktop region while excluding CanlıAltyazı (LiveSub)'s top-level overlay."""

    def __init__(self):
        name = ctypes.util.find_library("X11")
        if not name:
            raise RuntimeError("X11 kitaplığı bulunamadı.")
        self.x11 = ctypes.CDLL(name)
        self.x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self.x11.XOpenDisplay.restype = ctypes.c_void_p
        self.display = self.x11.XOpenDisplay(None)
        if not self.display:
            raise RuntimeError("X11 ekranına bağlanılamadı.")
        self.x11.XUnmapWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        self.x11.XMapRaised.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        self.x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.overlay_ids = ()

    def set_overlay(self, window_id):
        self.set_overlays([window_id] if window_id else [])

    def set_overlays(self, window_ids):
        self.overlay_ids = tuple(int(window_id) for window_id in window_ids if window_id)

    def capture_region(self, region, mss_instance):
        # Unmap only at the X server level, leaving Qt's window state untouched.
        # The source game remains mapped, so MSS captures its original subtitle;
        # the LiveSub overlay is absent from this one frame.
        if not self.display or not self.overlay_ids:
            return mss_instance.grab(region)
        for window_id in self.overlay_ids:
            try:
                self.x11.XUnmapWindow(self.display, window_id)
            except Exception:
                pass
        self.x11.XSync(self.display, 0)
        time.sleep(0.035)
        try:
            return mss_instance.grab(region)
        finally:
            for window_id in self.overlay_ids:
                try:
                    self.x11.XMapRaised(self.display, window_id)
                except Exception:
                    pass
            self.x11.XSync(self.display, 0)

    def close(self):
        if self.display:
            try:
                self.x11.XCloseDisplay(self.display)
            except Exception:
                pass
            self.display = None
