"""Network safety checks for URLs supplied to the scanner."""

from __future__ import annotations

import ipaddress
import socket
from functools import lru_cache
from urllib.parse import urlparse


class UnsafeUrlError(ValueError):
    pass


def _is_public_ip(value: str) -> bool:
    ip = ipaddress.ip_address(value)
    return ip.is_global


@lru_cache(maxsize=1024)
def resolve_public_host(host: str) -> tuple[str, ...]:
    try:
        literal = ipaddress.ip_address(host)
        addresses = {str(literal)}
    except ValueError:
        try:
            addresses = {
                item[4][0]
                for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
            }
        except socket.gaierror as exc:
            raise UnsafeUrlError(f"Could not resolve the domain: {host}") from exc
    if not addresses or any(not _is_public_ip(address) for address in addresses):
        raise UnsafeUrlError("The address points to a private or local network and was blocked")
    return tuple(sorted(addresses))


def validate_public_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeUrlError("Only HTTP or HTTPS URLs can be scanned")
    if not parsed.hostname or parsed.username or parsed.password:
        raise UnsafeUrlError("The website URL is invalid")
    try:
        port = parsed.port
    except ValueError as exc:
        raise UnsafeUrlError("The URL contains an invalid port") from exc
    if port and port not in {80, 443}:
        raise UnsafeUrlError("Only ports 80 and 443 are allowed")
    resolve_public_host(parsed.hostname.lower())
    return parsed.geturl()
