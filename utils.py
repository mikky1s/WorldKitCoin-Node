import hashlib
import json
import struct
import msgpack
import zlib
from typing import Any

def hash256(data: bytes) -> bytes:
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()

def hash256_hex(data: bytes) -> str:
    return hash256(data).hex()

def serialize(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(',', ':')).encode()

def bits_to_target(bits: int) -> int:
    exponent = bits >> 24
    mantissa = bits & 0x00ffffff
    return mantissa * (1 << (8 * (exponent - 3)))

def target_to_bits(target: int) -> int:
    if target <= 0:
        return 0x1d00ffff
    bit_length = target.bit_length()
    byte_len = (bit_length + 7) // 8
    if byte_len <= 3:
        mantissa = target << (8 * (3 - byte_len))
        exponent = 3
    else:
        mantissa = target >> (8 * (byte_len - 3))
        exponent = byte_len
    mantissa &= 0x00ffffff
    if mantissa & 0x00800000:
        mantissa >>= 8
        exponent += 1
    return (exponent << 24) | mantissa

def compute_checksum(data: dict) -> str:
    return hash256_hex(serialize(data))

def _encode_bigint(obj):
    if isinstance(obj, int):
        if obj > 2**63 or obj < -2**63:
            return {"__bigint__": hex(obj)}
    raise TypeError("Object not serializable")

def _decode_bigint(obj):
    if isinstance(obj, dict) and "__bigint__" in obj:
        return int(obj["__bigint__"], 16)
    return obj

def pack_data(data: dict) -> bytes:
    """Упаковывает словарь в сжатый msgpack с добавлением контрольной суммы."""
    packed = msgpack.packb(data, use_bin_type=True, default=_encode_bigint)
    checksum = hashlib.sha256(packed).digest()
    return checksum + zlib.compress(packed)

def unpack_data(raw: bytes) -> dict:
    """Распаковывает данные, проверяя контрольную сумму."""
    if len(raw) < 32:
        raise ValueError("Data too short")
    checksum, compressed = raw[:32], raw[32:]
    if hashlib.sha256(zlib.decompress(compressed)).digest() != checksum:
        raise ValueError("Checksum mismatch")
    return msgpack.unpackb(zlib.decompress(compressed), raw=False, object_hook=_decode_bigint)

def is_valid_address(address: str) -> bool:
    """Проверяет, что адрес — 64-символьная шестнадцатеричная строка."""
    return isinstance(address, str) and len(address) == 64 and all(c in "0123456789abcdef" for c in address)