import ctypes
import json
import os
import re
from pathlib import Path
from . import gguf_backend, hf_hub, paths, settings
_SENSITIVE_EXACT_NAMES = frozenset({
    ".ssh",
    ".gnupg",
    ".aws",
    ".azure",
    ".kube",
    ".docker",
    ".netrc",
    ".git-credentials",
    ".npmrc",
    ".pypirc",
    "system volume information",
    "$recycle.bin",
    "recovery",
    "owner_token.txt",
    "keys_db.json",
    "node_identity.json",
    "network_invites.json",
    ".privacy_salt",
})
_SENSITIVE_SUBPATHS = (
    ("windows", "system32"),
    ("windows", "syswow64"),
    ("windows", "csc"),
    ("programdata", "microsoft", "crypto"),
    ("appdata", "local", "google"),
    ("appdata", "local", "microsoft", "edge"),
    ("appdata", "local", "bravesoftware"),
    ("appdata", "roaming", "mozilla"),
    ("appdata", "roaming", "opera software"),
    ("appdata", "roaming", "microsoft", "credentials"),
    ("appdata", "local", "microsoft", "credentials"),
    ("appdata", "roaming", "microsoft", "protect"),
    (".config", "google-chrome"),
    (".config", "chromium"),
    (".config", "gcloud"),
    (".mozilla", "firefox"),
    ("library", "application support", "google", "chrome"),
    ("library", "keychains"),
)
_POSIX_BLOCKED_PREFIXES = ("/etc", "/proc", "/sys", "/dev", "/boot", "/root", "/var/run", "/private/etc")
_WEIGHT_EXTS = (".safetensors", ".bin")
_SAFE_HF_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$")
_DRIVE_LABEL_CACHE = {}
def _volume_label(root_path):
    if os.name != "nt":
        return ""
    norm = os.path.normcase(root_path)
    if norm in _DRIVE_LABEL_CACHE:
        return _DRIVE_LABEL_CACHE[norm]
    label = ""
    try:
        buf = ctypes.create_unicode_buffer(261)
        ok = ctypes.windll.kernel32.GetVolumeInformationW(ctypes.c_wchar_p(root_path), buf, 260, None, None, None, None, 0)
        if ok:
            label = buf.value.strip()
    except Exception:
        label = ""
    _DRIVE_LABEL_CACHE[norm] = label
    return label
def _has_subpath_seq(parts_lower, seq):
    n = len(seq)
    if len(parts_lower) < n:
        return False
    for i in range(len(parts_lower) - n + 1):
        if tuple(parts_lower[i:i + n]) == seq:
            return True
    return False
def is_sensitive_path(path_obj):
    s = str(path_obj)
    raw_posix = s.replace("\\", "/").lower()
    for pref in _POSIX_BLOCKED_PREFIXES:
        if raw_posix == pref or raw_posix.startswith(pref + "/"):
            return True
    try:
        real = os.path.realpath(s)
    except Exception:
        real = s
    for cand in (s, real):
        p = Path(cand)
        parts_lower = [pt.lower() for pt in p.parts if pt not in ("/", "\\")]
        if len(parts_lower) >= 2 and re.match(r"^[a-z]:", parts_lower[0]) and parts_lower[1] == "windows":
            return True
        for pt in parts_lower:
            if pt in _SENSITIVE_EXACT_NAMES:
                return True
        for seq in _SENSITIVE_SUBPATHS:
            if _has_subpath_seq(parts_lower, seq):
                return True
        norm_posix = cand.replace("\\", "/").lower()
        for pref in _POSIX_BLOCKED_PREFIXES:
            if norm_posix == pref or norm_posix.startswith(pref + "/"):
                return True
    return False
