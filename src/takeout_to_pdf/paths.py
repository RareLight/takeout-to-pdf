"""Portable archive paths and content fingerprints."""

import ctypes
import errno
import hashlib
import os
import re
import sys
import unicodedata
from pathlib import Path

_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def safe_component(value: str, limit: int = 60) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    text = "".join(c if c.isalnum() or c in "._@-" else "-" for c in normalized)
    text = re.sub("-+", "-", text).strip(" .-") or "unnamed"
    if text.split(".")[0].upper() in _RESERVED:
        text = "_" + text
    text = text.encode("utf-8")[:limit].decode("utf-8", errors="ignore").rstrip(" .")
    return text or "unnamed"


def safe_attachment_name(value: str, limit: int = 22) -> str:
    suffix = Path(value.replace("\\", "/")).suffix
    if suffix and len(suffix.encode("utf-8")) <= 8 and len(suffix.encode("utf-8")) < limit:
        extension = "." + safe_component(suffix[1:], 7)
        return (
            safe_component(value[: -len(suffix)], limit - len(extension.encode("utf-8")))
            + extension
        )
    return safe_component(value, limit)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def contained_file(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or "\\" in relative:
        raise ValueError(f"Unsafe archive path: {relative}")
    current = root
    for component in path.parts:
        current /= component
        if current.is_symlink():
            raise ValueError(f"Symlink is not an archive file: {relative}")
    if not current.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Path escapes archive: {relative}")
    return current


def publish_directory(stage: Path, target: Path) -> None:
    """Atomically publish a directory, refusing an existing destination."""
    source_bytes = os.fsencode(stage)
    target_bytes = os.fsencode(target)
    if sys.platform == "darwin":
        libc = ctypes.CDLL(None, use_errno=True)
        call = libc.renamex_np
        call.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        result = call(source_bytes, target_bytes, 0x00000004)
    elif sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        call = libc.renameat2
        call.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        result = call(-100, source_bytes, -100, target_bytes, 1)
    elif sys.platform == "win32":
        if target.exists() or target.is_symlink():
            raise FileExistsError(errno.EEXIST, "Output exists", str(target))
        os.rename(stage, target)
        return
    else:
        raise OSError(f"Atomic no-replace publication is unavailable on {sys.platform}")
    if result:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(target))
