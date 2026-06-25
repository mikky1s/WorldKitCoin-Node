import time
from typing import List, Optional
from utils import hash256_hex, serialize
from transaction import Transaction
from config import REWARD, VESTING_PERIODS

class Block:
    __slots__ = ('height', 'transactions', 'prev_hash', 'timestamp', 'nonce', 'bits', 'hash', 'merkle_root')
    def __init__(self, height: int, transactions: List[Transaction], prev_hash: str,
                 timestamp: Optional[int] = None, nonce: int = 0, bits: int = 0x1f00ffff,
                 compute_hash: bool = True):
        self.height = height
        self.transactions = transactions
        self.prev_hash = prev_hash
        self.timestamp = timestamp or int(time.time())
        self.nonce = nonce
        self.bits = bits
        self.merkle_root = self._compute_merkle_root()
        if compute_hash:
            self.hash = self.compute_hash()
        else:
            self.hash = None

    def _compute_merkle_root(self) -> str:
        if not self.transactions:
            return '0' * 64
        tx_hashes = [tx.hash for tx in self.transactions]
        combined = ''.join(tx_hashes).encode()
        return hash256_hex(combined)

    def compute_hash(self) -> str:
        header = {
            'height': self.height,
            'prev_hash': self.prev_hash,
            'timestamp': self.timestamp,
            'nonce': self.nonce,
            'bits': self.bits,
            'merkle_root': self.merkle_root
        }
        return hash256_hex(serialize(header))

    def mine(self, target: int) -> str:
        print(f"⛏️  Mining block {self.height} (target={target})...")
        start_time = time.time()
        attempts = 0
        while int(self.hash, 16) >= target:
            self.nonce += 1
            self.hash = self.compute_hash()
            attempts += 1
            if attempts % 10000 == 0:
                print(f"   ... {attempts} attempts, current hash: {self.hash[:16]}...")
        elapsed = time.time() - start_time
        print(f"✅ Block mined in {elapsed:.2f}s, nonce={self.nonce}, hash={self.hash[:16]}...")
        return self.hash

    @staticmethod
    def genesis(genesis_address: str) -> 'Block':
        coinbase = Transaction.create_coinbase(0, REWARD, genesis_address, VESTING_PERIODS)
        genesis_block = Block(0, [coinbase], '0' * 64, compute_hash=True)
        genesis_block.bits = 0x1f00ffff
        genesis_block.hash = genesis_block.compute_hash()
        return genesis_block

    def to_dict(self):
        return {
            'height': self.height,
            'transactions': [tx.to_dict() for tx in self.transactions],
            'prev_hash': self.prev_hash,
            'timestamp': self.timestamp,
            'nonce': self.nonce,
            'bits': self.bits,
            'merkle_root': self.merkle_root,
            'hash': self.hash
        }

    @classmethod
    def from_dict(cls, data):
        transactions = [Transaction.from_dict(tx) for tx in data['transactions']]
        block = cls(
            height=data['height'],
            transactions=transactions,
            prev_hash=data['prev_hash'],
            timestamp=data['timestamp'],
            nonce=data['nonce'],
            bits=data['bits'],
            compute_hash=False
        )
        block.merkle_root = data['merkle_root']
        block.hash = data['hash']
        return block