"""Read-only native Windows counters; GPU values describe the whole device.

NVML queries follow https://docs.nvidia.com/deploy/nvml-api/api/group__nvmlDeviceQueries.html.
No affinity, clocks, power policy, driver or model configuration is changed.
CPU counters retain process handles; memory and GPU sampling errors fail the
diagnostic rather than silently reporting zero utilization.
"""
import ctypes as C
from ctypes import wintypes as W
import hashlib
import os
from pathlib import Path

from scripts.windows_voice_cpu import WindowsCPU


class ProcessMemory(C.Structure):
    _fields_ = [('cb', W.DWORD), ('PageFaultCount', W.DWORD)] + [
        (n, C.c_size_t) for n in ('PeakWorkingSetSize', 'WorkingSetSize',
        'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
        'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage', 'PrivateUsage')]


class SystemMemory(C.Structure):
    _fields_ = [('dwLength', W.DWORD), ('dwMemoryLoad', W.DWORD)] + [
        (n, C.c_ulonglong) for n in ('ullTotalPhys', 'ullAvailPhys', 'ullTotalPageFile',
        'ullAvailPageFile', 'ullTotalVirtual', 'ullAvailVirtual', 'ullAvailExtendedVirtual')]


class GPUMemory(C.Structure):
    _fields_ = [(n, C.c_ulonglong) for n in ('total', 'free', 'used')]


class GPUUtilization(C.Structure):
    _fields_ = [('gpu', C.c_uint), ('memory', C.c_uint)]


class GPU:
    def __init__(self):
        self.path = Path(os.environ['SystemRoot']) / 'System32/nvml.dll'
        self.sha256 = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.lib = C.CDLL(str(self.path))
        self.live = False
        self.device = C.c_void_p()
        self.api('nvmlInit_v2', [])()
        self.live = True
        try:
            count = C.c_uint()
            self.api('nvmlDeviceGetCount_v2', [C.POINTER(C.c_uint)])(C.byref(count))
            if count.value != 1:
                raise RuntimeError('diagnostic requires the single assigned GPU')
            self.api('nvmlDeviceGetHandleByIndex_v2', [C.c_uint, C.POINTER(C.c_void_p)])(0, C.byref(self.device))
            self.identity = {'library': str(self.path), 'sha256': self.sha256}
            for name, api in [('name', 'nvmlDeviceGetName'), ('uuid', 'nvmlDeviceGetUUID')]:
                value = C.create_string_buffer(96)
                self.api(api, [C.c_void_p, C.c_void_p, C.c_uint])(self.device, value, len(value))
                self.identity[name] = value.value.decode()
            if 'GTX 1070' not in self.identity['name']:
                raise RuntimeError('unexpected assigned GPU')
        except BaseException:
            self.close()
            raise

    def api(self, name, args):
        f = getattr(self.lib, name)
        f.argtypes, f.restype = args, C.c_int
        def checked(*values):
            rc = f(*values)
            if rc:
                raise OSError(name, rc)
        return checked

    def snapshot(self):
        memory, utilization = GPUMemory(), GPUUtilization()
        self.api('nvmlDeviceGetMemoryInfo', [C.c_void_p, C.POINTER(GPUMemory)])(self.device, C.byref(memory))
        self.api('nvmlDeviceGetUtilizationRates', [C.c_void_p, C.POINTER(GPUUtilization)])(self.device, C.byref(utilization))
        row = {'memory_total': memory.total, 'memory_used': memory.used,
               'memory_free': memory.free, 'gpu_percent': utilization.gpu,
               'memory_percent': utilization.memory}
        for name, function, middle in (
            ('sm_mhz', 'nvmlDeviceGetClockInfo', [1]),
            ('memory_mhz', 'nvmlDeviceGetClockInfo', [2]),
            ('temperature_c', 'nvmlDeviceGetTemperature', [0]),
            ('pstate', 'nvmlDeviceGetPerformanceState', []),
        ):
            value = C.c_uint()
            self.api(function, [C.c_void_p] + [C.c_uint] * len(middle) + [C.POINTER(C.c_uint)])(
                self.device, *middle, C.byref(value))
            row[name] = value.value
        if not (0 <= row['gpu_percent'] <= 100 and 0 <= row['memory_percent'] <= 100
                and row['memory_used'] <= row['memory_total'] and row['sm_mhz'] > 0):
            raise ValueError('invalid GPU observation')
        return row

    def close(self):
        if self.live:
            self.api('nvmlShutdown', [])()
            self.live = False


class WindowsResources(WindowsCPU):
    def __init__(self):
        super().__init__()
        self.gpu = GPU()
        self.kernel.K32GetProcessMemoryInfo.argtypes = [W.HANDLE, C.POINTER(ProcessMemory), W.DWORD]
        self.kernel.K32GetProcessMemoryInfo.restype = W.BOOL
        self.kernel.GlobalMemoryStatusEx.argtypes = [C.POINTER(SystemMemory)]
        self.kernel.GlobalMemoryStatusEx.restype = W.BOOL

    def attach(self, label, pid):
        if label in self.handles or type(pid) is not int or pid <= 0:
            raise ValueError('fresh named process and positive PID required')
        handle = self.kernel.OpenProcess(0x400 | 0x10 | 0x100000, False, pid)
        if not handle:
            raise C.WinError(C.get_last_error())
        self.handles[label] = (pid, handle)

    def snapshot(self):
        row = super().snapshot()
        for label, (_, handle) in self.handles.items():
            memory = ProcessMemory()
            memory.cb = C.sizeof(memory)
            if not self.kernel.K32GetProcessMemoryInfo(handle, C.byref(memory), C.sizeof(memory)):
                raise C.WinError(C.get_last_error())
            row['processes'][label]['memory'] = {
                'working_bytes': memory.WorkingSetSize, 'private_bytes': memory.PrivateUsage,
                'peak_working_bytes': memory.PeakWorkingSetSize, 'page_faults': memory.PageFaultCount,
            }
        memory = SystemMemory()
        memory.dwLength = C.sizeof(memory)
        if not self.kernel.GlobalMemoryStatusEx(C.byref(memory)):
            raise C.WinError(C.get_last_error())
        row['system_memory'] = {'available_physical_bytes': memory.ullAvailPhys,
            'total_physical_bytes': memory.ullTotalPhys, 'available_commit_bytes': memory.ullAvailPageFile,
            'total_commit_bytes': memory.ullTotalPageFile, 'load_percent': memory.dwMemoryLoad}
        row['gpu'] = self.gpu.snapshot()
        return row

    def close(self):
        try:
            super().close()
        finally:
            self.gpu.close()
