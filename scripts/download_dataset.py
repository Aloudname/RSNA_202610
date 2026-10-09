"""Extract dataset bundle without ever storing the 265 GB zip.

Kaggle serves the whole competition as one zip64 archive on GCS.
It's recommended to read the archive through HTTP requests,
inflates it on the fly and writes only the extracted files.

Layout written under --dest (matches the archive's own entry names):

    train.csv  train_series.csv  test.csv  test_series.csv  sample_submission.csv
    train_series/<StudyInstanceUID>/<SeriesInstanceUID>/<SOPInstanceUID>.dcm
    test_series/<StudyInstanceUID>/<SeriesInstanceUID>/<SOPInstanceUID>.dcm

Pipeline: 
One reader thread walks the archive in offset order,
so the network link sees a single sequential stream, kept busy by a block prefetcher,
and a pool of writer threads inflates and stores entries concurrently.
Every entry is CRC32-verified against the archive index while it is written,
and the run is resumable: entries already on disk at the expected size are skipped,
so re-running after an interruption continues where it left off.

Usage:
    uv run scripts/download_dataset.py                 # -> $RAW_DIR from .env
    uv run scripts/download_dataset.py --limit 200     # smoke test
    uv run scripts/download_dataset.py --verify-only   # re-check sizes only
"""

from __future__ import annotations

import argparse
import io
import os
import struct
import sys
import threading
import time
import zipfile
import zlib
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

import requests
from dotenv import load_dotenv

COMPETITION = "rsna-knee-abnormality-detection"
DOWNLOAD_ALL_URL = "https://www.kaggle.com/api/v1/competitions/data/download-all/{comp}"
HEADERS = {"User-Agent": "rsna-202610-downloader/1.0"}
READ_TIMEOUT = (20, 180)  # (connect, read) seconds
LOCAL_HEADER = 30  # fixed part of a zip local file header

# auth + bundle location
def get_token() -> str:
    """Access token from the credentials `kaggle auth login` cached (auto-refreshed)."""
    from kagglesdk.kaggle_client import KaggleClient  # type: ignore
    from kagglesdk.kaggle_creds import KaggleCredentials  # type: ignore

    creds = KaggleCredentials.load(KaggleClient())
    if creds is None:
        sys.exit("No cached Kaggle credentials. Run `kaggle auth login` first.")
    token = creds.get_access_token()
    if not token:
        sys.exit("Could not obtain an access token. Run `kaggle auth login` first.")
    return token


class BundleLocator:
    """Resolves the signed GCS URL of the bundle, re-signing it when it expires."""

    def __init__(self, competition: str = COMPETITION):
        self._competition = competition
        self._url: str | None = None
        self._lock = threading.Lock()
        self._token = get_token()
        self.resigns = 0

    def url(self, refresh: bool = False) -> str:
        with self._lock:
            if self._url is not None and not refresh:
                return self._url
            url = self._request_url()
            if url is None:  # token may simply have aged out; retry once
                self._token = get_token()
                url = self._request_url()
            if url is None:
                sys.exit(
                    f"Could not resolve the bundle. Are you joined to the competition? "
                    f"(accept the rules at https://www.kaggle.com/competitions/"
                    f"{self._competition}/rules)"
                )
            self._url = url
            if refresh:
                self.resigns += 1
            return self._url

    def _request_url(self) -> str | None:
        response = requests.get(
            DOWNLOAD_ALL_URL.format(comp=self._competition),
            headers={"Authorization": f"Bearer {self._token}", **HEADERS},
            allow_redirects=False,
            timeout=300,
        )
        if response.status_code == 302 and "Location" in response.headers:
            return response.headers["Location"]
        if response.status_code not in (401, 403, 429):
            print(f"[warn] bundle lookup returned HTTP {response.status_code}",
                file=sys.stderr, flush=True)
        return None

    def size(self) -> int:
        response = requests.head(self.url(), headers=HEADERS, timeout=60)
        response.raise_for_status()
        return int(response.headers["Content-Length"])


