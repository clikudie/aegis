#!/usr/bin/env python3
import argparse
import json
import os
import socket
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from ipaddress import ip_address, ip_network
from pathlib import Path

from pywebostv.connection import WebOSClient
from pywebostv.controls import SystemControl

SSDP_DISCOVERY_TIMEOUT_SECONDS = 1.0
SSDP_DISCOVERY_ATTEMPTS = 2
SSDP_MULTICAST_ADDR = ("239.255.255.250", 1900)
SSDP_WEBOS_ST = "urn:lge-com:service:webos-second-screen:1"
LAN_SWEEP_TIMEOUT_SECONDS = 0.2
LAN_SWEEP_WORKERS = 64


def load_store(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_store(path: Path, store: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(store, f, indent=2)


def load_cached_host(path: Path | None) -> str:
    if not path:
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def save_cached_host(path: Path | None, host: str) -> None:
    if not path or not host:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(host, encoding="utf-8")
        tmp_path.replace(path)
    except OSError:
        return


def discover_webos_hosts(timeout_seconds: float = SSDP_DISCOVERY_TIMEOUT_SECONDS) -> list[str]:
    request = (
        "M-SEARCH * HTTP/1.1\r\n"
        f"HOST: {SSDP_MULTICAST_ADDR[0]}:{SSDP_MULTICAST_ADDR[1]}\r\n"
        'MAN: "ssdp:discover"\r\n'
        "MX: 1\r\n"
        f"ST: {SSDP_WEBOS_ST}\r\n"
        "\r\n"
    ).encode("utf-8")
    seen: set[str] = set()
    found: list[str] = []
    with closing(socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)) as sock:
        sock.settimeout(timeout_seconds)
        sock.sendto(request, SSDP_MULTICAST_ADDR)
        while True:
            try:
                _, addr = sock.recvfrom(2048)
            except socket.timeout:
                break
            host = addr[0]
            if host not in seen:
                seen.add(host)
                found.append(host)
    return found


def subnet_candidates_from_host(host: str) -> list[str]:
    value = host.strip()
    if not value:
        return []
    try:
        addr = ip_address(value)
    except ValueError:
        return []
    if addr.version != 4:
        return []
    net = ip_network(f"{addr}/24", strict=False)
    base_last = int(str(addr).split(".")[3])
    candidates = [str(candidate) for candidate in net.hosts() if candidate != addr]
    candidates.sort(key=lambda item: abs(int(item.split(".")[3]) - base_last))
    return candidates


def probe_webos_ssdp_unicast(host: str, timeout_seconds: float = LAN_SWEEP_TIMEOUT_SECONDS) -> bool:
    request = (
        "M-SEARCH * HTTP/1.1\r\n"
        f"HOST: {host}:1900\r\n"
        'MAN: "ssdp:discover"\r\n'
        "MX: 1\r\n"
        f"ST: {SSDP_WEBOS_ST}\r\n"
        "\r\n"
    ).encode("utf-8")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.settimeout(timeout_seconds)
    try:
        sock.sendto(request, (host, 1900))
        while True:
            try:
                payload, addr = sock.recvfrom(2048)
            except socket.timeout:
                return False
            if addr[0] != host:
                continue
            text = payload.decode("utf-8", errors="ignore").lower()
            if SSDP_WEBOS_ST in text:
                return True
    finally:
        sock.close()


def probe_webos_ports_quick(host: str, timeout_seconds: float = LAN_SWEEP_TIMEOUT_SECONDS) -> bool:
    for port in (3001, 3000):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout_seconds)
        try:
            if sock.connect_ex((host, port)) == 0:
                return True
        finally:
            sock.close()
    return False


def sweep_subnet_for_webos(host: str) -> list[str]:
    candidates = subnet_candidates_from_host(host)
    if not candidates:
        return []
    with ThreadPoolExecutor(max_workers=LAN_SWEEP_WORKERS) as pool:
        matches = list(pool.map(probe_webos_ssdp_unicast, candidates))
    filtered = [candidate for candidate, match in zip(candidates, matches) if match]
    if filtered:
        return filtered
    with ThreadPoolExecutor(max_workers=LAN_SWEEP_WORKERS) as pool:
        results = list(pool.map(probe_webos_ports_quick, candidates))
    return [candidate for candidate, is_on in zip(candidates, results) if is_on]


def unique_hosts(hosts: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for host in hosts:
        value = host.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def try_hosts(
    hosts: list[str],
    store: dict,
    key_path: Path,
    host_cache_path: Path | None,
    pair_only: bool,
) -> tuple[bool, Exception | None]:
    last_error: Exception | None = None
    for host in hosts:
        for secure in (False, True):
            client = WebOSClient(host, secure=secure)
            try:
                client.connect()
                for status in client.register(store):
                    if status == WebOSClient.PROMPTED:
                        print("Accept the pairing prompt on TV...")
                    elif status == WebOSClient.REGISTERED:
                        save_store(key_path, store)
                        break

                save_cached_host(host_cache_path, host)
                if pair_only:
                    print(f"Pairing complete. Key saved to {key_path}; host={host} (secure={secure})")
                    return True, None

                SystemControl(client).power_off()
                print(f"Power-off command sent successfully. host={host} (secure={secure})")
                return True, None
            except Exception as exc:
                last_error = exc
            finally:
                try:
                    client.close()
                except Exception:
                    pass
    return False, last_error


def main() -> int:
    parser = argparse.ArgumentParser(description="LG webOS pair/poweroff helper")
    parser.add_argument("--host", default=os.getenv("LG_TV_HOST"), help="TV IP/hostname")
    parser.add_argument(
        "--key-file",
        default=os.getenv("LG_TV_KEY_FILE", "/data/lgtv-key.json"),
        help="Path for persisted pairing key",
    )
    parser.add_argument(
        "--host-cache-file",
        default=os.getenv("LG_TV_HOST_CACHE_FILE", "/data/lgtv-host.txt"),
        help="Path for persisted discovered host",
    )
    parser.add_argument("--pair-only", action="store_true", help="Pair only, no power off")
    args = parser.parse_args()

    key_path = Path(args.key_file)
    host_cache_path = Path(args.host_cache_file) if args.host_cache_file else None
    store = load_store(key_path)
    primary_hosts = unique_hosts([args.host or "", load_cached_host(host_cache_path)])

    ok, last_error = try_hosts(primary_hosts, store, key_path, host_cache_path, args.pair_only)
    if ok:
        return 0

    fallback_hosts: list[str] = []
    for _ in range(SSDP_DISCOVERY_ATTEMPTS):
        fallback_hosts.extend(discover_webos_hosts())
    for seed in primary_hosts:
        fallback_hosts.extend(sweep_subnet_for_webos(seed))
    discovered_hosts = unique_hosts(fallback_hosts)
    if not primary_hosts and not discovered_hosts:
        print("No LG TV host found. Set --host, or keep TV/phone on same LAN for discovery.", file=sys.stderr)
        return 1

    ok, discovered_error = try_hosts(discovered_hosts, store, key_path, host_cache_path, args.pair_only)
    if ok:
        return 0
    if discovered_error is not None:
        last_error = discovered_error

    print(f"LG control error: {last_error}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
