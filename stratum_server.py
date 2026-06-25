import asyncio
import json
import time
import struct
import hashlib
from typing import Dict
from blockchain import Blockchain
from block import Block
from transaction import Transaction
from config import REWARD, VESTING_PERIODS
from utils import bits_to_target

class StratumServer:
    def __init__(self, blockchain: Blockchain, host: str = '0.0.0.0', port: int = 3333):
        self.blockchain = blockchain
        self.host = host
        self.port = port
        self.clients: Dict[asyncio.StreamWriter, dict] = {}
        self.job_id_counter = 0
        self.extra_nonce1 = 0

    def get_miner_count(self) -> int:
        """Возвращает количество активных майнеров, подключенных к пулу"""
        return len(self.clients)

    def get_miner_addresses(self) -> list:
        """Возвращает список адресов (username) активных майнеров"""
        return [client.get('username', 'unknown') for client in self.clients.values() if client.get('authorized')]

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        client_addr = writer.get_extra_info('peername')
        print(f"🔌 Майнер подключился: {client_addr}")
        self.clients[writer] = {'subscribed': False, 'authorized': False}

        try:
            while True:
                data = await reader.readline()
                if not data:
                    break
                try:
                    message = json.loads(data.decode().strip())
                    await self.process_message(message, writer)
                except json.JSONDecodeError:
                    print(f"⚠️ Невалидный JSON от {client_addr}: {data}")
                except Exception as e:
                    print(f"❌ Ошибка обработки: {e}")
        except ConnectionResetError:
            print(f"🔌 Майнер отключился: {client_addr}")
        finally:
            if writer in self.clients:
                del self.clients[writer]
            writer.close()
            await writer.wait_closed()

    async def process_message(self, message: dict, writer: asyncio.StreamWriter):
        method = message.get('method')
        params = message.get('params', [])
        msg_id = message.get('id')
        client_addr = writer.get_extra_info('peername')

        if method == 'mining.subscribe':
            await self.handle_subscribe(writer, msg_id)
        elif method == 'mining.authorize':
            await self.handle_authorize(writer, msg_id, params)
        elif method == 'mining.submit':
            await self.handle_submit(writer, msg_id, params)
        else:
            await self.send_response(writer, msg_id, None, error={"code": -1, "message": "Method not found"})

    async def handle_subscribe(self, writer: asyncio.StreamWriter, msg_id):
        self.extra_nonce1 += 1
        extra_nonce1_hex = f"{self.extra_nonce1:08x}"
        extra_nonce2_size = 4

        self.clients[writer]['extra_nonce1'] = extra_nonce1_hex
        self.clients[writer]['subscribed'] = True

        result = [
            ["mining.notify", "1.0"],
            extra_nonce1_hex,
            extra_nonce2_size
        ]
        await self.send_response(writer, msg_id, result)
        print(f"✅ Подписка подтверждена для {writer.get_extra_info('peername')}")

        await self.send_difficulty(writer)
        await self.send_job(writer)

    async def handle_authorize(self, writer: asyncio.StreamWriter, msg_id, params):
        username = params[0] if params else "miner"
        self.clients[writer]['username'] = username
        self.clients[writer]['authorized'] = True
        await self.send_response(writer, msg_id, True)
        print(f"✅ Авторизация успешна для {username}")

    async def send_difficulty(self, writer: asyncio.StreamWriter):
        target = self.blockchain.difficulty_target
        difficulty = 1  # упрощённо
        response = {"id": None, "method": "mining.set_difficulty", "params": [difficulty]}
        await self.send_raw(writer, response)

    async def send_job(self, writer: asyncio.StreamWriter):
        last_block = self.blockchain.get_last_block()
        new_height = last_block.height + 1

        address = self.clients[writer].get('username', 'miner')
        coinbase = Transaction.create_coinbase(new_height, REWARD, address, VESTING_PERIODS)

        bits = self.blockchain.current_bits

        block_template = Block(
            height=new_height,
            transactions=[coinbase],
            prev_hash=last_block.hash,
            timestamp=int(time.time()),
            bits=bits,
            compute_hash=False
        )
        block_template.merkle_root = block_template._compute_merkle_root()

        version = 0x20000000
        job_id = str(self.job_id_counter)
        self.job_id_counter += 1

        target = bits_to_target(bits)

        self.clients[writer]['job_data'] = {
            'job_id': job_id,
            'height': new_height,
            'coinbase_tx': coinbase,
            'prev_hash': block_template.prev_hash,
            'merkle_root': block_template.merkle_root,
            'bits': bits,
            'target': target,
            'timestamp': block_template.timestamp
        }

        notify_params = [
            job_id,
            block_template.prev_hash,
            block_template.merkle_root,
            f"{version:08x}",
            f"{block_template.timestamp:08x}",
            f"{bits:08x}",
            "00000000"
        ]
        await self.send_notify(writer, notify_params)
        print(f"⛏️ Отправлено задание {job_id} с bits={hex(bits)}")

    async def handle_submit(self, writer: asyncio.StreamWriter, msg_id, params):
        if not self.clients[writer].get('authorized'):
            await self.send_response(writer, msg_id, False, error={"code": -1, "message": "Not authorized"})
            return

        if len(params) < 5:
            await self.send_response(writer, msg_id, False, error={"code": -1, "message": "Invalid params"})
            return

        job_id = params[1]
        ntime_hex = params[3]
        nonce_hex = params[4]

        job_data = self.clients[writer].get('job_data')
        if not job_data or job_data['job_id'] != job_id:
            await self.send_response(writer, msg_id, False, error={"code": -1, "message": "Job not found"})
            return

        ntime_int = int(ntime_hex, 16) if ntime_hex != "00000000" else int(time.time())
        if abs(ntime_int - int(time.time())) > 3600:
            ntime_int = int(time.time())

        version = 0x20000000
        prev_hash_bytes = bytes.fromhex(job_data['prev_hash'])[::-1]
        merkle_root_bytes = bytes.fromhex(job_data['merkle_root'])[::-1]
        ntime_bytes = struct.pack('<I', ntime_int)
        bits_int = job_data['bits']
        bits_bytes = struct.pack('<I', bits_int)
        nonce_int = int(nonce_hex, 16)
        nonce_bytes = struct.pack('<I', nonce_int)

        header = (
            struct.pack('<I', version) +
            prev_hash_bytes +
            merkle_root_bytes +
            ntime_bytes +
            bits_bytes +
            nonce_bytes
        )
        hash_bytes = hashlib.sha256(hashlib.sha256(header).digest()).digest()
        block_hash_hex = hash_bytes[::-1].hex()

        target = job_data['target']
        if int(block_hash_hex, 16) >= target:
            await self.send_response(writer, msg_id, False, error={"code": -1, "message": "Low difficulty"})
            return

        block = Block(
            height=job_data['height'],
            transactions=[job_data['coinbase_tx']],
            prev_hash=job_data['prev_hash'],
            timestamp=ntime_int,
            nonce=nonce_int,
            bits=bits_int,
            compute_hash=False
        )
        block.merkle_root = job_data['merkle_root']
        block.hash = block_hash_hex

        if self.blockchain.add_block(block):
            print(f"✅ Блок {block.height} добавлен! Майнер: {params[0]}")
            await self.broadcast_job()
            await self.send_response(writer, msg_id, True)
        else:
            print(f"❌ Блок не добавлен: {block.height}")
            await self.send_response(writer, msg_id, False, error={"code": -1, "message": "Invalid block"})

    async def broadcast_job(self):
        for writer in list(self.clients.keys()):
            try:
                await self.send_job(writer)
            except:
                pass

    async def send_notify(self, writer: asyncio.StreamWriter, params):
        response = {"id": None, "method": "mining.notify", "params": params}
        await self.send_raw(writer, response)

    async def send_response(self, writer: asyncio.StreamWriter, msg_id, result, error=None):
        response = {"id": msg_id, "result": result, "error": error}
        await self.send_raw(writer, response)

    async def send_raw(self, writer: asyncio.StreamWriter, data: dict):
        message = json.dumps(data) + "\n"
        writer.write(message.encode())
        await writer.drain()

    async def run(self):
        server = await asyncio.start_server(self.handle_client, host=self.host, port=self.port)
        print(f"⛏️ Stratum-пул запущен на {self.host}:{self.port}")
        async with server:
            await server.serve_forever()

def start_stratum(blockchain: Blockchain, host: str = '0.0.0.0', port: int = 3333):
    server = StratumServer(blockchain, host, port)
    asyncio.run(server.run())
