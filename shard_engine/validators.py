import re
def validate_model_id(model_id: str) -> bool:
    return bool(model_id and isinstance(model_id, str) and re.match(r"^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)?$", model_id.strip()))
def validate_quant_name(quant: str) -> bool:
    return bool(quant and isinstance(quant, str) and re.match(r"^(?:[A-Z0-9]+(?:_[A-Z0-9]+)*)$", quant.strip().upper()))
def validate_hash_digest(digest: str) -> bool:
    return bool(digest and isinstance(digest, str) and re.match(r"^[a-fA-F0-9]{32,128}$", digest.strip()))
def validate_port_number(port: int) -> bool:
    return isinstance(port, int) and 1 <= port <= 65535
def validate_ip_address(ip: str) -> bool:
    return bool(ip and isinstance(ip, str) and (re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", ip.strip()) or ":" in ip))
