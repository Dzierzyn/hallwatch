"""HallWatch - prywatny pipeline computer vision dla korytarza."""

import os

# Musi byc ustawione PRZED otwarciem strumienia RTSP przez OpenCV:
# TCP zamiast UDP (brak artefaktow), krotszy timeout, maly bufor.
os.environ.setdefault(
    "OPENCV_FFMPEG_CAPTURE_OPTIONS",
    "rtsp_transport;tcp|stimeout;5000000|max_delay;500000|reorder_queue_size;0",
)

__version__ = "0.1.0"
