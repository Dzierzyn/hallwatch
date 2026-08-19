"""HallWatch: a private computer-vision pipeline for a corridor."""

import os

# Must be set BEFORE OpenCV opens the RTSP stream:
# TCP instead of UDP (no artefacts), shorter timeout, small buffer.
os.environ.setdefault(
    "OPENCV_FFMPEG_CAPTURE_OPTIONS",
    "rtsp_transport;tcp|stimeout;5000000|max_delay;500000|reorder_queue_size;0",
)

__version__ = "0.1.0"
