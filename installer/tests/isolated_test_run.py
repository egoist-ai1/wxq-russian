"""Запуск installer-тестов на отдельном невидимом desktop, без SwitchDesktop/input.
Вся семья процессов попадает в kill-on-close Job до ResumeThread.
"""
from __future__ import annotations
import ctypes as c
from ctypes import wintypes as w
import argparse,json,os,subprocess,sys,time,uuid,msvcrt
from pathlib import Path

class Startup(c.Structure):
    _fields_ = [("cb", w.DWORD), ("lpReserved", w.LPWSTR), ("lpDesktop", w.LPWSTR),
                ("lpTitle", w.LPWSTR), ("dwX", w.DWORD), ("dwY", w.DWORD),
                ("dwXSize", w.DWORD), ("dwYSize", w.DWORD), ("dwXCountChars", w.DWORD),
                ("dwYCountChars", w.DWORD), ("dwFillAttribute", w.DWORD),
                ("dwFlags", w.DWORD), ("wShowWindow", w.WORD), ("cbReserved2", w.WORD),
                ("lpReserved2", c.POINTER(c.c_ubyte)), ("hStdInput", w.HANDLE),
                ("hStdOutput", w.HANDLE), ("hStdError", w.HANDLE)]


class ProcessInfo(c.Structure):
    _fields_ = [("hProcess", w.HANDLE), ("hThread", w.HANDLE),
                ("dwProcessId", w.DWORD), ("dwThreadId", w.DWORD)]


class BasicLimit(c.Structure):
    _fields_ = [("PerProcessUserTimeLimit", c.c_longlong), ("PerJobUserTimeLimit", c.c_longlong),
                ("LimitFlags", w.DWORD), ("MinimumWorkingSetSize", c.c_size_t),
                ("MaximumWorkingSetSize", c.c_size_t), ("ActiveProcessLimit", w.DWORD),
                ("Affinity", c.c_size_t), ("PriorityClass", w.DWORD), ("SchedulingClass", w.DWORD)]


