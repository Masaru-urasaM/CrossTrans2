"""Own validation process trees with a Windows job; timeouts cannot target other apps."""
import ctypes
from ctypes import wintypes as W
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import threading
import time
from collections import deque


class BasicLimits(ctypes.Structure):
    _fields_ = [('process_time',ctypes.c_int64),('job_time',ctypes.c_int64),('flags',W.DWORD),
        ('min_ws',ctypes.c_size_t),('max_ws',ctypes.c_size_t),('active_limit',W.DWORD),
        ('affinity',ctypes.c_size_t),('priority',W.DWORD),('scheduling',W.DWORD)]


class IOCounts(ctypes.Structure):
    _fields_ = [(name,ctypes.c_uint64) for name in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [('basic',BasicLimits),('io',IOCounts),('process_limit',ctypes.c_size_t),
        ('job_limit',ctypes.c_size_t),('peak_process_commit',ctypes.c_size_t),('peak_job_commit',ctypes.c_size_t)]


class Accounting(ctypes.Structure):
    _fields_ = [(name,ctypes.c_int64) for name in ('user','kernel','period_user','period_kernel')] + [
        (name,W.DWORD) for name in ('page_faults','total_processes','active_processes','terminated_processes')]


class ProcessIds(ctypes.Structure):
    _fields_ = [('assigned',W.DWORD),('count',W.DWORD),('pids',ctypes.c_size_t*256)]


class MemoryCounters(ctypes.Structure):
    _fields_ = [('cb',W.DWORD),('faults',W.DWORD)] + [(name,ctypes.c_size_t) for name in
        ('peak_ws','working_set','peak_paged','paged','peak_nonpaged','nonpaged','pagefile','peak_pagefile','private')]


class OwnedJob:
    def __init__(self):
        if os.name != 'nt':
            raise RuntimeError('The declared Windows validation job is required')
        self.kernel = ctypes.WinDLL('kernel32',use_last_error=True)
        for name,args,result in (
            ('CreateJobObjectW',[ctypes.c_void_p,W.LPCWSTR],W.HANDLE),
            ('SetInformationJobObject',[W.HANDLE,ctypes.c_int,ctypes.c_void_p,W.DWORD],W.BOOL),
            ('AssignProcessToJobObject',[W.HANDLE,W.HANDLE],W.BOOL),
            ('QueryInformationJobObject',[W.HANDLE,ctypes.c_int,ctypes.c_void_p,W.DWORD,ctypes.POINTER(W.DWORD)],W.BOOL),
            ('OpenProcess',[W.DWORD,W.BOOL,W.DWORD],W.HANDLE),
            ('TerminateJobObject',[W.HANDLE,W.UINT],W.BOOL),('CloseHandle',[W.HANDLE],W.BOOL)):
            function = getattr(self.kernel,name)
            function.argtypes,function.restype = args,result
        self.handle = self.kernel.CreateJobObjectW(None,None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000
        if not self.kernel.SetInformationJobObject(self.handle,9,ctypes.byref(limits),ctypes.sizeof(limits)):
            error = ctypes.get_last_error()
            self.close()
            raise ctypes.WinError(error)
        self.psapi = ctypes.WinDLL('psapi',use_last_error=True)
        self.psapi.GetProcessMemoryInfo.argtypes = [W.HANDLE,ctypes.c_void_p,W.DWORD]
        self.psapi.GetProcessMemoryInfo.restype = W.BOOL

    def assign(self,process):
        if not self.kernel.AssignProcessToJobObject(self.handle,int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def snapshot(self):
        accounting,limits = Accounting(),ExtendedLimits()
        if not self.kernel.QueryInformationJobObject(self.handle,1,ctypes.byref(accounting),ctypes.sizeof(accounting),None):
            raise ctypes.WinError(ctypes.get_last_error())
        if not self.kernel.QueryInformationJobObject(self.handle,9,ctypes.byref(limits),ctypes.sizeof(limits),None):
            raise ctypes.WinError(ctypes.get_last_error())
        return dict(total_processes=accounting.total_processes,active_processes=accounting.active_processes,
            terminated_processes=accounting.terminated_processes,user_cpu_seconds=accounting.user/1e7,
            kernel_cpu_seconds=accounting.kernel/1e7,peak_job_commit_bytes=limits.peak_job_commit,
            peak_process_commit_bytes=limits.peak_process_commit)

    def memory(self, pid):
        handle = self.kernel.OpenProcess(0x410,False,pid)
        if not handle:
            return dict(pid=pid,error=ctypes.get_last_error())
        try:
            counters = MemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            if not self.psapi.GetProcessMemoryInfo(handle,ctypes.byref(counters),counters.cb):
                return dict(pid=pid,error=ctypes.get_last_error())
            return dict(pid=pid,working_set_bytes=counters.working_set,private_bytes=counters.private)
        finally:
            self.kernel.CloseHandle(handle)

    def resource_sample(self):
        identifiers = ProcessIds()
        if not self.kernel.QueryInformationJobObject(self.handle,3,ctypes.byref(identifiers),ctypes.sizeof(identifiers),None):
            raise ctypes.WinError(ctypes.get_last_error())
        members = [self.memory(pid) for pid in identifiers.pids[:identifiers.count]]
        complete = identifiers.assigned == identifiers.count and not any('error' in row for row in members)
        accounting = self.snapshot()
        return dict(perf_counter_seconds=time.perf_counter(),members=members,complete=complete,
            working_set_bytes=sum(row['working_set_bytes'] for row in members) if complete else None,
            private_bytes=sum(row['private_bytes'] for row in members) if complete else None,
            total_cpu_seconds=accounting['user_cpu_seconds']+accounting['kernel_cpu_seconds'],
            harness=self.memory(os.getpid()),harness_cpu_seconds=time.process_time())

    def terminate(self):
        if not self.kernel.TerminateJobObject(self.handle,1):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def run_owned(command,directory,*,cwd,timeout,env=None):
    directory = Path(directory)
    directory.mkdir(parents=True,exist_ok=False)
    environment = dict(os.environ if env is None else env)
    for name in ('CROSSTRANS_TESTS_ON_SCREEN','CROSSTRANS_TESTS_REAL_CLIPBOARD'):
        environment.pop(name,None)
    environment.update(PYTHONDONTWRITEBYTECODE='1',PYTHONUTF8='1',PYTHONIOENCODING='utf-8')
    result = dict(command=list(command),cwd=str(cwd),started_utc=datetime.now(timezone.utc).isoformat(),
                  timeout_seconds=timeout,timed_out=False,parent_pid=os.getpid(),scope='owned Windows job process tree')
    job,process = OwnedJob(),None
    samples,stopped = deque(maxlen=14400),threading.Event()
    sampler = None
    sample_count = 0
    started = time.monotonic()

    def sample_resources():
        nonlocal sample_count
        try:
            while not stopped.is_set():
                samples.append(job.resource_sample())
                sample_count += 1
                stopped.wait(.5)
        except Exception as error:
            result['resource_sampling_error'] = dict(type=type(error).__name__,winerror=getattr(error,'winerror',None))
    try:
        with (directory/'stdout.txt').open('w',encoding='utf-8') as stdout, (directory/'stderr.txt').open('w',encoding='utf-8') as stderr:
            process = subprocess.Popen(command,cwd=cwd,env=environment,stdout=stdout,stderr=stderr,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
            result['pid'] = process.pid
            try:
                job.assign(process)
            except Exception:
                process.kill()
                process.wait(timeout=10)
                raise
            (directory/'process.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
            sampler = threading.Thread(target=sample_resources,name='OwnedJobResources',daemon=True)
            sampler.start()
            try:
                result['exit_code'] = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                result['timed_out'] = True
                job.terminate()
                result['exit_code'] = process.wait(timeout=10)
            result['job'] = job.snapshot()
            settle_until = time.monotonic()+1
            while result['job']['active_processes'] and time.monotonic()<settle_until:
                time.sleep(.025)
                result['job'] = job.snapshot()
            if result['job']['active_processes']:
                result['terminated_remaining_descendants'] = True
                job.terminate()
                until = time.monotonic()+10
                while job.snapshot()['active_processes'] and time.monotonic()<until:
                    time.sleep(.05)
            result['owned_processes_stopped'] = job.snapshot()['active_processes'] == 0
    finally:
        stopped.set()
        if sampler:
            sampler.join(timeout=3)
        result['resource_sampler_stopped'] = not sampler or not sampler.is_alive()
        result['resource_samples'] = list(samples)
        result['resource_sample_limit'] = 14400
        result['resource_samples_dropped'] = max(0,sample_count-len(samples))
        job.close()
        result['elapsed_seconds'] = time.monotonic()-started
        (directory/'process.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result
