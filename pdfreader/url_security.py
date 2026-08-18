# -*- coding: utf-8 -*-
"""API base URL security checks shared by translation and vision clients."""
from __future__ import annotations

import ipaddress
from urllib.parse import urlparse


class InsecureBaseUrlError(ValueError):
    """Raised when a remote plaintext HTTP endpoint is not explicitly allowed."""


def _is_loopback_host(hostname: str | None) -> bool:
    if not hostname:
        return False
    host = hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_base_url(base_url: str, *, allow_insecure_http: bool = False) -> str:
    """Validate and normalize an OpenAI-compatible API base URL.

    HTTPS is always accepted. Plain HTTP is accepted for loopback hosts, or for
    remote hosts only when allow_insecure_http is explicitly enabled.
    """
    value = (base_url or "").strip().rstrip("/")
    if not value:
        raise ValueError("API base_url 不能为空")

    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("API base_url 必须使用 http:// 或 https://")
    if not parsed.hostname:
        raise ValueError("API base_url 缺少有效主机名")
    if parsed.username or parsed.password:
        raise ValueError("API base_url 不应包含用户名或密码")
    if parsed.query or parsed.fragment:
        raise ValueError("API base_url 不应包含查询参数或片段")

    if parsed.scheme == "http" and not _is_loopback_host(parsed.hostname):
        if not allow_insecure_http:
            raise InsecureBaseUrlError(
                "远程 HTTP 接口未加密，会明文发送 API Key 和文献内容；"
                "请改用 HTTPS，或在可信中转站场景显式设置 allow_insecure_http=true"
            )

    return value
