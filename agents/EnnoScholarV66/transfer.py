"""Public-only, bounded downloads with validated redirects and resumable retries."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import socket
import tempfile
import time
from pathlib import Path
from urllib.parse import urljoin

import httpx


class DownloadError(ValueError):
    def __init__(self, code, message, status=None):
        super().__init__(message)
        self.code, self.status = code, status


def public_target(url):
    original = httpx.URL(url)
    if (original.scheme not in {"http", "https"} or not original.host or original.userinfo
            or original.port not in {None, 80, 443}):
        raise DownloadError("unsafe_url", "Lien public HTTP(S), sans identifiants, requis.")
    addresses = socket.getaddrinfo(original.host, original.port or (443 if original.scheme == "https" else 80), type=socket.SOCK_STREAM)
    ips = list(dict.fromkeys(item[4][0] for item in addresses))
    if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
        raise DownloadError("unsafe_url", "Adresse privée ou réservée interdite.")
    return original, original.copy_with(host=ips[0])


def download(url, byte_limit, check=lambda: None, *, cache_dir=None, seconds=180, retries=2, transport=None):
    if cache_dir is None:
        with tempfile.TemporaryDirectory(prefix="scholar-download-") as directory:
            return download(url, byte_limit, check, cache_dir=Path(directory), seconds=seconds, retries=retries, transport=transport)
    folder = Path(cache_dir)
    folder.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(url.encode()).hexdigest()
    part, metadata = folder / (key + ".part"), folder / (key + ".json")
    try:
        state = json.loads(metadata.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    deadline = time.monotonic() + seconds
    last = None
    with httpx.Client(trust_env=False, follow_redirects=False, transport=transport) as client:
        for attempt in range(retries + 1):
            current = url
            try:
                for hop in range(7):
                    check()
                    if hop == 6:
                        raise DownloadError("redirect_limit", "Trop de redirections.")
                    original, target = public_target(current)
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise DownloadError("timeout", "Délai total du téléchargement atteint ; reprise disponible.")
                    headers = {"Host": original.netloc.decode(), "User-Agent": "EnnoScholarV66/1.0 (research)",
                               "Accept-Encoding": "identity"}
                    size = part.stat().st_size if part.exists() else 0
                    validator = state.get("etag") or state.get("last_modified")
                    resume = size > 0 and state.get("url") == str(original) and validator
                    if resume:
                        headers.update({"Range": f"bytes={size}-", "If-Range": validator})
                    timeout = httpx.Timeout(min(45, remaining), connect=min(15, remaining))
                    with client.stream("GET", target, headers=headers, extensions={"sni_hostname": original.host}, timeout=timeout) as response:
                        if response.is_redirect:
                            location = response.headers.get("location")
                            if not location:
                                raise DownloadError("redirect_invalid", "Redirection sans destination.")
                            current = urljoin(str(original), location)
                            continue
                        if response.status_code == 416 and resume:
                            part.unlink(missing_ok=True)
                            state = {}
                            raise httpx.ReadError("Range expired")
                        if response.status_code >= 400:
                            code = "access_denied" if response.status_code in {401, 403} else "not_found" if response.status_code == 404 else "http_error"
                            last = DownloadError(code, f"Le serveur a répondu HTTP {response.status_code}.", response.status_code)
                            if response.status_code == 429 or response.status_code >= 500:
                                raise httpx.ReadError("Temporary HTTP failure")
                            raise last
                        append = response.status_code == 206 and bool(resume)
                        if response.status_code == 206:
                            content_range = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("content-range", ""))
                            returned_validator = response.headers.get("etag") or response.headers.get("last-modified")
                            if (not append or not content_range or int(content_range[1]) != size
                                    or (returned_validator and returned_validator != validator)):
                                part.unlink(missing_ok=True)
                                state = {}
                                raise httpx.ReadError("Invalid resumed representation")
                            expected = int(content_range[3])
                        else:
                            size = 0
                            length = response.headers.get("content-length", "")
                            expected = int(length) if length.isdigit() and not response.headers.get("content-encoding") else None
                        if expected is not None and expected > byte_limit:
                            part.unlink(missing_ok=True)
                            raise DownloadError("too_large", f"Document supérieur à la limite configurée ({byte_limit // 1024 // 1024} Mo).")
                        state = {"url": str(original), "etag": response.headers.get("etag") or (state.get("etag") if append else None),
                                 "last_modified": response.headers.get("last-modified") or (state.get("last_modified") if append else None), "expected_bytes": expected,
                                 "content_type": response.headers.get("content-type", "")}
                        metadata.write_text(json.dumps(state), encoding="utf-8")
                        with part.open("ab" if append else "wb") as stream:
                            for chunk in response.iter_bytes(64 * 1024):
                                check()
                                if time.monotonic() >= deadline:
                                    raise DownloadError("timeout", "Délai total du téléchargement atteint ; reprise disponible.")
                                size += len(chunk)
                                if size > byte_limit:
                                    raise DownloadError("too_large", f"Document supérieur à la limite configurée ({byte_limit // 1024 // 1024} Mo).")
                                stream.write(chunk)
                        if expected is not None and size != expected:
                            raise httpx.ReadError("Incomplete transfer")
                        data = part.read_bytes()
                        part.unlink(missing_ok=True)
                        metadata.unlink(missing_ok=True)
                        return data, str(original), state["content_type"]
                raise DownloadError("redirect_limit", "Trop de redirections.")
            except DownloadError as exc:
                last = exc
                break
            except (httpx.HTTPError, OSError) as exc:
                if not last or last.code not in {"http_error"}:
                    last = DownloadError("timeout" if isinstance(exc, httpx.TimeoutException) else "network_error",
                                         "Connexion interrompue ou transfert incomplet ; reprise disponible.")
                if attempt < retries and time.monotonic() < deadline:
                    check()
                    time.sleep(.2)
                    continue
                break
    state["last_error"] = {"code": last.code, "message": str(last), "status": last.status}
    metadata.write_text(json.dumps(state), encoding="utf-8")
    if last.code == "too_large":
        part.unlink(missing_ok=True)
    raise last
