from flask import Flask, request, jsonify, current_app
import json
import time
import struct
import threading
import logging
import os
import secrets
from functools import wraps
from blockchain import Blockchain
from transaction import Transaction
from config import (
    REWARD, VESTING_PERIODS, MAX_BLOCK_SIZE, MAX_SIGOPS,
    RPC_USER, RPC_PASSWORD_FILE, API_RATE_LIMIT
)
from utils import bits_to_target

logger = logging.getLogger(__name__)

app = Flask(__name__)

_templates = {}
_templates_lock = threading.Lock()
_TEMPLATE_EXPIRE = 600

_rate_limit = {}
_rate_limit_lock = threading.Lock()

def load_rpc_password() -> str:
    env_pass = os.environ.get('RPC_PASSWORD')
    if env_pass:
        logger.info("RPC-пароль загружен из переменной окружения")
        return env_pass
    if os.path.exists(RPC_PASSWORD_FILE):
        with open(RPC_PASSWORD_FILE, 'r') as f:
            return f.read().strip()
    password = secrets.token_urlsafe(32)
    with open(RPC_PASSWORD_FILE, 'w') as f:
        f.write(password)
    os.chmod(RPC_PASSWORD_FILE, 0o600)
    logger.warning(f"Сгенерирован новый RPC-пароль. Сохранён в {RPC_PASSWORD_FILE}")
    return password

RPC_PASSWORD = load_rpc_password()

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or auth.username != RPC_USER or auth.password != RPC_PASSWORD:
            return jsonify({"jsonrpc": "2.0", "error": {"code": -32000, "message": "Unauthorized"}, "id": None}), 401
        return f(*args, **kwargs)
    return decorated

