"""How much memory the server has, and how much of it is free right now.

Read from the operating system itself (the Windows API through ctypes, /proc/meminfo elsewhere), so no extra
library is needed. Both functions return ``None`` when the figure cannot be read; callers then behave as if memory
were not a constraint rather than guessing."""

import ctypes
import os
import sys

_MB = 1024 * 1024


class _MemoryStatus(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_uint32),
        ("dwMemoryLoad", ctypes.c_uint32),
        ("ullTotalPhys", ctypes.c_uint64),
        ("ullAvailPhys", ctypes.c_uint64),
        ("ullTotalPageFile", ctypes.c_uint64),
        ("ullAvailPageFile", ctypes.c_uint64),
        ("ullTotalVirtual", ctypes.c_uint64),
        ("ullAvailVirtual", ctypes.c_uint64),
        ("sullAvailExtendedVirtual", ctypes.c_uint64),
    ]


def _windows() -> tuple[int, int] | None:
    status = _MemoryStatus()
    status.dwLength = ctypes.sizeof(_MemoryStatus)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return status.ullTotalPhys // _MB, status.ullAvailPhys // _MB


def _linux() -> tuple[int, int] | None:
    values: dict[str, int] = {}
    with open("/proc/meminfo", encoding="ascii") as handle:
        for line in handle:
            key, _, rest = line.partition(":")
            values[key] = int(rest.split()[0]) // 1024          # kB -> MB
    if "MemTotal" not in values:
        return None
    return values["MemTotal"], values.get("MemAvailable", values.get("MemFree", 0))


def _read() -> tuple[int, int] | None:
    try:
        if sys.platform == "win32" or os.name == "nt":
            return _windows()
        return _linux()
    except Exception:  # noqa: BLE001 - an unreadable figure must never stop a job from running
        return None


def total_mb() -> int | None:
    """The server's physical memory."""
    reading = _read()
    return reading[0] if reading else None


def available_mb() -> int | None:
    """Memory free for new work right now, whoever else is using the machine (cached pages count as free)."""
    reading = _read()
    return reading[1] if reading else None
