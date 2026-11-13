import datetime
def format_bytes(num_bytes: int) -> str:
    n = float(max(0, num_bytes))
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024.0 or unit == "TB":
            return f"{n:.1f} {unit}" if unit in ("MB", "GB", "TB") else f"{int(n)} {unit}"
        n /= 1024.0
    return f"{n:.1f} TB"
def format_speed(bytes_per_sec: float) -> str:
    return f"{format_bytes(int(bytes_per_sec))}/s"
def format_eta(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    m, rem = divmod(s, 60)
    if m < 60:
        return f"{m}m {rem}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m"
def format_percentage(current: int, total: int) -> str:
    if total <= 0:
        return "0%"
    pct = min(100.0, max(0.0, (float(current) / float(total)) * 100.0))
    return f"{pct:.1f}%"
def format_timestamp_iso(ts: float = 0.0) -> str:
    t = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc) if ts > 0 else datetime.datetime.now(datetime.timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")
def format_duration_ms(elapsed_sec: float) -> str:
    return f"{int(elapsed_sec * 1000.0)}ms"
