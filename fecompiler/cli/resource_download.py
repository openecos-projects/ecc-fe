"""Fetch a resource archive over a network that may be slow or drop out.

What a download is checked against, its size and SHA256, comes from the
registry, so where the bytes come from does not have to be trusted. That is
what lets this module

* continue a download that was cut off, from the bytes already on disk;
* try again after an error, a few times, waiting longer each time;
* take the archive from a mirror, and fall back to the original address;
* reuse an archive that is already complete in the cache.

A download is written to ``<target>.part`` and renamed to ``<target>`` only
once its size and checksum are right.
"""

from __future__ import annotations

import hashlib
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path

MIRRORS_ENV = "ECOS_RESOURCE_MIRRORS"
ATTEMPTS_ENV = "ECOS_DOWNLOAD_ATTEMPTS"
TIMEOUT_ENV = "ECOS_DOWNLOAD_TIMEOUT"
DEFAULT_ATTEMPTS = 4
DEFAULT_TIMEOUT_SECONDS = 60.0
# Seconds waited before the second, third, ... attempt at one address.
_BACKOFF_SECONDS = (1.0, 3.0, 10.0, 30.0)
_CHUNK = 1024 * 1024

# (phase, fields): "downloading" with received/total, "resuming", "retrying",
# "mirror", "cached".
Notify = Callable[[str, dict[str, object]], None]


class DownloadError(RuntimeError):
    """The archive could not be fetched, or is not the one the registry names."""


def mirror_rules(environ: Mapping[str, str] | None = None) -> list[tuple[str, str]]:
    """Return the ``prefix=replacement`` rules of ``ECOS_RESOURCE_MIRRORS``.

    Rules are separated by whitespace or commas. An address that starts with
    a rule's prefix is also tried with the prefix replaced.
    """
    env = environ if environ is not None else os.environ
    rules: list[tuple[str, str]] = []
    for item in env.get(MIRRORS_ENV, "").replace(",", " ").split():
        prefix, separator, replacement = item.partition("=")
        if not separator or not prefix or not replacement:
            raise DownloadError(
                f"{MIRRORS_ENV} entries must look like <prefix>=<replacement>: {item}"
            )
        rules.append((prefix, replacement))
    return rules


def candidate_urls(url: str, rules: list[tuple[str, str]]) -> list[str]:
    """Return the addresses to try for *url*: its mirrors first, then itself."""
    mirrored = [
        replacement + url[len(prefix) :] for prefix, replacement in rules if url.startswith(prefix)
    ]
    return list(dict.fromkeys([*mirrored, url]))


def _setting(environ: Mapping[str, str], name: str, default: float, minimum: float) -> float:
    try:
        value = float(environ.get(name, "") or default)
    except ValueError:
        return default
    return value if value >= minimum else default


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def is_complete(path: Path, *, size: int | None, sha256: str) -> bool:
    """Whether *path* already is the archive the registry names."""
    try:
        if not path.is_file() or (size is not None and path.stat().st_size != size):
            return False
        return file_sha256(path) == sha256.lower()
    except OSError:
        return False


