# p2p.py
import asyncio
import json
import time
import random
from typing import Set, Dict, List, Optional
from blockchain import Blockchain
from block import Block
from transaction import Transaction

class P2PServer:
    def __init__(self, blockchain: Blockchain, host: str = '0.0.0.0', port: int = 8333):
        self.blockchain = blockchain
        self.host = host
        self.port = port
        self.peers: Set[str] = set()  # адреса пиров в формате "ip:port"
        self.known_blocks: Set[str] = set()  # хэши известных блоков
        self.known_txs: Set[str] = set()  # хэши известных транзакций
        self.pending_blocks: Dict[str, Block] = {}  # блоки, которые ещё не добавлены
        self.is_running = False

    async def start(self):
        """Запускает P2P-сервер"""
        self.is_running = True
        server = await asyncio.start_server(
            self.handle_connection,
            host=self.host,
            port=self.port
        )
        print(f"🌐 P2P сервер запущен на {self.host}:{self.port}")

        # Загружаем известные блоки из цепочки
        for block in self.blockchain.chain:
            self.known_blocks.add(block.hash)

        # Запускаем фоновые задачи
        asyncio.create_task(self.discover_peers())
        asyncio.create_task(self.broadcast_loop())

        async with server:
            await server.serve_forever()

    async def handle_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Обрабатывает входящее соединение от другой ноды"""
        addr = writer.get_extra_info('peername')
        peer_addr = f"{addr[0]}:{addr[1]}"
        print(f"🔗 P2P подключение от {peer_addr}")

        try:
            # Отправляем приветствие с версией
            await self.send_version(writer)

            while True:
                data = await reader.readline()
                if not data:
                    break
                try:
                    message = json.loads(data.decode().strip())
                    await self.process_message(message, writer)
                except json.JSONDecodeError:
                    print(f"⚠️ Невалидный JSON от {peer_addr}")
        except ConnectionResetError:
            print(f"🔌 P2P отключился: {peer_addr}")
        finally:
            if peer_addr in self.peers:
                self.peers.remove(peer_addr)
            writer.close()
            await writer.wait_closed()

    async def send_version(self, writer: asyncio.StreamWriter):
        """Отправляет версию и запрашивает пиры"""
        message = {
            "type": "version",
            "version": 1,
            "height": self.blockchain.get_block_height(),
            "peer": f"{self.host}:{self.port}"
        }
        await self.send_message(writer, message)

    async def process_message(self, message: dict, writer: asyncio.StreamWriter):
        """Обрабатывает входящее сообщение от пира"""
        msg_type = message.get("type")
        peer_addr = writer.get_extra_info('peername')
        peer_key = f"{peer_addr[0]}:{peer_addr[1]}"

        if msg_type == "version":
            # Добавляем пира в список
            self.peers.add(peer_key)
            # Отвечаем списком пиров
            await self.send_peers(writer)
            # Проверяем, не отстаём ли мы по блокам
            if message.get("height", 0) > self.blockchain.get_block_height():
                # Запрашиваем недостающие блоки
                await self.request_blocks(writer, self.blockchain.get_block_height() + 1)

        elif msg_type == "peers":
            # Получаем список пиров от другого узла
            for peer in message.get("peers", []):
                if peer != f"{self.host}:{self.port}":
                    self.peers.add(peer)
                    # Подключаемся к новым пирам
                    asyncio.create_task(self.connect_to_peer(peer))

        elif msg_type == "new_block":
            # Получили новый блок от пира
            block_data = message.get("block")
            if block_data:
                block = Block.from_dict(block_data)
                if block.hash not in self.known_blocks:
                    # Добавляем блок
                    if self.blockchain.add_block(block):
                        self.known_blocks.add(block.hash)
                        # Рассылаем блок дальше
                        await self.broadcast_block(block)
                    else:
                        # Если блок не добавился, запрашиваем цепочку
                        await self.request_blocks(writer, self.blockchain.get_block_height() + 1)

        elif msg_type == "get_blocks":
            # Запрос на получение блоков с определённой высоты
            start_height = message.get("start_height", 0)
            blocks = []
            for i in range(start_height, min(start_height + 50, len(self.blockchain.chain))):
                blocks.append(self.blockchain.chain[i].to_dict())
            response = {"type": "blocks", "blocks": blocks}
            await self.send_message(writer, response)

        elif msg_type == "blocks":
            # Получили блоки от другого узла
            for block_data in message.get("blocks", []):
                block = Block.from_dict(block_data)
                if block.hash not in self.known_blocks:
                    if self.blockchain.add_block(block):
                        self.known_blocks.add(block.hash)
                    else:
                        print(f"⚠️ Не удалось добавить блок {block.height} от пира")

        elif msg_type == "new_tx":
            # Получили новую транзакцию
            tx_data = message.get("tx")
            if tx_data:
                tx = Transaction.from_dict(tx_data)
                if tx.hash not in self.known_txs:
                    if self.blockchain.add_to_mempool(tx):
                        self.known_txs.add(tx.hash)
                        # Рассылаем транзакцию дальше
                        await self.broadcast_tx(tx)

    async def send_peers(self, writer: asyncio.StreamWriter):
        """Отправляет список известных пиров"""
        peers_list = list(self.peers)[:10]  # максимум 10 пиров
        message = {"type": "peers", "peers": peers_list}
        await self.send_message(writer, message)

    async def request_blocks(self, writer: asyncio.StreamWriter, start_height: int):
        """Запрашивает блоки начиная с указанной высоты"""
        message = {"type": "get_blocks", "start_height": start_height}
        await self.send_message(writer, message)

    async def send_message(self, writer: asyncio.StreamWriter, data: dict):
        """Отправляет JSON-сообщение"""
        message = json.dumps(data) + "\n"
        writer.write(message.encode())
        await writer.drain()

    async def connect_to_peer(self, peer: str):
        """Подключается к другому пиру"""
        if peer in self.peers or peer == f"{self.host}:{self.port}":
            return
        try:
            ip, port = peer.split(':')
            reader, writer = await asyncio.open_connection(ip, int(port))
            self.peers.add(peer)
            # Отправляем версию новому пиру
            await self.send_version(writer)
            # Запускаем обработку сообщений от этого пира
            await self.handle_connection(reader, writer)
        except Exception as e:
            print(f"⚠️ Не удалось подключиться к {peer}: {e}")

    async def discover_peers(self):
        """Периодически ищет новых пиров"""
        while self.is_running:
            await asyncio.sleep(30)
            # Подключаемся к известным пирам, если их мало
            if len(self.peers) < 5:
                # В реальной сети здесь был бы DNS-запрос или запрос к seed-нодам
                pass

    async def broadcast_loop(self):
        """Периодически рассылает свои блоки и транзакции"""
        while self.is_running:
            await asyncio.sleep(10)
            # Рассылаем последний блок всем пирам
            last_block = self.blockchain.get_last_block()
            if last_block.hash not in self.known_blocks:
                self.known_blocks.add(last_block.hash)
                await self.broadcast_block(last_block)

    async def broadcast_block(self, block: Block):
        """Рассылает блок всем пирам"""
        message = {"type": "new_block", "block": block.to_dict()}
        for peer in list(self.peers):
            try:
                ip, port = peer.split(':')
                reader, writer = await asyncio.open_connection(ip, int(port))
                await self.send_message(writer, message)
                writer.close()
                await writer.wait_closed()
            except:
                pass

    async def broadcast_tx(self, tx: Transaction):
        """Рассылает транзакцию всем пирам"""
        message = {"type": "new_tx", "tx": tx.to_dict()}
        for peer in list(self.peers):
            try:
                ip, port = peer.split(':')
                reader, writer = await asyncio.open_connection(ip, int(port))
                await self.send_message(writer, message)
                writer.close()
                await writer.wait_closed()
            except:
                pass

    def add_peer(self, peer: str):
        """Добавляет пира вручную (например, из конфига)"""
        if peer not in self.peers and peer != f"{self.host}:{self.port}":
            self.peers.add(peer)
            asyncio.create_task(self.connect_to_peer(peer))

    def add_seed_peers(self, seeds: List[str]):
        """Добавляет начальные пиры (seed-ноды)"""
        for seed in seeds:
            self.add_peer(seed)