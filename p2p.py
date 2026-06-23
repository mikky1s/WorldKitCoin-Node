import asyncio
import json
import struct
import time
import logging
import os
from collections import deque
from typing import Set, Dict, List
from blockchain import Blockchain
from block import Block
from transaction import Transaction
from config import P2P_BAN_TIME, MAX_MESSAGE_SIZE, MAX_PEERS_PER_IP, PEERS_FILE

PROTOCOL_VERSION = 70001
USER_AGENT = "/WorldKitCoin:0.1/"
MAX_PEERS = 100
MESSAGE_LIMIT = 100
SYNC_INTERVAL = 30
RECONNECT_INTERVAL = 60

logger = logging.getLogger(__name__)

class P2PServer:
    def __init__(self, blockchain: Blockchain, host: str = '0.0.0.0', port: int = 8333):
        self.blockchain = blockchain
        self.host = host
        self.port = port
        self.peers: Set[asyncio.StreamWriter] = set()
        self.running = False
        self.peer_limits: Dict[asyncio.StreamWriter, deque] = {}
        self._banned_ips: Dict[str, float] = {}
        self._peer_count_by_ip: Dict[str, int] = {}
        self._sync_task = None
        self._reconnect_task = None
        self._known_peers: List[tuple] = []   # список (host, port)

    def _is_banned(self, addr) -> bool:
        ip = addr[0] if addr else ''
        if ip in self._banned_ips:
            if time.time() < self._banned_ips[ip]:
                return True
            else:
                del self._banned_ips[ip]
        return False

    def _ban_ip(self, addr, duration: int = P2P_BAN_TIME):
        ip = addr[0] if addr else ''
        self._banned_ips[ip] = time.time() + duration
        logger.warning(f"IP {ip} забанен на {duration} секунд")

    def _can_add_peer(self, addr) -> bool:
        ip = addr[0] if addr else ''
        current = self._peer_count_by_ip.get(ip, 0)
        if current >= MAX_PEERS_PER_IP:
            logger.warning(f"Превышен лимит соединений с IP {ip} ({current} >= {MAX_PEERS_PER_IP})")
            return False
        return True

    def _inc_peer_count(self, addr):
        ip = addr[0] if addr else ''
        self._peer_count_by_ip[ip] = self._peer_count_by_ip.get(ip, 0) + 1

    def _dec_peer_count(self, addr):
        ip = addr[0] if addr else ''
        if ip in self._peer_count_by_ip:
            self._peer_count_by_ip[ip] -= 1
            if self._peer_count_by_ip[ip] <= 0:
                del self._peer_count_by_ip[ip]

    def add_known_peer(self, host: str, port: int):
        """Добавляет пира в список известных и сохраняет в файл."""
        if (host, port) not in self._known_peers:
            self._known_peers.append((host, port))
            self._save_peers()

    def _save_peers(self):
        """Сохраняет список известных пиров в файл."""
        try:
            with open(PEERS_FILE, 'w') as f:
                json.dump(self._known_peers, f)
        except Exception as e:
            logger.error(f"Не удалось сохранить пиров: {e}")

    def load_peers(self):
        """Загружает список известных пиров из файла."""
        if os.path.exists(PEERS_FILE):
            try:
                with open(PEERS_FILE, 'r') as f:
                    data = json.load(f)
                    # Преобразуем списки в кортежи
                    self._known_peers = [tuple(p) for p in data]
                logger.info(f"Загружено {len(self._known_peers)} пиров из {PEERS_FILE}")
            except Exception as e:
                logger.error(f"Не удалось загрузить пиров: {e}")
                self._known_peers = []

    async def start(self):
        self.running = True
        server = await asyncio.start_server(self.handle_client, host=self.host, port=self.port)
        self.port = server.sockets[0].getsockname()[1]
        logger.info(f"P2P-сервер запущен на {self.host}:{self.port}")
        self._sync_task = asyncio.create_task(self._periodic_sync())
        self._reconnect_task = asyncio.create_task(self._periodic_reconnect())
        async with server:
            await server.serve_forever()

    async def _periodic_sync(self):
        while self.running:
            await asyncio.sleep(SYNC_INTERVAL)
            if not self.peers:
                continue
            peer = next(iter(self.peers))
            try:
                await self._send_getblocks(peer)
            except Exception as e:
                logger.error(f"Ошибка синхронизации: {e}")

    async def _periodic_reconnect(self):
        while self.running:
            await asyncio.sleep(RECONNECT_INTERVAL)
            if len(self.peers) >= MAX_PEERS:
                continue
            import random
            random.shuffle(self._known_peers)
            for host, port in self._known_peers:
                if len(self.peers) >= MAX_PEERS:
                    break
                if (host, port) == (self.host, self.port):
                    continue
                already = False
                for writer in self.peers:
                    addr = writer.get_extra_info('peername')
                    if addr and addr[0] == host and addr[1] == port:
                        already = True
                        break
                if already:
                    continue
                logger.info(f"Попытка переподключения к {host}:{port}")
                await self.connect_to_peer(host, port)

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        addr = writer.get_extra_info('peername')
        if self._is_banned(addr):
            writer.close()
            await writer.wait_closed()
            return
        if len(self.peers) >= MAX_PEERS:
            logger.warning(f"Достигнут лимит пиров, отклоняем {addr}")
            writer.close()
            await writer.wait_closed()
            return
        if not self._can_add_peer(addr):
            writer.close()
            await writer.wait_closed()
            return

        logger.info(f"Новый пир подключился: {addr}")
        self.peers.add(writer)
        self.peer_limits[writer] = deque(maxlen=MESSAGE_LIMIT * 2)
        self._inc_peer_count(addr)

        try:
            await self._send_version(writer)

            while True:
                try:
                    length_data = await asyncio.wait_for(reader.readexactly(4), timeout=30.0)
                except asyncio.TimeoutError:
                    logger.warning(f"Таймаут чтения от {addr}")
                    break
                if not length_data:
                    break

                length = struct.unpack('>I', length_data)[0]
                if length > MAX_MESSAGE_SIZE:
                    logger.warning(f"Слишком большое сообщение от {addr}, бан")
                    self._ban_ip(addr)
                    break

                data = await reader.readexactly(length)

                now = time.time()
                timestamps = self.peer_limits[writer]
                timestamps.append(now)
                while timestamps and timestamps[0] < now - 1.0:
                    timestamps.popleft()
                if len(timestamps) > MESSAGE_LIMIT:
                    logger.warning(f"Превышен лимит сообщений от {addr}, бан")
                    self._ban_ip(addr)
                    break

                asyncio.create_task(self._process_message(data, writer, addr))

        except (ConnectionResetError, asyncio.IncompleteReadError) as e:
            logger.info(f"Пир отключился: {addr} ({e})")
        except Exception as e:
            logger.error(f"Ошибка при обработке клиента {addr}: {e}")
        finally:
            self.peers.discard(writer)
            self.peer_limits.pop(writer, None)
            self._dec_peer_count(addr)
            writer.close()
            await writer.wait_closed()

    async def _process_message(self, raw_data: bytes, writer: asyncio.StreamWriter, addr: tuple):
        try:
            if len(raw_data) > MAX_MESSAGE_SIZE:
                self._ban_ip(addr)
                return

            message = json.loads(raw_data.decode())
            cmd = message.get('cmd')
            payload = message.get('payload', {})

            if not isinstance(cmd, str):
                return

            if cmd == 'version':
                await self._handle_version(writer, payload)
            elif cmd == 'verack':
                logger.debug("Получен verack")
            elif cmd == 'ping':
                await self._send_pong(writer)
            elif cmd == 'pong':
                pass
            elif cmd == 'getblocks':
                await self._handle_getblocks(writer, payload)
            elif cmd == 'inv':
                await self._handle_inv(writer, payload)
            elif cmd == 'getdata':
                await self._handle_getdata(writer, payload)
            elif cmd == 'block':
                block = Block.from_dict(payload)
                if self.blockchain.add_block(block):
                    logger.info(f"Получен и добавлен блок {block.height} от пира")
                    await self._broadcast_block(block)
                else:
                    logger.warning(f"Блок {block.height} отклонён")
            elif cmd == 'tx':
                tx = Transaction.from_dict(payload)
                if self.blockchain.add_to_mempool(tx):
                    logger.info("Получена новая транзакция от пира")
                    await self._broadcast_transaction(tx)
            else:
                logger.warning(f"Неизвестная команда: {cmd}")

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(f"Некорректное сообщение от {addr}: {e}")
            self._ban_ip(addr)
        except Exception as e:
            logger.error(f"Ошибка обработки сообщения: {e}")

    async def _handle_version(self, writer: asyncio.StreamWriter, payload: dict):
        if payload.get('version') != PROTOCOL_VERSION:
            logger.warning(f"Несовместимая версия протокола: {payload.get('version')}, отключаем")
            writer.close()
            await writer.wait_closed()
            return
        addr_from = payload.get('addr_from')
        if addr_from:
            try:
                host, port = addr_from.split(':')
                self.add_known_peer(host, int(port))
            except:
                pass
        await self._send_version(writer)
        await self._send_verack(writer)
        await self._send_getblocks(writer)

    async def _handle_getblocks(self, writer: asyncio.StreamWriter, payload: dict):
        locator = payload.get('locator', [])
        if not locator:
            start = max(0, len(self.blockchain.chain) - 500)
            block_hashes = [b.hash for b in self.blockchain.chain[start:]]
            inv = {"cmd": "inv", "payload": {"type": "block", "items": block_hashes}}
            await self._send_message(writer, inv)
            return

        last_common = None
        for block_hash in locator:
            block = self.blockchain.get_block_by_hash(block_hash)
            if block:
                last_common = block
                break

        if not last_common:
            genesis = self.blockchain.chain[0]
            await self._send_block(writer, genesis.hash)
            return

        start_idx = self.blockchain.chain.index(last_common) + 1
        end_idx = min(start_idx + 500, len(self.blockchain.chain))
        block_hashes = [b.hash for b in self.blockchain.chain[start_idx:end_idx]]
        if block_hashes:
            inv = {"cmd": "inv", "payload": {"type": "block", "items": block_hashes}}
            await self._send_message(writer, inv)

    async def _handle_inv(self, writer: asyncio.StreamWriter, payload: dict):
        items = payload.get('items', [])
        for block_hash in items[:100]:
            if not self.blockchain.get_block_by_hash(block_hash):
                getdata = {"cmd": "getdata", "payload": {"type": "block", "hash": block_hash}}
                await self._send_message(writer, getdata)

    async def _handle_getdata(self, writer: asyncio.StreamWriter, payload: dict):
        data_type = payload.get('type')
        data_hash = payload.get('hash')
        if data_type == 'block':
            await self._send_block(writer, data_hash)
        elif data_type == 'tx':
            pass

    async def _send_version(self, writer: asyncio.StreamWriter):
        msg = {
            "cmd": "version",
            "payload": {
                "version": PROTOCOL_VERSION,
                "height": self.blockchain.get_block_height(),
                "user_agent": USER_AGENT,
                "addr_from": f"{self.host}:{self.port}"
            }
        }
        await self._send_message(writer, msg)

    async def _send_verack(self, writer: asyncio.StreamWriter):
        await self._send_message(writer, {"cmd": "verack", "payload": {}})

    async def _send_pong(self, writer: asyncio.StreamWriter):
        await self._send_message(writer, {"cmd": "pong", "payload": {}})

    async def _send_getblocks(self, writer: asyncio.StreamWriter):
        locator = []
        chain_len = len(self.blockchain.chain)
        for i in range(min(10, chain_len)):
            locator.append(self.blockchain.chain[-(i+1)].hash)
        msg = {"cmd": "getblocks", "payload": {"locator": locator}}
        await self._send_message(writer, msg)

    async def _send_block(self, writer: asyncio.StreamWriter, block_hash: str):
        block = self.blockchain.get_block_by_hash(block_hash)
        if block:
            msg = {"cmd": "block", "payload": block.to_dict()}
            await self._send_message(writer, msg)

    async def _broadcast_block(self, block: Block):
        msg = {"cmd": "block", "payload": block.to_dict()}
        for peer in list(self.peers):
            try:
                await self._send_message(peer, msg)
            except Exception:
                pass

    async def _broadcast_transaction(self, tx):
        msg = {"cmd": "tx", "payload": tx.to_dict()}
        for peer in list(self.peers):
            try:
                await self._send_message(peer, msg)
            except Exception:
                pass

    async def _send_message(self, writer: asyncio.StreamWriter, message: dict):
        data = json.dumps(message).encode()
        writer.write(struct.pack('>I', len(data)) + data)
        await writer.drain()

    async def connect_to_peer(self, host: str, port: int):
        try:
            reader, writer = await asyncio.open_connection(host, port)
            addr = (host, port)
            if self._is_banned(addr):
                writer.close()
                await writer.wait_closed()
                return
            if len(self.peers) >= MAX_PEERS:
                writer.close()
                await writer.wait_closed()
                return
            if not self._can_add_peer(addr):
                writer.close()
                await writer.wait_closed()
                return

            self.peers.add(writer)
            self.peer_limits[writer] = deque(maxlen=MESSAGE_LIMIT * 2)
            self._inc_peer_count(addr)
            self.add_known_peer(host, port)
            logger.info(f"Подключились к пиру: {host}:{port}")
            await self._send_version(writer)
            asyncio.create_task(self._handle_peer_messages(reader, writer, addr))
        except Exception as e:
            logger.error(f"Не удалось подключиться к пиру {host}:{port} - {e}")

    async def _handle_peer_messages(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, addr: tuple):
        try:
            while True:
                try:
                    length_data = await asyncio.wait_for(reader.readexactly(4), timeout=30.0)
                except asyncio.TimeoutError:
                    logger.warning(f"Таймаут чтения от {addr}")
                    break
                if not length_data:
                    break
                length = struct.unpack('>I', length_data)[0]
                if length > MAX_MESSAGE_SIZE:
                    break
                data = await reader.readexactly(length)

                now = time.time()
                timestamps = self.peer_limits[writer]
                timestamps.append(now)
                while timestamps and timestamps[0] < now - 1.0:
                    timestamps.popleft()
                if len(timestamps) > MESSAGE_LIMIT:
                    logger.warning(f"Превышен лимит сообщений от {addr}, отключаем")
                    break

                asyncio.create_task(self._process_message(data, writer, addr))
        except Exception:
            pass
        finally:
            self.peers.discard(writer)
            self.peer_limits.pop(writer, None)
            self._dec_peer_count(addr)
            writer.close()
            await writer.wait_closed()