# WebRTC camera broadcaster with MQTT signaling.

# ---- Build stage: compiles wheels that have no musl binary ----
FROM python:3.12-alpine AS builder

# google-crc32c has no musllinux wheel: compile its C extension against Alpine's libcrc32c.
RUN apk add --no-cache build-base crc32c-dev

COPY requirements.txt .
# CRC32C_PURE_PYTHON=0 makes the build fail instead of silently falling back to pure Python.
RUN CRC32C_PURE_PYTHON=0 pip wheel --no-cache-dir -r requirements.txt -w /wheels

# ---- Runtime stage ----
FROM python:3.12-alpine

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# crc32c: shared library required by the native google-crc32c extension.
RUN apk add --no-cache crc32c

WORKDIR /app

COPY requirements.txt .
RUN --mount=type=bind,from=builder,source=/wheels,target=/wheels \
    pip install --no-index --find-links=/wheels -r requirements.txt \
    && python -c "import google_crc32c; assert google_crc32c.implementation == 'c', google_crc32c.implementation"

COPY main.py .

# Unprivileged user; webcam access is granted at runtime via --group-add <host video gid>.
RUN adduser -S -D -H -u 10001 camera
USER camera

# main.py reads ./camera_config.json from the working directory: mount it, never bake it into the image.
# Video options come from VIDEO_DEVICE / VIDEO_SIZE / VIDEO_FORMAT (defaults: /dev/video0, 640x480, v4l2).
ENTRYPOINT ["python", "main.py"]
