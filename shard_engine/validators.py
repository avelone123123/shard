import re
def validate_model_id(model_id: str) -> bool:
    return bool(model_id and isinstance(model_id, str) and re.match(r"^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)?$", model_id.strip()))
def validate_quant_name(quant: str) -> bool:
    return bool(quant and isinstance(quant, str) and re.match(r"^(?:[A-Z0-9]+(?:_[A-Z0-9]+)*)$", quant.strip().upper()))
