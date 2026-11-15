# Agromate Camera

The webcam ("broadcaster") component of the Agromate system. It captures a local camera and streams it to viewers over WebRTC, using an MQTT broker for signaling and a TURN+STUN server for NAT traversal.

## Requirements

- Python 3.10+ 
- FFmpeg
- Access to the Agromate MQTT broker and server

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 1. Generate the webcam credentials

```bash
python setup_mqtt_client.py --broker <broker-host> --server-url http://<server-host>:3000
```

| Option | Description |
| --- | --- |
| `--broker`, `-b` | MQTT broker hostname (required) |
| `--server-url` | Agromate server base URL, e.g. `http://192.168.1.59:3000` (required) |
| `--port`, `-p` | MQTT broker port (default `1883`) |
| `--output`, `-o` | Output file (default `camera_config.json`) |

This writes `camera_config.json` and prints a generated username (`cam_xxxxxxxx`) and password:

```json
{
  "broker": "<broker-host>",
  "port": 1883,
  "username": "cam_xxxxxxxx",
  "password": "...",
  "server_url": "http://<server-host>:3000"
}
```

### 2. Register the webcam

Add the webcam from the Agromate app using the printed username and password. The app registers it on the server and grants it access on the MQTT broker.

> `camera_config.json` contains real credentials: keep it out of version control.

## Running

`main.py` reads `camera_config.json` from the current working directory.

```bash
python main.py
```

| Option | Env variable | Default | Description |
| --- | --- | --- | --- |
| `--video-device`, `-vd` | `VIDEO_DEVICE` | `/dev/video0` | Camera device |
| `--video-format`, `-vf` | `VIDEO_FORMAT` | `v4l2` | FFmpeg input format |
| `--video-size`, `-v` | `VIDEO_SIZE` | `640x480` | Capture resolution |
| `--log-level`, `-l` | | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `--test`, `-t` | | | Stream a sample MP4 instead of the camera |

Command-line flags take precedence over environment variables.

### Choosing the camera device and format

The device and format are passed to FFmpeg, so they depend on the platform:

| Platform | Example | List available devices |
| --- | --- | --- |
| Linux | `-vd /dev/video0 -vf v4l2` | `v4l2-ctl --list-devices` |
| macOS | `-vd default:none -vf avfoundation` | `ffmpeg -f avfoundation -list_devices true -i ""` |
| Windows | `-vd "video=Integrated Camera" -vf dshow` | `ffmpeg -list_devices true -f dshow -i dummy` |

On macOS the terminal needs camera permission (System Settings → Privacy & Security → Camera).

## Docker

The image targets Linux hosts with a V4L2 camera.

```bash
docker build -t agrosmart-camera .

docker run -d --name agrosmart-camera --restart unless-stopped \
  --network host \
  --device /dev/video0:/dev/video0 \
  --group-add "$(getent group video | cut -d: -f3)" \
  -e VIDEO_DEVICE=/dev/video0 \
  -e VIDEO_SIZE=640x480 \
  -e VIDEO_FORMAT=v4l2 \
  -v "$PWD/camera_config.json:/app/camera_config.json:ro" \
  agrosmart-camera
```

- `camera_config.json` is mounted at runtime and never baked into the image.
- `VIDEO_DEVICE` must match the path mapped with `--device`.
- The container runs as an unprivileged user; `--group-add` gives it access to the host's `video` group.
- Arguments after the image name are passed to `main.py`, e.g. `agrosmart-camera -l DEBUG` or `agrosmart-camera --test`.

## How it works

The broadcaster runs two concurrent tasks:

- **MQTT signaling** — connects to the broker with the webcam credentials and listens on `webrtc/<id>/signaling`, where `<id>` is the webcam username.
- **TURN credential refresh** — calls `GET <server_url>/turn` with HTTP Basic auth (the same username/password) and refreshes the credentials shortly before their TTL expires.

### Signaling protocol

Topics are reserved to a single webcam; only the webcam and its owner can access them:

- `webrtc/<id>/signaling` — main topic
- `webrtc/<id>/<viewerId>` — topic reserved to one viewer

```
Viewer                                   Broadcaster
  │ register-viewer (main topic)              │
  │  target=<id>, topic=webrtc/<id>/<viewerId>│
  │──────────────────────────────────────────▶│ subscribes to viewer topic
  │ viewer-registered (main topic)            │
  │◀──────────────────────────────────────────│
  │ offer (viewer topic)                      │
  │──────────────────────────────────────────▶│ creates RTCPeerConnection
  │ answer (viewer topic)                     │
  │◀──────────────────────────────────────────│
  │ ice-candidate (viewer topic)              │
  │◀──────────────────────────────────────────│
  │            WebRTC video stream            │
  │◀══════════════════════════════════════════│
```

Every message is JSON with a `type` and a `senderId`; the broadcaster ignores its own messages. A registration is accepted only if `topic` is exactly `webrtc/<id>/<senderId>`. Each viewer gets its own peer connection and media player, which are closed when the connection fails or closes. See [`camera_protocol.md`](camera_protocol.md) for more details.

ICE servers are Google STUN plus the TURN server once credentials have been fetched.