def validate_browse_path(raw_path):
    raw = str(raw_path or "").strip()
    if not raw or "\x00" in raw or any(ord(c) < 32 for c in raw):
        raise ValueError("Invalid path.")
    if raw.startswith("\\\\") or raw.startswith("//"):
        raise PermissionError("Network UNC paths are not permitted.")
    expanded = os.path.expanduser(raw)
    resolved = os.path.realpath(os.path.abspath(expanded))
    if resolved.startswith("\\\\") or resolved.startswith("//"):
        raise PermissionError("Network UNC paths are not permitted.")
    if os.name == "nt":
        drv, _ = os.path.splitdrive(resolved)
        if not drv or not re.match(r"^[A-Za-z]:$", drv):
            raise ValueError("Path must be anchored to a valid local drive.")
    if is_sensitive_path(expanded) or is_sensitive_path(resolved):
        raise PermissionError("Access to system or sensitive credential directory is blocked.")
    return Path(resolved)
def _fmt_size(size_bytes):
    if size_bytes is None or size_bytes < 0:
        return "0 МБ"
    mb = size_bytes / (1024 * 1024)
    if mb >= 1024:
        return f"{round(mb / 1024, 2)} ГБ"
    return f"{round(mb, 1)} МБ"
def _bounded_dir_size(dir_path, max_files=250):
    total = 0
    count = 0
    try:
        for root, dirs, files in os.walk(str(dir_path)):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]
            for fn in files:
                fp = os.path.join(root, fn)
                try:
                    total += os.path.getsize(fp)
                except Exception:
                    pass
                count += 1
                if count >= max_files:
                    return total, True
    except Exception:
        pass
    return total, False
