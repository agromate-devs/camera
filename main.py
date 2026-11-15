# pi_sender.py
import argparse
import asyncio
import json
import logging
import uuid
import os

import aiohttp
import aiomqtt
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceCandidate, RTCConfiguration, RTCIceServer
from aiortc.contrib.media import MediaPlayer
from typing import Optional

logger = logging.getLogger()

MQTT_TOPIC = ""  # Initial topic for peer knowledge exchange: webrtc/<CLIENT_ID>/signaling
CLIENT_ID = ""  # Webcam username, used as webcam ID on the server, MQTT username and for signaling

pcs = {}  # Active peer connections

CONFIG_FILE = "camera_config.json"

# ---------------------------------------------------------------------------
# TURN credentials, refreshed periodically via GET /turn
# Protected by _turn_semaphore to avoid collisions during updates.
# ---------------------------------------------------------------------------
_turn_semaphore = asyncio.Semaphore(1)
turn_credentials: dict = {
    "username": None,
    "password": None,
    "ttl": None,
    "urls": [],
}

def load_config(config_path: str) -> dict:
    """Load configuration from JSON file."""
    if not os.path.exists(config_path):
        logger.error("Configuration file '%s' not found.", config_path)
        raise FileNotFoundError(f"Configuration file '{config_path}' not found.")
    with open(config_path, "r") as f:
        config = json.load(f)
    logger.info("Configuration loaded from '%s'.", config_path)
    return config


def get_topic(pc_id: str) -> str:
    return f"webrtc/{CLIENT_ID}/{pc_id}"


async def handle_offer(data: dict, client: aiomqtt.Client, video_size: str, video_device: str, video_format: str, test_mode: bool = False) -> None:
    pc_id = data.get("senderId")
    if not pc_id:
        logger.warning("Offer received without senderId, ignoring.")
        return
    logger.info("Offer received from viewer with senderId: %s", pc_id)

    # Config with STUN and TURN servers
    rtc_config = RTCConfiguration()
    if turn_credentials["username"] and turn_credentials["password"] and turn_credentials["urls"]:
        rtc_config.iceServers = [
            RTCIceServer(
                urls="stun:stun.l.google.com:19302"
            ),
            RTCIceServer(
                urls=turn_credentials["urls"],
                username=turn_credentials["username"],
                credential=turn_credentials["password"],
            )
        ]
        logger.info("Using TURN server: %s", turn_credentials["urls"])

    pc = RTCPeerConnection(configuration=rtc_config)

    if test_mode:
        player = MediaPlayer(
            'http://download.tsi.telecom-paristech.fr/'
            'gpac/dataset/dash/uhd/mux_sources/hevcds_720p30_2M.mp4')
    else:
        player = MediaPlayer(video_device, format=video_format, options={"video_size": video_size})

    if player.video is None:
        logger.warning("No video stream available from player.")
        return

    pc.addTrack(player.video)
    pcs[pc_id] = pc
    pc_topic = get_topic(pc_id)

    offer = RTCSessionDescription(sdp=data["sdp"], type=data["type"])
    await pc.setRemoteDescription(offer)

    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    await client.publish(
        pc_topic,
        json.dumps({
            "type": "answer",
            "sdp": pc.localDescription.sdp,
            "senderId": CLIENT_ID
        })
    )

    logger.info("Response sent to viewer.")

    @pc.on("connectionstatechange")
    async def on_connectionstatechange() -> None:  # Close the peer connection to save resources
        logger.info("Connection state is %s", pc.connectionState)
        if pc.connectionState == "failed" or pc.connectionState == "closed":
            await pc.close()
            pcs.pop(pc_id, None)
            await client.unsubscribe(pc_topic)
            if player.video:
                player.video.stop()
            if player.audio:
                player.audio.stop()

    @pc.on("icecandidate")
    async def on_icecandidate(candidate):  # Send ICE candidates to the viewer
        if candidate:
            await client.publish(
                pc_topic,
                json.dumps({
                    "type": "ice-candidate",
                    "candidate": candidate.candidate,
                    "sdpMid": candidate.sdpMid,
                    "sdpMLineIndex": candidate.sdpMLineIndex,
                    "senderId": CLIENT_ID
                })
            )


async def _fetch_turn_credentials(
    server_url: str, username: str, password: str
) -> Optional[dict]:
    """
    Call GET /turn on the server with HTTP Basic auth using webcam credentials.
    Returns the parsed JSON response, or None on failure.
    """
    url = f"{server_url.rstrip('/')}/turn"
    auth = aiohttp.BasicAuth(username, password)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, auth=auth, raise_for_status=False) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error("GET /turn failed (%d): %s", resp.status, body)
                    return None
                data = await resp.json()
                return data
    except Exception as e:
        logger.error("GET /turn request failed: %s", e)
        return None