class _NeedsResign(Exception):
    """Signed URL was rejected; fetch a fresh one and retry."""


# seekable remote file
class ParallelHTTPFile(io.RawIOBase):
    """Read-only seekable view of a remote file: block cache + parallel prefetch."""

    def __init__(self, locator: BundleLocator, size: int, *, block_size: int,
                    workers: int, cache_blocks: int, blocks_per_session: int = 16):
        self._locator = locator
        self._size = size
        self._block_size = block_size
        self._nblocks = (size + block_size - 1) // block_size
        self._workers = workers
        self._cache_blocks = cache_blocks
        self._blocks_per_session = blocks_per_session or float("inf")
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="fetch")
        self._lock = threading.Lock()
        self._local = threading.local()
        self._cache: OrderedDict[int, bytes] = OrderedDict()
        self._inflight: dict[int, Future] = {}
        self._pos = 0
        self.net_bytes = 0
        self.net_seconds = 0.0
        self.retries = 0
        self.session_recycles = 0

    def _session(self) -> requests.Session:
        """One keep-alive session per worker: at 16-32 MB per block a fresh TLS
        handshake costs more than the transfer itself."""
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.mount("https://", requests.adapters.HTTPAdapter( # type: ignore
                pool_connections=1, pool_maxsize=1, max_retries=0))
            session.headers.update(HEADERS)
            self._local.session = session
            self._local.blocks = 0
        return session

    def _drop_session(self) -> None:
        """Close this worker's connection so its next request opens a fresh one.

        GCS connections degrade once they have moved a couple of GB:
        mid-run the 24 established sessions were down to ~10 MB/s *combined*,
        while 8 brand new connections pulled 42 MB/s at the same moment.
        Recycling a session every few hundred MB keeps every worker on a fast connection.
        """
        session = getattr(self._local, "session", None)
        if session is not None:
            session.close()
            self._local.session = None
            with self._lock:
                self.session_recycles += 1

    # io.RawIOBase plumbing
    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            new = offset
        elif whence == io.SEEK_CUR:
            new = self._pos + offset
        elif whence == io.SEEK_END:
            new = self._size + offset
        else:
            raise ValueError(f"bad whence {whence}")
        if new < 0:
            raise ValueError("negative seek position")
        self._pos = new
        return new

    def readinto(self, buf: Any) -> int:
        view = memoryview(buf)
        want = len(view)
        got = 0
        while got < want and self._pos < self._size:
            index, offset = divmod(self._pos, self._block_size)
            block = self._block(index)
            take = min(want - got, len(block) - offset)
            view[got:got + take] = block[offset:offset + take]
            got += take
            self._pos += take
        return got

    # block machinery
    def _block(self, index: int) -> bytes:
        with self._lock:
            block = self._cache.get(index)
            if block is not None:
                self._cache.move_to_end(index)
                return block
            future = self._inflight.get(index)
            if future is None:
                future = self._pool.submit(self._fetch, index)
                self._inflight[index] = future
        block = future.result()  # outside the lock: other threads may share it
        self._prefetch(index + 1)
        return block

    def _prefetch(self, first: int) -> None:
        budget = max(1, self._workers - 1)  # keep one worker for demand fetches
        with self._lock:
            for index in range(first, min(first + budget, self._nblocks)):
                if len(self._inflight) >= budget:
                    break
                if index in self._cache or index in self._inflight:
                    continue
                self._inflight[index] = self._pool.submit(self._fetch, index)

    def _fetch(self, index: int) -> bytes:
        try:
            start = index * self._block_size
            end = min(start + self._block_size, self._size) - 1
            data = self._get_range(start, end)
            with self._lock:
                self._cache[index] = data
                self._cache.move_to_end(index)
                while len(self._cache) > self._cache_blocks:
                    self._cache.popitem(last=False)
            used = getattr(self._local, "blocks", 0) + 1
            self._local.blocks = used
            if used >= self._blocks_per_session:
                self._drop_session()  # retire this connection before it degrades
            return data
        finally:
            with self._lock:
                self._inflight.pop(index, None)

    def _get_range(self, start: int, end: int) -> bytes:
        wanted = end - start + 1
        needs_resign = False
        delay = 1.0
        reason = ""
        for _ in range(8):
            url = self._locator.url(refresh=needs_resign)
            needs_resign = False
            try:
                started = time.time()
                response = self._session().get(
                    url,
                    headers={"Range": f"bytes={start}-{end}", "Accept-Encoding": "identity"},
                    stream=True,
                    timeout=READ_TIMEOUT,
                )
                try:
                    if response.status_code == 206:
                        data = response.content
                        if len(data) != wanted:
                            raise IOError(f"short block: {len(data)} of {wanted} bytes")  # noqa: UP024
                        with self._lock:
                            self.net_bytes += len(data)
                            self.net_seconds += time.time() - started
                        return data
                    if response.status_code in (401, 403):
                        raise _NeedsResign()
                    reason = ("rate limited (429)" if response.status_code == 429
                              else f"HTTP {response.status_code}")
                finally:
                    response.close()
            except _NeedsResign:
                needs_resign = True
                reason = "signed URL rejected"
            except requests.RequestException as exc:
                reason = f"{type(exc).__name__}: {exc}"
            with self._lock:
                self.retries += 1
            self._drop_session()  # a connection that just failed must not be reused
            print(f"[retry] bytes {start}-{end}: {reason}; retrying in {delay:.0f}s",
                  file=sys.stderr, flush=True)
            time.sleep(delay)
            delay = min(delay * 2, 30)
        raise RuntimeError(f"gave up fetching bytes {start}-{end}: {reason}")

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
        super().close()