def rate_limit(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        ip = request.remote_addr
        now = time.time()
        with _rate_limit_lock:
            timestamps = _rate_limit.get(ip, [])
            timestamps = [t for t in timestamps if t > now - 60]
            if len(timestamps) >= API_RATE_LIMIT:
                logger.warning(f"Rate limit exceeded for IP {ip}")
                return jsonify({"error": "Too many requests"}), 429
            timestamps.append(now)
            _rate_limit[ip] = timestamps
        return f(*args, **kwargs)
    return decorated

def init_api(app_instance, blockchain_instance):
    app_instance.config['blockchain'] = blockchain_instance

def get_blockchain():
    return current_app.config.get('blockchain')

def handle_getblocktemplate(params, msg_id):
    bc = get_blockchain()
    if bc is None:
        return {"jsonrpc": "2.0", "error": {"code": -32000, "message": "Blockchain not initialized"}, "id": msg_id}
    address = None
    if params and isinstance(params[0], dict):
        address = params[0].get('address')
    if not address:
        address = "miner"

    with _templates_lock:
        now = time.time()
        for tid in list(_templates.keys()):
            if now - _templates[tid]['created_at'] > _TEMPLATE_EXPIRE:
                del _templates[tid]

        last = bc.get_last_block()
        new_height = last.height + 1
        # Создаём coinbase с возможностью extra_nonce
        coinbase = Transaction.create_coinbase(
            new_height,
            REWARD,
            address,
            VESTING_PERIODS,
            extra_nonce1=None,
            extra_nonce2=None  # будет заполнено при майнинге
        )
        txs = bc.get_mempool_snapshot(100)
        block_txs = [coinbase] + txs

        from block import Block
        temp_block = Block(
            height=new_height,
            transactions=block_txs,
            prev_hash=last.hash,
            timestamp=int(time.time()),
            bits=bc.current_bits,
            compute_hash=False
        )
        merkle_root = temp_block._compute_merkle_root()
        template_id = secrets.token_hex(8)
        _templates[template_id] = {
            'height': new_height,
            'transactions': block_txs,
            'prev_hash': last.hash,
            'bits': bc.current_bits,
            'timestamp': temp_block.timestamp,
            'merkle_root': merkle_root,
            'address': address,
            'created_at': time.time()
        }

    target = bits_to_target(bc.current_bits)
    target_hex = f"{target:064x}"
    version = 0x20000000

    result = {
        "version": version,
        "prevhash": last.hash,
        "merkleroot": merkle_root,
        "timestamp": temp_block.timestamp,
        "bits": f"{bc.current_bits:08x}",
        "target": target_hex,
        "height": new_height,
        "coinbasevalue": REWARD,
        "transactions": [],
        "nonce_range": "00000000ffffffff",
        "curtime": temp_block.timestamp,
        "mintime": temp_block.timestamp - 3600,
        "maxtime": temp_block.timestamp + 3600,
        "maxsize": MAX_BLOCK_SIZE,
        "maxsigop": MAX_SIGOPS,
        "sigoplimit": MAX_SIGOPS,
        "sizelimit": MAX_BLOCK_SIZE,
        "coinbase_aux": {"flags": "062f503253482f"},
        "template_id": template_id
    }
    return {"id": msg_id, "jsonrpc": "2.0", "result": result}

def handle_submitblock(params, msg_id):
    bc = get_blockchain()
    if bc is None:
        return {"jsonrpc": "2.0", "error": {"code": -32000, "message": "Blockchain not initialized"}, "id": msg_id}
    if not params or not isinstance(params[0], dict):
        return {"jsonrpc": "2.0", "error": {"code": -32600, "message": "Invalid request"}, "id": msg_id}

    block_data = params[0]
    template_id = block_data.get('template_id')
    nonce = block_data.get('nonce')
    if nonce is None:
        return {"jsonrpc": "2.0", "error": {"code": -32600, "message": "Missing nonce"}, "id": msg_id}
    extra_nonce2 = block_data.get('extra_nonce2', 0)

    with _templates_lock:
        template = _templates.get(template_id)
        if template is None:
            return {"jsonrpc": "2.0", "error": {"code": -32000, "message": "Template not found or expired"}, "id": msg_id}
        if time.time() - template['created_at'] > _TEMPLATE_EXPIRE:
            del _templates[template_id]
            return {"jsonrpc": "2.0", "error": {"code": -32000, "message": "Template expired"}, "id": msg_id}

        from block import Block
        # Создаём блок с переданными параметрами
        block = Block(
            height=template['height'],
            transactions=template['transactions'][:],  # копия
            prev_hash=template['prev_hash'],
            timestamp=template['timestamp'],о
            nonce=nonce,
            bits=template['bits'],
            extra_nonce1=None,
            extra_nonce2=extra_nonce2,
            compute_hash=False
        )
        if block.transactions and block.transactions[0].is_coinbase():
            block.update_coinbase_extra_nonce(extra_nonce2)
        block.merkle_root = block._compute_merkle_root()
        block.hash = block.compute_hash()
        target = bits_to_target(template['bits'])
        if int(block.hash, 16) >= target:
            return {"jsonrpc": "2.0", "error": {"code": -1, "message": "Low difficulty"}, "id": msg_id}

        if bc.add_block(block):
            del _templates[template_id]
            return {"jsonrpc": "2.0", "result": "success", "id": msg_id}
        else:
            return {"jsonrpc": "2.0", "error": {"code": -1, "message": "Invalid block"}, "id": msg_id}

@app.route('/rpc', methods=['POST'])
@require_auth
def rpc_handler():
    data = request.get_json()
    if not data:
        return jsonify({"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}, "id": None}), 400
    method = data.get('method')
    params = data.get('params', [])
    msg_id = data.get('id')
    if method == 'getblocktemplate':
        return jsonify(handle_getblocktemplate(params, msg_id))
    elif method == 'submitblock':
        return jsonify(handle_submitblock(params, msg_id))
    else:
        return jsonify({"jsonrpc": "2.0", "error": {"code": -32601, "message": "Method not found"}, "id": msg_id}), 404

@app.route('/info', methods=['GET'])
@rate_limit
def get_info():
    bc = get_blockchain()
    if bc is None:
        return jsonify({'error': 'Blockchain not initialized'}), 500
    try:
        last = bc.get_last_block()
        info = {
            'height': bc.get_block_height(),
            'hash': last.hash,
            'timestamp': last.timestamp,
            'bits': f"{last.bits:08x}",
            'current_bits': f"{bc.current_bits:08x}",
            'difficulty_target': bc.difficulty_target,
            'total_supply': bc.get_total_supply(),
            'utxo_count': len(bc.utxo),
            'mempool_size': bc.get_mempool_size()
        }
        return jsonify(info)
    except Exception as e:
        logger.error(f"Ошибка в /info: {e}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/balance/<address>', methods=['GET'])
@rate_limit
def get_balance(address):
    bc = get_blockchain()
    if bc is None:
        return jsonify({'error': 'Blockchain not initialized'}), 500
    try:
        height = bc.get_block_height()
        available, locked = bc.get_balance(address, height)
        return jsonify({'address': address, 'available': available, 'locked': locked})
    except Exception as e:
        logger.error(f"Ошибка в /balance: {e}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/history/<address>', methods=['GET'])
@rate_limit
def get_history(address):
    bc = get_blockchain()
    if bc is None:
        return jsonify({'error': 'Blockchain not initialized'}), 500
    try:
        height = bc.get_block_height()
        history = bc.get_transaction_history(address, height)
        return jsonify(history)
    except Exception as e:
        logger.error(f"Ошибка в /history: {e}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/utxos/<address>', methods=['GET'])
@rate_limit
def get_utxos(address):
    bc = get_blockchain()
    if bc is None:
        return jsonify({'error': 'Blockchain not initialized'}), 500
    try:
        height = bc.get_block_height()
        utxos = bc.get_utxos_for_address(address, height)
        return jsonify(utxos)
    except Exception as e:
        logger.error(f"Ошибка в /utxos: {e}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/create_transaction', methods=['POST'])
@rate_limit
def create_transaction():
    bc = get_blockchain()
    if bc is None:
        return jsonify({'error': 'Blockchain not initialized'}), 500
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Missing JSON'}), 400
    from_addr = data.get('from_address')
    to_addr = data.get('to_address')
    amount = data.get('amount')
    priv_key = data.get('private_key')
    pubkey = data.get('pubkey')
    if not all([from_addr, to_addr, amount, priv_key]):
        return jsonify({'error': 'Missing required fields'}), 400
    try:
        amount = int(amount)
    except:
        return jsonify({'error': 'Invalid amount'}), 400

    height = bc.get_block_height()
    tx = bc.create_transaction(from_addr, to_addr, amount, height)
    if tx is None:
        return jsonify({'error': 'Insufficient funds or no UTXOs'}), 400

    if not pubkey:
        from ecdsa import SigningKey, SECP256k1
        try:
            sk = SigningKey.from_string(bytes.fromhex(priv_key), curve=SECP256k1)
            pubkey = sk.get_verifying_key().to_string().hex()
        except:
            return jsonify({'error': 'Invalid private key'}), 400

    tx.sign(priv_key, pubkey)
    return jsonify({'transaction': tx.to_dict(), 'tx_hash': tx.hash})

@app.route('/send_transaction', methods=['POST'])
@rate_limit
def send_transaction():
    bc = get_blockchain()
    if bc is None:
        return jsonify({'error': 'Blockchain not initialized'}), 500
    data = request.get_json()
    if not data or 'transaction' not in data:
        return jsonify({'error': 'Missing transaction'}), 400
    try:
        tx_dict = data['transaction']
        tx = Transaction.from_dict(tx_dict)
        if bc.add_to_mempool(tx):
            return jsonify({'status': 'ok', 'tx_hash': tx.hash})
        else:
            return jsonify({'status': 'error', 'message': 'Transaction invalid or already in mempool'}), 400
    except Exception as e:
        logger.error(f"Ошибка при отправке транзакции: {e}")
        return jsonify({'status': 'error', 'message': 'Invalid transaction data'}), 400

def run_api(host='0.0.0.0', port=5000, blockchain_instance=None, certfile=None, keyfile=None):
    if blockchain_instance:
        init_api(app, blockchain_instance)
    if certfile and keyfile:
        app.run(host=host, port=port, debug=False, ssl_context=(certfile, keyfile))
    else:
        app.run(host=host, port=port, debug=False)
