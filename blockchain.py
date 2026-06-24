import os
import time
import sqlite3
import threading
import logging
from typing import Dict, Tuple, List, Optional, Set
from block import Block
from transaction import Transaction, TxIn, TxOut
import config
from utils import bits_to_target, target_to_bits, is_valid_address

logger = logging.getLogger(__name__)

class Blockchain:
    def __init__(self, genesis_address: str, load_from_file: bool = True,
                 verify_checkpoints: bool = True, db_path: str = None):
        self.lock = threading.RLock()
        self.db_path = db_path if db_path else "blockchain.db"
        self.utxo: Dict[Tuple[str, int], Tuple[TxOut, int]] = {}
        self.mempool: List[Transaction] = []
        self._mempool_spent: Set[Tuple[str, int]] = set()
        self.difficulty_target: int = 0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff
        self.current_bits: int = target_to_bits(self.difficulty_target)
        self.total_supply: int = 0
        self._last_block_cache: Optional[Block] = None
        self._balance_cache: Dict[str, Tuple[int, int, float]] = {}
        self._cache_lock = threading.RLock()
        self.orphans: Dict[str, Block] = {}
        # Одно соединение для всей БД
        self._conn = None
        self._init_db()
        if load_from_file and self._has_data():
            self.load(verify_checkpoints=verify_checkpoints)
            logger.info("Блокчейн загружен из SQLite")
        else:
            genesis = Block.genesis(genesis_address)
            genesis.bits = 0x1f00ffff
            genesis.hash = genesis.compute_hash()
            self._save_block(genesis)
            self._add_block_to_utxo(genesis)
            self.total_supply = config.REWARD
            self.difficulty_target = bits_to_target(genesis.bits)
            self.current_bits = genesis.bits
            self._last_block_cache = genesis
            self._set_metadata('total_supply', str(self.total_supply))
            self._set_metadata('current_bits', str(self.current_bits))
            self._set_metadata('difficulty_target', str(self.difficulty_target))
            self._invalidate_cache()
            logger.info(f"Genesis block created: {genesis.hash[:16]}...")
        self._clean_mempool()

    def _get_conn(self):
        """Возвращает соединение, создавая его при первом вызове."""
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _init_db(self):
        conn = self._get_conn()
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS blocks (
            height INTEGER PRIMARY KEY,
            hash TEXT UNIQUE,
            prev_hash TEXT,
            timestamp INTEGER,
            nonce INTEGER,
            bits INTEGER,
            merkle_root TEXT,
            extra_nonce INTEGER,
            data BLOB
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS transactions (
            tx_hash TEXT PRIMARY KEY,
            block_height INTEGER,
            idx_in_block INTEGER,
            data BLOB
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS utxo (
            txid TEXT,
            idx INTEGER,
            amount INTEGER,
            address TEXT,
            lock_until INTEGER,
            block_height INTEGER,
            PRIMARY KEY (txid, idx)
        )''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_utxo_address ON utxo(address)')
        c.execute('''CREATE TABLE IF NOT EXISTS mempool (
            tx_hash TEXT PRIMARY KEY,
            data BLOB,
            fee INTEGER,
            timestamp INTEGER
        )''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_mempool_fee ON mempool(fee DESC)')
        c.execute('''CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT
        )''')
        conn.commit()

    def _has_data(self) -> bool:
        conn = self._get_conn()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM blocks")
        return c.fetchone()[0] > 0

    def _get_metadata(self, key: str, default=None):
        conn = self._get_conn()
        c = conn.cursor()
        c.execute("SELECT value FROM metadata WHERE key=?", (key,))
        row = c.fetchone()
        if row:
            return row[0]
        return default

    def _set_metadata(self, key: str, value: str):
        conn = self._get_conn()
        c = conn.cursor()
        c.execute("REPLACE INTO metadata (key, value) VALUES (?, ?)", (key, value))
        conn.commit()

    def _save_block(self, block: Block):
        conn = self._get_conn()
        c = conn.cursor()
        c.execute('''INSERT OR REPLACE INTO blocks 
            (height, hash, prev_hash, timestamp, nonce, bits, merkle_root, extra_nonce, data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (block.height, block.hash, block.prev_hash, block.timestamp,
             block.nonce, block.bits, block.merkle_root, block.extra_nonce,
             block.to_bytes()))
        for idx, tx in enumerate(block.transactions):
            c.execute('''INSERT OR REPLACE INTO transactions (tx_hash, block_height, idx_in_block, data)
                VALUES (?, ?, ?, ?)''',
                (tx.hash, block.height, idx, tx.to_bytes()))
        conn.commit()
        self._last_block_cache = block

    def load(self, verify_checkpoints: bool = True):
        conn = self._get_conn()
        c = conn.cursor()
        total_supply = self._get_metadata('total_supply')
        if total_supply:
            self.total_supply = int(total_supply)
        current_bits = self._get_metadata('current_bits')
        if current_bits:
            self.current_bits = int(current_bits)
        difficulty_target = self._get_metadata('difficulty_target')
        if difficulty_target:
            self.difficulty_target = int(difficulty_target)

        c.execute('''SELECT data FROM blocks ORDER BY height DESC LIMIT 1''')
        row = c.fetchone()
        if row:
            self._last_block_cache = Block.from_bytes(row[0])
        self._rebuild_utxo()

        if verify_checkpoints:
            for height, chash in config.CHECKPOINTS.items():
                c.execute("SELECT hash FROM blocks WHERE height=?", (height,))
                row = c.fetchone()
                if row and row[0] != chash:
                    raise ValueError(f"Checkpoint mismatch at height {height}")

        self._load_mempool()

    def _rebuild_utxo(self):
        self.utxo.clear()
        conn = self._get_conn()
        c = conn.cursor()
        c.execute("SELECT txid, idx, amount, address, lock_until, block_height FROM utxo")
        for txid, idx, amount, address, lock_until, height in c.fetchall():
            out = TxOut(amount, address, lock_until)
            self.utxo[(txid, idx)] = (out, height)

    def _add_block_to_utxo(self, block: Block):
        conn = self._get_conn()
        c = conn.cursor()
        for tx in block.transactions[1:]:
            for txin in tx.inputs:
                c.execute("DELETE FROM utxo WHERE txid=? AND idx=?", (txin.prev_tx_hash, txin.prev_output_index))
        for tx in block.transactions:
            for i, out in enumerate(tx.outputs):
                c.execute('''INSERT OR REPLACE INTO utxo (txid, idx, amount, address, lock_until, block_height)
                             VALUES (?, ?, ?, ?, ?, ?)''',
                          (tx.hash, i, out.amount, out.address, out.lock_until, block.height))
        conn.commit()
        self._rebuild_utxo()

    def _load_mempool(self):
        self.mempool.clear()
        self._mempool_spent.clear()
        conn = self._get_conn()
        c = conn.cursor()
        c.execute("SELECT data, fee FROM mempool ORDER BY fee DESC")
        rows = c.fetchall()
        for data, fee in rows:
            tx = Transaction.from_bytes(data)
            tx.fee = fee
            self.mempool.append(tx)
            for txin in tx.inputs:
                self._mempool_spent.add((txin.prev_tx_hash, txin.prev_output_index))

    def _save_mempool(self):
        conn = self._get_conn()
        c = conn.cursor()
        c.execute("DELETE FROM mempool")
        for tx in self.mempool:
            c.execute("INSERT INTO mempool (tx_hash, data, fee, timestamp) VALUES (?, ?, ?, ?)",
                      (tx.hash, tx.to_bytes(), tx.fee, int(time.time())))
        conn.commit()

    # ---------- Основные методы ----------
    def get_last_block(self) -> Block:
        with self._cache_lock:
            if self._last_block_cache is None:
                conn = self._get_conn()
                c = conn.cursor()
                c.execute("SELECT data FROM blocks ORDER BY height DESC LIMIT 1")
                row = c.fetchone()
                if row:
                    self._last_block_cache = Block.from_bytes(row[0])
            return self._last_block_cache

    def get_block_height(self) -> int:
        conn = self._get_conn()
        c = conn.cursor()
        c.execute("SELECT MAX(height) FROM blocks")
        row = c.fetchone()
        return row[0] if row[0] is not None else -1

    def get_block_by_hash(self, block_hash: str) -> Optional[Block]:
        conn = self._get_conn()
        c = conn.cursor()
        c.execute("SELECT data FROM blocks WHERE hash=?", (block_hash,))
        row = c.fetchone()
        if row:
            return Block.from_bytes(row[0])
        return self.orphans.get(block_hash)

    def get_block_by_height(self, height: int) -> Optional[Block]:
        conn = self._get_conn()
        c = conn.cursor()
        c.execute("SELECT data FROM blocks WHERE height=?", (height,))
        row = c.fetchone()
        if row:
            return Block.from_bytes(row[0])
        return None

    def get_balance(self, address: str, current_height: int) -> Tuple[int, int]:
        if not is_valid_address(address):
            return 0, 0
        with self._cache_lock:
            if address in self._balance_cache:
                avail, locked, ts = self._balance_cache[address]
                if time.time() - ts < config.CACHE_BALANCE_TTL:
                    return avail, locked
        available = 0
        locked = 0
        for (txid, idx), (out, _) in self.utxo.items():
            if out.address == address:
                if out.lock_until <= current_height:
                    available += out.amount
                else:
                    locked += out.amount
        with self._cache_lock:
            self._balance_cache[address] = (available, locked, time.time())
        return available, locked

    def get_transaction_history(self, address: str, current_height: int) -> List[dict]:
        if not is_valid_address(address):
            return []
        history = []
        conn = self._get_conn()
        c = conn.cursor()
        c.execute('''SELECT DISTINCT t.tx_hash, t.block_height, b.timestamp
                     FROM transactions t
                     JOIN blocks b ON t.block_height = b.height
                     JOIN utxo u ON t.tx_hash = u.txid
                     WHERE u.address = ?''', (address,))
        rows = c.fetchall()
        for tx_hash, block_height, timestamp in rows:
            confirmations = current_height - block_height + 1
            history.append({
                'tx_hash': tx_hash,
                'block_height': block_height,
                'timestamp': timestamp,
                'amount': 0,
                'is_coinbase': (tx_hash == '0'*64),
                'confirmations': confirmations
            })
        return history

    def get_utxos_for_address(self, address: str, current_height: int) -> List[dict]:
        if not is_valid_address(address):
            return []
        result = []
        for (txid, idx), (out, _) in self.utxo.items():
            if out.address == address and out.lock_until <= current_height:
                result.append({
                    'txid': txid,
                    'index': idx,
                    'amount': out.amount,
                    'address': address,
                    'lock_until': out.lock_until
                })
        return result

    def get_mempool_snapshot(self, limit: int = 100) -> List[Transaction]:
        with self.lock:
            return self.mempool[:limit]

    def get_mempool_size(self) -> int:
        with self.lock:
            return len(self.mempool)

    def get_total_supply(self) -> int:
        return self.total_supply

    def create_transaction(self, from_address: str, to_address: str,
                           amount: int, current_height: int) -> Optional[Transaction]:
        if not is_valid_address(from_address) or not is_valid_address(to_address):
            logger.error("Invalid address format")
            return None
        with self.lock:
            inputs = []
            total = 0
            for key, (out, _) in self.utxo.items():
                if out.address == from_address:
                    if out.lock_until > current_height:
                        continue
                    inputs.append(TxIn(key[0], key[1]))
                    total += out.amount
                    if total >= amount:
                        break
            if total < amount:
                logger.error(f"Insufficient funds: {total} < {amount}")
                return None
            outputs = [TxOut(amount, to_address)]
            change = total - amount
            if change > 0:
                outputs.append(TxOut(change, from_address))
            tx = Transaction(inputs, outputs)
            logger.info(f"Transaction created: {tx.hash[:16]}... (amount={amount})")
            return tx

    # ---------- Сложность ----------
    def _get_expected_bits(self, height: int) -> int:
        if height == 0:
            return 0x1d00ffff
        if height % config.DIFFICULTY_ADJUSTMENT_INTERVAL != 0:
            prev_block = self.get_block_by_height(height - 1)
            return prev_block.bits if prev_block else self.current_bits
        else:
            if height < config.DIFFICULTY_ADJUSTMENT_INTERVAL + 1:
                genesis = self.get_block_by_height(0)
                return genesis.bits if genesis else 0x1d00ffff
            prev = self.get_block_by_height(height - config.DIFFICULTY_ADJUSTMENT_INTERVAL)
            last = self.get_block_by_height(height - 1)
            if prev is None or last is None:
                return self.current_bits
            expected_time = config.BLOCK_TIME_SEC * config.DIFFICULTY_ADJUSTMENT_INTERVAL
            actual_time = last.timestamp - prev.timestamp
            if actual_time <= 0:
                actual_time = 1
            last_target = bits_to_target(last.bits)
            new_target = last_target * actual_time // expected_time
            max_change = 4
            if new_target > last_target * max_change:
                new_target = last_target * max_change
            elif new_target < last_target // max_change:
                new_target = last_target // max_change
            max_target = 0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff
            min_target = 0x00000000ffff0000000000000000000000000000000000000000000000000000
            new_target = max(min(new_target, max_target), min_target)
            return target_to_bits(new_target)

    def _adjust_difficulty(self):
        height = self.get_block_height()
        if height < config.DIFFICULTY_ADJUSTMENT_INTERVAL:
            return
        if height % config.DIFFICULTY_ADJUSTMENT_INTERVAL != 0:
            return
        last = self.get_last_block()
        prev = self.get_block_by_height(height - config.DIFFICULTY_ADJUSTMENT_INTERVAL)
        if prev is None:
            return
        expected_time = config.BLOCK_TIME_SEC * config.DIFFICULTY_ADJUSTMENT_INTERVAL
        actual_time = last.timestamp - prev.timestamp
        if actual_time <= 0:
            actual_time = 1

        new_target = self.difficulty_target * actual_time // expected_time
        max_change = 4
        if new_target > self.difficulty_target * max_change:
            new_target = self.difficulty_target * max_change
        elif new_target < self.difficulty_target // max_change:
            new_target = self.difficulty_target // max_change

        max_target = 0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff
        min_target = 0x00000000ffff0000000000000000000000000000000000000000000000000000
        new_target = max(min(new_target, max_target), min_target)

        self.difficulty_target = new_target
        self.current_bits = target_to_bits(new_target)
        self._set_metadata('current_bits', str(self.current_bits))
        self._set_metadata('difficulty_target', str(self.difficulty_target))
        logger.info(f"Difficulty adjusted: bits={self.current_bits:08x}, target={self.difficulty_target}")

    # ---------- Добавление блока ----------
    def add_block(self, block: Block) -> bool:
        with self.lock:
            # Проверка чекпоинта
            if block.height in config.CHECKPOINTS:
                if block.hash != config.CHECKPOINTS[block.height]:
                    logger.error(f"Блок на высоте {block.height} не соответствует чекпоинту")
                    return False
            max_checkpoint = max(config.CHECKPOINTS.keys()) if config.CHECKPOINTS else -1
            if block.height < max_checkpoint:
                logger.error(f"Блок высотой {block.height} ниже максимального чекпоинта {max_checkpoint}")
                return False

            last = self.get_last_block()
            if block.prev_hash == last.hash:
                if not self._validate_new_block(block):
                    return False
                result = self._append_block(block)
                if result:
                    self._try_add_orphans()
                return result

            parent = self.get_block_by_hash(block.prev_hash)
            if parent is None:
                self.orphans[block.hash] = block
                logger.info(f"Блок {block.height} сохранён как orphan (предок не найден)")
                self._try_add_orphans()
                return False

            if parent.height < last.height:
                logger.info("Форк короче текущей цепочки, игнорируем")
                return False

            if parent.height == last.height:
                if int(block.hash, 16) < int(last.hash, 16):
                    logger.info(f"Форк на той же высоте: заменяем блок {last.hash[:16]} на {block.hash[:16]}")
                    self._replace_last_block(block)
                    self._try_add_orphans()
                    return True
                else:
                    logger.info("Форк на той же высоте отклонён (хеш больше)")
                    return False

            logger.info(f"Обнаружен более длинный форк: высота {parent.height} -> {block.height} (текущая {last.height})")
            result = self._reorganize_chain(parent, block)
            if result:
                self._try_add_orphans()
            return result

    def _append_block(self, block: Block, from_fork: bool = False) -> bool:
        if not from_fork:
            if len(str(block.to_dict())) > config.MAX_BLOCK_SIZE * 2:
                logger.error("Размер блока превышает лимит")
                return False
            total_inputs = sum(len(tx.inputs) for tx in block.transactions)
            if total_inputs > config.MAX_SIGOPS:
                logger.error("Слишком много входов")
                return False

        spent_inputs = set()
        for tx in block.transactions[1:]:
            if not self._validate_transaction(tx, block.height, spent_inputs):
                logger.error(f"Транзакция {tx.hash[:16]} невалидна")
                return False

        # Проверка награды coinbase
        coinbase = block.transactions[0]
        reward = sum(out.amount for out in coinbase.outputs)
        max_allowed = config.REWARD if (self.total_supply + config.REWARD) <= config.MAX_SUPPLY else 0
        if reward > max_allowed:
            logger.error(f"Награда {reward} превышает допустимую {max_allowed}")
            return False

        self._save_block(block)
        self._add_block_to_utxo(block)
        self.total_supply += reward
        self._set_metadata('total_supply', str(self.total_supply))

        for tx in block.transactions[1:]:
            self._remove_from_mempool(tx)

        self._adjust_difficulty()
        self._invalidate_cache()
        self._save_mempool()
        logger.info(f"Блок {block.height} добавлен")
        return True

    def _remove_from_mempool(self, tx: Transaction):
        if tx in self.mempool:
            self.mempool.remove(tx)
            for txin in tx.inputs:
                self._mempool_spent.discard((txin.prev_tx_hash, txin.prev_output_index))

    def _replace_last_block(self, new_block: Block):
        self._rebuild_utxo_from_height(new_block.height - 1)
        self._save_block(new_block)
        self._add_block_to_utxo(new_block)
        self._clean_mempool()
        self._invalidate_cache()
        logger.info(f"Блок заменён на {new_block.hash[:16]}...")

    def _rebuild_utxo_from_height(self, up_to_height: int):
        self.utxo.clear()
        conn = self._get_conn()
        c = conn.cursor()
        c.execute("DELETE FROM utxo")
        for height in range(0, up_to_height + 1):
            block = self.get_block_by_height(height)
            if block:
                for tx in block.transactions:
                    for i, out in enumerate(tx.outputs):
                        c.execute('''INSERT OR REPLACE INTO utxo (txid, idx, amount, address, lock_until, block_height)
                                     VALUES (?, ?, ?, ?, ?, ?)''',
                                  (tx.hash, i, out.amount, out.address, out.lock_until, height))
        conn.commit()
        self._rebuild_utxo()

    def _reorganize_chain(self, common_ancestor: Block, new_tip: Block) -> bool:
        fork_blocks = []
        current = new_tip
        while current.height > common_ancestor.height:
            fork_blocks.append(current)
            prev = self.get_block_by_hash(current.prev_hash)
            if prev is None:
                logger.error("Не удалось найти предка в форке")
                return False
            current = prev
        fork_blocks.reverse()

        if len(fork_blocks) > config.MAX_REORG_DEPTH:
            logger.error(f"Слишком длинная реорганизация: {len(fork_blocks)} блоков, максимум {config.MAX_REORG_DEPTH}")
            return False

        for b in fork_blocks:
            if not self._validate_new_block(b, check_prev=False):
                logger.error(f"Блок {b.height} невалиден в форке")
                return False

        self._rebuild_utxo_from_height(common_ancestor.height)

        for b in fork_blocks:
            if not self._append_block(b, from_fork=True):
                logger.error(f"Не удалось добавить блок {b.height} при реорганизации")
                self._rebuild_utxo_from_height(common_ancestor.height)
                return False

        self._clean_mempool()
        self._invalidate_cache()
        logger.info(f"Реорганизация завершена, новая высота: {self.get_block_height()}")
        return True

    def _try_add_orphans(self):
        added = True
        while added:
            added = False
            for orphan_hash, orphan_block in list(self.orphans.items()):
                parent = self.get_block_by_hash(orphan_block.prev_hash)
                if parent is not None:
                    logger.info(f"Найден предок для орфана {orphan_block.height}, пытаемся добавить")
                    if self.add_block(orphan_block):
                        del self.orphans[orphan_hash]
                        added = True
                        break

    # ---------- Валидация ----------
    def _validate_new_block(self, block: Block, check_prev: bool = True) -> bool:
        if check_prev:
            last = self.get_last_block()
            if block.prev_hash != last.hash:
                logger.error("Invalid prev_hash")
                return False
            if block.height != last.height + 1:
                logger.error("Invalid height")
                return False
            if block.timestamp < last.timestamp:
                logger.error("Block timestamp is earlier than previous block")
                return False
        current_time = int(time.time())
        if abs(block.timestamp - current_time) > config.MAX_TIME_DRIFT:
            logger.error(f"Block timestamp too far from current time: {block.timestamp} vs {current_time}")
            return False

        if not config.SKIP_DIFFICULTY_CHECK:
            expected_bits = self._get_expected_bits(block.height)
            if block.bits != expected_bits:
                logger.error(f"Invalid bits: {block.bits:08x} != expected {expected_bits:08x} for height {block.height}")
                return False

        target = bits_to_target(block.bits)
        if int(block.hash, 16) >= target:
            logger.error("Invalid PoW")
            return False
        if not self._validate_block_transactions(block):
            logger.error("Transactions invalid")
            return False
        return True

    def _validate_block_transactions(self, block: Block) -> bool:
        if not block.transactions:
            logger.error("Block has no transactions")
            return False
        if not block.transactions[0].is_coinbase():
            logger.error("First transaction is not coinbase")
            return False
        coinbase_count = sum(1 for tx in block.transactions if tx.is_coinbase())
        if coinbase_count != 1:
            logger.error(f"Wrong coinbase count: {coinbase_count}")
            return False

        coinbase = block.transactions[0]
        if len(coinbase.outputs) != len(config.VESTING_PERIODS):
            logger.error(f"Coinbase имеет {len(coinbase.outputs)} выходов, ожидается {len(config.VESTING_PERIODS)}")
            return False

        for i, out in enumerate(coinbase.outputs):
            expected_lock = block.height + config.VESTING_PERIODS[i]
            if out.lock_until != expected_lock:
                logger.error(f"Неверный lock_until для выхода {i}: {out.lock_until} != {expected_lock}")
                return False

        total_reward = sum(out.amount for out in coinbase.outputs)
        if total_reward > config.REWARD:
            logger.error(f"Invalid coinbase reward: {total_reward} > {config.REWARD}")
            return False

        spent_inputs: Set[Tuple[str, int]] = set()
        tx_hashes = set()
        for tx in block.transactions[1:]:
            if tx.hash in tx_hashes:
                logger.error("Duplicate transaction in block")
                return False
            tx_hashes.add(tx.hash)
            if not self._validate_transaction(tx, block.height, spent_inputs):
                return False
        return True

    def _validate_transaction(self, tx: Transaction, block_height: int, spent_inputs: Set[Tuple[str, int]]) -> bool:
        for out in tx.outputs:
            if not is_valid_address(out.address):
                logger.error(f"Invalid address in output: {out.address}")
                return False
        total_input = 0
        for txin in tx.inputs:
            key = (txin.prev_tx_hash, txin.prev_output_index)
            if key in spent_inputs:
                logger.error("Double spend within block")
                return False
            if key not in self.utxo:
                logger.error("UTXO not found")
                return False
            out, _ = self.utxo[key]
            if not is_valid_address(out.address):
                logger.error(f"Invalid address in UTXO: {out.address}")
                return False
            if out.lock_until > block_height:
                logger.error("Output locked")
                return False
            total_input += out.amount
            spent_inputs.add(key)
        if not tx.verify_signatures(self.utxo):
            logger.error("Invalid signature")
            return False
        total_output = sum(out.amount for out in tx.outputs)
        if total_output > total_input:
            logger.error("Output > Input")
            return False
        return True

    # ---------- Мемпул ----------
    def add_to_mempool(self, tx: Transaction) -> bool:
        with self.lock:
            if tx in self.mempool:
                return False
            if len(self.mempool) >= config.MAX_MEMPOOL_SIZE:
                if self.mempool and tx.fee > self.mempool[-1].fee:
                    old = self.mempool.pop()
                    for txin in old.inputs:
                        self._mempool_spent.discard((txin.prev_tx_hash, txin.prev_output_index))
                else:
                    logger.warning("Мемпул переполнен")
                    return False

            if not tx.verify_signatures(self.utxo):
                return False

            # Проверка на двойную трату с заменой
            for txin in tx.inputs:
                if (txin.prev_tx_hash, txin.prev_output_index) in self._mempool_spent:
                    for old_tx in self.mempool:
                        if any((tin.prev_tx_hash, tin.prev_output_index) == (txin.prev_tx_hash, txin.prev_output_index)
                               for tin in old_tx.inputs):
                            if tx.fee > old_tx.fee:
                                self.mempool.remove(old_tx)
                                for tin in old_tx.inputs:
                                    self._mempool_spent.discard((tin.prev_tx_hash, tin.prev_output_index))
                                break
                            else:
                                return False

            self.mempool.append(tx)
            self.mempool.sort(key=lambda t: t.fee, reverse=True)
            for txin in tx.inputs:
                self._mempool_spent.add((txin.prev_tx_hash, txin.prev_output_index))
            self._save_mempool()
            return True

    def _clean_mempool(self):
        with self.lock:
            to_remove = []
            for tx in self.mempool:
                valid = True
                for txin in tx.inputs:
                    if (txin.prev_tx_hash, txin.prev_output_index) not in self.utxo:
                        valid = False
                        break
                if not valid:
                    to_remove.append(tx)
            for tx in to_remove:
                self.mempool.remove(tx)
                for txin in tx.inputs:
                    self._mempool_spent.discard((txin.prev_tx_hash, txin.prev_output_index))
            if to_remove:
                self._save_mempool()
                logger.info(f"Очищено {len(to_remove)} транзакций из мемпула")

    # ---------- Кеширование ----------
    def _invalidate_cache(self):
        with self._cache_lock:
            self._balance_cache.clear()
            self._last_block_cache = None

    def save(self):
        self._set_metadata('current_bits', str(self.current_bits))
        self._set_metadata('difficulty_target', str(self.difficulty_target))
        self._set_metadata('total_supply', str(self.total_supply))
        self._save_mempool()
        logger.info("Блокчейн сохранён (метаданные и мемпул)")

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    @property
    def chain(self) -> List[Block]:
        blocks = []
        conn = self._get_conn()
        c = conn.cursor()
        c.execute("SELECT data FROM blocks ORDER BY height")
        for (data,) in c.fetchall():
            blocks.append(Block.from_bytes(data))
        return blocks
