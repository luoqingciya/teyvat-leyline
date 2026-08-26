"""HTTP 探测与下载请求相关工具（基于 httpx）。"""

from __future__ import annotations

import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse, urlunparse

import httpx

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36 TeyuatLeyline/0.1"
)

CHUNK_SIZE = 256 * 1024          # 单次读取 256 KiB
MAX_PROBE_RETRIES = 2
CONNECT_TIMEOUT = 10.0
READ_TIMEOUT = 60.0


@dataclass
class ProbeResult:
    """服务器在下载前探测得到的关键信息。"""

    content_length: int | None
    supports_range: bool
    filename: str
    content_type: str
    etag: str
    last_modified: str
    final_url: str
    method: str


def _safe_client(*, verify: bool = True) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": DEFAULT_USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
        follow_redirects=True,
        verify=verify,
        timeout=httpx.Timeout(connect=CONNECT_TIMEOUT, read=READ_TIMEOUT, write=30.0, pool=10.0),
    )


def _parse_content_length(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_content_range(value: str | None) -> int | None:
    """从 ``Content-Range: bytes 0-0/12345`` 提取总长度。"""
    if not value:
        return None
    match = re.search(r"bytes\s+\d+-\d+/(\d+)", value, re.IGNORECASE)
    if not match:
        return None
    total = match.group(1)
    if total == "*":
        return None
    try:
        return int(total)
    except ValueError:
        return None


def _filename_from_disposition(value: str | None, url: str) -> str:
    if value:
        raw = re.search(r"filename\*=UTF-8''([^;]+)", value, re.IGNORECASE)
        if raw:
            return unquote(raw.group(1).strip())
        raw = re.search(r'filename="?([^";]+)"?', value, re.IGNORECASE)
        if raw:
            return raw.group(1).strip()
    # 回退到 URL 路径
    path = urlparse(url).path
    name = unquote(path.rsplit("/", 1)[-1]) if path else ""
    return name or "download.bin"


def _suggest_filename(url: str, content_type: str | None) -> str:
    name = unquote(urlparse(url).path.rsplit("/", 1)[-1]) if urlparse(url).path else ""
    if not name:
        name = "download"
    ext = mimetypes.guess_extension(content_type or "") or ""
    if ext and not Path(name).suffix:
        name += ext
    return name or "download.bin"


def probe(url: str, *, verify: bool = True, extra_headers: dict[str, str] | None = None) -> ProbeResult:
    """探测远程文件：大小、是否支持 Range、推荐文件名。

    优先 HEAD；若服务器拒绝 HEAD（405/501/403 等），则用 ``Range: bytes=0-0``
    的 GET 探一下 ``Content-Range`` 头。
    """
    headers = {"Accept": "*/*"}
    if extra_headers:
        headers.update(extra_headers)

    with _safe_client(verify=verify) as client:
        resp = client.head(url, headers=headers)
        method = "HEAD"

        if resp.status_code in (405, 501, 403):
            resp = client.get(url, headers={**headers, "Range": "bytes=0-0"})
            method = "GET(0-0)"

        supports_range = "bytes" in (resp.headers.get("accept-ranges", "") or "").lower()
        content_length = _parse_content_length(resp.headers.get("content-length"))

        if method == "GET(0-0)":
            cr_total = _parse_content_range(resp.headers.get("content-range"))
            if cr_total is not None:
                content_length = cr_total
                supports_range = True

        content_type = resp.headers.get("content-type", "")
        fname = _filename_from_disposition(resp.headers.get("content-disposition"), url)
        if not Path(fname).suffix:
            fname = _suggest_filename(url, content_type)

        return ProbeResult(
            content_length=content_length,
            supports_range=supports_range,
            filename=fname,
            content_type=content_type,
            etag=resp.headers.get("etag", ""),
            last_modified=resp.headers.get("last-modified", ""),
            final_url=str(resp.url),
            method=method,
        )


def sanitize_filename(name: str) -> str:
    """去掉 Windows/常见非法字符，避免保存失败。"""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip().rstrip(". ")
    return cleaned[:200] or "download.bin"


def unique_dest(directory: str, filename: str) -> str:
    """若目标文件已存在，追加 ``(1)``/``(2)`` 等后缀。"""
    directory = directory or "."
    path = Path(directory) / filename
    if not path.exists():
        return str(path)
    stem, suffix = path.stem, path.suffix
    i = 1
    while True:
        candidate = Path(directory) / f"{stem} ({i}){suffix}"
        if not candidate.exists():
            return str(candidate)
        i += 1


def url_without_query(url: str) -> str:
    """去掉查询串（用于命名临时/校验文件，避免文件名带长参数）。"""
    parsed = urlparse(url)
    return urlunparse(parsed._replace(query=""))