# helpers
def safe_join(root: str, name: str) -> str:
    parts = name.split("/")
    if name.startswith("/") or ".." in parts:
        raise ValueError(f"unsafe archive entry name: {name!r}")
    return os.path.join(root, *(p for p in parts if p not in ("", ".")))


def human(n: float) -> str:
    """For human readable format."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


class Reporter:
    """Throttled progress line shared by the reader and the writer pool."""

    def __init__(self, total_entries: int, total_bytes: int, remote: ParallelHTTPFile,
                 locator: BundleLocator, skipped: int):
        self._total_entries = total_entries
        self._total_bytes = total_bytes
        self._remote = remote
        self._locator = locator
        self._lock = threading.Lock()
        self.written = 0
        self.entries_written = 0
        self._entries = skipped
        self._skipped = skipped
        self._started = time.time()
        self._last = 0.0

    def add(self, nbytes: int) -> None:
        with self._lock:
            self.written += nbytes
            self.entries_written += 1
            self._entries += 1
            now = time.time()
            if now - self._last < 5.0:
                return
            self._last = now
            elapsed = now - self._started
            rate = self.written / elapsed if elapsed else 0.0
            net_rate = self._remote.net_bytes / elapsed / 1e6 if elapsed else 0.0
            eta = (self._total_bytes - self.written) / rate / 60 if rate else float("inf")
            print(
                f"[{self._entries:,}/{self._total_entries:,}] {human(self.written)} written "
                f"({self._skipped:,} skipped) | net {net_rate:.0f} MB/s "
                f"| disk {rate / 1e6:.0f} MB/s | retries {self._remote.retries} "
                f"| recycles {self._remote.session_recycles} "
                f"| resigns {self._locator.resigns} | ETA {eta:.0f} min",
                flush=True,
            )


def inflate(raw: bytes, method: int, usize: int, crc: int, name: str) -> bytes:
    """Decompress one entry, checking the archive's own size and CRC32."""
    if method == 8:
        out = zlib.decompress(raw, -zlib.MAX_WBITS)
    elif method == 0:
        out = raw
    else:
        raise ValueError(f"unsupported compression method {method} for {name}")
    if len(out) != usize:
        raise IOError(f"{name}: inflated {len(out)} bytes, expected {usize}")  # noqa: UP024
    if zlib.crc32(out) & 0xFFFFFFFF != crc:
        raise IOError(f"{name}: CRC32 mismatch")  # noqa: UP024
    return out


