# transaction.py
from utils import hash256_hex, serialize
from typing import List, Optional, Tuple
from ecdsa import SigningKey, VerifyingKey, SECP256k1
import hashlib

class TxIn:
    __slots__ = ('prev_tx_hash', 'prev_output_index', 'signature')
    def __init__(self, prev_tx_hash: str, prev_output_index: int, signature: Optional[bytes] = None):
        self.prev_tx_hash = prev_tx_hash
        self.prev_output_index = prev_output_index
        self.signature = signature

    def to_dict(self):
        return {
            'prev_tx_hash': self.prev_tx_hash,
            'prev_output_index': self.prev_output_index,
            'signature': self.signature.hex() if self.signature else None
        }

    @classmethod
    def from_dict(cls, data):
        sig = bytes.fromhex(data['signature']) if data['signature'] else None
        return cls(data['prev_tx_hash'], data['prev_output_index'], sig)

class TxOut:
    __slots__ = ('amount', 'pubkey_hash', 'lock_until')
    def __init__(self, amount: int, pubkey_hash: str, lock_until: int = 0):
        self.amount = amount
        self.pubkey_hash = pubkey_hash  # публичный ключ в hex (66 символов)
        self.lock_until = lock_until

    def to_dict(self):
        return {
            'amount': self.amount,
            'pubkey_hash': self.pubkey_hash,
            'lock_until': self.lock_until
        }

    @classmethod
    def from_dict(cls, data):
        return cls(data['amount'], data['pubkey_hash'], data['lock_until'])

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
                'inputs': [txin.to_dict() for txin in self.inputs],
                'outputs': [txout.to_dict() for txout in self.outputs],
                'locktime': self.locktime
            }
            self._hash = hash256_hex(serialize(data))
        return self._hash

    def is_coinbase(self) -> bool:
        return len(self.inputs) == 1 and self.inputs[0].prev_tx_hash == '0'*64 and self.inputs[0].prev_output_index == 0xffffffff

    @staticmethod
    def create_coinbase(block_height: int, reward: int, address: str, vesting_periods: List[int]) -> 'Transaction':
        shares = len(vesting_periods)
        amount_per_share = reward // shares
        remainder = reward % shares
        outputs = []
        for i, period in enumerate(vesting_periods):
            amount = amount_per_share + (remainder if i == 0 else 0)
            lock_until = block_height + period
            outputs.append(TxOut(amount, address, lock_until))
        txin = TxIn('0'*64, 0xffffffff)
        return Transaction([txin], outputs)

    def sign(self, private_key_hex: str):
        """Подписывает каждый вход транзакции (все входы одним ключом)"""
        sk = SigningKey.from_string(bytes.fromhex(private_key_hex), curve=SECP256k1)
        for txin in self.inputs:
            tx_hash = self.hash.encode()
            signature = sk.sign(tx_hash)
            txin.signature = signature

    def verify_signatures(self, utxo: dict) -> bool:
        """Проверяет подписи всех входов, используя публичный ключ из UTXO"""
        for txin in self.inputs:
            if txin.signature is None:
                return False
            key = (txin.prev_tx_hash, txin.prev_output_index)
            if key not in utxo:
                return False
            out, _ = utxo[key]
            # out.pubkey_hash содержит публичный ключ в hex
            try:
                vk = VerifyingKey.from_string(bytes.fromhex(out.pubkey_hash), curve=SECP256k1)
            except:
                return False
            tx_hash = self.hash.encode()
            try:
                if not vk.verify(txin.signature, tx_hash):
                    return False
            except:
                return False
        return True

    def to_dict(self):
        return {
            'inputs': [txin.to_dict() for txin in self.inputs],
            'outputs': [txout.to_dict() for txout in self.outputs],
            'locktime': self.locktime,
            '_hash': self._hash
        }

    @classmethod
    def from_dict(cls, data):
        inputs = [TxIn.from_dict(txin) for txin in data['inputs']]
        outputs = [TxOut.from_dict(txout) for txout in data['outputs']]
        tx = cls(inputs, outputs, data['locktime'])
        tx._hash = data.get('_hash')
        return tx