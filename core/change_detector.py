import cv2
import numpy as np


class ChangeDetector:
    def __init__(self, pixel_diff_threshold=9, min_changed_pixels=3):
        self.pixel_diff_threshold = pixel_diff_threshold
        self.min_changed_pixels = min_changed_pixels
        self.previous = None

    def changed(self, image):
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (3, 3), 0)
        small = cv2.resize(gray, (320, 100), interpolation=cv2.INTER_AREA)
        if self.previous is None:
            self.previous = small
            return True
        delta = cv2.absdiff(self.previous, small)
        changed_count = int(np.count_nonzero(delta > self.pixel_diff_threshold))
        if changed_count >= self.min_changed_pixels:
            self.previous = small
            return True
        return False
