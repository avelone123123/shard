import os
import re
EMOJI_PATTERN = re.compile(
    r"[\U00010000-\U0010ffff\u2600-\u26ff\u2700-\u27bf\u1f300-\u1f64f\u1f680-\u1f6ff]"
)
def clean_code_content(content, file_ext=""):
    lines = content.splitlines()
    cleaned = []
    in_block_comment = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if file_ext in (".cpp", ".c", ".h", ".js", ".ts"):
            if "/*" in stripped and "*/" in stripped:
                line = re.sub(r"/\*.*?\*/", "", line)
                stripped = line.strip()
            elif "/*" in stripped:
                in_block_comment = True
                continue
            elif in_block_comment:
                if "*/" in stripped:
                    in_block_comment = False
                continue
            if stripped.startswith("//"):
                continue
            if "//" in line:
                line = line.split("//")[0].rstrip()
                stripped = line.strip()
        elif file_ext in (".py", ".sh", ".bash"):
            if stripped.startswith("#"):
                continue
            if "#" in line:
                parts = line.split("#")
                if not (parts[0].count('"') % 2 != 0 or parts[0].count("'") % 2 != 0):
                    line = parts[0].rstrip()
                    stripped = line.strip()
        elif file_ext in (".asm",):
            if stripped.startswith(";"):
                continue
            if ";" in line:
                line = line.split(";")[0].rstrip()
                stripped = line.strip()
        line = EMOJI_PATTERN.sub("", line)
        if line.strip():
            cleaned.append(line)
    return "\n".join(cleaned)
def sanitize_file(file_path):
    if not os.path.exists(file_path):
        return False
    ext = os.path.splitext(file_path)[1].lower()
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            raw = f.read()
        sanitized = clean_code_content(raw, ext)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(sanitized)
        return True
    except Exception:
        return False