"""HallWatch: an open-source computer-vision pipeline for counting people and vehicles from any camera."""

import os

# Must be set BEFORE OpenCV opens the RTSP stream:
# TCP instead of UDP (no artefacts), shorter timeout, small buffer.
os.environ.setdefault(
    "OPENCV_FFMPEG_CAPTURE_OPTIONS",
    "rtsp_transport;tcp|stimeout;5000000|max_delay;500000|reorder_queue_size;0",
)

__version__ = "0.1.0"
