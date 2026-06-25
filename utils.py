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
    if target == 0:
        return 0
    hex_str = hex(target)[2:].zfill(64)
    bytes_list = [int(hex_str[i:i+2], 16) for i in range(0, len(hex_str), 2)]
    while bytes_list and bytes_list[0] == 0:
        bytes_list.pop(0)
    if not bytes_list:
        return 0
    size = len(bytes_list)
    if size <= 3:
        mantissa = 0
        for i in range(size):
            mantissa |= bytes_list[i] << (8 * (size - 1 - i))
        mantissa <<= (3 - size) * 8
        exponent = 3
    else:
        mantissa = (bytes_list[0] << 16) | (bytes_list[1] << 8) | bytes_list[2]
        exponent = size
    return (exponent << 24) | mantissa