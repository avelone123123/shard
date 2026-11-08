import glob
import os
import subprocess
import sys
NATIVE_DIR = os.path.dirname(os.path.abspath(__file__))
SHARD_DIR = os.path.dirname(NATIVE_DIR)
ASM_SRC = os.path.join(NATIVE_DIR, "shard_asm.asm")
ASM_OBJ = os.path.join(NATIVE_DIR, "shard_asm.obj")
CPP_SRC = os.path.join(NATIVE_DIR, "shard_core.cpp")
GUI_SRC = os.path.join(NATIVE_DIR, "shard_gui.cpp")
OUT_DLL = os.path.join(SHARD_DIR, "shard_core.dll")
OUT_EXE = os.path.join(SHARD_DIR, "shard-native.exe")
def _find_vcvars():
    candidates = [
        r"C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvars64.bat",
        r"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat",
        r"C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat"
    ]
    candidates.extend(glob.glob(r"C:\Program Files*\Microsoft Visual Studio\*\*\VC\Auxiliary\Build\vcvars64.bat"))
    for c in candidates:
        if os.path.exists(c):
            return c
    return ""
def _run_hidden(cmd_str):
    kw = {}
    if os.name == "nt":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW
        kw["startupinfo"] = si
    return subprocess.run(cmd_str, shell=True, cwd=NATIVE_DIR, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, **kw)
def build():
    vcvars = _find_vcvars()
    if not vcvars:
        print("ERROR: vcvars64.bat not found")
        sys.exit(1)
    cmd_asm = f'call "{vcvars}" >nul && ml64 /nologo /c /Fo"{ASM_OBJ}" "{ASM_SRC}"'
    r_asm = _run_hidden(cmd_asm)
    if r_asm.returncode != 0:
        print("ASM Compilation failed:\n", r_asm.stdout, r_asm.stderr)
        sys.exit(1)
    cmd_cpp = f'call "{vcvars}" >nul && cl /nologo /O2 /EHsc /arch:AVX2 /std:c++20 /LD "{CPP_SRC}" "{ASM_OBJ}" /Fe:"{OUT_DLL}"'
    r_cpp = _run_hidden(cmd_cpp)
    if r_cpp.returncode != 0:
        print("C++ DLL Compilation failed:\n", r_cpp.stdout, r_cpp.stderr)
        sys.exit(1)
    if os.path.exists(GUI_SRC):
        cmd_gui = f'call "{vcvars}" >nul && cl /nologo /O2 /EHsc /arch:AVX2 /std:c++20 "{GUI_SRC}" "{ASM_OBJ}" user32.lib gdi32.lib /link /SUBSYSTEM:WINDOWS /OUT:"{OUT_EXE}"'
        r_gui = _run_hidden(cmd_gui)
        if r_gui.returncode != 0:
            print("Native GUI Compilation failed:\n", r_gui.stdout, r_gui.stderr)
            sys.exit(1)
    for folder in (NATIVE_DIR, SHARD_DIR):
        for fn in os.listdir(folder):
            if fn.endswith((".exp", ".lib")) or (fn.endswith(".obj") and fn != "shard_asm.obj"):
                try:
                    os.remove(os.path.join(folder, fn))
                except Exception:
                    pass
    print("OK:", OUT_DLL, OUT_EXE)
if __name__ == "__main__":
    build()
