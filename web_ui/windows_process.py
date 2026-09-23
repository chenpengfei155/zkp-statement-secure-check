"""Windows process-tree ownership using Job Objects (standard library only).

The small child shim waits until it belongs to the job before starting a solver.
Closing the handle, even if the supervisor crashes, terminates its descendants.
"""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import sys
import time


class BasicLimits(ctypes.Structure):
    _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
                ('flags', wintypes.DWORD), ('min_working', ctypes.c_size_t),
                ('max_working', ctypes.c_size_t), ('active_limit', wintypes.DWORD),
                ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD),
                ('scheduling', wintypes.DWORD)]


class IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in ('read_ops', 'write_ops', 'other_ops',
                                                   'read_bytes', 'write_bytes', 'other_bytes')]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [('basic', BasicLimits), ('io', IoCounters), ('process_memory', ctypes.c_size_t),
                ('job_memory', ctypes.c_size_t), ('peak_process', ctypes.c_size_t),
                ('peak_job', ctypes.c_size_t)]


class Accounting(ctypes.Structure):
    _fields_ = [('user', ctypes.c_int64), ('kernel', ctypes.c_int64),
                ('period_user', ctypes.c_int64), ('period_kernel', ctypes.c_int64),
                ('faults', wintypes.DWORD), ('total', wintypes.DWORD),
                ('active', wintypes.DWORD), ('terminated', wintypes.DWORD)]


class ProcessJob:
    def __init__(self, memory_mib=4096):
        if os.name != 'nt':
            raise OSError('Windows Job Objects require Windows.')
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        signatures = {
            'CreateJobObjectW': ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            'SetInformationJobObject': ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
            'QueryInformationJobObject': ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL),
            'AssignProcessToJobObject': ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            'TerminateJobObject': ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            'CloseHandle': ([wintypes.HANDLE], wintypes.BOOL),
            'OpenProcess': ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            'WaitForSingleObject': ([wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = args, result
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000 | 0x100  # KILL_ON_JOB_CLOSE | PROCESS_MEMORY
        limits.process_memory = memory_mib * 1024 * 1024
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def spawn(self, command, *, cwd=None, env=None):
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--child', *command],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd, env=env,
            creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            if not self.api.AssignProcessToJobObject(self.handle, wintypes.HANDLE(process._handle)):
                raise ctypes.WinError(ctypes.get_last_error())
            process.stdin.write(b'go\n')
            process.stdin.flush()
            return process
        except BaseException:
            process.kill()
            process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()
            raise

    def close(self):
        if not self.handle:
            return
        handles = []
        try:
            # Active-process accounting can reach zero before the final process
            # handles are signalled. Wait on those handles before removing files.
            capacity = 64
            while True:
                buffer = ctypes.create_string_buffer(8 + capacity * ctypes.sizeof(ctypes.c_size_t))
                if self.api.QueryInformationJobObject(self.handle, 3, buffer, len(buffer), None):
                    count = wintypes.DWORD.from_buffer(buffer, 4).value
                    pids = (ctypes.c_size_t * count).from_buffer(buffer, 8)
                    for pid in pids:
                        handle = self.api.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE
                        if handle:
                            handles.append(handle)
                    break
                if ctypes.get_last_error() != 234 or capacity >= 65536:
                    raise ctypes.WinError(ctypes.get_last_error())
                capacity *= 2
            if not self.api.TerminateJobObject(self.handle, 1):
                raise ctypes.WinError(ctypes.get_last_error())
            deadline = time.monotonic() + 5
            info = Accounting()
            while True:
                if not self.api.QueryInformationJobObject(self.handle, 1, ctypes.byref(info), ctypes.sizeof(info), None):
                    raise ctypes.WinError(ctypes.get_last_error())
                if not info.active:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError('Windows has not finished terminating the analysis process tree.')
                time.sleep(0.01)
            for handle in handles:
                remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
                if self.api.WaitForSingleObject(handle, remaining_ms) != 0:
                    raise TimeoutError('Windows analysis process has not fully exited.')
        finally:
            for handle in handles:
                self.api.CloseHandle(handle)
            self.api.CloseHandle(self.handle)
            self.handle = None


def checked_run(command, *, env, cwd=None, timeout=20, memory_mib=4096):
    """Bounded capture for native environment checks, including descendants."""
    job = ProcessJob(memory_mib)
    process = None
    try:
        process = job.spawn(command, env=env, cwd=cwd)
        stdout, stderr = process.communicate(timeout=timeout)
        return subprocess.CompletedProcess(command, process.returncode,
                                           stdout.decode('utf-8', errors='replace'),
                                           stderr.decode('utf-8', errors='replace'))
    finally:
        job.close()
        if process is not None:
            process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()


if __name__ == '__main__':
    if len(sys.argv) < 3 or sys.argv[1] != '--child' or sys.stdin.buffer.readline() != b'go\n':
        sys.exit(1)
    sys.exit(subprocess.run(sys.argv[2:], stdin=subprocess.DEVNULL).returncode)
