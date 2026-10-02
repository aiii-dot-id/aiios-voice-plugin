"""Physical comparison keys without requiring the Windows mount-point manager.

Keys are never I/O paths. Windows keys come from an opened object's normalized
NT name, including its volume, not an unresolved drive-letter spelling. Existing
I/O paths remain ordinary DOS/UNC paths for Python and inference libraries.
Only missing final components may be appended for a non-strict comparison;
access denial and every other resolution error remain failures.
"""

import functools
import ntpath
import os
from pathlib import Path, PurePath, PureWindowsPath


def windows_path_identity(value):
    """Fold DOS/extended-DOS/UNC spelling only; this is NOT physical resolution."""
    text = str(PureWindowsPath(value))
    if "\0" in text:
        raise ValueError("Windows path cannot contain NUL")
    if text[:8].casefold() == "\\\\?\\unc\\":
        text = "\\\\" + text[8:]
    elif text.startswith("\\\\?\\") and len(text) >= 7 and text[5:7] == ":\\":
        text = text[4:]
    elif text.startswith(("\\\\?\\", "\\\\.\\")):
        raise ValueError("unsupported Windows path identity namespace")
    path = PureWindowsPath(text)
    if not path.is_absolute():
        raise ValueError("Windows path identity must be absolute")
    return path


@functools.lru_cache(maxsize=1)
def _windows_api():
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create.restype = wintypes.HANDLE
    final = kernel.GetFinalPathNameByHandleW
    final.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    final.restype = wintypes.DWORD
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    return ctypes, create, final, close


def _final_nt(path):
    ctypes, create, final, close = _windows_api()
    # FILE_READ_ATTRIBUTES, all sharing, OPEN_EXISTING, BACKUP_SEMANTICS.
    # Follow reparse points to compare the actual target, not the link spelling.
    handle = create(path, 0x80, 7, None, 3, 0x02000000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        capacity = 512
        while capacity <= 65536:
            buffer = ctypes.create_unicode_buffer(capacity)
            # FILE_NAME_NORMALIZED | VOLUME_NAME_NT: no DOS/GUID mount query.
            count = final(handle, buffer, capacity, 2)
            if count == 0:
                raise ctypes.WinError(ctypes.get_last_error())
            if count < capacity:
                name = buffer.value
                if not name.startswith("\\Device\\"):
                    raise ValueError("unexpected physical Windows volume namespace")
                return PureWindowsPath(name)
            capacity = count + 1
        raise ValueError("physical Windows path exceeds bounded buffer")
    finally:
        if not close(handle):
            raise ctypes.WinError(ctypes.get_last_error())


def _windows_identity(value, *, strict):
    path = ntpath.abspath(os.fspath(value))
    windows_path_identity(path)  # Refuse device namespace input before opening.
    tail = []
    while True:
        try:
            return _final_nt(path).joinpath(*reversed(tail))
        except OSError as error:
            if strict or getattr(error, "winerror", None) not in (2, 3):
                raise
            parent, name = ntpath.split(path)
            if not name or parent == path:
                raise
            tail.append(name)
            path = parent


def path_identity(value, *, strict=False):
    """Resolve equality/containment afresh; never memoize filesystem authority."""
    if os.name == "nt":
        return _windows_identity(value, strict=strict)
    return PurePath(Path(value).resolve(strict=strict))


def existing_io_path(value):
    """Existing usable path; use path_identity separately for physical comparison.

    On Windows the returned spelling need not be canonical: parent junctions
    such as a mounted work volume remain usable by normal library APIs. Handle
    resolution proves existence; comparison must use the physical key, not this
    lexical spelling. Unix retains its previous strict-resolve behavior.
    """
    if os.name != "nt":
        return Path(value).resolve(strict=True)
    path = Path(ntpath.abspath(os.fspath(value)))
    _windows_identity(path, strict=True)
    return path
