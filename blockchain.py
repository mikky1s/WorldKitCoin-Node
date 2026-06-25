import time
import json
from typing import Dict, Tuple, List, Optional, Set
from block import Block
from transaction import Transaction, TxIn, TxOut
from config import (
    INITIAL_DIFFICULTY_TARGET, DIFFICULTY_ADJUSTMENT_INTERVAL,
    BLOCK_TIME_SEC, REWARD, VESTING_PERIODS, MAX_SUPPLY, MAX_TARGET
)
from utils import bits_to_target, target_to_bits, hash256_hex
from db import BlockchainDB

class Blockchain:
    def __init__(self, genesis_address: str, load_from_file: bool = True):
        self.db = BlockchainDB()
        self.chain: List[Block] = []
        self.utxo: Dict[Tuple[str, int], Tuple[TxOut, int]] = {}
        self.mempool: List[Transaction] = []
        self.total_supply = 0
        self.difficulty_target = MAX_TARGET
        self.current_bits = 0x1f00ffff

        if load_from_file:
            self.load()

        if not self.chain:
            genesis = Block.genesis(genesis_address)
            genesis.bits = 0x1f00ffff
            genesis.hash = genesis.compute_hash()
            self.chain.append(genesis)
            self._add_block_to_utxo(genesis)
            self.total_supply = REWARD
            self.save()
            print(f"🚀 Genesis block created: {genesis.hash[:16]}...")

    def load(self):
        blocks = self.db.get_chain()
        if blocks:
            self.chain = blocks
            self.utxo = self.db.get_utxo()
            self.mempool = self.db.get_mempool_txs()
            self.total_supply = int(self.db.get_metadata('total_supply', '0'))
            self.difficulty_target = int(self.db.get_metadata('difficulty_target', str(MAX_TARGET)))
            self.current_bits = int(self.db.get_metadata('current_bits', str(0x1f00ffff)))
            print(f"📂 Blockchain loaded from DB. Height: {self.get_block_height()}, UTXO: {len(self.utxo)}, Mempool: {len(self.mempool)}")

    def save(self):
        self.db.replace_chain(self.chain)
        self.db.update_utxo(self.utxo)
        self.db.clear_mempool()
        for tx in self.mempool:
            self.db.add_mempool_tx(tx)
        self.db.set_metadata('total_supply', str(self.total_supply))
        self.db.set_metadata('difficulty_target', str(self.difficulty_target))
        self.db.set_metadata('current_bits', str(self.current_bits))
        print("💾 Blockchain saved to DB")

    def get_last_block(self) -> Block:
        return self.chain[-1]

    def get_block_height(self) -> int:
        return len(self.chain) - 1

    def add_block(self, block: Block) -> bool:
        if not self._validate_new_block(block):
            return False
        self.chain.append(block)
        self._add_block_to_utxo(block)
        self.total_supply += REWARD
        for tx in block.transactions[1:]:
            if tx in self.mempool:
                self.mempool.remove(tx)
                self.db.remove_mempool_tx(tx.hash)
        self._adjust_difficulty()
        self.save()
        print(f"✅ Block {block.height} added! Hash: {block.hash[:16]}...")
        return True

    def _validate_new_block(self, block: Block) -> bool:
        last = self.get_last_block()
        if block.prev_hash != last.hash:
            print("❌ Invalid prev_hash")
            return False
        if block.height != last.height + 1:
            print("❌ Invalid height")
            return False
        target = bits_to_target(block.bits)
        if int(block.hash, 16) >= target:
            print(f"❌ Invalid PoW: hash {block.hash[:16]}... >= target {target}")
            return False
        if not self._validate_block_transactions(block):
            print("❌ Transactions invalid")
            return False
        return True

    def _validate_block_transactions(self, block: Block) -> bool:
        if not block.transactions:
            print("❌ Block has no transactions")
            return False
        if not block.transactions[0].is_coinbase():
            print("❌ First transaction is not coinbase")
            return False
        coinbase_count = sum(1 for tx in block.transactions if tx.is_coinbase())
        if coinbase_count != 1:
            print(f"❌ Wrong coinbase count: {coinbase_count}")
            return False
        spent_inputs: Set[Tuple[str, int]] = set()
        for tx in block.transactions[1:]:
            if not self._validate_transaction(tx, block.height, spent_inputs):
                return False
        coinbase = block.transactions[0]
        total_reward = sum(out.amount for out in coinbase.outputs)
        if total_reward != REWARD:
            print(f"❌ Invalid coinbase reward: {total_reward} != {REWARD}")
            return False
        return True

    def _validate_transaction(self, tx: Transaction, block_height: int, spent_inputs: Set[Tuple[str, int]]) -> bool:
        total_input = 0
        for txin in tx.inputs:
            key = (txin.prev_tx_hash, txin.prev_output_index)
            if key in spent_inputs:
                print("❌ Double spend")
                return False
            if key not in self.utxo:
                print("❌ UTXO not found")
                return False
            out, created_height = self.utxo[key]
            if out.lock_until > block_height:
                print("❌ Output locked")
                return False
            total_input += out.amount
            spent_inputs.add(key)

        if not tx.verify_signatures(self.utxo):
            print("❌ Invalid signature")
            return False

        total_output = sum(out.amount for out in tx.outputs)
        if total_output > total_input:
            print("❌ Output > Input")
            return False
        return True

    def _add_block_to_utxo(self, block: Block):
        for tx in block.transactions[1:]:
            for txin in tx.inputs:
                key = (txin.prev_tx_hash, txin.prev_output_index)
                if key in self.utxo:
                    del self.utxo[key]

        for tx in block.transactions:
            for i, out in enumerate(tx.outputs):
                key = (tx.hash, i)
                self.utxo[key] = (out, block.height)

    def _adjust_difficulty(self):
        height = self.get_block_height()
        if height % DIFFICULTY_ADJUSTMENT_INTERVAL != 0:
            return
        if height < DIFFICULTY_ADJUSTMENT_INTERVAL:
            return
        start_height = height - DIFFICULTY_ADJUSTMENT_INTERVAL
        first_block = self.chain[start_height]
        last_block = self.chain[height]
        actual_time = last_block.timestamp - first_block.timestamp
        expected_time = BLOCK_TIME_SEC * DIFFICULTY_ADJUSTMENT_INTERVAL
        if actual_time < 1:
            actual_time = 1
        old_target = bits_to_target(self.current_bits)
        new_target = old_target * actual_time // expected_time
        if new_target > old_target * 4:
            new_target = old_target * 4
        if new_target < old_target // 4:
            new_target = old_target // 4
        if new_target > MAX_TARGET:
            new_target = MAX_TARGET
        self.difficulty_target = new_target
        self.current_bits = target_to_bits(new_target)
        print(f"🔄 Difficulty adjusted: new bits = {hex(self.current_bits)}, target = {new_target}")

    def get_balance(self, address: str, current_height: int) -> Tuple[int, int]:
        available = 0
        locked = 0
        for (out, created_height) in self.utxo.values():
            if out.pubkey_hash == address:
                if out.lock_until <= current_height:
                    available += out.amount
                else:
                    locked += out.amount
        return available, locked

    def get_utxo_for_address(self, address: str, current_height: int) -> List[dict]:
        result = []
        for (txid, idx), (out, height) in self.utxo.items():
            if out.pubkey_hash == address:
                result.append({
                    'txid': txid,
                    'output_index': idx,
                    'amount': out.amount,
                    'lock_until': out.lock_until,
                    'block_height': height,
                    'spendable': out.lock_until <= current_height
                })
        return result

    def create_transaction(self, from_address: str, to_address: str,
                           amount: int, current_height: int) -> Optional[Transaction]:
        inputs = []
        total = 0
        for key, (out, created_height) in self.utxo.items():
            if out.pubkey_hash == from_address:
                if out.lock_until > current_height:
                    continue
                inputs.append(TxIn(key[0], key[1]))
                total += out.amount
                if total >= amount:
                    break
        if total < amount:
            print(f"❌ Insufficient funds: {total} < {amount}")
            return None
        outputs = [TxOut(amount, to_address)]
        change = total - amount
        if change > 0:
            outputs.append(TxOut(change, from_address))
        tx = Transaction(inputs, outputs)
        print(f"✅ Transaction created: {tx.hash[:16]}... (amount={amount})")
        return tx

    def add_to_mempool(self, tx: Transaction) -> bool:
        if tx in self.mempool:
            return False
        self.mempool.append(tx)
        self.db.add_mempool_tx(tx)
        return True

    def get_total_supply(self) -> int:
        return self.total_supply

    def get_transaction_history(self, address: str, current_height: int) -> List[dict]:
        history = []
        for block in self.chain:
            for tx in block.transactions:
                involved = False
                for out in tx.outputs:
                    if out.pubkey_hash == address:
                        involved = True
                        break
                if not involved:
                    for txin in tx.inputs:
                        key = (txin.prev_tx_hash, txin.prev_output_index)
                        if key in self.utxo:
                            out, _ = self.utxo[key]
                            if out.pubkey_hash == address:
                                involved = True
                                break
                if involved:
                    amount = sum(out.amount for out in tx.outputs if out.pubkey_hash == address)
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

    def get_transaction_by_hash(self, tx_hash: str) -> Optional[Transaction]:
        # Сначала ищем в мемпуле
        for tx in self.mempool:
            if tx.hash == tx_hash:
                return tx
        # Потом в цепочке через БД (используем существующий self.db)
        try:
            # Получаем данные транзакции из таблицы transactions
            import sqlite3
            conn = self.db.conn  # используем соединение из db
            cur = conn.cursor()
            cur.execute('SELECT tx_data FROM transactions WHERE tx_hash = ?', (tx_hash,))
            row = cur.fetchone()
            if row:
                return Transaction.from_dict(json.loads(row[0]))
        except Exception as e:
            print(f"⚠️ Ошибка при поиске транзакции: {e}")
        return None

    def get_transaction_block_height(self, tx_hash: str) -> Optional[int]:
        try:
            cur = self.db.conn.cursor()
            cur.execute('SELECT block_height FROM transactions WHERE tx_hash = ?', (tx_hash,))
            row = cur.fetchone()
            if row:
                return row[0]
        except Exception as e:
            print(f"⚠️ Ошибка при получении высоты блока транзакции: {e}")
        return None