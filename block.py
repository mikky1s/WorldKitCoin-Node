import time
import struct
import logging
from typing import List, Optional
from utils import hash256, serialize_varint, deserialize_varint
from transaction import Transaction
from config import REWARD, VESTING_PERIODS

logger = logging.getLogger(__name__)

class Block:
    __slots__ = ('height', 'transactions', 'prev_hash', 'timestamp', 'nonce', 'bits',
                 'hash', 'merkle_root', 'extra_nonce')
    def __init__(self, height: int, transactions: List[Transaction], prev_hash: str,
                 timestamp: Optional[int] = None, nonce: int = 0, bits: int = 0x1d00ffff,
                 extra_nonce: int = 0, compute_hash: bool = True,
                 extra_nonce1: Optional[int] = None, extra_nonce2: Optional[int] = None):
        """
        Создаёт новый блок.
        extra_nonce1 и extra_nonce2 добавлены для обратной совместимости,
        они игнорируются, используется extra_nonce.
        """
        self.height = height
        self.transactions = transactions
        self.prev_hash = prev_hash
        self.timestamp = timestamp or int(time.time())
        self.nonce = nonce
        self.bits = bits
        # Если передан extra_nonce1 или extra_nonce2, используем их (приоритет extra_nonce1)
        if extra_nonce1 is not None:
            self.extra_nonce = extra_nonce1
        elif extra_nonce2 is not None:
            self.extra_nonce = extra_nonce2
        else:
            self.extra_nonce = extra_nonce
        self.merkle_root = self._compute_merkle_root()
        self.hash = self.compute_hash() if compute_hash else None

    def _compute_merkle_root(self) -> str:
        if not self.transactions:
            return '0' * 64
        hashes = [bytes.fromhex(tx.hash) for tx in self.transactions]
        while len(hashes) > 1:
            if len(hashes) % 2 == 1:
                hashes.append(hashes[-1])
            hashes = [hash256(hashes[i] + hashes[i+1]) for i in range(0, len(hashes), 2)]
        return hashes[0].hex()

    def compute_hash(self) -> str:
        version = 0x20000000
        version_bytes = struct.pack('<I', version)
        prev_hash_bytes = bytes.fromhex(self.prev_hash)[::-1]
        merkle_root_bytes = bytes.fromhex(self.merkle_root)[::-1]
        ntime_bytes = struct.pack('<I', self.timestamp)
        bits_bytes = struct.pack('<I', self.bits)
        nonce_bytes = struct.pack('<I', self.nonce)
        header = version_bytes + prev_hash_bytes + merkle_root_bytes + ntime_bytes + bits_bytes + nonce_bytes
        return hash256(header)[::-1].hex()

    def update_coinbase_extra_nonce(self, extra_nonce: int) -> None:
        if not self.transactions or not self.transactions[0].is_coinbase():
            return
        coinbase = self.transactions[0]
        extra_hex = hex(extra_nonce)[2:].zfill(8)
        if coinbase.inputs[0].signature is None:
            coinbase.inputs[0].signature = b''
        coinbase.inputs[0].signature += extra_hex.encode()
        coinbase._hash = None
        self.merkle_root = self._compute_merkle_root()
        self.extra_nonce = extra_nonce

    def mine(self, target: int, extra_nonce_start: int = 0) -> str:
        logger.info(f"⛏️  Mining block {self.height} (target={target})...")
        start_time = time.time()
        attempts = 0
        max_nonce = 0xffffffff
        extra_nonce = extra_nonce_start

        while True:
            while self.nonce < max_nonce:
                self.hash = self.compute_hash()
                if int(self.hash, 16) < target:
                    elapsed = time.time() - start_time
                    logger.info(f"✅ Block mined in {elapsed:.2f}s, nonce={self.nonce}, hash={self.hash[:16]}...")
                    return self.hash
                self.nonce += 1
                attempts += 1
                if attempts % 100000 == 0:
                    logger.info(f"   ... {attempts} attempts, current hash: {self.hash[:16]}...")

            self.nonce = 0
            self.timestamp += 1
            extra_nonce += 1
            self.update_coinbase_extra_nonce(extra_nonce)
            attempts = 0

    @staticmethod
    def genesis(genesis_address: str) -> 'Block':
        coinbase = Transaction.create_coinbase(0, REWARD, genesis_address, VESTING_PERIODS)
        genesis_block = Block(0, [coinbase], '0' * 64, compute_hash=False)
        genesis_block.bits = 0x1d00ffff
        genesis_block.hash = genesis_block.compute_hash()
        return genesis_block

    def to_bytes(self) -> bytes:
        data = struct.pack('<I', self.height)
        data += struct.pack('<I', self.timestamp)
        data += struct.pack('<I', self.nonce)
        data += struct.pack('<I', self.bits)
        data += struct.pack('<I', self.extra_nonce)
        data += bytes.fromhex(self.prev_hash)[::-1]
        data += bytes.fromhex(self.merkle_root)[::-1]
        data += bytes.fromhex(self.hash)[::-1] if self.hash else b'\x00'*32
        data += serialize_varint(len(self.transactions))
        for tx in self.transactions:
            data += tx.to_bytes()
        return data

    @classmethod
    def from_bytes(cls, data: bytes) -> 'Block':
        pos = 0
        height = struct.unpack('<I', data[pos:pos+4])[0]; pos += 4
        timestamp = struct.unpack('<I', data[pos:pos+4])[0]; pos += 4
        nonce = struct.unpack('<I', data[pos:pos+4])[0]; pos += 4
        bits = struct.unpack('<I', data[pos:pos+4])[0]; pos += 4
        extra_nonce = struct.unpack('<I', data[pos:pos+4])[0]; pos += 4
        prev_hash = data[pos:pos+32][::-1].hex(); pos += 32
        merkle_root = data[pos:pos+32][::-1].hex(); pos += 32
        block_hash = data[pos:pos+32][::-1].hex(); pos += 32
        n_txs, pos = deserialize_varint(data, pos)
        txs = []
        for _ in range(n_txs):
            tx = Transaction.from_bytes(data[pos:])
            pos += len(tx.to_bytes())
            txs.append(tx)
        block = cls(height, txs, prev_hash, timestamp, nonce, bits, extra_nonce, compute_hash=False)
        block.merkle_root = merkle_root
        block.hash = block_hash
        return block

    def to_dict(self) -> dict:
        return {
            'height': self.height,
            'transactions': [tx.to_dict() for tx in self.transactions],
            'prev_hash': self.prev_hash,
            'timestamp': self.timestamp,
            'nonce': self.nonce,
            'bits': self.bits,
            'merkle_root': self.merkle_root,
            'hash': self.hash,
            'extra_nonce': self.extra_nonce
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'Block':
        transactions = [Transaction.from_dict(tx) for tx in data['transactions']]
        block = cls(
            height=data['height'],
            transactions=transactions,
            prev_hash=data['prev_hash'],
            timestamp=data['timestamp'],
            nonce=data['nonce'],
            bits=data['bits'],
            extra_nonce=data.get('extra_nonce', 0),
            compute_hash=False
        )
        block.merkle_root = data['merkle_root']
        block.hash = data['hash']
        return block
