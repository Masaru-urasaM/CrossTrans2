"""Own validation descendants so a timeout cannot leave a worker running unnoticed."""
import ctypes
from ctypes import wintypes
import os
import subprocess
import time


class JobLimits(ctypes.Structure):
    _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
                ('flags', wintypes.DWORD), ('min_working_set', ctypes.c_size_t),
                ('max_working_set', ctypes.c_size_t), ('active_processes', wintypes.DWORD),
                ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD),
                ('scheduling', wintypes.DWORD)]


class IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in
                ('reads', 'writes', 'other', 'read_bytes', 'write_bytes', 'other_bytes')]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [('basic', JobLimits), ('io', IoCounters), ('process_memory', ctypes.c_size_t),
                ('job_memory', ctypes.c_size_t), ('peak_process_memory', ctypes.c_size_t),
                ('peak_job_memory', ctypes.c_size_t)]


class ThreadEntry(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('usage', wintypes.DWORD), ('thread_id', wintypes.DWORD),
                ('process_id', wintypes.DWORD), ('base_priority', wintypes.LONG),
                ('delta_priority', wintypes.LONG), ('flags', wintypes.DWORD)]


class ProcessTree:
    """No resource cap: only terminate this batch's descendants when its job closes."""

    def __init__(self):
        self.handle = None
        if os.name != 'nt':
            return
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                    ctypes.c_void_p, wintypes.DWORD]
        self.api.SetInformationJobObject.restype = wintypes.BOOL
        self.api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.api.AssignProcessToJobObject.restype = wintypes.BOOL
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.CloseHandle.restype = wintypes.BOOL
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.basic.flags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def attach(self, process):
        if self.handle and not self.api.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def resume(self, process):
        if os.name != 'nt':
            return
        api = self.api
        api.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        api.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        for name in ('Thread32First', 'Thread32Next'):
            function = getattr(api, name)
            function.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
            function.restype = wintypes.BOOL
        api.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenThread.restype = wintypes.HANDLE
        api.ResumeThread.argtypes = [wintypes.HANDLE]
        api.ResumeThread.restype = wintypes.DWORD
        snapshot = api.CreateToolhelp32Snapshot(0x00000004, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        threads = []
        try:
            entry = ThreadEntry()
            entry.size = ctypes.sizeof(entry)
            present = api.Thread32First(snapshot, ctypes.byref(entry))
            while present:
                if entry.process_id == process.pid:
                    threads.append(entry.thread_id)
                present = api.Thread32Next(snapshot, ctypes.byref(entry))
        finally:
            api.CloseHandle(snapshot)
        if len(threads) != 1:
            raise RuntimeError('The suspended child has no unique initial thread')
        thread = api.OpenThread(0x0002, False, threads[0])
        if not thread:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            count = api.ResumeThread(thread)
            if count != 1:
                raise RuntimeError('The owned initial thread was not resumed exactly once')
        finally:
            api.CloseHandle(thread)

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def run_owned(command, *, cwd, env, stream, timeout, started):
    """Return only after the directly owned handle is terminal; preserve its PID."""
    tree, process = ProcessTree(), None
    begin = time.perf_counter()
    try:
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        if os.name == 'nt':
            flags |= 0x00000004  # CREATE_SUSPENDED: attach ownership before child code can spawn.
        process = subprocess.Popen(command, cwd=cwd, env=env, stdout=stream,
            stderr=subprocess.STDOUT, creationflags=flags)
        try:
            tree.attach(process)
        except Exception:
            process.terminate()
            process.wait(timeout=15)
            raise
        started(process.pid)
        tree.resume(process)
        try:
            code, expired = process.wait(timeout=timeout), False
        except subprocess.TimeoutExpired:
            tree.close()
            if os.name != 'nt':
                process.terminate()
            code, expired = process.wait(timeout=15), True
        return dict(exit_code=124 if expired else code, process_exit_code=code,
                    timeout_reached=expired, seconds=time.perf_counter() - begin)
    finally:
        tree.close()
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass  # Closing the job may already have terminated this process.
            process.wait(timeout=15)
