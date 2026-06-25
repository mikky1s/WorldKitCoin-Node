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
        self.peers: Set[str] = set()
        self.known_blocks: Set[str] = set()
        self.known_txs: Set[str] = set()
        self.is_running = False
        self.pending_block_requests: Dict[str, asyncio.Future] = {}
        self.pending_tx_requests: Dict[str, asyncio.Future] = {}

    async def start(self):
        self.is_running = True
        server = await asyncio.start_server(
            self.handle_connection,
            host=self.host,
            port=self.port
        )
        print(f"🌐 P2P сервер запущен на {self.host}:{self.port}")

        for block in self.blockchain.chain:
            self.known_blocks.add(block.hash)

        asyncio.create_task(self.discover_peers())
        asyncio.create_task(self.broadcast_loop())

        async with server:
            await server.serve_forever()

    async def handle_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        addr = writer.get_extra_info('peername')
        peer_addr = f"{addr[0]}:{addr[1]}"
        print(f"🔗 P2P подключение от {peer_addr}")

        try:
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
        message = {
            "type": "version",
            "version": 1,
            "height": self.blockchain.get_block_height(),
            "peer": f"{self.host}:{self.port}"
        }
        await self.send_message(writer, message)

    async def process_message(self, message: dict, writer: asyncio.StreamWriter):
        msg_type = message.get("type")
        peer_addr = writer.get_extra_info('peername')
        peer_key = f"{peer_addr[0]}:{peer_addr[1]}"

        if msg_type == "version":
            self.peers.add(peer_key)
            await self.send_peers(writer)
            if message.get("height", 0) > self.blockchain.get_block_height():
                await self.send_getblocks(writer, self.blockchain.get_block_height() + 1)

        elif msg_type == "peers":
            for peer in message.get("peers", []):
                if peer != f"{self.host}:{self.port}":
                    self.peers.add(peer)
                    asyncio.create_task(self.connect_to_peer(peer))

        elif msg_type == "getblocks":
            start_height = message.get("start_height", 0)
            hashes = []
            for i in range(start_height, min(start_height + 500, len(self.blockchain.chain))):
                hashes.append(self.blockchain.chain[i].hash)
            response = {"type": "inv", "kind": "block", "hashes": hashes}
            await self.send_message(writer, response)

        elif msg_type == "inv":
            kind = message.get("kind")
            hashes = message.get("hashes", [])
            if kind == "block":
                needed = [h for h in hashes if h not in self.known_blocks]
                if needed:
                    await self.send_getdata(writer, "block", needed)
            elif kind == "tx":
                needed = [h for h in hashes if h not in self.known_txs]
                if needed:
                    await self.send_getdata(writer, "tx", needed)

        elif msg_type == "getdata":
            kind = message.get("kind")
            hashes = message.get("hashes", [])
            if kind == "block":
                for h in hashes:
                    block = next((b for b in self.blockchain.chain if b.hash == h), None)
                    if block:
                        response = {"type": "block_data", "block": block.to_dict()}
                        await self.send_message(writer, response)
            elif kind == "tx":
                for h in hashes:
                    tx = next((tx for tx in self.blockchain.mempool if tx.hash == h), None)
                    if tx:
                        response = {"type": "tx_data", "tx": tx.to_dict()}
                        await self.send_message(writer, response)

        elif msg_type == "block_data":
            block_data = message.get("block")
            if block_data:
                block = Block.from_dict(block_data)
                if block.hash not in self.known_blocks:
                    if self.blockchain.add_block(block):
                        self.known_blocks.add(block.hash)
                        await self.broadcast_inv("block", [block.hash])
                    else:
                        await self.send_getblocks(writer, self.blockchain.get_block_height() + 1)

        elif msg_type == "tx_data":
            tx_data = message.get("tx")
            if tx_data:
                tx = Transaction.from_dict(tx_data)
                if tx.hash not in self.known_txs:
                    if self.blockchain.add_to_mempool(tx):
                        self.known_txs.add(tx.hash)
                        await self.broadcast_inv("tx", [tx.hash])

        # совместимость со старым протоколом
        elif msg_type == "new_block":
            block_data = message.get("block")
            if block_data:
                block = Block.from_dict(block_data)
                if block.hash not in self.known_blocks:
                    if self.blockchain.add_block(block):
                        self.known_blocks.add(block.hash)
                        await self.broadcast_inv("block", [block.hash])
                    else:
                        await self.send_getblocks(writer, self.blockchain.get_block_height() + 1)

        elif msg_type == "new_tx":
            tx_data = message.get("tx")
            if tx_data:
                tx = Transaction.from_dict(tx_data)
                if tx.hash not in self.known_txs:
                    if self.blockchain.add_to_mempool(tx):
                        self.known_txs.add(tx.hash)
                        await self.broadcast_inv("tx", [tx.hash])

        elif msg_type == "get_blocks":
            start_height = message.get("start_height", 0)
            blocks = []
            for i in range(start_height, min(start_height + 50, len(self.blockchain.chain))):
                blocks.append(self.blockchain.chain[i].to_dict())
            response = {"type": "blocks", "blocks": blocks}
            await self.send_message(writer, response)

        elif msg_type == "blocks":
            for block_data in message.get("blocks", []):
                block = Block.from_dict(block_data)
                if block.hash not in self.known_blocks:
                    if self.blockchain.add_block(block):
                        self.known_blocks.add(block.hash)

    async def send_getblocks(self, writer: asyncio.StreamWriter, start_height: int):
        message = {"type": "getblocks", "start_height": start_height}
        await self.send_message(writer, message)

    async def send_getdata(self, writer: asyncio.StreamWriter, kind: str, hashes: List[str]):
        message = {"type": "getdata", "kind": kind, "hashes": hashes}
        await self.send_message(writer, message)

    async def broadcast_inv(self, kind: str, hashes: List[str]):
        message = {"type": "inv", "kind": kind, "hashes": hashes}
        for peer in list(self.peers):
            try:
                ip, port = peer.split(':')
                reader, writer = await asyncio.open_connection(ip, int(port))
                await self.send_message(writer, message)
                writer.close()
                await writer.wait_closed()
            except:
                pass

    async def broadcast_block(self, block: Block):
        await self.broadcast_inv("block", [block.hash])

    async def broadcast_tx(self, tx: Transaction):
        await self.broadcast_inv("tx", [tx.hash])

    async def send_peers(self, writer: asyncio.StreamWriter):
        peers_list = list(self.peers)[:10]
        message = {"type": "peers", "peers": peers_list}
        await self.send_message(writer, message)

    async def send_message(self, writer: asyncio.StreamWriter, data: dict):
        message = json.dumps(data) + "\n"
        writer.write(message.encode())
        await writer.drain()

    async def connect_to_peer(self, peer: str):
        if peer in self.peers or peer == f"{self.host}:{self.port}":
            return
        try:
            ip, port = peer.split(':')
            reader, writer = await asyncio.open_connection(ip, int(port))
            self.peers.add(peer)
            await self.send_version(writer)
            await self.handle_connection(reader, writer)
        except Exception as e:
            print(f"⚠️ Не удалось подключиться к {peer}: {e}")

    async def discover_peers(self):
        while self.is_running:
            await asyncio.sleep(30)
            if len(self.peers) < 5:
                pass

    async def broadcast_loop(self):
        while self.is_running:
            await asyncio.sleep(10)
            last_block = self.blockchain.get_last_block()
            if last_block.hash not in self.known_blocks:
                self.known_blocks.add(last_block.hash)
                await self.broadcast_block(last_block)

    def add_peer(self, peer: str):
        if peer not in self.peers and peer != f"{self.host}:{self.port}":
            self.peers.add(peer)
            asyncio.create_task(self.connect_to_peer(peer))

    def add_seed_peers(self, seeds: List[str]):
        for seed in seeds:
            self.add_peer(seed)

    def get_peers_list(self) -> List[str]:
        return list(self.peers)