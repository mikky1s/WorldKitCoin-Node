import hashlib
import json
import struct

def hash256(data: bytes) -> bytes:
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()

def hash256_hex(data: bytes) -> str:
    return hash256(data).hex()

def serialize(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(',', ':')).encode()

def bits_to_target(bits: int) -> int:
    exponent = bits >> 24
    mantissa = bits & 0x00ffffff
    target = mantissa * (1 << (8 * (exponent - 3)))
    return target

def target_to_bits(target: int) -> int:
    # Упрощённая версия для демонстрации
    # В реальном проекте нужно полноценное преобразование
    return 0x1f3ffc2f