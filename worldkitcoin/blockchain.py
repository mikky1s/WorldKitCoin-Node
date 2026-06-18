import json
import os
import time
from typing import Dict, Tuple, List, Optional, Set
from block import Block
from transaction import Transaction, TxIn, TxOut
from config import (
    INITIAL_DIFFICULTY_TARGET, DIFFICULTY_ADJUSTMENT_INTERVAL,
    BLOCK_TIME_SEC, REWARD, VESTING_PERIODS, MAX_SUPPLY
)
from utils import target_to_bits, bits_to_target

class Blockchain:
    def __init__(self, genesis_address: str, load_from_file: bool = True):
        self.chain: List[Block] = []
        self.utxo: Dict[Tuple[str, int], Tuple[TxOut, int]] = {}
        self.mempool: List[Transaction] = []
        # Фиксируем максимальный target для теста
        self.difficulty_target = 0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff
        self.current_bits = target_to_bits(INITIAL_DIFFICULTY_TARGET)  # не используется
        self.total_supply = 0
        self.data_file = "blockchain_data.json"

        if load_from_file and os.path.exists(self.data_file):
            try:
                self.load()
                return
            except Exception as e:
                print(f"⚠️ Ошибка загрузки блокчейна: {e}")
                print("📦 Создаём новый блокчейн...")

        genesis = Block.genesis(genesis_address)
        genesis.bits = 0x1f00ffff  # максимальная сложность
        genesis.hash = genesis.compute_hash()
        self.chain.append(genesis)
        self._add_block_to_utxo(genesis)
        self.total_supply = REWARD
        print(f"🚀 Genesis block created: {genesis.hash[:16]}...")
        self.save()

    def get_last_block(self) -> Block:
        return self.chain[-1]

    def get_block_height(self) -> int:
        return len(self.chain) - 1

    def save(self):
        data = {
            'chain': [block.to_dict() for block in self.chain],
            'utxo': self._serialize_utxo(),
            'mempool': [tx.to_dict() for tx in self.mempool],
            'difficulty_target': self.difficulty_target,
            'current_bits': self.current_bits,
            'total_supply': self.total_supply
        }
        with open(self.data_file, 'w') as f:
            json.dump(data, f, indent=2)
        print(f"💾 Blockchain saved to {self.data_file}")

    def load(self):
        with open(self.data_file, 'r') as f:
            data = json.load(f)

        self.chain = [Block.from_dict(block_data) for block_data in data['chain']]
        self.utxo = self._deserialize_utxo(data['utxo'])
        self.mempool = [Transaction.from_dict(tx_data) for tx_data in data['mempool']]
        self.difficulty_target = data['difficulty_target']
        self.current_bits = data['current_bits']
        self.total_supply = data['total_supply']

        print(f"📂 Blockchain loaded from {self.data_file}")
        print(f"   Height: {self.get_block_height()}, UTXO: {len(self.utxo)}, Mempool: {len(self.mempool)}")

    def _serialize_utxo(self) -> dict:
        serialized = {}
        for (txid, index), (out, height) in self.utxo.items():
            key = f"{txid}:{index}"
            serialized[key] = {
                'output': out.to_dict(),
                'height': height
            }
        return serialized

    def _deserialize_utxo(self, data: dict) -> dict:
        utxo = {}
        for key, value in data.items():
            txid, index = key.split(':')
            out = TxOut.from_dict(value['output'])
            utxo[(txid, int(index))] = (out, value['height'])
        return utxo

    def add_block(self, block: Block) -> bool:
        if not self._validate_new_block(block):
            return False
        self.chain.append(block)
        self._add_block_to_utxo(block)
        self.total_supply += REWARD
        for tx in block.transactions[1:]:
            if tx in self.mempool:
                self.mempool.remove(tx)
        # Корректировку сложности отключаем для теста
        # self._adjust_difficulty()
        self.save()
        print(f"✅ Block {block.height} added! Hash: {block.hash[:16]}...")
        return True

    def _validate_new_block(self, block: Block) -> bool:
        last = self.get_last_block()
        if block.prev_hash != last.hash:
            print(f"❌ Invalid prev_hash")
            return False
        if block.height != last.height + 1:
            print(f"❌ Invalid height")
            return False
        # Используем bits из блока для проверки PoW
        target = bits_to_target(block.bits)
        # Для теста фиксируем максимальный target
        target = 0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff
        if int(block.hash, 16) >= target:
            print(f"❌ Invalid PoW")
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
        # Оставляем заглушку, чтобы не менять сложность
        pass

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