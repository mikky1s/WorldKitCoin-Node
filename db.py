import sqlite3
import json
from typing import List, Dict, Tuple, Optional
from block import Block
from transaction import Transaction, TxIn, TxOut

class BlockchainDB:
    def __init__(self, db_path="blockchain.db"):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.init_tables()

    def init_tables(self):
        cur = self.conn.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS blocks (
                height INTEGER PRIMARY KEY,
                hash TEXT UNIQUE,
                prev_hash TEXT,
                timestamp INTEGER,
                nonce INTEGER,
                bits INTEGER,
                merkle_root TEXT,
                data TEXT
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS transactions (
                tx_hash TEXT PRIMARY KEY,
                block_height INTEGER,
                tx_data TEXT,
                FOREIGN KEY (block_height) REFERENCES blocks(height)
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS utxo (
                txid TEXT,
                output_index INTEGER,
                amount INTEGER,
                pubkey_hash TEXT,
                lock_until INTEGER,
                block_height INTEGER,
                PRIMARY KEY (txid, output_index)
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS mempool (
                tx_hash TEXT PRIMARY KEY,
                tx_data TEXT
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        self.conn.commit()
        cur.close()

    def save_block(self, block: Block):
        cur = self.conn.cursor()
        block_data = json.dumps(block.to_dict())
        cur.execute('''
            INSERT OR REPLACE INTO blocks (height, hash, prev_hash, timestamp, nonce, bits, merkle_root, data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (block.height, block.hash, block.prev_hash, block.timestamp, block.nonce, block.bits, block.merkle_root, block_data))
        for tx in block.transactions:
            cur.execute('''
                INSERT OR REPLACE INTO transactions (tx_hash, block_height, tx_data)
                VALUES (?, ?, ?)
            ''', (tx.hash, block.height, json.dumps(tx.to_dict())))
        self.conn.commit()
        cur.close()

    def delete_block(self, height: int):
        cur = self.conn.cursor()
        cur.execute('DELETE FROM transactions WHERE block_height = ?', (height,))
        cur.execute('DELETE FROM blocks WHERE height = ?', (height,))
        self.conn.commit()
        cur.close()

    def get_block(self, height: int) -> Optional[Block]:
        cur = self.conn.cursor()
        cur.execute('SELECT data FROM blocks WHERE height = ?', (height,))
        row = cur.fetchone()
        if row:
            return Block.from_dict(json.loads(row['data']))
        return None

    def get_last_block(self) -> Optional[Block]:
        cur = self.conn.cursor()
        cur.execute('SELECT data FROM blocks ORDER BY height DESC LIMIT 1')
        row = cur.fetchone()
        if row:
            return Block.from_dict(json.loads(row['data']))
        return None

    def get_block_height(self) -> int:
        cur = self.conn.cursor()
        cur.execute('SELECT MAX(height) as max FROM blocks')
        row = cur.fetchone()
        return row['max'] if row['max'] is not None else -1

    def get_chain(self) -> List[Block]:
        cur = self.conn.cursor()
        cur.execute('SELECT data FROM blocks ORDER BY height ASC')
        rows = cur.fetchall()
        return [Block.from_dict(json.loads(row['data'])) for row in rows]

    def replace_chain(self, blocks: List[Block]):
        cur = self.conn.cursor()
        cur.execute('DELETE FROM blocks')
        cur.execute('DELETE FROM transactions')
        for block in blocks:
            self.save_block(block)
        self.conn.commit()

    def update_utxo(self, utxo_dict: Dict[Tuple[str, int], Tuple[TxOut, int]]):
        cur = self.conn.cursor()
        cur.execute('DELETE FROM utxo')
        for (txid, idx), (out, height) in utxo_dict.items():
            cur.execute('''
                INSERT INTO utxo (txid, output_index, amount, pubkey_hash, lock_until, block_height)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (txid, idx, out.amount, out.pubkey_hash, out.lock_until, height))
        self.conn.commit()

    def get_utxo(self) -> Dict[Tuple[str, int], Tuple[TxOut, int]]:
        cur = self.conn.cursor()
        cur.execute('SELECT txid, output_index, amount, pubkey_hash, lock_until, block_height FROM utxo')
        rows = cur.fetchall()
        utxo = {}
        for row in rows:
            out = TxOut(row['amount'], row['pubkey_hash'], row['lock_until'])
            utxo[(row['txid'], row['output_index'])] = (out, row['block_height'])
        return utxo

    def clear_mempool(self):
        cur = self.conn.cursor()
        cur.execute('DELETE FROM mempool')
        self.conn.commit()

    def add_mempool_tx(self, tx: Transaction):
        cur = self.conn.cursor()
        cur.execute('INSERT OR REPLACE INTO mempool (tx_hash, tx_data) VALUES (?, ?)',
                    (tx.hash, json.dumps(tx.to_dict())))
        self.conn.commit()

    def remove_mempool_tx(self, tx_hash: str):
        cur = self.conn.cursor()
        cur.execute('DELETE FROM mempool WHERE tx_hash = ?', (tx_hash,))
        self.conn.commit()

    def get_mempool_txs(self) -> List[Transaction]:
        cur = self.conn.cursor()
        cur.execute('SELECT tx_data FROM mempool')
        rows = cur.fetchall()
        return [Transaction.from_dict(json.loads(row['tx_data'])) for row in rows]

    def get_metadata(self, key: str, default=None):
        cur = self.conn.cursor()
        cur.execute('SELECT value FROM metadata WHERE key = ?', (key,))
        row = cur.fetchone()
        if row:
            return row['value']
        return default

    def set_metadata(self, key: str, value: str):
        cur = self.conn.cursor()
        cur.execute('INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)', (key, value))
        self.conn.commit()

    def close(self):
        self.conn.close()