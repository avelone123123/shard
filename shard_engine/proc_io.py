import ctypes
import locale
import os
import re
import subprocess
import threading
import time
_WIN_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_UTF8_MULTIBYTE_HINT_RE = re.compile(rb"(?:[\xd0-\xd1][\x80-\xbf]|\xe2(?:\x80|[\x94-\x97])[\x80-\xbf]|\xf0\x9f[\x80-\xbf]{2})")
def _win_ansi_encoding():
    try:
        enc = locale.getencoding() if hasattr(locale, "getencoding") else locale.getpreferredencoding(False)
    except Exception:
        enc = ""
    low = str(enc or "").strip().lower()
    if low and low not in ("utf-8", "utf8", "utf_8", "ascii"):
        return enc
    if os.name == "nt":
        try:
            acp = ctypes.windll.kernel32.GetACP()
            if acp and int(acp) != 65001:
                return f"cp{int(acp)}"
        except Exception:
            pass
    return None
def _win_oem_encoding():
    if os.name != "nt":
        return None
    try:
        oem = ctypes.windll.kernel32.GetOEMCP()
        if oem and int(oem) != 65001:
            return f"cp{int(oem)}"
    except Exception:
        pass
    return None
def _decode_segment(raw, prefer_utf8=True, try_oem=False):
    if not raw:
        return ""
    try:
        return raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        pass
    if not try_oem and _UTF8_MULTIBYTE_HINT_RE.search(raw):
        return raw.decode("utf-8", errors="replace")
    if os.name == "nt":
        cands = []
        ansi = _win_ansi_encoding()
        oem = _win_oem_encoding()
        if try_oem:
            if oem:
                cands.append(oem)
            if ansi and ansi not in cands:
                cands.append(ansi)
        elif prefer_utf8:
            if ansi:
                cands.append(ansi)
        for enc in cands:
            try:
                return raw.decode(enc, errors="strict")
            except UnicodeDecodeError:
                pass
            except LookupError:
                pass
    return raw.decode("utf-8", errors="replace")
def safe_decode_subprocess_output(data, prefer_utf8=True, try_oem=False):
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    raw = bytes(data)
    if not raw:
        return ""
    try:
        return raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        pass
    if b"\n" in raw or b"\r" in raw:
        return "".join(_decode_segment(seg, prefer_utf8=prefer_utf8, try_oem=try_oem) for seg in raw.splitlines(keepends=True))
    return _decode_segment(raw, prefer_utf8=prefer_utf8, try_oem=try_oem)
def run_subprocess_safe(cmd, *, timeout=None, cwd=None, env=None, stdin=subprocess.DEVNULL, creationflags=None, prefer_utf8=True, try_oem=False, shell=False):
    kw = {"stdin": stdin, "capture_output": True, "timeout": timeout, "shell": shell}
    if cwd is not None:
        kw["cwd"] = cwd
    if env is not None:
        kw["env"] = env
    if creationflags is not None:
        kw["creationflags"] = creationflags
    try:
        res = subprocess.run(cmd, **kw)
    except subprocess.TimeoutExpired as exc:
        out_t = safe_decode_subprocess_output(getattr(exc, "stdout", None), prefer_utf8=prefer_utf8, try_oem=try_oem)
        err_t = safe_decode_subprocess_output(getattr(exc, "stderr", None), prefer_utf8=prefer_utf8, try_oem=try_oem)
        raise subprocess.TimeoutExpired(exc.cmd, exc.timeout, output=out_t, stderr=err_t) from exc
    out_txt = safe_decode_subprocess_output(getattr(res, "stdout", b""), prefer_utf8=prefer_utf8, try_oem=try_oem)
    err_txt = safe_decode_subprocess_output(getattr(res, "stderr", b""), prefer_utf8=prefer_utf8, try_oem=try_oem)
    return subprocess.CompletedProcess(args=getattr(res, "args", cmd), returncode=res.returncode, stdout=out_txt, stderr=err_txt)
def _utf8_safe_split_index(buf, limit):
    if len(buf) <= limit:
        return len(buf)
    pos = limit - 1
    steps = 0
    while pos >= 0 and steps < 4 and (buf[pos] & 0xC0) == 0x80:
        pos -= 1
        steps += 1
    if pos < 0:
        return limit
    b = buf[pos]
    if b < 0x80:
        exp = 1
    elif 0xC2 <= b <= 0xDF:
        exp = 2
    elif 0xE0 <= b <= 0xEF:
        exp = 3
    elif 0xF0 <= b <= 0xF4:
        exp = 4
    else:
        exp = 1
    if pos + exp > limit and pos > 0:
        return pos
    return limit
