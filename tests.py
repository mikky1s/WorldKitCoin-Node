import unittest
import tempfile
import os
import time
import json
import asyncio
import struct
import hashlib
import gc
from copy import deepcopy

from utils import (
    hash256, hash256_hex, bits_to_target, target_to_bits,
    serialize, compute_checksum, pack_data, unpack_data, is_valid_address,
    serialize_varint, deserialize_varint
)
from transaction import TxIn, TxOut, Transaction
from block import Block
from blockchain import Blockchain
from config import REWARD, VESTING_PERIODS, MAX_SUPPLY, DIFFICULTY_ADJUSTMENT_INTERVAL, BLOCK_TIME_SEC
import config
from ecdsa import SigningKey, SECP256k1
from api import app, init_api
from p2p import P2PServer
from p2p_crypto import generate_self_signed_cert, verify_shared_secret, generate_challenge, solve_pow, verify_pow

# Отключаем проверку сложности для тестов
config.SKIP_DIFFICULTY_CHECK = True


class TestUtils(unittest.TestCase):
    def test_varint(self):
        test_cases = [
            (0, b'\x00'),
            (0xfc, b'\xfc'),
            (0xfd, b'\xfd\xfd\x00'),
            (0xffff, b'\xfd\xff\xff'),
            (0x10000, b'\xfe\x00\x00\x01\x00'),
            (0xffffffff, b'\xfe\xff\xff\xff\xff'),
            (0x100000000, b'\xff\x00\x00\x00\x00\x01\x00\x00\x00'),
        ]
        for value, expected in test_cases:
            with self.subTest(value=value):
                encoded = serialize_varint(value)
                self.assertEqual(encoded, expected)
                decoded, pos = deserialize_varint(encoded, 0)
                self.assertEqual(decoded, value)
                self.assertEqual(pos, len(encoded))

    def test_bits_target_conversion(self):
        target = 0x00000000ffff0000000000000000000000000000000000000000000000000000
        bits = target_to_bits(target)
        self.assertEqual(bits_to_target(bits), target)

    def test_checksum(self):
        data = {"a": 1, "b": 2}
        cs = compute_checksum(data)
        self.assertIsInstance(cs, str)
        self.assertEqual(len(cs), 64)

    def test_pack_unpack(self):
        data = {"x": [1,2,3], "y": "test"}
        packed = pack_data(data)
        self.assertIsInstance(packed, bytes)
        unpacked = unpack_data(packed)
        self.assertEqual(unpacked, data)

    def test_invalid_checksum(self):
        data = {"a": 1}
        packed = pack_data(data)
        corrupted = packed[:10] + b'\x00' + packed[11:]
        with self.assertRaises(ValueError):
            unpack_data(corrupted)

    def test_is_valid_address(self):
        self.assertTrue(is_valid_address('a'*64))
        self.assertFalse(is_valid_address('a'*63))
        self.assertFalse(is_valid_address('g'*64))
        self.assertFalse(is_valid_address(''))


