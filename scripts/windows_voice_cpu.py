"""Read-only, process-creation-bound Windows CPU observations for speech proofs.

Counters are cumulative CPU seconds, not inferred utilization or GPU placement.
Retained process handles avoid attributing a recycled PID to the previous worker.
No affinity, priority, process, audio, model, or operating-system setting changes.
"""

import ctypes
import math
import os
import threading
import time
from ctypes import wintypes


def filetime_seconds(value):
    return ((value.dwHighDateTime << 32) | value.dwLowDateTime) / 10_000_000


class WindowsCPU:
    def __init__(self):
        if os.name != "nt":
            raise RuntimeError("native Windows counters required")
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        pointer = ctypes.POINTER(wintypes.FILETIME)
        self.kernel.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            pointer,
            pointer,
            pointer,
            pointer,
        ]
        self.kernel.GetProcessTimes.restype = wintypes.BOOL
        self.kernel.GetSystemTimes.argtypes = [pointer, pointer, pointer]
        self.kernel.GetSystemTimes.restype = wintypes.BOOL
        self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel.WaitForSingleObject.restype = wintypes.DWORD
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.handles = {}

    def attach(self, label, pid):
        if label in self.handles or type(pid) is not int or pid <= 0:
            raise ValueError("fresh named process and positive PID required")
        handle = self.kernel.OpenProcess(0x1000 | 0x100000, False, pid)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self.handles[label] = (pid, handle)

    def snapshot(self):
        idle, kernel, user = (wintypes.FILETIME() for _ in range(3))
        if not self.kernel.GetSystemTimes(
            ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        result = {
            "system_idle_seconds": filetime_seconds(idle),
            "system_kernel_seconds": filetime_seconds(kernel),
            "system_user_seconds": filetime_seconds(user),
            "processes": {},
        }
        for label, (pid, handle) in self.handles.items():
            created, ended, kernel, user = (wintypes.FILETIME() for _ in range(4))
            if not self.kernel.GetProcessTimes(
                handle,
                ctypes.byref(created),
                ctypes.byref(ended),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            wait = self.kernel.WaitForSingleObject(handle, 0)
            if wait not in (0, 258):
                raise OSError("unexpected process wait result", wait)
            result["processes"][label] = {
                "pid": pid,
                "creation_seconds": filetime_seconds(created),
                "kernel_seconds": filetime_seconds(kernel),
                "user_seconds": filetime_seconds(user),
                "exited": wait == 0,
            }
        return result

    def close(self):
        errors = []
        for _, handle in self.handles.values():
            if not self.kernel.CloseHandle(handle):
                errors.append(ctypes.get_last_error())
        self.handles.clear()
        if errors:
            raise OSError("CPU observation handle retirement failed", errors)


class CPUSampler:
    def __init__(self, origin, reader=None, *, period=0.1, maximum=6500):
        if not 0.02 <= period <= 1 or not 2 <= maximum <= 10000:
            raise ValueError("bounded CPU sampling required")
        self.origin, self.period, self.maximum = origin, period, maximum
        self.reader = WindowsCPU() if reader is None else reader
        self.rows, self.errors = [], []
        self.lock, self.stop = threading.Lock(), threading.Event()
        self.thread = threading.Thread(
            target=self.run, name="speech-cpu-observer", daemon=True
        )

    def attach(self, label, pid):
        with self.lock:
            self.reader.attach(label, pid)

    def run(self):
        try:
            while not self.stop.is_set():
                with self.lock:
                    before = time.perf_counter()
                    row = self.reader.snapshot()
                    after = time.perf_counter()
                    if len(self.rows) >= self.maximum:
                        raise RuntimeError("CPU observation bound exceeded")
                    self.rows.append(
                        {
                            **row,
                            "elapsed": (before + after) / 2 - self.origin,
                            "read_seconds": after - before,
                        }
                    )
                self.stop.wait(self.period)
        except Exception as error:
            self.errors.append(repr(error))

    def start(self):
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread.ident is not None:
            self.thread.join(timeout=2)
        if self.thread.is_alive():
            raise RuntimeError("CPU observer did not retire")
        self.reader.close()
        if self.errors:
            raise RuntimeError("CPU observation failed: " + "; ".join(self.errors))


def summarize(rows, begin, end):
    """Use only complete sample intervals within a named measured region."""
    if not math.isfinite(begin) or not math.isfinite(end) or not 0 <= begin < end:
        raise ValueError("finite ordered CPU window required")
    previous = -math.inf
    for row in rows:
        if (
            not math.isfinite(row["elapsed"])
            or row["elapsed"] <= previous
            or not math.isfinite(row["read_seconds"])
            or row["read_seconds"] < 0
        ):
            raise ValueError("invalid CPU observation clock or read duration")
        previous = row["elapsed"]
    selected = [r for r in rows if begin <= r["elapsed"] <= end]
    if len(selected) < 2:
        raise ValueError("insufficient CPU samples")
    totals, durations = {}, {}
    for a, b in zip(selected, selected[1:], strict=False):
        dt = b["elapsed"] - a["elapsed"]
        if not math.isfinite(dt) or dt <= 0:
            raise ValueError("CPU observation clock is not monotonic")
        if a["processes"].keys() != b["processes"].keys():
            raise ValueError("CPU process census changed within window")
        for label in a["processes"]:
            x, y = a["processes"][label], b["processes"][label]
            for process in (x, y):
                if (type(process['pid']) is not int or process['pid'] <= 0 or
                    not math.isfinite(process['creation_seconds']) or process['creation_seconds'] <= 0 or
                    process['exited'] is not False or
                    any(not math.isfinite(process[k]) or process[k] < 0
                        for k in ('kernel_seconds', 'user_seconds'))):
                    raise ValueError('invalid live process counters')
            if (x["pid"], x["creation_seconds"]) != (y["pid"], y["creation_seconds"]):
                raise ValueError("CPU process identity changed")
            values = [y[k] - x[k] for k in ("kernel_seconds", "user_seconds")]
            if any(not math.isfinite(v) or v < 0 for v in values):
                raise ValueError("CPU process counter regressed")
            totals[label] = totals.get(label, 0) + sum(values)
            durations[label] = durations.get(label, 0) + dt
        system_keys = ('system_kernel_seconds', 'system_user_seconds', 'system_idle_seconds')
        if any(not math.isfinite(row[k]) or row[k] < 0 for row in (a, b) for k in system_keys):
            raise ValueError('invalid system CPU counters')
        counters = [
            b[k] - a[k]
            for k in (
                "system_kernel_seconds",
                "system_user_seconds",
                "system_idle_seconds",
            )
        ]
        if any(not math.isfinite(v) or v < 0 for v in counters):
            raise ValueError("system CPU counter regressed")
        busy = counters[0] + counters[1] - counters[2]
        if busy < 0:
            raise ValueError("invalid system idle accounting")
        totals["system_busy"] = totals.get("system_busy", 0) + busy
        durations["system_busy"] = durations.get("system_busy", 0) + dt
    return {
        "begin": begin,
        "end": end,
        "sample_intervals": len(selected) - 1,
        "mean_cores": {k: v / durations[k] for k, v in totals.items()},
        "observed_wall_seconds": durations,
        "maximum_counter_read_seconds": max(r["read_seconds"] for r in selected),
    }


def process_idle_delta(before, after):
    """Whole-process CPU includes exited threads; no thread census is needed."""
    dt = after['elapsed'] - before['elapsed']
    if not math.isfinite(dt) or dt < 3:
        raise ValueError('complete three-second process idle interval required')
    for row in (before, after):
        if set(row['processes']) != {'worker'}:
            raise ValueError('exact worker process required')
        worker = row['processes']['worker']
        if (type(worker['pid']) is not int or worker['pid'] <= 0 or
                not math.isfinite(worker['creation_seconds']) or
                worker['creation_seconds'] <= 0 or worker['exited'] is not False):
            raise ValueError('live creation-bound worker required')
        if any(not math.isfinite(worker[k]) or worker[k] < 0
               for k in ('kernel_seconds', 'user_seconds')):
            raise ValueError('invalid worker CPU counter')
    checked = summarize([before, after], before['elapsed'], after['elapsed'])
    return {'seconds': dt, 'process_mean_cores': checked['mean_cores']['worker'],
            'process_cpu_seconds': checked['mean_cores']['worker'] * dt,
            'maximum_counter_read_seconds': checked['maximum_counter_read_seconds'],
            'measurement': 'GetProcessTimes; retained process handle; no thread census'}


def process_idle_window(pid, origin, seconds=3, *, reader=None):
    if seconds != 3:
        raise ValueError('fixed three-second idle window required')
    reader = WindowsCPU() if reader is None else reader
    result = {}
    def sample():
        began = time.perf_counter()
        row = reader.snapshot()
        ended = time.perf_counter()
        return {**row, 'elapsed': (began + ended) / 2 - origin,
                'read_seconds': ended - began}
    try:
        reader.attach('worker', pid)
        result['before'] = sample()
        time.sleep(seconds)
        result['after'] = sample()
        result['summary'] = process_idle_delta(result['before'], result['after'])
    except BaseException as error:
        result['error'] = repr(error)
    finally:
        reader.close()
    return result