class IoCounters(c.Structure):
    _fields_ = [(name, c.c_ulonglong) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class ExtendedLimit(c.Structure):
    _fields_ = [("BasicLimitInformation", BasicLimit), ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", c.c_size_t), ("JobMemoryLimit", c.c_size_t),
                ("PeakProcessMemoryUsed", c.c_size_t), ("PeakJobMemoryUsed", c.c_size_t)]


class BasicAccounting(c.Structure):
    _fields_ = [("TotalUserTime", c.c_longlong), ("TotalKernelTime", c.c_longlong),
                ("ThisPeriodTotalUserTime", c.c_longlong), ("ThisPeriodTotalKernelTime", c.c_longlong),
                ("TotalPageFaultCount", w.DWORD), ("TotalProcesses", w.DWORD),
                ("ActiveProcesses", w.DWORD), ("TotalTerminatedProcesses", w.DWORD)]



def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--work',type=Path,required=True)
    parser.add_argument('--timeout',type=int,default=600)
    parser.add_argument('command',nargs=argparse.REMAINDER)
    a=parser.parse_args();cmd=a.command
    if cmd and cmd[0]=='--':cmd=cmd[1:]
    if not cmd:raise ValueError('command required')
    work=a.work.resolve();temp=Path(os.environ['TEMP']).resolve()
    if not work.is_relative_to(temp):raise ValueError('work must stay in Temp')
    work.mkdir(parents=True,exist_ok=True)
    kernel=c.WinDLL('kernel32',use_last_error=True);user=c.WinDLL('user32',use_last_error=True)
    user.CreateDesktopW.argtypes=[w.LPCWSTR,w.LPCWSTR,c.c_void_p,w.DWORD,w.DWORD,c.c_void_p];user.CreateDesktopW.restype=w.HANDLE
    user.CloseDesktop.argtypes=[w.HANDLE];user.CloseDesktop.restype=w.BOOL
    kernel.CreateJobObjectW.argtypes=[c.c_void_p,w.LPCWSTR];kernel.CreateJobObjectW.restype=w.HANDLE
    kernel.SetInformationJobObject.argtypes=[w.HANDLE,c.c_int,c.c_void_p,w.DWORD]
    kernel.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE]
    kernel.CreateProcessW.argtypes=[w.LPCWSTR,w.LPWSTR,c.c_void_p,c.c_void_p,w.BOOL,w.DWORD,c.c_void_p,w.LPCWSTR,c.POINTER(Startup),c.POINTER(ProcessInfo)]
    kernel.ResumeThread.argtypes=[w.HANDLE];kernel.ResumeThread.restype=w.DWORD
    kernel.WaitForSingleObject.argtypes=[w.HANDLE,w.DWORD];kernel.WaitForSingleObject.restype=w.DWORD
    kernel.GetExitCodeProcess.argtypes=[w.HANDLE,c.POINTER(w.DWORD)]
    kernel.QueryInformationJobObject.argtypes=[w.HANDLE,c.c_int,c.c_void_p,w.DWORD,c.c_void_p]
    kernel.CloseHandle.argtypes=[w.HANDLE]
    kernel.TerminateProcess.argtypes=[w.HANDLE,w.UINT]
    def check(value):
        if not value:raise c.WinError(c.get_last_error())
    name='WXQTest-'+uuid.uuid4().hex
    desktop=user.CreateDesktopW(name,None,None,0,0x01FF,None);check(desktop)
    job=None;info=ProcessInfo();result={};assigned=False;started=time.monotonic()
    try:
        job=kernel.CreateJobObjectW(None,None);check(job)
        limits=ExtendedLimit();limits.BasicLimitInformation.LimitFlags=0x2000
        check(kernel.SetInformationJobObject(job,9,c.byref(limits),c.sizeof(limits)))
        allowed=['SYSTEMDRIVE','SYSTEMROOT','WINDIR','COMSPEC','TEMP','TMP','USERPROFILE','LOCALAPPDATA','APPDATA','PROGRAMDATA','ALLUSERSPROFILE','PUBLIC','USERDOMAIN','USERNAME','OS','PROGRAMFILES','PROGRAMFILES(X86)','PROGRAMW6432','HOMEDRIVE','HOMEPATH','NUMBER_OF_PROCESSORS','PROCESSOR_ARCHITECTURE','PATH','PATHEXT']
        env={k:v for k,v in os.environ.items() if k.upper() in allowed};env['PYTHONUTF8']='1'
        block=c.create_unicode_buffer('\0'.join(k+'='+v for k,v in sorted(env.items(),key=lambda i:i[0].upper()))+'\0\0')
        with (work/'run.log').open('wb') as output,open(os.devnull,'rb') as input_:
            stdout=msvcrt.get_osfhandle(output.fileno());stdin=msvcrt.get_osfhandle(input_.fileno())
            os.set_handle_inheritable(stdout,True);os.set_handle_inheritable(stdin,True)
            startup=Startup();startup.cb=c.sizeof(startup);startup.lpDesktop=name;startup.dwFlags=0x100
            startup.hStdInput=stdin;startup.hStdOutput=stdout;startup.hStdError=stdout
            command=c.create_unicode_buffer(subprocess.list2cmdline(cmd))
            check(kernel.CreateProcessW(cmd[0],command,None,None,True,0x4|0x400|0x08000000,c.cast(block,c.c_void_p),os.getcwd(),c.byref(startup),c.byref(info)))
            check(kernel.AssignProcessToJobObject(job,info.hProcess));assigned=True
            if kernel.ResumeThread(info.hThread)==0xFFFFFFFF:raise c.WinError(c.get_last_error())
            kernel.CloseHandle(info.hThread);info.hThread=None
            while kernel.WaitForSingleObject(info.hProcess,1000)==0x102:
                if time.monotonic()-started>a.timeout:raise TimeoutError('isolated test deadline exceeded')
            code=w.DWORD();check(kernel.GetExitCodeProcess(info.hProcess,c.byref(code)))
            accounting=BasicAccounting();check(kernel.QueryInformationJobObject(job,1,c.byref(accounting),c.sizeof(accounting),None))
            result={'exit_code':code.value,'pid':info.dwProcessId,'elapsed_seconds':round(time.monotonic()-started,3),'private_desktop':name,'switch_desktop_called':False,'job_assigned_before_resume':True,'job_kill_on_close':True,'job_total_processes':accounting.TotalProcesses,'job_active_before_close':accounting.ActiveProcesses}
    finally:
        if info.hProcess and not assigned:kernel.TerminateProcess(info.hProcess,71)
        if job:kernel.CloseHandle(job)
        if info.hThread:kernel.CloseHandle(info.hThread)
        if info.hProcess:kernel.CloseHandle(info.hProcess)
        result['desktop_closed']=bool(user.CloseDesktop(desktop))
        (work/'isolation.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result),flush=True)
    return result['exit_code']
if __name__=='__main__':raise SystemExit(main())