class TestTransaction(unittest.TestCase):
    def setUp(self):
        self.sk = SigningKey.generate(curve=SECP256k1)
        self.vk = self.sk.get_verifying_key()
        self.pubkey_hex = self.vk.to_string().hex()
        self.address = hash256(self.vk.to_string()).hex()
        tx = Transaction([TxIn('0'*64, 0)], [TxOut(100, self.address)])
        self.tx_hash = tx.hash
        self.utxo = {
            (self.tx_hash, 0): (TxOut(100, self.address, 0), 0),
        }

    def test_tx_creation(self):
        txin = TxIn(self.tx_hash, 0)
        txout = TxOut(100, self.address)
        tx = Transaction([txin], [txout])
        self.assertEqual(len(tx.inputs), 1)
        self.assertEqual(len(tx.outputs), 1)
        self.assertIsNotNone(tx.hash)

    def test_coinbase(self):
        coinbase = Transaction.create_coinbase(0, REWARD, self.address, VESTING_PERIODS)
        self.assertTrue(coinbase.is_coinbase())
        self.assertEqual(len(coinbase.outputs), len(VESTING_PERIODS))
        total = sum(out.amount for out in coinbase.outputs)
        self.assertEqual(total, REWARD)

    def test_extra_nonce(self):
        coinbase = Transaction.create_coinbase(0, REWARD, self.address, VESTING_PERIODS)
        old_hash = coinbase.hash
        coinbase.update_extra_nonce(12345)
        self.assertNotEqual(coinbase.hash, old_hash)

    def test_sign_and_verify(self):
        txin = TxIn(self.tx_hash, 0)
        txout = TxOut(100, self.address)
        tx = Transaction([txin], [txout])
        tx.sign(self.sk.to_string().hex(), self.pubkey_hex)
        self.assertTrue(tx.verify_signatures(self.utxo))
        self.assertEqual(tx.fee, 0)

    def test_verify_fails_bad_signature(self):
        txin = TxIn(self.tx_hash, 0)
        txout = TxOut(100, self.address)
        tx = Transaction([txin], [txout])
        self.assertFalse(tx.verify_signatures(self.utxo))

    def test_verify_fails_wrong_address(self):
        other_sk = SigningKey.generate(curve=SECP256k1)
        other_pub = other_sk.get_verifying_key().to_string().hex()
        other_addr = hash256(bytes.fromhex(other_pub)).hex()
        utxo2 = {("0"*64, 0): (TxOut(100, other_addr, 0), 0)}
        txin = TxIn("0"*64, 0)
        txout = TxOut(100, self.address)
        tx = Transaction([txin], [txout])
        tx._hash = "0"*64
        tx.sign(self.sk.to_string().hex(), self.pubkey_hex)
        self.assertFalse(tx.verify_signatures(utxo2))

    def test_fee_calculation(self):
        txin = TxIn(self.tx_hash, 0)
        txout = TxOut(90, self.address)
        tx = Transaction([txin], [txout])
        tx.sign(self.sk.to_string().hex(), self.pubkey_hex)
        self.assertTrue(tx.verify_signatures(self.utxo))
        self.assertEqual(tx.fee, 10)

    def test_serialization_binary(self):
        tx = Transaction.create_coinbase(5, REWARD, self.address, VESTING_PERIODS)
        tx.update_extra_nonce(42)
        data = tx.to_bytes()
        restored = Transaction.from_bytes(data)
        self.assertEqual(restored.hash, tx.hash)
        self.assertEqual(len(restored.inputs), 1)
        self.assertEqual(len(restored.outputs), len(VESTING_PERIODS))