# extraction
def open_index(remote: ParallelHTTPFile) -> tuple[zipfile.ZipFile, list[zipfile.ZipInfo]]:
    started = time.time()
    zf = zipfile.ZipFile(remote)
    # offset order == near-sequential reads, whatever order the CD lists them in
    infos = sorted(zf.infolist(), key=lambda i: i.header_offset)
    methods = {i.compress_type for i in infos}
    if not methods <= {0, 8}:
        sys.exit(f"Unexpected compression methods in archive: {methods}")
    print(f"[zip] {len(infos):,} entries, {human(sum(i.file_size for i in infos))} "
          f"uncompressed; index read in {time.time() - started:.1f}s; "
          f"methods {sorted(methods)}", flush=True)
    return zf, infos


def extract_parallel(dest_root: str, infos: list[zipfile.ZipInfo], remote: ParallelHTTPFile,
                     locator: BundleLocator, *, write_workers: int,
                     outstanding: int) -> tuple[int, int | Reporter]:
    """Reader walks the archive in offset order; a pool inflates and stores entries.

    Returns (entries skipped as already present, bytes written)."""
    pending = threading.Semaphore(outstanding)
    pool = ThreadPoolExecutor(max_workers=write_workers, thread_name_prefix="write")
    skipped = 0
    todo = []
    for info in infos:
        if info.filename.endswith("/"):
            continue
        dest = safe_join(dest_root, info.filename)
        if os.path.exists(dest) and os.path.getsize(dest) == info.file_size:
            skipped += 1
            continue
        todo.append((info, dest))

    reporter = Reporter(len(infos), sum(info.file_size for info, _ in todo),
                        remote, locator, skipped)
    failures: list[BaseException] = []

    def store(info: zipfile.ZipInfo, raw: bytes, dest: str) -> None:
        try:
            out = inflate(raw, info.compress_type, info.file_size, info.CRC, info.filename)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb", buffering=1 << 20) as handle:
                handle.write(out)
            reporter.add(info.file_size)
        except BaseException as exc:
            if os.path.exists(dest):
                os.remove(dest)
            failures.append(exc)
            raise
        finally:
            pending.release()

    try:
        for info, dest in todo:
            if failures:
                break
            pending.acquire()
            remote.seek(info.header_offset)
            head = remote.read(LOCAL_HEADER)
            if len(head) != LOCAL_HEADER or head[:4] != b"PK\x03\x04":
                raise IOError(f"bad local header at offset {info.header_offset} "  # noqa: UP024
                              f"for {info.filename}")
            name_len, extra_len = struct.unpack("<HH", head[26:30])
            # sizes/CRC come from the central directory, which also covers
            # entries whose local header defers them to a data descriptor
            remote.seek(info.header_offset + LOCAL_HEADER + name_len + extra_len)
            raw = remote.read(info.compress_size)
            if len(raw) != info.compress_size:
                pending.release()
                raise IOError(f"short read for {info.filename}: "  # noqa: UP024
                              f"{len(raw)} of {info.compress_size} bytes")
            pool.submit(store, info, raw, dest)
    finally:
        # never cancel here: queued stores are real work, and cancelling them
        # drops entries that the reader has already accounted for
        pool.shutdown(wait=True)

    if failures:
        raise RuntimeError(f"extraction failed: {failures[0]}") from failures[0]
    return skipped, reporter


def verify_all(dest_root: str, infos: list[zipfile.ZipInfo]) -> int:
    missing = 0
    for info in infos:
        if info.filename.endswith("/"):
            continue
        dest = safe_join(dest_root, info.filename)
        if not os.path.exists(dest):
            missing += 1
            if missing <= 20:
                print(f"[verify] missing: {info.filename}", file=sys.stderr)
        elif os.path.getsize(dest) != info.file_size:
            missing += 1
            if missing <= 20:
                print(f"[verify] size mismatch: {info.filename}", file=sys.stderr)
    return missing


