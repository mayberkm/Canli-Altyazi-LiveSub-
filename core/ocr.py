import os
import time

import cv2


class PaddleTextReader:
    def __init__(self, device="auto"):
        from paddleocr import PaddleOCR
        # PaddleOCR 2.x can use CUDA when paddlepaddle-gpu is installed. CPU is the fallback.
        self.device = device
        if device == "gpu":
            use_gpu = True
        elif device == "cpu":
            use_gpu = False
        else:
            try:
                import paddle
                use_gpu = bool(paddle.device.is_compiled_with_cuda() and paddle.device.cuda.device_count() > 0)
            except Exception:
                use_gpu = False
        self.engine = PaddleOCR(use_angle_cls=False, lang="en", use_gpu=use_gpu,
                                show_log=False, enable_mkldnn=not use_gpu,
                                det_db_thresh=0.23, det_db_box_thresh=0.45, det_db_unclip_ratio=1.8)

    def read(self, image, line_filter=None):
        started = time.perf_counter()
        orig_h, orig_w = image.shape[:2]
        pad = 24
        # Add replicated border padding so text touching the top, bottom, or side edges
        # (especially subtitles sitting at the very bottom edge of the screen/crop)
        # provides a complete convolutional receptive field for DBNet detection.
        padded_img = cv2.copyMakeBorder(image, pad, pad, pad, pad, cv2.BORDER_REPLICATE)
        gray = cv2.cvtColor(padded_img, cv2.COLOR_BGR2GRAY)
        result = self.engine.ocr(gray, cls=False)
        raw_lines = []
        if result and result[0]:
            for item in result[0]:
                text = item[1][0].strip()
                confidence = float(item[1][1])
                if not text:
                    continue
                alpha = [c for c in text if c.isalpha()]
                if confidence < 0.65 or (len(alpha) <= 2 and confidence < 0.72 and not text.endswith(("!", "?", "."))):
                    continue
                box = item[0]
                # Map coordinates back to the original unpadded image space
                xs = [max(0.0, min(float(orig_w), float(point[0]) - pad)) for point in box]
                ys = [max(0.0, min(float(orig_h), float(point[1]) - pad)) for point in box]
                x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
                w = max(1.0, x1 - x0)
                cx = (x0 + x1) / 2.0
                if text.lower() in {"mk", "mki", "mkice", "mkiceandfire"} or (
                    orig_w > 0 and (w / orig_w) < 0.18 and ((cx / orig_w) > 0.90 or (cx / orig_w) < 0.04 or x1 >= orig_w - 3)
                ):
                    continue
                raw_lines.append({"text": text, "confidence": confidence,
                                  "box": [x0, y0, x1, y1]})
        lines = [l for l in raw_lines if line_filter(l["text"])] if line_filter else raw_lines
        blocks = self._group_lines(lines)
        all_text = "\n\n".join(block["text"] for block in blocks)
        if blocks:
            bounds = [min(b["bounds"][0] for b in blocks), min(b["bounds"][1] for b in blocks),
                      max(b["bounds"][2] for b in blocks), max(b["bounds"][3] for b in blocks)]
        else:
            bounds = None
        return {"lines": lines, "blocks": blocks, "text": all_text,
                "bounds": bounds,
                "elapsed_ms": (time.perf_counter() - started) * 1000}

    @staticmethod
    def _group_lines(lines):
        """Unify nearby OCR rows and words into coherent subtitle sentences without merging distant text."""
        if not lines:
            return []

        # Sort primarily by vertical position (center y), then by horizontal x
        ordered = sorted(lines, key=lambda l: ((l["box"][1] + l["box"][3]) / 2, l["box"][0]))
        groups = []

        for line in ordered:
            x0, y0, x1, y1 = line["box"]
            w = max(1, x1 - x0)
            h = max(1, y1 - y0)
            cy = (y0 + y1) / 2
            cx = (x0 + x1) / 2

            best = None
            best_dist = float("inf")

            for g in groups:
                gx0, gy0, gx1, gy1 = g["bounds"]
                gw = max(1, gx1 - gx0)
                gh = max(1, gy1 - gy0)
                gcy = (gy0 + gy1) / 2
                gcx = (gx0 + gx1) / 2

                # 1. Same-line horizontal neighbors (e.g. "Sorry." and "Are Rebecca and I...")
                y_overlap = max(0, min(y1, gy1) - max(y0, gy0))
                is_same_line = (y_overlap / max(1, min(h, gh)) >= 0.45) or (abs(cy - gcy) <= 0.35 * max(h, gh))
                x_gap = max(0, x0 - gx1, gx0 - x1)

                if is_same_line and x_gap <= max(h, gh) * 3.2:
                    dist = x_gap
                    if dist < best_dist:
                        best = g
                        best_dist = dist
                    continue

                # 2. Multi-line subtitle block (stacked vertically on top of each other)
                vertical_gap = max(0, y0 - gy1, gy0 - y1)
                max_v_gap = max(28, int(max(h, gh) * 1.85))
                is_vertical_neighbor = vertical_gap <= max_v_gap

                x_overlap = max(0, min(x1, gx1) - max(x0, gx0))
                overlap_ratio = x_overlap / max(1, min(w, gw))
                # For stacked multi-line subtitles, they MUST have significant horizontal overlap
                # or centers closely aligned.
                is_stacked = (overlap_ratio >= 0.25) or (abs(cx - gcx) <= max(w, gw) * 0.35)

                if is_vertical_neighbor and is_stacked:
                    dist = vertical_gap * 2 + abs(cx - gcx)
                    if dist < best_dist:
                        best = g
                        best_dist = dist

            if best is None:
                groups.append({"lines": [line], "bounds": [x0, y0, x1, y1]})
            else:
                best["lines"].append(line)
                bx0, by0, bx1, by1 = best["bounds"]
                best["bounds"] = [min(bx0, x0), min(by0, y0), max(bx1, x1), max(by1, y1)]

        blocks = []
        for g in groups:
            # Sort lines inside group top-to-bottom, left-to-right
            g["lines"].sort(key=lambda l: ((l["box"][1] + l["box"][3]) / 2, l["box"][0]))
            text_lines = []
            current_line_parts = []
            last_cy = None
            for item in g["lines"]:
                item_cy = (item["box"][1] + item["box"][3]) / 2
                item_h = max(1, item["box"][3] - item["box"][1])
                if last_cy is None or abs(item_cy - last_cy) <= item_h * 0.5:
                    current_line_parts.append(item["text"])
                else:
                    text_lines.append(" ".join(current_line_parts))
                    current_line_parts = [item["text"]]
                last_cy = item_cy
            if current_line_parts:
                text_lines.append(" ".join(current_line_parts))

            blocks.append({"text": "\n".join(text_lines), "bounds": g["bounds"]})

        return sorted(blocks, key=lambda b: (b["bounds"][1], b["bounds"][0]))
