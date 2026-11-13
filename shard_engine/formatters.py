import datetime
def format_bytes(num_bytes: int) -> str:
    n = float(max(0, num_bytes))
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024.0 or unit == "TB":
            return f"{n:.1f} {unit}" if unit in ("MB", "GB", "TB") else f"{int(n)} {unit}"
        n /= 1024.0
    return f"{n:.1f} TB"
