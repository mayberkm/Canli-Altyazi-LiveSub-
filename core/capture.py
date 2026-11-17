import numpy as np


class ScreenCapture:
    def __init__(self):
        import mss
        self._sct = mss.mss()

    @property
    def monitors(self):
        return self._sct.monitors[1:]

    def grab(self, region):
        shot = self._sct.grab(region)
        return np.asarray(shot)[:, :, :3].copy()

    def close(self):
        self._sct.close()