class TestBlock(unittest.TestCase):
    def setUp(self):
        self.sk = SigningKey.generate(curve=SECP256k1)
        self.address = hash256(self.sk.get_verifying_key().to_string()).hex()
        coinbase = Transaction.create_coinbase(0, REWARD, self.address, VESTING_PERIODS)
        self.genesis = Block(0, [coinbase], "0"*64, compute_hash=False)
        self.genesis.bits = 0x1d00ffff
        self.genesis.hash = self.genesis.compute_hash()

    def test_block_hash(self):
        h = self.genesis.compute_hash()
        self.assertIsInstance(h, str)
        self.assertEqual(len(h), 64)

    def test_mine(self):
        block = Block(1, [Transaction.create_coinbase(1, REWARD, self.address, VESTING_PERIODS)],
                      self.genesis.hash, bits=0x207fffff, compute_hash=False)
        target = bits_to_target(block.bits)
        start = time.time()
        block.mine(target)
        elapsed = time.time() - start
        self.assertLess(elapsed, 5.0)
        self.assertIsNotNone(block.hash)
        self.assertTrue(int(block.hash, 16) < target)

    def test_merkle_root(self):
        tx1 = Transaction.create_coinbase(1, REWARD, self.address, VESTING_PERIODS)
        tx2 = Transaction([], [])
        block = Block(2, [tx1, tx2], self.genesis.hash, compute_hash=False)
        root = block._compute_merkle_root()
        self.assertIsInstance(root, str)
        self.assertEqual(len(root), 64)

    def test_genesis(self):
        genesis = Block.genesis(self.address)
        self.assertEqual(genesis.height, 0)
        self.assertEqual(genesis.prev_hash, "0"*64)
        self.assertTrue(genesis.transactions[0].is_coinbase())

    def test_update_extra_nonce(self):
        coinbase = Transaction.create_coinbase(1, REWARD, self.address, VESTING_PERIODS)
        block = Block(1, [coinbase], self.genesis.hash, compute_hash=False)
        old_root = block.merkle_root
        block.update_coinbase_extra_nonce(12345)
        self.assertNotEqual(block.merkle_root, old_root)
        self.assertEqual(block.extra_nonce, 12345)

    def test_serialization_binary(self):
        last = Block(0, [], "0"*64)
        coinbase = Transaction.create_coinbase(1, REWARD, self.address, VESTING_PERIODS)
        block = Block(1, [coinbase], last.hash, bits=0x207fffff, compute_hash=False)
        block.mine(bits_to_target(block.bits))
        data = block.to_bytes()
        restored = Block.from_bytes(data)
        self.assertEqual(restored.height, block.height)
        self.assertEqual(restored.hash, block.hash)
        self.assertEqual(restored.merkle_root, block.merkle_root)
        self.assertEqual(len(restored.transactions), len(block.transactions))
        self.assertEqual(restored.nonce, block.nonce)
        self.assertEqual(restored.timestamp, block.timestamp)


class TestBlockchain(unittest.TestCase):
    def setUp(self):
        self.original_checkpoints = config.CHECKPOINTS.copy()
        config.CHECKPOINTS.clear()

        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.temp_db.close()
        self.db_path = self.temp_db.name
        self.sk = SigningKey.generate(curve=SECP256k1)
        self.address = hash256(self.sk.get_verifying_key().to_string()).hex()
        self.bc = Blockchain(self.address, load_from_file=False, verify_checkpoints=False, db_path=self.db_path)

    def tearDown(self):
        if self.bc:
            self.bc.close()
            self.bc = None
        gc.collect()
        time.sleep(0.1)
        try:
            if os.path.exists(self.db_path):
                os.remove(self.db_path)
        except PermissionError:
            time.sleep(0.2)
            if os.path.exists(self.db_path):
                os.remove(self.db_path)
        config.CHECKPOINTS.clear()
        config.CHECKPOINTS.update(self.original_checkpoints)

    def test_genesis_block(self):
        self.assertEqual(self.bc.get_block_height(), 0)
        genesis = self.bc.get_block_by_height(0)
        self.assertEqual(genesis.height, 0)
        self.assertTrue(genesis.transactions[0].is_coinbase())
        self.assertEqual(self.bc.total_supply, REWARD)

    def test_add_block(self):
        last = self.bc.get_last_block()
        coinbase = Transaction.create_coinbase(1, REWARD, self.address, VESTING_PERIODS)
        block = Block(1, [coinbase], last.hash, bits=0x207fffff, compute_hash=False)
        block.mine(bits_to_target(block.bits))
        self.assertTrue(self.bc.add_block(block))
        self.assertEqual(self.bc.get_block_height(), 1)
        self.assertEqual(self.bc.total_supply, 2 * REWARD)

    def test_balance(self):
        height = self.bc.get_block_height()
        available, locked = self.bc.get_balance(self.address, height)
        first_share = REWARD // len(VESTING_PERIODS)
        expected_available = first_share + (REWARD % len(VESTING_PERIODS))
        self.assertEqual(available, expected_available)
        self.assertEqual(locked, REWARD - expected_available)

    def test_vesting_locking(self):
        height = self.bc.get_block_height()
        genesis_tx = self.bc.get_block_by_height(0).transactions[0]
        utxo_key = (genesis_tx.hash, 1)
        out, _ = self.bc.utxo[utxo_key]
        self.assertGreater(out.lock_until, height)

        txin = TxIn(genesis_tx.hash, 1)
        txout = TxOut(10, self.address)
        tx = Transaction([txin], [txout])
        spent = set()
        self.assertFalse(self.bc._validate_transaction(tx, height, spent))

    def test_mempool_with_fee(self):
        height = self.bc.get_block_height()
        other_sk = SigningKey.generate(curve=SECP256k1)
        other_addr = hash256(other_sk.get_verifying_key().to_string()).hex()
        tx = self.bc.create_transaction(self.address, other_addr, 10, height)
        self.assertIsNotNone(tx)
        tx.outputs[0].amount = 5
        tx.sign(self.sk.to_string().hex(), self.sk.get_verifying_key().to_string().hex())
        self.assertTrue(tx.verify_signatures(self.bc.utxo))
        self.assertGreater(tx.fee, 0)
        self.assertTrue(self.bc.add_to_mempool(tx))
        self.assertEqual(self.bc.get_mempool_size(), 1)

        last = self.bc.get_last_block()
        coinbase = Transaction.create_coinbase(height+1, REWARD, self.address, VESTING_PERIODS)
        block = Block(height+1, [coinbase, tx], last.hash, bits=0x207fffff, compute_hash=False)
        block.mine(bits_to_target(block.bits))
        self.assertTrue(self.bc.add_block(block))
        self.assertEqual(self.bc.get_mempool_size(), 0)
        avail, _ = self.bc.get_balance(other_addr, height+1)
        self.assertEqual(avail, 5)

    def test_mempool_replacement(self):
        height = self.bc.get_block_height()
        other_sk = SigningKey.generate(curve=SECP256k1)
        other_addr = hash256(other_sk.get_verifying_key().to_string()).hex()

        my_utxos = []
        for key, (out, _) in self.bc.utxo.items():
            if out.address == self.address and out.lock_until <= height:
                my_utxos.append((key, out.amount))
        self.assertTrue(len(my_utxos) > 0, "Нет доступных UTXO")
        (txid, idx), amount = my_utxos[0]

        tx1 = Transaction([TxIn(txid, idx)], [TxOut(amount, other_addr)])
        tx1.sign(self.sk.to_string().hex(), self.sk.get_verifying_key().to_string().hex())
        tx1.verify_signatures(self.bc.utxo)
        self.assertEqual(tx1.fee, 0)
        self.assertTrue(self.bc.add_to_mempool(tx1))

        tx2 = Transaction([TxIn(txid, idx)], [TxOut(amount - 20, other_addr)])
        tx2.sign(self.sk.to_string().hex(), self.sk.get_verifying_key().to_string().hex())
        tx2.verify_signatures(self.bc.utxo)
        self.assertEqual(tx2.fee, 20)
        self.assertTrue(self.bc.add_to_mempool(tx2))
        self.assertEqual(self.bc.get_mempool_size(), 1)
        self.assertIn(tx2, self.bc.mempool)
        self.assertNotIn(tx1, self.bc.mempool)

    def test_difficulty_adjustment(self):
        original_interval = DIFFICULTY_ADJUSTMENT_INTERVAL
        import config as cfg
        cfg.DIFFICULTY_ADJUSTMENT_INTERVAL = 2
        try:
            self.bc.current_bits = 0x207fffff
            self.bc.difficulty_target = bits_to_target(0x207fffff)
            initial_bits = self.bc.current_bits

            for i in range(1, 3):
                last = self.bc.get_last_block()
                coinbase = Transaction.create_coinbase(i, REWARD, self.address, VESTING_PERIODS)
                block = Block(
                    i, [coinbase], last.hash,
                    timestamp=last.timestamp + BLOCK_TIME_SEC + 30,
                    bits=self.bc.current_bits,
                    compute_hash=False
                )
                target = bits_to_target(block.bits)
                nonce = 0
                while nonce < 0xffffffff:
                    block.nonce = nonce
                    block.hash = block.compute_hash()
                    if int(block.hash, 16) < target:
                        break
                    nonce += 1
                else:
                    self.fail("Не удалось найти nonce для блока")
                self.assertTrue(self.bc.add_block(block))

            self.assertNotEqual(self.bc.current_bits, initial_bits)
        finally:
            cfg.DIFFICULTY_ADJUSTMENT_INTERVAL = original_interval
            self.bc.current_bits = 0x1d00ffff
            self.bc.difficulty_target = bits_to_target(0x1d00ffff)

    def test_checkpoints(self):
        for i in range(1, 3):
            last = self.bc.get_last_block()
            coinbase = Transaction.create_coinbase(i, REWARD, self.address, VESTING_PERIODS)
            block = Block(i, [coinbase], last.hash, bits=0x207fffff, compute_hash=False)
            block.mine(bits_to_target(block.bits))
            self.bc.add_block(block)

        checkpoint_hash = self.bc.get_block_by_height(2).hash
        config.CHECKPOINTS[2] = checkpoint_hash

        last = self.bc.get_block_by_height(1)
        coinbase = Transaction.create_coinbase(2, REWARD, self.address, VESTING_PERIODS)
        block2 = Block(2, [coinbase], last.hash, bits=0x207fffff, compute_hash=False)
        block2.mine(bits_to_target(block2.bits))
        block2.hash = "f"*64
        self.assertFalse(self.bc.add_block(block2))
        self.assertEqual(self.bc.get_block_height(), 2)

    def test_reward_validation(self):
        last = self.bc.get_last_block()
        coinbase = Transaction.create_coinbase(1, REWARD+10, self.address, VESTING_PERIODS)
        block = Block(1, [coinbase], last.hash, bits=0x207fffff, compute_hash=False)
        block.mine(bits_to_target(block.bits))
        self.assertFalse(self.bc.add_block(block))
        self.assertEqual(self.bc.get_block_height(), 0)

    def test_reorganization(self):
        # Основная цепочка: генезис + блок 1
        last = self.bc.get_last_block()
        coinbase = Transaction.create_coinbase(1, REWARD, self.address, VESTING_PERIODS)
        block1 = Block(1, [coinbase], last.hash, bits=0x207fffff, compute_hash=False)
        block1.mine(bits_to_target(block1.bits))
        self.assertTrue(self.bc.add_block(block1))
        self.assertEqual(self.bc.get_block_height(), 1)

        # Форк от блока 1: блоки 2, 3, 4
        fork_parent = self.bc.get_block_by_height(1)
        fork_blocks = []
        for height in range(2, 5):
            coinbase = Transaction.create_coinbase(height, REWARD, self.address, VESTING_PERIODS)
            prev_hash = fork_parent.hash if height == 2 else fork_blocks[-1].hash
            block = Block(height, [coinbase], prev_hash, bits=0x207fffff, compute_hash=False)
            block.mine(bits_to_target(block.bits))
            fork_blocks.append(block)

        for block in fork_blocks:
            self.assertTrue(self.bc.add_block(block), f"Не удалось добавить блок {block.height} форка")
        self.assertEqual(self.bc.get_block_height(), 4)
        self.assertEqual(self.bc.get_last_block().hash, fork_blocks[-1].hash)

    def test_save_load(self):
        for i in range(1, 4):
            last = self.bc.get_last_block()
            coinbase = Transaction.create_coinbase(i, REWARD, self.address, VESTING_PERIODS)
            block = Block(i, [coinbase], last.hash, bits=0x207fffff, compute_hash=False)
            block.mine(bits_to_target(block.bits))
            self.bc.add_block(block)

        self.bc.save()
        self.bc.close()
        new_bc = Blockchain(self.address, load_from_file=True, verify_checkpoints=False, db_path=self.db_path)
        self.assertEqual(new_bc.get_block_height(), self.bc.get_block_height())
        self.assertEqual(new_bc.total_supply, self.bc.total_supply)
        for h in range(self.bc.get_block_height()+1):
            self.assertEqual(new_bc.get_block_by_height(h).hash, self.bc.get_block_by_height(h).hash)
        new_bc.close()


class TestAPI(unittest.TestCase):
    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.temp_db.close()
        self.db_path = self.temp_db.name
        self.sk = SigningKey.generate(curve=SECP256k1)
        self.address = hash256(self.sk.get_verifying_key().to_string()).hex()
        self.bc = Blockchain(self.address, load_from_file=False, verify_checkpoints=False, db_path=self.db_path)
        init_api(app, self.bc)
        self.app = app.test_client()

    def tearDown(self):
        if self.bc:
            self.bc.close()
            self.bc = None
        gc.collect()
        time.sleep(0.1)
        try:
            if os.path.exists(self.db_path):
                os.remove(self.db_path)
        except PermissionError:
            time.sleep(0.2)
            if os.path.exists(self.db_path):
                os.remove(self.db_path)

    def test_info(self):
        resp = self.app.get('/info')
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertIn('height', data)

    def test_balance(self):
        resp = self.app.get(f'/balance/{self.address}')
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertIn('available', data)

    def test_create_transaction(self):
        other_sk = SigningKey.generate(curve=SECP256k1)
        other_addr = hash256(other_sk.get_verifying_key().to_string()).hex()
        payload = {
            "from_address": self.address,
            "to_address": other_addr,
            "amount": 10,
            "private_key": self.sk.to_string().hex(),
            "pubkey": self.sk.get_verifying_key().to_string().hex()
        }
        resp = self.app.post('/create_transaction', json=payload)
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertIn('transaction', data)
        self.assertIn('tx_hash', data)

    def test_send_transaction(self):
        height = self.bc.get_block_height()
        other_sk = SigningKey.generate(curve=SECP256k1)
        other_addr = hash256(other_sk.get_verifying_key().to_string()).hex()
        tx = self.bc.create_transaction(self.address, other_addr, 10, height)
        tx.sign(self.sk.to_string().hex(), self.sk.get_verifying_key().to_string().hex())
        payload = {"transaction": tx.to_dict()}
        resp = self.app.post('/send_transaction', json=payload)
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertEqual(data['status'], 'ok')

    def test_balance_after_transaction(self):
        resp = self.app.get(f'/balance/{self.address}')
        initial = json.loads(resp.data)['available']
        self.assertGreater(initial, 0)

        other_sk = SigningKey.generate(curve=SECP256k1)
        other_addr = hash256(other_sk.get_verifying_key().to_string()).hex()
        amount = 5
        payload = {
            "from_address": self.address,
            "to_address": other_addr,
            "amount": amount,
            "private_key": self.sk.to_string().hex(),
            "pubkey": self.sk.get_verifying_key().to_string().hex()
        }
        resp = self.app.post('/create_transaction', json=payload)
        tx_dict = json.loads(resp.data)['transaction']
        send_resp = self.app.post('/send_transaction', json={"transaction": tx_dict})
        self.assertEqual(send_resp.status_code, 200)

        last = self.bc.get_last_block()
        miner_sk = SigningKey.generate(curve=SECP256k1)
        miner_addr = hash256(miner_sk.get_verifying_key().to_string()).hex()
        coinbase = Transaction.create_coinbase(last.height+1, REWARD, miner_addr, VESTING_PERIODS)
        block = Block(last.height+1, [coinbase, Transaction.from_dict(tx_dict)],
                      last.hash, bits=0x207fffff, compute_hash=False)
        block.mine(bits_to_target(block.bits))
        self.assertTrue(self.bc.add_block(block))

        resp2 = self.app.get(f'/balance/{self.address}')
        new_balance = json.loads(resp2.data)['available']
        self.assertEqual(new_balance, initial - amount)


