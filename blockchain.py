import os
import time
import threading
import logging
from typing import Dict, Tuple, List, Optional, Set
from collections import deque
from block import Block
from transaction import Transaction, TxIn, TxOut
import config
from utils import bits_to_target, target_to_bits, pack_data, unpack_data, is_valid_address

logger = logging.getLogger(__name__)

class Blockchain:
    def __init__(self, genesis_address: str, load_from_file: bool = True, verify_checkpoints: bool = True):
        self.lock = threading.RLock()
        self.chain: List[Block] = []
        self.utxo: Dict[Tuple[str, int], Tuple[TxOut, int]] = {}
        self.mempool: Set[Transaction] = set()
        self._mempool_spent: Set[Tuple[str, int]] = set()
        self.difficulty_target: int = 0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff
        self.current_bits: int = target_to_bits(self.difficulty_target)
        self.total_supply: int = 0
        self.data_file: str = "blockchain_data.bin"
        self.checksum: Optional[str] = None
        self.orphans: Dict[str, Block] = {}
        self._balance_cache: Dict[str, Tuple[int, int, float]] = {}
        self._last_block_cache: Optional[Block] = None
        self._cache_lock = threading.RLock()

        if load_from_file and os.path.exists(self.data_file):
            try:
                self.load(verify_checkpoints=verify_checkpoints)
                logger.info("Блокчейн загружен из бинарного файла")
                self._try_add_orphans()
                return
            except Exception as e:
                logger.error(f"Ошибка загрузки: {e}")
                logger.info("Создаём новый блокчейн...")

        genesis = Block.genesis(genesis_address)
        genesis.bits = 0x1d00ffff
        genesis.hash = genesis.compute_hash()
        self.chain.append(genesis)
        self._add_block_to_utxo(genesis)
        self.total_supply = config.REWARD
        self.difficulty_target = bits_to_target(genesis.bits)
        self.current_bits = genesis.bits
        self._invalidate_cache()
        logger.info(f"Genesis block created: {genesis.hash[:16]}...")
        self.save()

    def _invalidate_cache(self) -> None:
        with self._cache_lock:
            self._balance_cache.clear()
            self._last_block_cache = None

    def get_last_block(self) -> Block:
        with self._cache_lock:
            if self._last_block_cache is None:
                with self.lock:
                    self._last_block_cache = self.chain[-1]
            return self._last_block_cache

    def get_block_height(self) -> int:
        with self.lock:
            return len(self.chain) - 1

    def get_block_by_hash(self, block_hash: str) -> Optional[Block]:
        with self.lock:
            for block in self.chain:
                if block.hash == block_hash:
                    return block
            return self.orphans.get(block_hash)

    def get_block_by_height(self, height: int) -> Optional[Block]:
        with self.lock:
            if height < 0 or height >= len(self.chain):
                return None
            return self.chain[height]

    def get_balance(self, address: str, current_height: int) -> Tuple[int, int]:
        if not is_valid_address(address):
            return 0, 0
        with self._cache_lock:
            if address in self._balance_cache:
                avail, locked, ts = self._balance_cache[address]
                if time.time() - ts < config.CACHE_BALANCE_TTL:
                    return avail, locked

        with self.lock:
            available = 0
            locked = 0
            for (out, created_height) in self.utxo.values():
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
        with self.lock:
            history = []
            for block in self.chain:
                for tx in block.transactions:
                    involved = False
                    for out in tx.outputs:
                        if out.address == address:
                            involved = True
                            break
                    if not involved:
                        for txin in tx.inputs:
                            key = (txin.prev_tx_hash, txin.prev_output_index)
                            if key in self.utxo:
                                out, _ = self.utxo[key]
                                if out.address == address:
                                    involved = True
                                    break
                    if involved:
                        amount = sum(out.amount for out in tx.outputs if out.address == address)
                        is_coinbase = tx.is_coinbase()
                        confirmations = current_height - block.height + 1
                        history.append({
                            'tx_hash': tx.hash,
                            'block_height': block.height,
                            'timestamp': block.timestamp,
                            'amount': amount,
                            'is_coinbase': is_coinbase,
                            'confirmations': confirmations
                        })
            history.sort(key=lambda x: x['block_height'], reverse=True)
            return history

    def get_utxos_for_address(self, address: str, current_height: int) -> List[dict]:
        if not is_valid_address(address):
            return []
        with self.lock:
            result = []
            for (txid, idx), (out, created_height) in self.utxo.items():
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
            return list(self.mempool)[:limit]

    def get_mempool_size(self) -> int:
        with self.lock:
            return len(self.mempool)

    def get_total_supply(self) -> int:
        with self.lock:
            return self.total_supply

    def _get_expected_bits(self, height: int) -> int:
        if height == 0:
            return 0x1d00ffff
        if height % config.DIFFICULTY_ADJUSTMENT_INTERVAL != 0:
            prev_block = self.get_block_by_height(height - 1)
            return prev_block.bits if prev_block else self.current_bits
        else:
            if height < config.DIFFICULTY_ADJUSTMENT_INTERVAL + 1:
                return self.chain[0].bits if self.chain else 0x1d00ffff
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

    def add_block(self, block: Block) -> bool:
        with self.lock:
            for height, checkpoint_hash in config.CHECKPOINTS.items():
                if block.height == height and block.hash != checkpoint_hash:
                    logger.error(f"Блок на высоте {height} не соответствует чекпоинту")
                    return False
                if block.height < height:
                    logger.error(f"Попытка добавить блок высотой {block.height} ниже чекпоинта {height}")
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
                logger.info(f"Форк короче текущей цепочки, игнорируем")
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

        self._rebuild_utxo(common_ancestor.height)

        for b in fork_blocks:
            if not self._append_block(b, from_fork=True):
                logger.error(f"Не удалось добавить блок {b.height} при реорганизации")
                self._rebuild_utxo(common_ancestor.height)
                return False

        self._clean_mempool()
        self._invalidate_cache()
        logger.info(f"Реорганизация завершена, новая высота: {self.get_block_height()}")
        return True

    def _replace_last_block(self, new_block: Block) -> None:
        self._rebuild_utxo(new_block.height - 1)
        self.chain[-1] = new_block
        self._add_block_to_utxo(new_block)
        self._clean_mempool()
        self._invalidate_cache()
        logger.info(f"Блок заменён на {new_block.hash[:16]}...")

    def _rebuild_utxo(self, up_to_height: int) -> None:
        self.utxo.clear()
        for block in self.chain[:up_to_height+1]:
            self._add_block_to_utxo(block)

    # ----- ИСПРАВЛЕННЫЙ МЕТОД _append_block -----
    def _append_block(self, block: Block, from_fork: bool = False) -> bool:
        """Добавляет блок в конец цепочки, обновляет состояние."""
        if not from_fork:
            if len(str(block.to_dict())) > config.MAX_BLOCK_SIZE * 2:
                logger.error("Размер блока превышает лимит")
                return False
            total_inputs = sum(len(tx.inputs) for tx in block.transactions)
            if total_inputs > config.MAX_SIGOPS:
                logger.error("Слишком много входов")
                return False

        # Проверяем все транзакции, кроме coinbase, через _validate_transaction
        spent_inputs = set()
        for tx in block.transactions[1:]:
            if not self._validate_transaction(tx, block.height, spent_inputs):
                logger.error(f"Транзакция {tx.hash[:16]} невалидна")
                return False
            # Если транзакция была в мемпуле, удаляем её оттуда позже

        reward = config.REWARD if self.total_supply + config.REWARD <= config.MAX_SUPPLY else 0

        self.chain.append(block)
        self._add_block_to_utxo(block)
        self.total_supply += reward

        # Удаляем транзакции из мемпула
        for tx in block.transactions[1:]:
            self.mempool.discard(tx)
            for txin in tx.inputs:
                self._mempool_spent.discard((txin.prev_tx_hash, txin.prev_output_index))
        self._clean_mempool_after_block(block)
        self._clean_mempool()

        self._adjust_difficulty()
        self._invalidate_cache()
        self.save()
        logger.info(f"Блок {block.height} добавлен")
        return True

    def _clean_mempool_after_block(self, block: Block) -> None:
        spent_in_block = set()
        for tx in block.transactions[1:]:
            for txin in tx.inputs:
                spent_in_block.add((txin.prev_tx_hash, txin.prev_output_index))
        to_remove = set()
        for tx in self.mempool:
            for txin in tx.inputs:
                if (txin.prev_tx_hash, txin.prev_output_index) in spent_in_block:
                    to_remove.add(tx)
                    break
        for tx in to_remove:
            self.mempool.discard(tx)
            for txin in tx.inputs:
                self._mempool_spent.discard((txin.prev_tx_hash, txin.prev_output_index))

    def _try_add_orphans(self) -> None:
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

    def _add_block_to_utxo(self, block: Block) -> None:
        for tx in block.transactions[1:]:
            for txin in tx.inputs:
                key = (txin.prev_tx_hash, txin.prev_output_index)
                if key in self.utxo:
                    del self.utxo[key]
        for tx in block.transactions:
            for i, out in enumerate(tx.outputs):
                key = (tx.hash, i)
                self.utxo[key] = (out, block.height)

    def _adjust_difficulty(self) -> None:
        if len(self.chain) < config.DIFFICULTY_ADJUSTMENT_INTERVAL + 1:
            return
        if (len(self.chain) - 1) % config.DIFFICULTY_ADJUSTMENT_INTERVAL != 0:
            return
        last = self.get_last_block()
        prev = self.chain[-config.DIFFICULTY_ADJUSTMENT_INTERVAL]
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
        logger.info(f"Difficulty adjusted: bits={self.current_bits:08x}, target={self.difficulty_target}")

    def _verify_checkpoints(self) -> None:
        with self.lock:
            for height, checkpoint_hash in config.CHECKPOINTS.items():
                if height < len(self.chain):
                    block = self.chain[height]
                    if block.hash != checkpoint_hash:
                        raise ValueError(f"Чекпоинт на высоте {height} не совпадает! Ожидалось {checkpoint_hash}, получено {block.hash}")

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

    def add_to_mempool(self, tx: Transaction) -> bool:
        with self.lock:
            if tx in self.mempool:
                return False
            if len(self.mempool) >= config.MAX_MEMPOOL_SIZE:
                logger.warning("Мемпул переполнен (по количеству), транзакция отклонена")
                return False

            current_size = sum(tx.size() for tx in self.mempool)
            tx_size = tx.size()
            if current_size + tx_size > config.MAX_MEMPOOL_BYTES:
                logger.warning(f"Мемпул переполнен по размеру ({current_size + tx_size} > {config.MAX_MEMPOOL_BYTES})")
                return False

            if not tx.verify_signatures(self.utxo):
                logger.error("Транзакция имеет неверные подписи")
                return False

            total_input = 0
            for txin in tx.inputs:
                key = (txin.prev_tx_hash, txin.prev_output_index)
                if key not in self.utxo:
                    logger.error("UTXO не найден")
                    return False
                out, _ = self.utxo[key]
                total_input += out.amount
            total_output = sum(out.amount for out in tx.outputs)
            if total_output > total_input:
                logger.error("Сумма выходов превышает сумму входов")
                return False

            for txin in tx.inputs:
                key = (txin.prev_tx_hash, txin.prev_output_index)
                if key in self._mempool_spent:
                    logger.error("Вход уже используется в другой транзакции мемпула")
                    return False

            self.mempool.add(tx)
            for txin in tx.inputs:
                self._mempool_spent.add((txin.prev_tx_hash, txin.prev_output_index))
            logger.debug(f"Транзакция {tx.hash[:16]} добавлена в мемпул")
            return True

    def _clean_mempool(self) -> None:
        with self.lock:
            to_remove = set()
            for tx in self.mempool:
                if not tx.verify_signatures(self.utxo):
                    to_remove.add(tx)
                    continue
                for txin in tx.inputs:
                    key = (txin.prev_tx_hash, txin.prev_output_index)
                    if key not in self.utxo:
                        to_remove.add(tx)
                        break
            for tx in to_remove:
                self.mempool.discard(tx)
                for txin in tx.inputs:
                    self._mempool_spent.discard((txin.prev_tx_hash, txin.prev_output_index))
            if to_remove:
                logger.info(f"Очищено {len(to_remove)} транзакций из мемпула")

    def save(self) -> None:
        with self.lock:
            data = {
                'chain': [block.to_dict() for block in self.chain],
                'utxo': self._serialize_utxo(),
                'mempool': [tx.to_dict() for tx in self.mempool],
                'mempool_spent': [(txid, idx) for txid, idx in self._mempool_spent],
                'difficulty_target': self.difficulty_target,
                'current_bits': self.current_bits,
                'total_supply': self.total_supply,
                'orphans': {h: b.to_dict() for h, b in self.orphans.items()}
            }
            packed = pack_data(data)
            with open(self.data_file, 'wb') as f:
                f.write(packed)
            logger.info(f"Блокчейн сохранён (сжатый msgpack) размер {len(packed)} байт")

    def load(self, verify_checkpoints: bool = True) -> None:
        with self.lock:
            with open(self.data_file, 'rb') as f:
                raw = f.read()
            data = unpack_data(raw)
            self.chain = [Block.from_dict(block_data) for block_data in data['chain']]
            self.utxo = self._deserialize_utxo(data['utxo'])
            self.mempool = set(Transaction.from_dict(tx_data) for tx_data in data['mempool'])
            self._mempool_spent = set((txid, idx) for txid, idx in data.get('mempool_spent', []))
            self.difficulty_target = data['difficulty_target']
            self.current_bits = data['current_bits']
            self.total_supply = data['total_supply']
            self.orphans = {h: Block.from_dict(bd) for h, bd in data.get('orphans', {}).items()}
            self._invalidate_cache()
            if verify_checkpoints:
                self._verify_checkpoints()
            logger.info(f"Блокчейн загружен, высота: {self.get_block_height()}")

    def _serialize_utxo(self) -> dict:
        serialized = {}
        for (txid, index), (out, height) in self.utxo.items():
            key = f"{txid}:{index}"
            serialized[key] = {'output': out.to_dict(), 'height': height}
        return serialized

    def _deserialize_utxo(self, data: dict) -> dict:
        utxo = {}
        for key, value in data.items():
            txid, index = key.split(':')
            out = TxOut.from_dict(value['output'])
            utxo[(txid, int(index))] = (out, value['height'])
        return utxo