def extract(dest_root: str, *, limit: int | None, net_workers: int, block_mb: int,
            cache_mb: int, write_workers: int, session_blocks: int,
            verify_only: bool, dry_run: bool) -> int:
    locator = BundleLocator()
    size = locator.size()
    print(f"[bundle] {human(size)} compressed -> {dest_root}", flush=True)

    remote = ParallelHTTPFile(
        locator, size,
        block_size=block_mb << 20,
        workers=net_workers,
        cache_blocks=max(8, cache_mb // block_mb),
        blocks_per_session=session_blocks,
    )
    started = time.time()
    _zf, infos = open_index(remote)
    if limit:
        infos = infos[:limit]
    total_bytes = sum(i.file_size for i in infos)

    if verify_only:
        missing = verify_all(dest_root, infos)
        print(f"[verify] {len(infos):,} entries checked, {missing:,} missing/mismatched",
              flush=True)
        remote.close()
        return 1 if missing else 0
    if dry_run:
        print(f"[dry-run] would extract {len(infos):,} entries, {human(total_bytes)}; "
              f"nothing written", flush=True)
        remote.close()
        return 0

    skipped, reporter = extract_parallel(dest_root, infos, remote, locator,
                                         write_workers=write_workers,
                                         outstanding=max(16, write_workers * 6))
    elapsed = time.time() - started
    if isinstance(reporter, Reporter):
        print(f"\n[done] {reporter.entries_written:,} extracted, {skipped:,} already present, "
            f"{human(reporter.written)} written in {elapsed / 60:.1f} min; "
            f"net {human(remote.net_bytes)} ({remote.net_bytes / elapsed / 1e6:.0f} MB/s), "
            f"retries {remote.retries}, recycles {remote.session_recycles}, "
            f"re-signs {locator.resigns}", flush=True)
    missing = verify_all(dest_root, infos)
    if missing:
        print(f"[INCOMPLETE] {missing:,} of {len(infos):,} entries are missing or truncated "
              f"- re-run to repair (already-present entries are skipped)", file=sys.stderr)
    else:
        print(f"[verified] all {len(infos):,} entries present at the expected size", flush=True)
    remote.close()
    return 1 if missing else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dest", help="destination directory (default: $RAW_DIR from .env)")
    parser.add_argument("--limit", type=int, help="only process the first N entries (smoke test)")
    parser.add_argument("--net-workers", type=int, default=24,
                        help="parallel range requests / prefetch threads (default 24)")
    parser.add_argument("--block-mb", type=int, default=32, help="range request size (default 32)")
    parser.add_argument("--cache-mb", type=int, default=2048, help="block cache ceiling (default 2048)")
    parser.add_argument("--write-workers", type=int, default=8,
                        help="parallel inflate+write threads (default 8)")
    parser.add_argument("--session-blocks", type=int, default=16,
                        help="recycle each HTTP connection after this many blocks "
                             "(default 16; 0 disables recycling)")
    parser.add_argument("--verify-only", action="store_true",
                        help="check every entry against disk, download no file data")
    parser.add_argument("--dry-run", action="store_true", help="read the index, write nothing")
    args = parser.parse_args()

    load_dotenv()
    dest = args.dest or os.environ.get("RAW_DIR")
    if not dest:
        sys.exit("No destination: pass --dest or set RAW_DIR in .env")
    dest = os.path.abspath(os.path.expanduser(dest))
    os.makedirs(dest, exist_ok=True)
    return extract(dest, limit=args.limit, net_workers=args.net_workers, block_mb=args.block_mb,
                   cache_mb=args.cache_mb, write_workers=args.write_workers,
                   session_blocks=args.session_blocks,
                   verify_only=args.verify_only, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
