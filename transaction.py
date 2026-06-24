from utils import hash256_hex, hash256, is_valid_address, serialize_varint, deserialize_varint
from typing import List, Optional, Tuple
from ecdsa import SigningKey, VerifyingKey, SECP256k1
import struct

class TxIn:
    __slots__ = ('prev_tx_hash', 'prev_output_index', 'signature', 'pubkey')
    def __init__(self, prev_tx_hash: str, prev_output_index: int,
                 signature: Optional[bytes] = None, pubkey: Optional[str] = None):
        self.prev_tx_hash = prev_tx_hash
        self.prev_output_index = prev_output_index
        self.signature = signature
        self.pubkey = pubkey

    def to_bytes(self) -> bytes:
        data = bytes.fromhex(self.prev_tx_hash)[::-1]
        data += struct.pack('<I', self.prev_output_index)
        sig = self.signature or b''
        data += serialize_varint(len(sig)) + sig
        pub = bytes.fromhex(self.pubkey) if self.pubkey else b''
        data += serialize_varint(len(pub)) + pub
        return data

    @classmethod
    def from_bytes(cls, data: bytes, pos: int = 0) -> Tuple['TxIn', int]:
        prev_tx_hash = data[pos:pos+32][::-1].hex()
        pos += 32
        prev_output_index = struct.unpack('<I', data[pos:pos+4])[0]
        pos += 4
        sig_len, pos = deserialize_varint(data, pos)
        signature = data[pos:pos+sig_len]
        pos += sig_len
        pub_len, pos = deserialize_varint(data, pos)
        pubkey = data[pos:pos+pub_len].hex() if pub_len else None
        pos += pub_len
        return cls(prev_tx_hash, prev_output_index, signature, pubkey), pos

    def to_dict(self) -> dict:
        return {
            'prev_tx_hash': self.prev_tx_hash,
            'prev_output_index': self.prev_output_index,
            'signature': self.signature.hex() if self.signature else None,
            'pubkey': self.pubkey
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'TxIn':
        sig = bytes.fromhex(data['signature']) if data['signature'] else None
        return cls(data['prev_tx_hash'], data['prev_output_index'], sig, data.get('pubkey'))


class TxOut:
    __slots__ = ('amount', 'address', 'lock_until')
    def __init__(self, amount: int, address: str, lock_until: int = 0):
        self.amount = amount
        self.address = address
        self.lock_until = lock_until

    def to_bytes(self) -> bytes:
        data = struct.pack('<Q', self.amount)
        data += bytes.fromhex(self.address)
        data += struct.pack('<I', self.lock_until)
        return data

    @classmethod
    def from_bytes(cls, data: bytes, pos: int = 0) -> Tuple['TxOut', int]:
        amount = struct.unpack('<Q', data[pos:pos+8])[0]
        pos += 8
        address = data[pos:pos+32].hex()
        pos += 32
        lock_until = struct.unpack('<I', data[pos:pos+4])[0]
        pos += 4
        return cls(amount, address, lock_until), pos

    def to_dict(self) -> dict:
        return {'amount': self.amount, 'address': self.address, 'lock_until': self.lock_until}

    @classmethod
    def from_dict(cls, data: dict) -> 'TxOut':
        return cls(data['amount'], data['address'], data['lock_until'])


class Transaction:
    __slots__ = ('inputs', 'outputs', 'locktime', '_hash', 'fee')
    def __init__(self, inputs: List[TxIn], outputs: List[TxOut], locktime: int = 0):
        self.inputs = inputs
        self.outputs = outputs
        self.locktime = locktime
        self._hash = None
        self.fee = 0

    @property
    def hash(self) -> str:
        if self._hash is None:
            self._hash = hash256_hex(self._serialize_for_hashing())
        return self._hash

    def _serialize_for_hashing(self) -> bytes:
        """Сериализует транзакцию для вычисления хеша. Для coinbase включает сигнатуру."""
        data = b''
        data += serialize_varint(len(self.inputs))
        for txin in self.inputs:
            data += bytes.fromhex(txin.prev_tx_hash)[::-1]
            data += struct.pack('<I', txin.prev_output_index)
            if txin.prev_tx_hash == '0'*64 and txin.prev_output_index == 0xffffffff:
                sig = txin.signature or b''
                data += serialize_varint(len(sig)) + sig
                data += b'\x00'  # пустой pubkey
            else:
                data += b'\x00'  # длина сигнатуры = 0
                data += b'\x00'  # длина pubkey = 0
        data += serialize_varint(len(self.outputs))
        for txout in self.outputs:
            data += txout.to_bytes()
        data += struct.pack('<I', self.locktime)
        return data

    def to_bytes(self, include_signatures: bool = True) -> bytes:
        data = b''
        data += serialize_varint(len(self.inputs))
        for txin in self.inputs:
            data += bytes.fromhex(txin.prev_tx_hash)[::-1]
            data += struct.pack('<I', txin.prev_output_index)
            sig = txin.signature or b''
            data += serialize_varint(len(sig)) + sig
            pub = bytes.fromhex(txin.pubkey) if txin.pubkey else b''
            data += serialize_varint(len(pub)) + pub
        data += serialize_varint(len(self.outputs))
        for txout in self.outputs:
            data += txout.to_bytes()
        data += struct.pack('<I', self.locktime)
        return data

    @classmethod
    def from_bytes(cls, data: bytes) -> 'Transaction':
        pos = 0
        n_inputs, pos = deserialize_varint(data, pos)
        inputs = []
        for _ in range(n_inputs):
            txin, pos = TxIn.from_bytes(data, pos)
            inputs.append(txin)
        n_outputs, pos = deserialize_varint(data, pos)
        outputs = []
        for _ in range(n_outputs):
            txout, pos = TxOut.from_bytes(data, pos)
            outputs.append(txout)
        locktime = struct.unpack('<I', data[pos:pos+4])[0]
        tx = cls(inputs, outputs, locktime)
        return tx

    def is_coinbase(self) -> bool:
        return (len(self.inputs) == 1 and
                self.inputs[0].prev_tx_hash == '0'*64 and
                self.inputs[0].prev_output_index == 0xffffffff)

    @staticmethod
    def create_coinbase(block_height: int, reward: int, address: str,
                        vesting_periods: List[int],
                        extra_nonce1: Optional[str] = None,
                        extra_nonce2: Optional[str] = None) -> 'Transaction':
        coinbase_data = b''
        if extra_nonce1:
            coinbase_data += bytes.fromhex(extra_nonce1)
        if extra_nonce2:
            coinbase_data += bytes.fromhex(extra_nonce2)
        signature = coinbase_data if coinbase_data else None

        shares = len(vesting_periods)
        amount_per_share = reward // shares
        remainder = reward % shares
        outputs = []
        for i, period in enumerate(vesting_periods):
            amount = amount_per_share + (remainder if i == 0 else 0)
            lock_until = block_height + period
            outputs.append(TxOut(amount, address, lock_until))

        txin = TxIn('0'*64, 0xffffffff, signature, None)
        return Transaction([txin], outputs)

    def update_extra_nonce(self, extra_nonce: int):
        if not self.is_coinbase():
            return
        if self.inputs[0].signature is None:
            self.inputs[0].signature = b''
        self.inputs[0].signature += struct.pack('<Q', extra_nonce)
        self._hash = None   # сброс хеша

    def sign(self, private_key_hex: str, pubkey_hex: str) -> None:
        sk = SigningKey.from_string(bytes.fromhex(private_key_hex), curve=SECP256k1)
        for txin in self.inputs:
            if txin.prev_tx_hash == '0'*64 and txin.prev_output_index == 0xffffffff:
                continue
            tx_hash = self.hash.encode()
            txin.signature = sk.sign(tx_hash)
            txin.pubkey = pubkey_hex

    def verify_signatures(self, utxo: dict) -> bool:
        total_in = 0
        for txin in self.inputs:
            if txin.prev_tx_hash == '0'*64 and txin.prev_output_index == 0xffffffff:
                continue
            if txin.signature is None or txin.pubkey is None:
                return False
            key = (txin.prev_tx_hash, txin.prev_output_index)
            if key not in utxo:
                return False
            out, _ = utxo[key]
            if not is_valid_address(out.address):
                return False
            try:
                pubkey_bytes = bytes.fromhex(txin.pubkey)
                addr_hash = hash256(pubkey_bytes).hex()
            except:
                return False
            if addr_hash != out.address:
                return False
            try:
                vk = VerifyingKey.from_string(pubkey_bytes, curve=SECP256k1)
            except:
                return False
            try:
                if not vk.verify(txin.signature, self.hash.encode()):
                    return False
            except:
                return False
            total_in += out.amount
        total_out = sum(out.amount for out in self.outputs)
        self.fee = total_in - total_out
        return self.fee >= 0

    def to_dict(self) -> dict:
        return {
            'inputs': [txin.to_dict() for txin in self.inputs],
            'outputs': [txout.to_dict() for txout in self.outputs],
            'locktime': self.locktime,
            '_hash': self._hash,
            'fee': self.fee
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'Transaction':
        inputs = [TxIn.from_dict(txin) for txin in data['inputs']]
        outputs = [TxOut.from_dict(txout) for txout in data['outputs']]
        tx = cls(inputs, outputs, data['locktime'])
        tx._hash = data.get('_hash')
        tx.fee = data.get('fee', 0)
        return tx

    def __eq__(self, other) -> bool:
        if not isinstance(other, Transaction):
            return False
        return self.hash == other.hash

    def __hash__(self) -> int:
        return hash(self.hash)

    def size(self) -> int:
        return len(self.to_bytes())
