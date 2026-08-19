# HallWatch - container image.
#
# Perfect for RTSP cameras (no device passthrough needed):
#   docker compose up            -> dashboard on http://localhost:8000
#
# Webcams need a device mount and are easier with a native install; see README.

FROM python:3.12-slim

# ffmpeg: recording + audio. libgl1/libglib2.0: OpenCV runtime deps.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src

# CPU-only torch keeps the image several GB smaller than the CUDA default.
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu \
        torch torchvision \
    && pip install --no-cache-dir .

# Config and data live outside the image - mount them (see compose.yaml).
VOLUME ["/app/data"]
EXPOSE 8000

# Bind to 0.0.0.0 inside the container; compose publishes the port on the
# host's localhost only. Set web.auth_token in config.yaml before exposing wider.
CMD ["hallwatch", "run", "--host", "0.0.0.0"]
