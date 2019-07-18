from ctypes import *
from ctypes.wintypes import *
import os
import time
from subprocess import check_output
import psutil


OpenProcess = windll.kernel32.OpenProcess
ReadProcessMemory = windll.kernel32.ReadProcessMemory
CloseHandle = windll.kernel32.CloseHandle

PROCESS_ALL_ACCESS = 0x1F0FFF
time.sleep(1)

PROCNAME = "MapleSaga.exe"

for proc in psutil.process_iter():
    if proc.name() == PROCNAME:
        print(proc.pid)
pid = proc.pid   # I assume you have this from somewhere.
address = 0x1000000  # Likewise; for illustration I'll get the .exe header.

buffer = c_char_p("The data goes here")
bufferSize = len(buffer.value)
bytesRead = c_ulong(0)

processHandle = OpenProcess(PROCESS_ALL_ACCESS, False, pid)
if ReadProcessMemory(processHandle, address, buffer, bufferSize, byref(bytesRead)):
    print("Success:", buffer)
else:
    print("Failed.")

CloseHandle(processHandle)