def fetch(
    url: str,
    target: Path,
    *,
    size: int | None,
    sha256: str,
    notify: Notify | None = None,
    environ: Mapping[str, str] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> str:
    """Make *target* the archive of *size* and *sha256*; return how it got there.

    Returns ``"cached"`` when it was complete already, otherwise the address
    it was downloaded from. Raises :class:`DownloadError` when every address
    failed; what was received so far stays in ``<target>.part`` for the next
    call to continue.
    """
    env = environ if environ is not None else os.environ
    tell = notify or (lambda phase, fields: None)
    pause = sleep or time.sleep
    if is_complete(target, size=size, sha256=sha256):
        tell("cached", {"path": str(target)})
        return "cached"
    target.unlink(missing_ok=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    attempts = int(_setting(env, ATTEMPTS_ENV, DEFAULT_ATTEMPTS, 1))
    timeout = _setting(env, TIMEOUT_ENV, DEFAULT_TIMEOUT_SECONDS, 1)
    addresses = candidate_urls(url, mirror_rules(env))
    failures: list[str] = []
    for address in addresses:
        if address != url:
            tell("mirror", {"url": address})
        attempt = 0
        while attempt < attempts:
            attempt += 1
            try:
                continued = _receive(address, partial, size=size, timeout=timeout, tell=tell)
                actual = file_sha256(partial)
                if actual != sha256.lower():
                    # Nothing of a file with the wrong content is worth keeping.
                    partial.unlink(missing_ok=True)
                    if continued:
                        # The bytes it was continued from may have been the
                        # wrong ones: fetch it whole, once, before judging.
                        attempt -= 1
                        continue
                    raise _WrongContent(
                        f"SHA256 verification failed for {address}: "
                        f"expected {sha256}, received {actual}"
                    )
                os.replace(partial, target)
                return address
            except (OSError, DownloadError) as error:
                reason = _reason(error)
                failures.append(f"{address}: {reason}")
                if attempt == attempts or _is_final(error):
                    # This address is given up; the next one, if any, is tried.
                    break
                wait = _BACKOFF_SECONDS[min(attempt - 1, len(_BACKOFF_SECONDS) - 1)]
                tell(
                    "retrying",
                    {
                        "url": address,
                        "attempt": attempt + 1,
                        "of": attempts,
                        "after_s": wait,
                        "reason": reason,
                    },
                )
                pause(wait)
    raise DownloadError(
        f"Download failed for {url}: " + "; ".join(dict.fromkeys(failures[-3:]))
    )


class _WrongContent(DownloadError):
    """A complete download whose content is not what the registry names."""


def _is_final(error: BaseException) -> bool:
    """An answer that trying the same address again will not change.

    That is a refusal by the server, and a file that arrived whole with the
    wrong content: fetching it again would bring the same bytes.
    """
    if isinstance(error, _WrongContent):
        return True
    return isinstance(error, urllib.error.HTTPError) and error.code in {400, 401, 403, 404, 410}


def _reason(error: BaseException) -> str:
    if isinstance(error, urllib.error.HTTPError):
        return f"HTTP {error.code} {error.reason}"
    if isinstance(error, urllib.error.URLError):
        return str(error.reason)
    return str(error) or type(error).__name__


def _receive(
    url: str, partial: Path, *, size: int | None, timeout: float, tell: Notify
) -> bool:
    """Bring *partial* to *size* bytes from *url*, continuing what is there.

    Returns whether bytes that were on disk before the call are part of it.
    """
    have = partial.stat().st_size if partial.is_file() else 0
    if size is not None and have > size:
        partial.unlink()
        have = 0
    if size is not None and have == size:
        return True
    request = urllib.request.Request(url)
    if have:
        request.add_header("Range", f"bytes={have}-")
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        if error.code == 416 and have:
            # The server has no such range: what is on disk is not this file.
            partial.unlink(missing_ok=True)
            raise DownloadError("the server refused to continue the download") from error
        raise
    with response:
        # Only "206 Partial Content" continues; anything else sends the whole file.
        continued = have > 0 and getattr(response, "status", None) == 206
        if continued:
            tell("resuming", {"url": url, "received": have, "total": size})
        else:
            have = 0
        reported = -1
        with partial.open("ab" if continued else "wb") as output:
            while chunk := response.read(_CHUNK):
                output.write(chunk)
                have += len(chunk)
                if size:
                    step = min(10, have * 10 // size)
                    if step > reported:
                        reported = step
                        tell("downloading", {"received": have, "total": size})
    if size is not None and have != size:
        if have > size:
            partial.unlink(missing_ok=True)
        raise DownloadError(f"the download ended at {have} of {size} bytes")
    return continued


__all__ = [
    "ATTEMPTS_ENV",
    "DownloadError",
    "MIRRORS_ENV",
    "TIMEOUT_ENV",
    "candidate_urls",
    "fetch",
    "file_sha256",
    "is_complete",
    "mirror_rules",
]
