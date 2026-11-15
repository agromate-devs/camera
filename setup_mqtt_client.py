import argparse
import json
import secrets
import string


def generate_username(prefix: str = "cam_", length: int = 8) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return prefix + "".join(secrets.choice(alphabet) for _ in range(length))


def generate_password(length: int = 24) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate webcam credentials. They must be added to the user account from the Agromate app, "
                    "which registers the webcam on the server and on the MQTT broker."
    )
    parser.add_argument(
        "--broker", "-b",
        type=str,
        required=True,
        help="MQTT broker hostname",
    )
    parser.add_argument(
        "--port", "-p",
        type=int,
        default=1883,
        help="MQTT broker port (default: 1883)",
    )
    parser.add_argument(
        "--server-url",
        type=str,
        required=True,
        help="Agromate server base URL (e.g. http://192.168.1.59:3000)",
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default="camera_config.json",
        help="Output file path for the configuration JSON (default: camera_config.json)",
    )

    args = parser.parse_args()

    # The username is the webcam ID: used on the server, as MQTT username and for signaling
    username = generate_username()
    password = generate_password()

    config = {
        "broker": args.broker,
        "port": args.port,
        "username": username,
        "password": password,
        "server_url": args.server_url,
    }

    with open(args.output, "w") as f:
        json.dump(config, f, indent=2)

    print(f"[OK] Config saved in: {args.output}")
    print("Add the webcam from the Agromate app with these credentials:")
    print(f"  Username: {username}")
    print(f"  Password: {password}")


if __name__ == "__main__":
    main()
