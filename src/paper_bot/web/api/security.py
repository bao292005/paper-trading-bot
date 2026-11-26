"""Kiểm tra nguồn gọi: web app chỉ phục vụ trang chạy trên chính máy này (127.0.0.1 / localhost)."""

from typing import Optional
from urllib.parse import urlsplit

def host_only(value: Optional[str]) -> Optional[str]:
    """'127.0.0.1:8600' hoặc 'http://localhost:8600' -> 'localhost' (bỏ scheme và cổng)."""
    if not value:
        return None
    parts = urlsplit(value if "//" in value else "//" + value)
    return (parts.hostname or "").lower() or None


def is_local(value: Optional[str]) -> bool:
    return host_only(value) in ("127.0.0.1", "localhost", "::1")


def origin_ok(origin: Optional[str], host: Optional[str]) -> bool:
    """Origin (nếu trình duyệt gửi) và Host đều phải là localhost. Không có Origin (curl, test) thì chỉ xét Host."""
    return is_local(host) and (origin is None or is_local(origin))
