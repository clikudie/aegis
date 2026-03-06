#!/usr/bin/env python3
import argparse
import json
import os
import sys
from pathlib import Path

from pywebostv.connection import WebOSClient
from pywebostv.controls import SystemControl


def load_store(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_store(path: Path, store: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(store, f, indent=2)


def main() -> int:
    parser = argparse.ArgumentParser(description="LG webOS pair/poweroff helper")
    parser.add_argument("--host", default=os.getenv("LG_TV_HOST"), help="TV IP/hostname")
    parser.add_argument(
        "--key-file",
        default=os.getenv("LG_TV_KEY_FILE", "/data/lgtv-key.json"),
        help="Path for persisted pairing key",
    )
    parser.add_argument("--pair-only", action="store_true", help="Pair only, no power off")
    args = parser.parse_args()

    if not args.host:
        print("Missing TV host. Set --host or LG_TV_HOST.", file=sys.stderr)
        return 1

    key_path = Path(args.key_file)
    store = load_store(key_path)

    last_error: Exception | None = None
    for secure in (False, True):
        client = WebOSClient(args.host, secure=secure)
        try:
            client.connect()
            for status in client.register(store):
                if status == WebOSClient.PROMPTED:
                    print("Accept the pairing prompt on TV...")
                elif status == WebOSClient.REGISTERED:
                    save_store(key_path, store)
                    break

            if args.pair_only:
                print(f"Pairing complete. Key saved to {key_path} (secure={secure})")
                return 0

            SystemControl(client).power_off()
            print(f"Power-off command sent successfully. (secure={secure})")
            return 0
        except Exception as exc:
            last_error = exc
        finally:
            try:
                client.close()
            except Exception:
                pass

    print(f"LG control error: {last_error}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
