from utils import hash256_hex, serialize, hash256, is_valid_address
from typing import List, Optional, Tuple
from ecdsa import SigningKey, VerifyingKey, SECP256k1
import json

class TxIn:
    __slots__ = ('prev_tx_hash', 'prev_output_index', 'signature', 'pubkey')
    def __init__(self, prev_tx_hash: str, prev_output_index: int,
                 signature: Optional[bytes] = None, pubkey: Optional[str] = None):
        self.prev_tx_hash = prev_tx_hash
        self.prev_output_index = prev_output_index
        self.signature = signature
        self.pubkey = pubkey

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

    def to_dict(self) -> dict:
        return {'amount': self.amount, 'address': self.address, 'lock_until': self.lock_until}

    @classmethod
    def from_dict(cls, data: dict) -> 'TxOut':
        return cls(data['amount'], data['address'], data['lock_until'])


class Transaction:
    __slots__ = ('inputs', 'outputs', 'locktime', '_hash')
    def __init__(self, inputs: List[TxIn], outputs: List[TxOut], locktime: int = 0):
        self.inputs = inputs
        self.outputs = outputs
        self.locktime = locktime
        self._hash = None

    @property
    def hash(self) -> str:
        if self._hash is None:
            data = {
                'inputs': [{'prev_tx_hash': txin.prev_tx_hash,
                            'prev_output_index': txin.prev_output_index} for txin in self.inputs],
                'outputs': [txout.to_dict() for txout in self.outputs],
                'locktime': self.locktime
            }
            self._hash = hash256_hex(serialize(data))
        return self._hash

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

    def sign(self, private_key_hex: str, pubkey_hex: str) -> None:
        sk = SigningKey.from_string(bytes.fromhex(private_key_hex), curve=SECP256k1)
        for txin in self.inputs:
            # Проверяем, является ли вход coinbase (т.е. не требует подписи)
            if txin.prev_tx_hash == '0'*64 and txin.prev_output_index == 0xffffffff:
                continue
            tx_hash = self.hash.encode()
            txin.signature = sk.sign(tx_hash)
            txin.pubkey = pubkey_hex

    def verify_signatures(self, utxo: dict) -> bool:
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
        return True

    def to_dict(self) -> dict:
        return {
            'inputs': [txin.to_dict() for txin in self.inputs],
            'outputs': [txout.to_dict() for txout in self.outputs],
            'locktime': self.locktime,
            '_hash': self._hash
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'Transaction':
        inputs = [TxIn.from_dict(txin) for txin in data['inputs']]
        outputs = [TxOut.from_dict(txout) for txout in data['outputs']]
        tx = cls(inputs, outputs, data['locktime'])
        tx._hash = data.get('_hash')
        return tx

    def __eq__(self, other) -> bool:
        if not isinstance(other, Transaction):
            return False
        return self.hash == other.hash

    def __hash__(self) -> int:
        return hash(self.hash)

    def size(self) -> int:
        return len(json.dumps(self.to_dict()).encode())