def _read_json_small(path_obj):
    try:
        if path_obj.is_file() and path_obj.stat().st_size <= 512 * 1024:
            return json.loads(path_obj.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        pass
    return None
def map_gguf_to_hf_base(path_or_name):
    raw = str(path_or_name or "").strip()
    if not raw:
        return {"suggested_hf_base_repo": None, "hf_base_cached_locally": False, "local_hf_snapshot_path": None}
    norm = raw.replace("\\", "/")
    m_cache = re.search(r"models--([^/]+)--([^/]+)", norm)
    cand_repo = None
    if m_cache:
        cand_repo = f"{m_cache.group(1)}/{m_cache.group(2)}"
    base_name = os.path.basename(norm)
    stem = re.sub(r"(?i)\.gguf$", "", base_name)
    stem = re.sub(r"(?i)[-_.]?(?:i1-)?(?:IQ\d_[A-Z0-9_]+|Q\d+_K(?:_[A-Z]+)?|Q\d+_\d+|Q\d+|F16|F32|BF16)(?:-\d+-of-\d+)?$", "", stem)
    stem = re.sub(r"(?i)[-_]?(?:imatrix|gguf)$", "", stem).strip("-_.")
    stem_low = stem.lower()
    suggested = None
    for m in hf_hub.CURATED_MODELS:
        mid = m["id"]
        mtail = mid.split("/")[-1].lower()
        if mtail == stem_low or stem_low in mtail or mtail in stem_low:
            suggested = mid
            break
    if not suggested and cand_repo:
        cleaned_repo = re.sub(r"(?i)[-_]?gguf$", "", cand_repo)
        author = cleaned_repo.split("/")[0] if "/" in cleaned_repo else ""
        tail = cleaned_repo.split("/")[-1]
        org_map = {
            "bartowski": None,
            "unsloth": None,
            "maziyarpanahi": None,
            "thebloke": None,
            "mradermacher": None,
        }
        if author.lower() in org_map:
            for m in hf_hub.CURATED_MODELS:
                if m["id"].split("/")[-1].lower() == tail.lower():
                    suggested = m["id"]
                    break
        elif "/" in cleaned_repo:
            suggested = cleaned_repo
    if not suggested and stem:
        if "qwen" in stem_low:
            suggested = f"Qwen/{stem}"
        elif "llama" in stem_low:
            suggested = f"meta-llama/{stem}"
        elif "deepseek" in stem_low:
            suggested = f"deepseek-ai/{stem}"
        elif "gemma" in stem_low:
            suggested = f"google/{stem}"
        elif "mistral" in stem_low:
            suggested = f"mistralai/{stem}"
        elif "phi" in stem_low:
            suggested = f"microsoft/{stem}"
        elif "smollm" in stem_low:
            suggested = f"HuggingFaceTB/{stem}"
    local_hf = None
    cached_locally = False
    if suggested:
        cache_folder_name = f"models--{suggested.replace('/', '--')}"
        for hub_root in gguf_backend.all_hub_dirs():
            cand_dir = Path(hub_root) / cache_folder_name / "snapshots"
            if cand_dir.is_dir():
                for snap in sorted(cand_dir.iterdir()):
                    if snap.is_dir() and (snap / "config.json").is_file() and any(snap.glob("*.safetensors")):
                        local_hf = str(snap)
                        cached_locally = True
                        break
            if cached_locally:
                break
    return {
        "suggested_hf_base_repo": suggested,
        "hf_base_cached_locally": cached_locally,
        "local_hf_snapshot_path": local_hf,
    }
def _compat_block(gguf_ok, gguf_reason, pt_ok, pt_reason, soup_ok, soup_reason, suggested_base=None, resolved_base=None):
    return {
        "gguf_inference": {"supported": bool(gguf_ok), "reason_ru": gguf_reason},
        "pytorch_inference": {"supported": bool(pt_ok), "reason_ru": pt_reason},
        "soup_training": {
            "supported": bool(soup_ok),
            "can_resolve_base": bool(suggested_base),
            "suggested_base_model": suggested_base,
            "resolved_base_model": resolved_base,
            "reason_ru": soup_reason,
        },
    }
def classify_model_path(target_path, deep_size=False):
    p = validate_browse_path(target_path)
    if p.is_file():
        sz = p.stat().st_size
        ext = p.suffix.lower()
        if ext == ".gguf":
            val = gguf_backend.validate_gguf_file(str(p))
            quant = gguf_backend.parse_quant_from_name(p.name)
            hf_map = map_gguf_to_hf_base(str(p))
            sug = hf_map["local_hf_snapshot_path"] or hf_map["suggested_hf_base_repo"]
            if not val.get("valid"):
                compat = _compat_block(
                    False,
                    f"Некорректный или повреждённый GGUF-файл: {val.get('reason')}",
                    False,
                    "Формат GGUF не поддерживается PyTorch.",
                    False,
                    "Повреждённый GGUF-файл нельзя использовать ни для запуска, ни для обучения.",
                    suggested_base=sug,
                )
            else:
                reason = "Квантованные файлы .gguf подходят только для инференса SHARD, но не могут обучаться напрямую в PyTorch/SOUP. Выберите папку с SafeTensors (config.json + *.safetensors) или HF ID."
                compat = _compat_block(
                    True,
                    f"Готов к запуску в SHARD ({quant or 'GGUF'}, {_fmt_size(sz)}).",
                    False,
                    "Для PyTorch требуется папка Hugging Face с SafeTensors.",
                    False,
                    reason,
                    suggested_base=sug,
                )
            return {
                "path": str(p),
                "name": p.name,
                "is_dir": False,
                "format": "gguf_inference",
                "format_label": "GGUF (Инференс)" if val.get("valid") else "GGUF (Повреждён)",
                "valid": bool(val.get("valid")),
                "quant": quant,
                "size_bytes": sz,
                "size_str": _fmt_size(sz),
                "compatibility": compat,
            }
        if ext == ".safetensors":
            parent = p.parent
            has_cfg = (parent / "config.json").is_file()
            has_adp = (parent / "adapter_config.json").is_file()
            if has_cfg:
                compat = _compat_block(
                    False,
                    "Для GGUF-инференса конвертируйте или выберите GGUF-вариант.",
                    True,
                    "Используется родительская папка модели Hugging Face.",
                    True,
                    "Используется родительская папка Hugging Face с config.json — подходит для обучения SOUP.",
                    resolved_base=str(parent),
                )
            elif has_adp:
                adp_cfg = _read_json_small(parent / "adapter_config.json") or {}
                compat = _compat_block(
                    False,
                    "LoRA-адаптер требует слияния (Merge) и экспорта в GGUF.",
                    True,
                    "LoRA-адаптер загружается поверх базовой модели.",
                    False,
                    "Вы выбрали файл весов LoRA-адаптера. Для нового обучения SOUP нужна базовая модель (config.json + *.safetensors).",
                    suggested_base=adp_cfg.get("base_model_name_or_path"),
                )
            else:
                compat = _compat_block(
                    False,
                    "Не является GGUF-файлом.",
                    False,
                    "Рядом отсутствует config.json.",
                    False,
                    "Отдельный файл .safetensors без config.json и токенизатора нельзя загрузить для обучения SOUP.",
                )
            return {
                "path": str(p),
                "name": p.name,
                "is_dir": False,
                "format": "safetensors_file",
                "format_label": "SafeTensors (Веса)",
                "valid": True,
                "size_bytes": sz,
                "size_str": _fmt_size(sz),
                "resolved_parent_dir": str(parent) if has_cfg else None,
                "compatibility": compat,
            }
        return {
            "path": str(p),
            "name": p.name,
            "is_dir": False,
            "format": "other_file",
            "format_label": "Файл",
            "valid": False,
            "size_bytes": sz,
            "size_str": _fmt_size(sz),
            "compatibility": _compat_block(False, "Не является моделью.", False, "Не является моделью.", False, "Файл не является поддерживаемым форматом модели."),
        }
    if p.is_dir():
        cfg = _read_json_small(p / "config.json")
        adp = _read_json_small(p / "adapter_config.json")
        has_weights = any(p.glob("*.safetensors")) or any(p.glob("pytorch_model*.bin")) or (p / "model.safetensors.index.json").is_file()
        gguf_files = sorted(p.glob("*.gguf"))
        if cfg and (has_weights or "model_type" in cfg or "architectures" in cfg):
            sz, partial = _bounded_dir_size(p) if deep_size else _bounded_dir_size(p, max_files=80)
            compat = _compat_block(
                bool(gguf_files),
                "В папке есть GGUF." if gguf_files else "Папка формата Hugging Face (SafeTensors) — для GGUF используйте PyTorch или GGUF-версию.",
                bool(has_weights),
                "Готова к инференсу PyTorch." if has_weights else "В папке нет файлов весов .safetensors/.bin.",
                bool(has_weights),
                "Локальная модель Hugging Face (config.json + веса) — полностью готова для SFT/DPO обучения SOUP." if has_weights else "Найдена config.json, но отсутствуют веса .safetensors/.bin.",
                resolved_base=str(p) if has_weights else None,
            )
            return {
                "path": str(p),
                "name": p.name,
                "is_dir": True,
                "format": "hf_model_dir",
                "format_label": "Модель Hugging Face (SafeTensors)",
                "valid": bool(has_weights),
                "size_bytes": sz,
                "size_str": _fmt_size(sz) + ("+" if partial else ""),
                "model_type": cfg.get("model_type"),
                "compatibility": compat,
            }
        if adp and (any(p.glob("adapter_model*")) or "base_model_name_or_path" in adp):
            sz, _ = _bounded_dir_size(p, max_files=60)
            base_ref = adp.get("base_model_name_or_path")
            compat = _compat_block(
                bool(gguf_files),
                "Для запуска в GGUF выполните Merge & Export GGUF во вкладке «Версии»." if not gguf_files else "Готов GGUF.",
                True,
                "PEFT/LoRA-адаптер поддерживается PyTorch.",
                False,
                "Это папка обученного LoRA-адаптера (чекпоинт), а не полная базовая модель. Используйте базовую модель или --resume.",
                suggested_base=base_ref,
            )
            return {
                "path": str(p),
                "name": p.name,
                "is_dir": True,
                "format": "lora_adapter",
                "format_label": "LoRA Адаптер (Чекпоинт)",
                "valid": True,
                "base_model_name_or_path": base_ref,
                "size_bytes": sz,
                "size_str": _fmt_size(sz),
                "compatibility": compat,
            }
        if p.name.startswith("models--") or (p / "snapshots").is_dir():
            snap_root = p / "snapshots"
            best_snap = None
            has_hf_snap = False
            has_gguf_snap = False
            if snap_root.is_dir():
                for ch in sorted(snap_root.iterdir()):
                    if not ch.is_dir():
                        continue
                    if (ch / "config.json").is_file() and (any(ch.glob("*.safetensors")) or any(ch.glob("pytorch_model*.bin"))):
                        best_snap = ch
                        has_hf_snap = True
                        break
                    if any(ch.glob("*.gguf")):
                        best_snap = best_snap or ch
                        has_gguf_snap = True
            hf_map = map_gguf_to_hf_base(str(p))
            sz, partial = _bounded_dir_size(p, max_files=120) if deep_size else (0, False)
            if has_hf_snap and best_snap:
                compat = _compat_block(
                    has_gguf_snap,
                    "В снимке есть GGUF." if has_gguf_snap else "Кэш содержит веса Hugging Face SafeTensors.",
                    True,
                    "Готова к инференсу PyTorch.",
                    True,
                    f"Снимок Hugging Face ({best_snap.name[:12]}) готов для обучения SOUP.",
                    resolved_base=str(best_snap),
                )
            elif has_gguf_snap:
                compat = _compat_block(
                    True,
                    "Локальный GGUF-кэш готов к запуску в SHARD.",
                    False,
                    "Кэш содержит только .gguf (для llama.cpp).",
                    False,
                    "В этой папке кэша лежат только квантованные файлы .gguf, которые не подходят для обучения SOUP. Выберите HF SafeTensors модель.",
                    suggested_base=hf_map["local_hf_snapshot_path"] or hf_map["suggested_hf_base_repo"],
                )
            else:
                compat = _compat_block(False, "Снимки моделей не найдены.", False, "Снимки моделей не найдены.", False, "Папка кэша не содержит готовых весов модели.")
            return {
                "path": str(p),
                "name": p.name,
                "is_dir": True,
                "format": "hf_hub_cache",
                "format_label": "Кэш HF (SafeTensors)" if has_hf_snap else ("Кэш HF (GGUF)" if has_gguf_snap else "Кэш HF"),
                "valid": has_hf_snap or has_gguf_snap,
                "resolved_snapshot": str(best_snap) if best_snap else None,
                "size_bytes": sz,
                "size_str": _fmt_size(sz) + ("+" if partial else "") if sz else "",
                "compatibility": compat,
            }
        if gguf_files:
            valid_gguf = [g for g in gguf_files if gguf_backend.validate_gguf_file(str(g)).get("valid")]
            first_g = valid_gguf[0] if valid_gguf else gguf_files[0]
            hf_map = map_gguf_to_hf_base(str(first_g))
            sz = sum(g.stat().st_size for g in gguf_files if g.is_file())
            compat = _compat_block(
                bool(valid_gguf),
                f"Найдено GGUF-файлов: {len(valid_gguf)} ({_fmt_size(sz)})." if valid_gguf else "GGUF-файлы повреждены.",
                False,
                "GGUF не предназначен для PyTorch.",
                False,
                "Папка содержит только файлы .gguf для инференса. Для SOUP обучения выберите модель в формате Hugging Face SafeTensors.",
                suggested_base=hf_map["local_hf_snapshot_path"] or hf_map["suggested_hf_base_repo"],
            )
            return {
                "path": str(p),
                "name": p.name,
                "is_dir": True,
                "format": "gguf_inference",
                "format_label": f"Папка GGUF ({len(gguf_files)})",
                "valid": bool(valid_gguf),
                "size_bytes": sz,
                "size_str": _fmt_size(sz),
                "compatibility": compat,
            }
        return {
            "path": str(p),
            "name": p.name,
            "is_dir": True,
            "format": "directory",
            "format_label": "Папка",
            "valid": False,
            "size_bytes": None,
            "size_str": "",
            "compatibility": _compat_block(False, "Откройте папку чтобы найти файлы моделей.", False, "Откройте папку чтобы найти файлы моделей.", False, "Обычная папка — перейдите внутрь для поиска модели."),
        }
    raise FileNotFoundError(f"Path not found: {target_path}")
def list_roots():
    drives = []
    for d in settings.available_drives():
        lbl = _volume_label(d["path"])
        drives.append({