async def turn_updater(
    server_url: str,
    username: str,
    password: str,
    default_ttl: int = 3600,
) -> None:
    """
    Background task that periodically updates TURN credentials.

    - Calls GET /turn authenticating with webcam credentials.
    - Uses the TTL from the response to schedule the next refresh
      (refreshes at TTL - 120 to be safe).
    - Uses a semaphore to prevent collisions with credential consumers.
    """
    while True:
        try:
            # Fetch TURN credentials
            data = await _fetch_turn_credentials(server_url, username, password)
            if data is None:
                logger.warning("TURN updater: failed to fetch credentials, retrying in 60s...")
                await asyncio.sleep(60)
                continue

            ttl = data.get("ttl", default_ttl)

            # Update shared credentials under semaphore
            async with _turn_semaphore:
                turn_credentials["username"] = data["username"]
                turn_credentials["password"] = data["password"]
                turn_credentials["ttl"] = ttl
                turn_credentials["urls"] = data.get("urls", [])


            # Schedule next refresh at TTL minus 2 minutes to ensure we never expire
            sleep_seconds = ttl - 120

            if ttl < 240:
                # Avoid too fast refreshes if the server returns a very short TTL
                sleep_seconds = ttl - 10


            logger.info(
                "TURN credentials updated (ttl=%ds). Next refresh in %ds.",
                ttl,
                sleep_seconds,
            )

            await asyncio.sleep(sleep_seconds)

        except asyncio.CancelledError:
            logger.info("TURN updater cancelled.")
            break
        except Exception as e:
            logger.error("TURN updater error: %s", e, exc_info=True)
            await asyncio.sleep(60)


async def main(mqtt_broker: str, mqtt_port: int, mqtt_username: str, mqtt_password: str):
    async with aiomqtt.Client(hostname=mqtt_broker, port=mqtt_port, identifier=CLIENT_ID, username=mqtt_username, password=mqtt_password) as client:
        await client.subscribe(MQTT_TOPIC)  # Subscribe to the main signaling topic
        logger.info("Subscribed to '%s'", MQTT_TOPIC)

        async for message in client.messages:
            data = json.loads(message.payload.decode())

            # Ignore messages sent by this client
            if data.get("senderId") == CLIENT_ID:
                continue

            if data["type"] == "register-viewer":
                if data["target"] != CLIENT_ID:
                    logger.info("Received registration for another client, ignoring.")
                    continue

                # Get topic and subscribe to it. Only the topic reserved to the viewer is accepted
                topic = data.get("topic")
                if topic and topic == get_topic(str(data.get("senderId"))):
                    await client.subscribe(topic)
                    await client.publish(MQTT_TOPIC, json.dumps({"type": "viewer-registered", "senderId": CLIENT_ID}))
                    logger.info("Subscribed to '%s' for viewer signaling", topic)
                else:
                    logger.warning("Missing or invalid topic provided for viewer registration.")

            if data["type"] == "offer":
                await handle_offer(data, client, video_size=args.video_size, video_device=args.video_device, video_format=args.video_format, test_mode=args.test)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WebRTC camera sender via MQTT signaling")
    parser.add_argument("--log-level", "-l", type=str, default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
                        help="Set the logging level (default: INFO)")
    # Video options default to environment variables (used by the Docker image); CLI flags take precedence.
    parser.add_argument("--video-size", "-v", type=str, default=os.environ.get("VIDEO_SIZE", "640x480"),
                        help="Set the video size for the camera (env: VIDEO_SIZE, default: 640x480)")
    parser.add_argument("--video-device", "-vd", type=str, default=os.environ.get("VIDEO_DEVICE", "/dev/video0"),
                        help="Set the video device for the camera (env: VIDEO_DEVICE, default: /dev/video0). Check README for more information on how to find the correct device. Test mode overrides this option and uses a test video stream instead.")
    parser.add_argument("--video-format", "-vf", type=str, default=os.environ.get("VIDEO_FORMAT", "v4l2"),
                        help="Set the video format for the camera (env: VIDEO_FORMAT, default: v4l2). Check README for more information on how to find the correct format. Test mode overrides this option and uses a test video stream instead.")
    parser.add_argument("--test", "-t", action="store_true", help="Run in test mode (no actual camera streaming)")

    args, _ = parser.parse_known_args()

    logging.basicConfig(level=getattr(logging, args.log_level, None),
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S")
    args = parser.parse_args()

    # Parse JSON configuration
    config: dict = load_config(CONFIG_FILE)
    mqtt_broker: str = str(config.get("broker", ""))
    mqtt_port: int = int(config.get("port", 1883))
    mqtt_username: str = str(config.get("username", ""))
    mqtt_password: str = str(config.get("password", ""))
    server_url: str = str(config.get("server_url", ""))

    if not mqtt_username or not mqtt_password:
        logger.warning("MQTT username or password not found in '%s'.", CONFIG_FILE)
        raise ValueError(f"MQTT username or password not found in '{CONFIG_FILE}'.")

    if not mqtt_broker:
        logger.error("No 'broker' specified in '%s'.", CONFIG_FILE)
        raise ValueError(f"No 'broker' specified in '{CONFIG_FILE}'.")

    if not server_url:
        logger.error("No 'server_url' specified in '%s'.", CONFIG_FILE)
        raise ValueError(f"No 'server_url' specified in '{CONFIG_FILE}'.")

    CLIENT_ID = mqtt_username
    MQTT_TOPIC = f"webrtc/{CLIENT_ID}/signaling"


    print(f"Starting WebRTC camera {CLIENT_ID} sender with MQTT signaling to broker {mqtt_broker}:{mqtt_port}")

    async def run():
        # Start TURN updater as a background task
        # The webcam credentials are the same for the server and the MQTT broker
        updater_task = asyncio.create_task(
            turn_updater(server_url, mqtt_username, mqtt_password)
        )
        try:
            await main(
                mqtt_broker, mqtt_port, mqtt_username, mqtt_password
            )
        finally:
            updater_task.cancel()
            try:
                await updater_task
            except asyncio.CancelledError:
                pass

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass