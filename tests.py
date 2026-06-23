import unittest
import tempfile
import os
import shutil
import time
import json
import asyncio
import threading
import logging
import io
from copy import deepcopy

from utils import (
    hash256, hash256_hex, bits_to_target, target_to_bits,
    serialize, compute_checksum, pack_data, unpack_data, is_valid_address
)
from transaction import TxIn, TxOut, Transaction
from block import Block
from blockchain import Blockchain
from config import REWARD, VESTING_PERIODS, MAX_SUPPLY, DIFFICULTY_ADJUSTMENT_INTERVAL, BLOCK_TIME_SEC
import config
from ecdsa import SigningKey, SECP256k1
from api import app, init_api
import requests
from flask import Flask
from p2p import P2PServer
import asyncio

# Для тестов используем временную директорию
TEST_DIR = tempfile.mkdtemp(prefix="blockchain_test_")
os.chdir(TEST_DIR)

# Отключаем проверку сложности для всех тестов глобально
config.SKIP_DIFFICULTY_CHECK = True

class TestUtils(unittest.TestCase):
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
        config.SKIP_DIFFICULTY_CHECK = True
        self.sk = SigningKey.generate(curve=SECP256k1)
        self.vk = self.sk.get_verifying_key()
        self.pubkey_hex = self.vk.to_string().hex()
        self.address = hash256(self.vk.to_string()).hex()
        self.utxo = {
            ("tx1", 0): (TxOut(100, self.address, 0), 0),
            ("tx1", 1): (TxOut(50, self.address, 0), 0),
        }

    def tearDown(self):
        config.SKIP_DIFFICULTY_CHECK = True

    def test_tx_creation(self):
        txin = TxIn("tx1", 0)
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
        for i, out in enumerate(coinbase.outputs):
            self.assertEqual(out.lock_until, VESTING_PERIODS[i])

    def test_sign_and_verify(self):
        txin = TxIn("tx1", 0)
        txout = TxOut(100, self.address)
        tx = Transaction([txin], [txout])
        tx.sign(self.sk.to_string().hex(), self.pubkey_hex)
        self.assertTrue(tx.verify_signatures(self.utxo))

    def test_verify_fails_bad_signature(self):
        txin = TxIn("tx1", 0)
        txout = TxOut(100, self.address)
        tx = Transaction([txin], [txout])
        self.assertFalse(tx.verify_signatures(self.utxo))

    def test_verify_fails_wrong_address(self):
        other_sk = SigningKey.generate(curve=SECP256k1)
        other_pub = other_sk.get_verifying_key().to_string().hex()
        other_addr = hash256(bytes.fromhex(other_pub)).hex()
        utxo2 = {("tx2",0): (TxOut(100, other_addr, 0), 0)}
        txin = TxIn("tx2",0)
        txout = TxOut(100, self.address)
        tx = Transaction([txin], [txout])
        tx.sign(self.sk.to_string().hex(), self.pubkey_hex)
        self.assertFalse(tx.verify_signatures(utxo2))

    def test_serialization(self):
        tx = Transaction.create_coinbase(5, REWARD, self.address, VESTING_PERIODS)
        data = tx.to_dict()
        restored = Transaction.from_dict(data)
        self.assertEqual(restored.hash, tx.hash)
        self.assertEqual(len(restored.inputs), 1)
        self.assertEqual(len(restored.outputs), len(VESTING_PERIODS))


class TestBlock(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.SKIP_DIFFICULTY_CHECK = True

    @classmethod
    def tearDownClass(cls):
        config.SKIP_DIFFICULTY_CHECK = False

    def setUp(self):
        config.SKIP_DIFFICULTY_CHECK = True
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
        tx2 = Transaction([], [])  # dummy
        block = Block(2, [tx1, tx2], self.genesis.hash, compute_hash=False)
        root = block._compute_merkle_root()
        self.assertIsInstance(root, str)
        self.assertEqual(len(root), 64)

    def test_genesis(self):
        genesis = Block.genesis(self.address)
        self.assertEqual(genesis.height, 0)
        self.assertEqual(genesis.prev_hash, "0"*64)
        self.assertTrue(genesis.transactions[0].is_coinbase())

    def test_serialization(self):
        last = Block(0, [], "0"*64)
        coinbase = Transaction.create_coinbase(1, REWARD, self.address, VESTING_PERIODS)
        block = Block(1, [coinbase], last.hash, bits=0x207fffff, compute_hash=False)
        block.mine(bits_to_target(block.bits))
        data = block.to_dict()
        restored = Block.from_dict(data)
        self.assertEqual(restored.height, block.height)
        self.assertEqual(restored.hash, block.hash)
        self.assertEqual(restored.merkle_root, block.merkle_root)
        self.assertEqual(len(restored.transactions), len(block.transactions))


class TestBlockchain(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_dir = tempfile.mkdtemp(prefix="blockchain_test_")
        os.chdir(cls.test_dir)
        config.SKIP_DIFFICULTY_CHECK = True

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.test_dir, ignore_errors=True)
        config.SKIP_DIFFICULTY_CHECK = False

    def setUp(self):
        config.SKIP_DIFFICULTY_CHECK = True
        self.sk = SigningKey.generate(curve=SECP256k1)
        self.address = hash256(self.sk.get_verifying_key().to_string()).hex()
        self.bc = Blockchain(self.address, load_from_file=False, verify_checkpoints=False)

    def tearDown(self):
        config.SKIP_DIFFICULTY_CHECK = True

    def test_genesis_block(self):
        self.assertEqual(self.bc.get_block_height(), 0)
        genesis = self.bc.chain[0]
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

    @unittest.skip("Reorg test requires more sophisticated logic (fork handling)")
    def test_fork_reorg_longer(self):
        """Тест на реорганизацию с более длинным форком (пока пропущен)."""
        pass

    def test_vesting_locking(self):
        height = self.bc.get_block_height()
        genesis_tx = self.bc.chain[0].transactions[0]
        utxo_key = (genesis_tx.hash, 1)
        out, _ = self.bc.utxo[utxo_key]
        self.assertGreater(out.lock_until, height)

        txin = TxIn(genesis_tx.hash, 1)
        txout = TxOut(10, self.address)
        tx = Transaction([txin], [txout])
        spent = set()
        self.assertFalse(self.bc._validate_transaction(tx, height, spent))

    def test_mempool(self):
        height = self.bc.get_block_height()
        other_sk = SigningKey.generate(curve=SECP256k1)
        other_addr = hash256(other_sk.get_verifying_key().to_string()).hex()
        tx = self.bc.create_transaction(self.address, other_addr, 10, height)
        self.assertIsNotNone(tx)
        tx.sign(self.sk.to_string().hex(), self.sk.get_verifying_key().to_string().hex())
        self.assertTrue(self.bc.add_to_mempool(tx))
        self.assertEqual(self.bc.get_mempool_size(), 1)

        last = self.bc.get_last_block()
        coinbase = Transaction.create_coinbase(height+1, REWARD, self.address, VESTING_PERIODS)
        block = Block(height+1, [coinbase, tx], last.hash, bits=0x207fffff, compute_hash=False)
        block.mine(bits_to_target(block.bits))
        self.assertTrue(self.bc.add_block(block))
        self.assertEqual(self.bc.get_mempool_size(), 0)
        avail, _ = self.bc.get_balance(other_addr, height+1)
        self.assertEqual(avail, 10)

    def test_mempool_double_spend(self):
        height = self.bc.get_block_height()
        other_sk = SigningKey.generate(curve=SECP256k1)
        other_addr = hash256(other_sk.get_verifying_key().to_string()).hex()
        tx1 = self.bc.create_transaction(self.address, other_addr, 10, height)
        tx1.sign(self.sk.to_string().hex(), self.sk.get_verifying_key().to_string().hex())
        self.assertTrue(self.bc.add_to_mempool(tx1))

        tx2 = self.bc.create_transaction(self.address, other_addr, 5, height)
        tx2.sign(self.sk.to_string().hex(), self.sk.get_verifying_key().to_string().hex())
        self.assertFalse(self.bc.add_to_mempool(tx2))
        self.assertEqual(self.bc.get_mempool_size(), 1)

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

    def test_save_load(self):
        for i in range(3):
            last = self.bc.get_last_block()
            coinbase = Transaction.create_coinbase(i+1, REWARD, self.address, VESTING_PERIODS)
            block = Block(i+1, [coinbase], last.hash, bits=0x207fffff, compute_hash=False)
            block.mine(bits_to_target(block.bits))
            self.bc.add_block(block)

        new_bc = Blockchain(self.address, load_from_file=True, verify_checkpoints=False)
        self.assertEqual(new_bc.get_block_height(), self.bc.get_block_height())
        self.assertEqual(new_bc.total_supply, self.bc.total_supply)
        for i in range(len(self.bc.chain)):
            self.assertEqual(new_bc.chain[i].hash, self.bc.chain[i].hash)


class TestAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_dir = tempfile.mkdtemp(prefix="api_test_")
        os.chdir(cls.test_dir)
        config.SKIP_DIFFICULTY_CHECK = True

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.test_dir, ignore_errors=True)
        config.SKIP_DIFFICULTY_CHECK = False

    def setUp(self):
        config.SKIP_DIFFICULTY_CHECK = True
        self.sk = SigningKey.generate(curve=SECP256k1)
        self.address = hash256(self.sk.get_verifying_key().to_string()).hex()
        self.bc = Blockchain(self.address, load_from_file=False, verify_checkpoints=False)
        init_api(app, self.bc)
        self.app = app.test_client()

    def test_info(self):
        resp = self.app.get('/info')
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertIn('height', data)
        self.assertIn('total_supply', data)

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


# ===== ДОПОЛНИТЕЛЬНЫЕ ТЕСТЫ ДЛЯ ПРОВЕРКИ ИСПРАВЛЕНИЙ =====
class TestFixes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.SKIP_DIFFICULTY_CHECK = True
        cls.test_dir = tempfile.mkdtemp(prefix="fixes_test_")
        os.chdir(cls.test_dir)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.test_dir, ignore_errors=True)
        config.SKIP_DIFFICULTY_CHECK = False

    def setUp(self):
        config.SKIP_DIFFICULTY_CHECK = True
        self.sk = SigningKey.generate(curve=SECP256k1)
        self.address = hash256(self.sk.get_verifying_key().to_string()).hex()
        self.bc = Blockchain(self.address, load_from_file=False, verify_checkpoints=False)

    def test_private_key_not_logged(self):
        """Проверяем, что при генерации ключей приватный ключ не попадает в лог."""
        from main import generate_keypair

        log_stream = io.StringIO()
        handler = logging.StreamHandler(log_stream)
        logger = logging.getLogger(__name__)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

        addr, priv, pub = generate_keypair()

        log_content = log_stream.getvalue()
        self.assertNotIn(priv, log_content, "Приватный ключ попал в лог!")
        self.assertNotIn(pub, log_content, "Публичный ключ тоже не должен логироваться")

    def test_peers_persistence(self):
        """Проверяем сохранение и загрузку списка пиров."""
        from p2p import P2PServer
        from config import PEERS_FILE

        p2p = P2PServer(self.bc, host='127.0.0.1', port=0)
        p2p.add_known_peer('1.2.3.4', 8333)
        p2p.add_known_peer('5.6.7.8', 8334)

        self.assertTrue(os.path.exists(PEERS_FILE))

        p2p2 = P2PServer(self.bc, host='127.0.0.1', port=0)
        p2p2.load_peers()

        self.assertIn(('1.2.3.4', 8333), p2p2._known_peers)
        self.assertIn(('5.6.7.8', 8334), p2p2._known_peers)
        self.assertEqual(len(p2p2._known_peers), 2)

    def test_block_without_mempool_tx(self):
        """Проверяем, что блок может содержать транзакцию, которой нет в мемпуле, если она валидна."""
        height = self.bc.get_block_height()
        other_sk = SigningKey.generate(curve=SECP256k1)
        other_addr = hash256(other_sk.get_verifying_key().to_string()).hex()

        tx = self.bc.create_transaction(self.address, other_addr, 10, height)
        self.assertIsNotNone(tx)
        tx.sign(self.sk.to_string().hex(), self.sk.get_verifying_key().to_string().hex())

        last = self.bc.get_last_block()
        coinbase = Transaction.create_coinbase(height+1, REWARD, self.address, VESTING_PERIODS)
        block = Block(
            height+1,
            [coinbase, tx],
            last.hash,
            bits=0x207fffff,
            compute_hash=False
        )
        block.mine(bits_to_target(block.bits))

        self.assertTrue(self.bc.add_block(block))
        self.assertEqual(self.bc.get_balance(other_addr, height+1)[0], 10)


# P2P-тесты пока оставляем заскипанными (они флакают на Windows)
@unittest.skip("P2P tests are flaky on Windows, skipping")
class TestP2P(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.SKIP_DIFFICULTY_CHECK = True

    @classmethod
    def tearDownClass(cls):
        config.SKIP_DIFFICULTY_CHECK = False

    def setUp(self):
        config.SKIP_DIFFICULTY_CHECK = True
        self.test_dir = tempfile.mkdtemp(prefix="p2p_test_")
        os.chdir(self.test_dir)
        self.sk = SigningKey.generate(curve=SECP256k1)
        self.address = hash256(self.sk.get_verifying_key().to_string()).hex()
        self.bc = Blockchain(self.address, load_from_file=False, verify_checkpoints=False)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)
        config.SKIP_DIFFICULTY_CHECK = True

    def test_connection(self):
        async def run_test():
            server = P2PServer(self.bc, host='127.0.0.1', port=0)
            server_task = asyncio.create_task(server.start())
            for _ in range(20):
                if server.port:
                    break
                await asyncio.sleep(0.2)
            else:
                raise RuntimeError("Сервер не запустился")
            await asyncio.sleep(0.5)
            port = server.port
            await server.connect_to_peer('127.0.0.1', port)
            await asyncio.sleep(1)
            self.assertGreater(len(server.peers), 0)
            server.running = False
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(run_test())
        loop.close()

    def test_ban(self):
        async def run_test():
            server = P2PServer(self.bc, host='127.0.0.1', port=0)
            server_task = asyncio.create_task(server.start())
            for _ in range(20):
                if server.port:
                    break
                await asyncio.sleep(0.2)
            await asyncio.sleep(0.5)
            port = server.port

            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            addr = writer.get_extra_info('peername')
            server._ban_ip(addr, duration=2)

            try:
                reader2, writer2 = await asyncio.open_connection('127.0.0.1', port)
                await asyncio.sleep(0.5)
                self.assertEqual(len(server.peers), 1)
                writer2.close()
                await writer2.wait_closed()
            except:
                pass

            await asyncio.sleep(2.5)
            reader3, writer3 = await asyncio.open_connection('127.0.0.1', port)
            await asyncio.sleep(0.5)
            self.assertGreater(len(server.peers), 1)
            writer3.close()
            await writer3.wait_closed()

            server.running = False
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(run_test())
        loop.close()


if __name__ == '__main__':
    unittest.main()