import re
def validate_model_id(model_id: str) -> bool:
    return bool(model_id and isinstance(model_id, str) and re.match(r"^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)?$", model_id.strip()))
def validate_quant_name(quant: str) -> bool:
    return bool(quant and isinstance(quant, str) and re.match(r"^(?:[A-Z0-9]+(?:_[A-Z0-9]+)*)$", quant.strip().upper()))
def validate_hash_digest(digest: str) -> bool:
    return bool(digest and isinstance(digest, str) and re.match(r"^[a-fA-F0-9]{32,128}$", digest.strip()))
