import time
import struct
import logging
from typing import List, Optional
from utils import hash256
from transaction import Transaction
from config import REWARD, VESTING_PERIODS

logger = logging.getLogger(__name__)

class Block:
    __slots__ = ('height', 'transactions', 'prev_hash', 'timestamp', 'nonce', 'bits',
                 'hash', 'merkle_root', 'extra_nonce')
    def __init__(self, height: int, transactions: List[Transaction], prev_hash: str,
                 timestamp: Optional[int] = None, nonce: int = 0, bits: int = 0x1d00ffff,
                 extra_nonce: int = 0, compute_hash: bool = True):
        """
        Создаёт новый блок.

        :param height: высота блока
        :param transactions: список транзакций (первая должна быть coinbase)
        :param prev_hash: хеш предыдущего блока
        :param timestamp: время создания (по умолчанию текущее)
        :param nonce: начальное значение nonce
        :param bits: сложность в сжатом виде
        :param extra_nonce: дополнительное значение для изменения merkle_root
        :param compute_hash: если True, сразу вычисляет хеш
        """
        self.height = height
        self.transactions = transactions
        self.prev_hash = prev_hash
        self.timestamp = timestamp or int(time.time())
        self.nonce = nonce
        self.bits = bits
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
        """Вычисляет хеш блока (SHA-256d от заголовка)."""
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
        """
        Обновляет coinbase-транзакцию, добавляя extra_nonce,
        и пересчитывает merkle_root.
        """
        if not self.transactions or not self.transactions[0].is_coinbase():
            return
        coinbase = self.transactions[0]
        # Извлекаем исходные параметры coinbase
        # Предполагаем, что coinbase создан через create_coinbase с address и vesting_periods
        # Но мы не знаем address и vesting_periods. Мы можем пересоздать coinbase,
        # используя те же параметры, что были при создании.
        # Для простоты будем менять только extra_nonce, добавляя его в сигнатуру.
        # Создадим новую coinbase с теми же адресом и периодами.
        # Так как мы не храним эти параметры, мы можем изменить только сигнатуру.
        # Вместо этого мы можем добавить extra_nonce в поле signature.
        # Но для корректности проще пересоздать транзакцию, зная address и vesting_periods.
        # В нашей реализации мы не храним address в блоке. Поэтому мы можем модифицировать
        # сигнатуру coinbase, добавив extra_nonce.
        # Но сигнатура coinbase — это произвольные данные, мы можем их изменить.
        # Будем использовать extra_nonce как байты, добавляемые к сигнатуре.
        # Для этого преобразуем extra_nonce в hex-строку и добавим к существующей сигнатуре.
        extra_hex = hex(extra_nonce)[2:].zfill(8)
        # Обновляем сигнатуру
        if coinbase.inputs[0].signature is None:
            coinbase.inputs[0].signature = b''
        # Добавляем extra_nonce в конец сигнатуры (можно в начало)
        coinbase.inputs[0].signature += extra_hex.encode()
        # Пересчитываем хеш транзакции
        coinbase._hash = None
        # Пересчитываем merkle_root
        self.merkle_root = self._compute_merkle_root()
        self.extra_nonce = extra_nonce

    def mine(self, target: int, extra_nonce_start: int = 0) -> str:
        """
        Майнит блок, перебирая nonce и timestamp, а также extra_nonce.

        :param target: целевое значение сложности
        :param extra_nonce_start: начальное значение extra_nonce
        :return: хеш найденного блока
        """
        logger.info(f"⛏️  Mining block {self.height} (target={target})...")
        start_time = time.time()
        attempts = 0
        max_nonce = 0xffffffff
        extra_nonce = extra_nonce_start

        while True:
            # Перебор nonce
            while self.nonce < max_nonce:
                self.hash = self.compute_hash()
                if int(self.hash, 16) < target:
                    elapsed = time.time() - start_time
                    logger.info(f"✅ Block mined in {elapsed:.2f}s, nonce={self.nonce}, hash={self.hash[:16]}...")
                    return self.hash
                self.nonce += 1
                attempts += 1
                if attempts % 100000 == 0:
                    logger.debug(f"   ... {attempts} attempts, current hash: {self.hash[:16]}...")

            # Исчерпали nonce – меняем timestamp и extra_nonce
            self.nonce = 0
            self.timestamp += 1
            # Также меняем extra_nonce, чтобы изменить merkle_root
            extra_nonce += 1
            self.update_coinbase_extra_nonce(extra_nonce)
            # Сброс счётчика попыток (для логирования)
            attempts = 0

    @staticmethod
    def genesis(genesis_address: str) -> 'Block':
        """Создаёт генезис-блок."""
        coinbase = Transaction.create_coinbase(0, REWARD, genesis_address, VESTING_PERIODS)
        genesis_block = Block(0, [coinbase], '0' * 64, compute_hash=False)
        genesis_block.bits = 0x1d00ffff
        genesis_block.hash = genesis_block.compute_hash()
        return genesis_block

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