def _read_pipe_chunk(fh):
    try:
        fd = fh.fileno()
        if isinstance(fd, int) and fd >= 0:
            return os.read(fd, 4096)
    except Exception:
        pass
    if hasattr(fh, "read1"):
        try:
            return fh.read1(4096)
        except Exception:
            return b""
    try:
        return fh.read(4096)
    except Exception:
        return b""
def terminate_process_tree(proc):
    if proc is None:
        return
    try:
        if proc.poll() is not None:
            return
        if os.name == "nt":
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=8, creationflags=_WIN_NO_WINDOW)
            except Exception:
                proc.terminate()
        else:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
    except Exception:
        pass
def stream_subprocess_safe(cmd, *, on_stdout=None, on_stderr=None, merge_stderr=False, on_proc=None, cwd=None, env=None, stdin=subprocess.DEVNULL, creationflags=None, prefer_utf8=True, try_oem=False, cancel_check=None, on_cancel=None, timeout=None, line_limit=4096):
    kw = {
        "stdin": stdin,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT if merge_stderr else subprocess.PIPE,
        "bufsize": 0,
    }
    if cwd is not None:
        kw["cwd"] = cwd
    if env is not None:
        kw["env"] = env
    if creationflags is not None:
        kw["creationflags"] = creationflags
    proc = subprocess.Popen(cmd, **kw)
    if on_proc:
        try:
            on_proc(proc)
        except Exception:
            pass
    out_parts = []
    err_parts = []
    def _emit(chunk_bytes, parts, cb):
        txt = safe_decode_subprocess_output(chunk_bytes, prefer_utf8=prefer_utf8, try_oem=try_oem)
        parts.append(txt)
        if cb and txt:
            try:
                cb(txt)
            except Exception:
                pass
    def _reader(fh, parts, cb):
        buf = bytearray()
        skip_lf = False
        try:
            while True:
                chunk = _read_pipe_chunk(fh)
                if not chunk:
                    if buf:
                        _emit(bytes(buf), parts, cb)
                        buf.clear()
                    break
                if skip_lf:
                    if chunk[:1] == b"\n":
                        chunk = chunk[1:]
                    skip_lf = False
                if not chunk:
                    continue
                buf.extend(chunk)
                while True:
                    idx_n = buf.find(b"\n")
                    idx_r = buf.find(b"\r")
                    if idx_n == -1 and idx_r == -1:
                        break
                    if idx_r != -1 and (idx_n == -1 or idx_r < idx_n):
                        line = bytes(buf[:idx_r])
                        if idx_r + 1 < len(buf):
                            step = 2 if buf[idx_r + 1] == 10 else 1
                            del buf[:idx_r + step]
                        else:
                            del buf[:idx_r + 1]
                            skip_lf = True
                        _emit(line, parts, cb)
                    else:
                        line = bytes(buf[:idx_n])
                        del buf[:idx_n + 1]
                        _emit(line, parts, cb)
                while len(buf) > line_limit:
                    cut = _utf8_safe_split_index(buf, line_limit)
                    _emit(bytes(buf[:cut]), parts, cb)
                    del buf[:cut]
        finally:
            try:
                fh.close()
            except Exception:
                pass
    threads = []
    if proc.stdout is not None:
        t_out = threading.Thread(target=_reader, args=(proc.stdout, out_parts, on_stdout), daemon=True)
        t_out.start()
        threads.append(t_out)
    if proc.stderr is not None and not merge_stderr:
        t_err = threading.Thread(target=_reader, args=(proc.stderr, err_parts, on_stderr), daemon=True)
        t_err.start()
        threads.append(t_err)
    cancelled = False
    timed_out = False
    deadline = (time.monotonic() + float(timeout)) if timeout is not None else None
    while proc.poll() is None:
        if cancel_check and cancel_check():
            cancelled = True
            if on_cancel:
                try:
                    on_cancel(proc)
                except Exception:
                    terminate_process_tree(proc)
            else:
                terminate_process_tree(proc)
            break
        if deadline is not None and time.monotonic() >= deadline:
            timed_out = True
            terminate_process_tree(proc)
            break
        time.sleep(0.05)
    try:
        rc = proc.wait(timeout=5)
    except Exception:
        terminate_process_tree(proc)
        try:
            rc = proc.wait(timeout=3)
        except Exception:
            rc = -1
    for t in threads:
        t.join(timeout=3.0)
    out_full = "\n".join(out_parts)
    err_full = "\n".join(err_parts)
    if timed_out:
        raise subprocess.TimeoutExpired(cmd, timeout, output=out_full, stderr=err_full)
    return {
        "returncode": rc,
        "stdout": out_full,
        "stderr": err_full,
        "cancelled": cancelled,
