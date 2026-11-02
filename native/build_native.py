import os
import subprocess
import sys
VCVARS = r"C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
NATIVE_DIR = os.path.dirname(os.path.abspath(__file__))
SHARD_DIR = os.path.dirname(NATIVE_DIR)
ASM_SRC = os.path.join(NATIVE_DIR, "shard_asm.asm")
ASM_OBJ = os.path.join(NATIVE_DIR, "shard_asm.obj")
CPP_SRC = os.path.join(NATIVE_DIR, "shard_core.cpp")
OUT_DLL = os.path.join(SHARD_DIR, "shard_core.dll")
def build():
    if not os.path.exists(VCVARS):
        print(f"ERROR: vcvars64 not found at {VCVARS}")
        sys.exit(1)
    cmd_asm = f'call "{VCVARS}" && ml64 /c /Fo"{ASM_OBJ}" "{ASM_SRC}"'
    r_asm = subprocess.run(cmd_asm, shell=True, capture_output=True, text=True)
    if r_asm.returncode != 0:
        print("ASM Compilation failed:\n", r_asm.stdout, r_asm.stderr)
        sys.exit(1)
    print("ASM Compiled successfully:", ASM_OBJ)
    cmd_cpp = f'call "{VCVARS}" && cl /O2 /EHsc /arch:AVX2 /std:c++20 /LD "{CPP_SRC}" "{ASM_OBJ}" /Fe:"{OUT_DLL}"'
    r_cpp = subprocess.run(cmd_cpp, shell=True, capture_output=True, text=True)
    if r_cpp.returncode != 0:
        print("C++ Compilation failed:\n", r_cpp.stdout, r_cpp.stderr)
        sys.exit(1)
    print("C++ / Assembly DLL built successfully:", OUT_DLL)
    for ext in [".obj", ".exp", ".lib"]:
        garbage = os.path.splitext(CPP_SRC)[0] + ext
        if os.path.exists(garbage):
            try:
                os.remove(garbage)
            except Exception:
                pass
if __name__ == "__main__":
    build()