class TestP2P(unittest.TestCase):
    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.temp_db.close()
        self.db_path = self.temp_db.name
        self.sk = SigningKey.generate(curve=SECP256k1)
        self.address = hash256(self.sk.get_verifying_key().to_string()).hex()
        self.bc = Blockchain(self.address, load_from_file=False, verify_checkpoints=False, db_path=self.db_path)

    def tearDown(self):
        if self.bc:
            self.bc.close()
            self.bc = None
        gc.collect()
        time.sleep(0.1)
        try:
            if os.path.exists(self.db_path):
                os.remove(self.db_path)
        except PermissionError:
            time.sleep(0.2)
            if os.path.exists(self.db_path):
                os.remove(self.db_path)

    def test_handshake(self):
        async def run_test():
            import p2p as p2p_module
            original_limit = p2p_module.MESSAGE_LIMIT
            p2p_module.MESSAGE_LIMIT = 10000

            server = P2PServer(self.bc, host='127.0.0.1', port=0, use_ssl=False)
            server_task = asyncio.create_task(server.start())
            for _ in range(20):
                if server.port:
                    break
                await asyncio.sleep(0.1)
            else:
                raise RuntimeError("Сервер не запустился")
            await asyncio.sleep(0.5)
            port = server.port

            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            challenge = generate_challenge()
            writer.write(challenge)
            await writer.drain()
            data = await asyncio.wait_for(reader.readexactly(32+32+8+32), timeout=5.0)
            peer_challenge = data[:32]
            response = data[32:64]
            nonce = struct.unpack('<Q', data[64:72])[0]
            pow_digest = data[72:104]
            self.assertTrue(verify_pow(peer_challenge, nonce, config.P2P_POW_DIFFICULTY))
            self.assertTrue(verify_shared_secret(config.P2P_SHARED_SECRET, challenge, response))
            writer.write(b"OK")
            await writer.drain()
            writer.close()
            await writer.wait_closed()

            server.running = False
            server_task.cancel()
            try:
                await asyncio.wait_for(server_task, timeout=1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

            p2p_module.MESSAGE_LIMIT = original_limit

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(asyncio.wait_for(run_test(), timeout=8.0))
        except asyncio.TimeoutError:
            self.fail("Тест handshake завис")
        finally:
            loop.close()

    def test_connection(self):
        async def run_test():
            import p2p as p2p_module
            original_limit = p2p_module.MESSAGE_LIMIT
            original_sync = p2p_module.SYNC_INTERVAL
            original_reconnect = p2p_module.RECONNECT_INTERVAL
            p2p_module.MESSAGE_LIMIT = 10000
            p2p_module.SYNC_INTERVAL = 1000
            p2p_module.RECONNECT_INTERVAL = 1000

            server = P2PServer(self.bc, host='127.0.0.1', port=0, use_ssl=False)
            server_task = asyncio.create_task(server.start())
            for _ in range(20):
                if server.port:
                    break
                await asyncio.sleep(0.1)
            else:
                raise RuntimeError("Сервер не запустился")
            await asyncio.sleep(0.5)
            port = server.port

            server._banned_ips.clear()

            await asyncio.wait_for(server.connect_to_peer('127.0.0.1', port), timeout=5.0)
            await asyncio.sleep(1)
            self.assertGreater(len(server.peers), 0, "Пир не подключился")
            server.running = False
            server_task.cancel()
            try:
                await asyncio.wait_for(server_task, timeout=1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

            p2p_module.MESSAGE_LIMIT = original_limit
            p2p_module.SYNC_INTERVAL = original_sync
            p2p_module.RECONNECT_INTERVAL = original_reconnect

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(asyncio.wait_for(run_test(), timeout=10.0))
        except asyncio.TimeoutError:
            self.fail("Тест P2P connection завис")
        finally:
            loop.close()


if __name__ == '__main__':
    unittest.main()
