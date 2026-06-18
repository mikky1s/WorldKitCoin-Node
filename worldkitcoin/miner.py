# miner.py
import time
from blockchain import Blockchain
from block import Block
from transaction import Transaction
from config import REWARD, VESTING_PERIODS
from utils import bits_to_target

class SimpleMiner:
    def __init__(self, address: str, blockchain: Blockchain):
        self.address = address
        self.bc = blockchain
        self.mined_blocks = 0

    def mine_block(self, transactions=None):
        if transactions is None:
            transactions = []

        last_block = self.bc.get_last_block()
        new_height = last_block.height + 1

        coinbase = Transaction.create_coinbase(
            new_height, REWARD, self.address, VESTING_PERIODS
        )

        if not transactions:
            transactions = self.bc.mempool[:10]

        all_txs = [coinbase] + transactions

        block = Block(
            height=new_height,
            transactions=all_txs,
            prev_hash=last_block.hash,
            timestamp=int(time.time()),
            bits=self.bc.current_bits
        )

        target = bits_to_target(block.bits)
        block.mine(target)

        if self.bc.add_block(block):
            self.mined_blocks += 1
            print(f"🎉 Miner {self.address[:8]}... mined block {block.height}!")
            return block
        else:
            print(f"❌ Failed to add block {block.height}")
            return None