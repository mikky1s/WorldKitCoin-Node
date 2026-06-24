import asyncio
import json
import time
import secrets
import logging
from typing import Dict, List, Optional
from block import Block
from transaction import Transaction, TxIn
from blockchain import Blockchain
from config import REWARD, VESTING_PERIODS
from utils import bits_to_target

logger = logging.getLogger(__name__)

STRATUM_PORT = 3333
EXTRA_NONCE1_SIZE = 8   # байт
EXTRA_NONCE2_SIZE = 8   # байт

class StratumServer:
    def __init__(self, blockchain: Blockchain, host: str = '0.0.0.0',
                 port: int = STRATUM_PORT, pool_address: str = None):
        self.bc = blockchain
        self.host = host
        self.port = port
        self.pool_address = pool_address
        self.running = False
        self.sessions: Dict[str, dict] = {}
        self._next_session_id = 0
        self._template_cache: Optional[dict] = None

    async def start(self):
        self.running = True
        server = await asyncio.start_server(self._handle_client, self.host, self.port)
        logger.info(f"Stratum server listening on {self.host}:{self.port}")
        async with server:
            await server.serve_forever()

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        addr = writer.get_extra_info('peername')
        logger.info(f"Stratum client connected: {addr}")
        session_id = f"{addr[0]}:{addr[1]}-{self._next_session_id}"
        self._next_session_id += 1
        self.sessions[session_id] = {
            'reader': reader,
            'writer': writer,
            'subscribed': False,
            'authorized': False,
            'extra_nonce1': None,
        }
        try:
            while self.running:
                line = await reader.readline()
                if not line:
                    break
                line = line.decode().strip()
                if not line:
                    continue
                await self._process_message(session_id, line)
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            logger.info(f"Stratum client disconnected: {addr}")
            if session_id in self.sessions:
                del self.sessions[session_id]
            writer.close()
            await writer.wait_closed()

    async def _process_message(self, session_id: str, raw: str):
        try:
            msg = json.loads(raw)
            method = msg.get('method')
            params = msg.get('params', [])
            msg_id = msg.get('id')
            session = self.sessions.get(session_id)
            if not session:
                return

            if method == 'mining.subscribe':
                result = await self._handle_subscribe(session_id, params)
            elif method == 'mining.authorize':
                result = await self._handle_authorize(session_id, params)
            elif method == 'mining.submit':
                result = await self._handle_submit(session_id, params)
            else:
                await self._send_response(session_id, {"error": "Method not found", "id": msg_id})
                return

            if isinstance(result, dict):
                if 'id' not in result:
                    result['id'] = msg_id
                await self._send_response(session_id, result)

        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON from {session_id}: {raw}")
        except Exception as e:
            logger.error(f"Error processing message: {e}")

    async def _send_response(self, session_id: str, response: dict):
        session = self.sessions.get(session_id)
        if not session:
            return
        writer = session['writer']
        payload = json.dumps(response) + '\n'
        writer.write(payload.encode())
        await writer.drain()

    async def _send_notification(self, session_id: str, method: str, params: list):
        notification = {"method": method, "params": params, "id": None}
        await self._send_response(session_id, notification)

    async def _handle_subscribe(self, session_id: str, params: list):
        session = self.sessions[session_id]
        extra_nonce1 = secrets.token_hex(EXTRA_NONCE1_SIZE)
        session['extra_nonce1'] = extra_nonce1
        session['subscribed'] = True

        subscription = ["mining.notify", secrets.token_hex(8)]
        result = [subscription, extra_nonce1, EXTRA_NONCE2_SIZE]
        await self._send_notify(session_id)
        return {"result": result, "id": None}

    async def _handle_authorize(self, session_id: str, params: list):
        if len(params) < 1:
            return {"result": False, "error": "Missing worker name"}
        worker = params[0]
        session = self.sessions[session_id]
        session['authorized'] = True
        session['worker'] = worker
        logger.info(f"Worker {worker} authorized")
        return {"result": True}

    async def _handle_submit(self, session_id: str, params: list):
        if len(params) < 5:
            return {"result": False, "error": "Invalid params"}

        worker, job_id, extra_nonce2_hex, ntime_hex, nonce_hex = params[:5]
        session = self.sessions.get(session_id)
        if not session or not session.get('authorized'):
            return {"result": False, "error": "Not authorized"}

        if not self._template_cache or job_id != self._template_cache.get('job_id'):
            return {"result": False, "error": "Job not found"}

        try:
            extra_nonce2 = bytes.fromhex(extra_nonce2_hex)
            if len(extra_nonce2) != EXTRA_NONCE2_SIZE:
                return {"result": False, "error": "Invalid extra_nonce2 size"}
            ntime = int(ntime_hex, 16)
            nonce = int(nonce_hex, 16)
        except ValueError:
            return {"result": False, "error": "Invalid hex values"}

        template = self._template_cache
        coinbase_tx = template['coinbase_tx']

        # Создаём новую coinbase с extra_nonce2 в подписи
        new_coinbase = Transaction(
            inputs=[TxIn(
                prev_tx_hash='0'*64,
                prev_output_index=0xffffffff,
                signature=coinbase_tx.inputs[0].signature + extra_nonce2
            )],
            outputs=coinbase_tx.outputs[:],
            locktime=coinbase_tx.locktime
        )
        new_coinbase._hash = None

        # Используем extra_nonce из шаблона (не extra_nonce1)
        block = Block(
            height=template['height'],
            transactions=[new_coinbase] + template['txs'],
            prev_hash=template['prev_hash'],
            timestamp=ntime,
            nonce=nonce,
            bits=template['bits'],
            extra_nonce=0,  # не используется
            compute_hash=False
        )
        block.merkle_root = block._compute_merkle_root()
        block.hash = block.compute_hash()

        target = bits_to_target(block.bits)
        if int(block.hash, 16) >= target:
            logger.warning(f"Submit with low difficulty: {block.hash[:16]}...")
            return {"result": False, "error": "Low difficulty"}

        if self.bc.add_block(block):
            logger.info(f"Block {block.height} mined by {worker}!")
            self._template_cache = self._prepare_template()
            await self._broadcast_new_template()
            return {"result": True}
        else:
            logger.warning(f"Block {block.height} rejected")
            return {"result": False, "error": "Invalid block"}

    def _prepare_template(self) -> dict:
        last_block = self.bc.get_last_block()
        new_height = last_block.height + 1
        mempool_txs = self.bc.get_mempool_snapshot(100)

        if self.pool_address is None:
            self.pool_address = self.bc.chain[0].transactions[0].outputs[0].address

        coinbase_tx = Transaction.create_coinbase(
            new_height, REWARD, self.pool_address, VESTING_PERIODS
        )
        coinbase_tx.inputs[0].signature = b''  # будет дополнено позже

        all_txs = [coinbase_tx] + mempool_txs
        temp_block = Block(
            height=new_height,
            transactions=all_txs,
            prev_hash=last_block.hash,
            timestamp=int(time.time()),
            bits=self.bc.current_bits,
            extra_nonce=0,
            compute_hash=False
        )
        merkle_root = temp_block._compute_merkle_root()

        template = {
            'job_id': secrets.token_hex(8),
            'height': new_height,
            'prev_hash': last_block.hash,
            'bits': self.bc.current_bits,
            'timestamp': temp_block.timestamp,
            'coinbase_tx': coinbase_tx,
            'txs': mempool_txs,
            'merkle_root': merkle_root,
        }
        return template

    async def _send_notify(self, session_id: str):
        if not self._template_cache:
            self._template_cache = self._prepare_template()
        template = self._template_cache
        session = self.sessions.get(session_id)
        if not session:
            return

        extra_nonce1 = session['extra_nonce1']
        coinbase_tx = template['coinbase_tx']

        coinbase1_tx = Transaction(
            inputs=[TxIn(
                prev_tx_hash='0'*64,
                prev_output_index=0xffffffff,
                signature=bytes.fromhex(extra_nonce1)
            )],
            outputs=coinbase_tx.outputs[:],
            locktime=coinbase_tx.locktime
        )
        coinbase1_hex = json.dumps(coinbase1_tx.to_dict()).encode().hex()
        coinbase2_hex = ''  # extra_nonce2 будет добавлен в конце подписи

        params = [
            template['job_id'],
            template['prev_hash'],
            coinbase1_hex,
            coinbase2_hex,
            [],  # merkle_branch
            '00000002',  # version
            f"{template['bits']:08x}",
            f"{template['timestamp']:08x}",
            False  # clean_jobs
        ]
        await self._send_notification(session_id, 'mining.notify', params)

    async def _broadcast_new_template(self):
        self._template_cache = self._prepare_template()
        for sid, sess in self.sessions.items():
            if sess.get('authorized') and sess.get('subscribed'):
                await self._send_notify(sid)
