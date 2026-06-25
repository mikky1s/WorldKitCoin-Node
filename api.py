from flask import Flask, request, jsonify, render_template
from blockchain import Blockchain
from miner import SimpleMiner
from ecdsa import SigningKey, SECP256k1
import time
import json

app = Flask(__name__)
blockchain = None
p2p_server = None
stratum_server = None

def set_blockchain(bc: Blockchain):
    global blockchain
    blockchain = bc

def set_p2p(p2p):
    global p2p_server
    p2p_server = p2p

def set_stratum(srv):
    global stratum_server
    stratum_server = srv

# === Веб-интерфейс эксплорера ===
@app.route('/')
def index():
    return render_template('index.html')

# === Основные API-эндпоинты ===
@app.route('/info', methods=['GET'])
def get_info():
    if not blockchain:
        return jsonify({"error": "Blockchain not initialized"}), 500
    info = {
        "height": blockchain.get_block_height(),
        "difficulty_target": blockchain.difficulty_target,
        "current_bits": hex(blockchain.current_bits),
        "utxo_count": len(blockchain.utxo),
        "mempool_size": len(blockchain.mempool),
        "total_supply": blockchain.total_supply,
        "chain_tip": blockchain.get_last_block().hash
    }
    return jsonify(info), 200

@app.route('/balance/<address>', methods=['GET'])
def get_balance(address):
    if not blockchain:
        return jsonify({"error": "Blockchain not initialized"}), 500
    if len(address) not in (128, 130) or not all(c in '0123456789abcdefABCDEF' for c in address):
        return jsonify({"error": "Invalid address format"}), 400
    height = blockchain.get_block_height()
    available, locked = blockchain.get_balance(address, height)
    return jsonify({
        "address": address,
        "available": available,
        "locked": locked,
        "total": available + locked
    }), 200

@app.route('/block/<int:height>', methods=['GET'])
def get_block_by_height(height):
    if not blockchain:
        return jsonify({"error": "Blockchain not initialized"}), 500
    if height < 0 or height >= len(blockchain.chain):
        return jsonify({"error": "Block height out of range"}), 404
    block = blockchain.chain[height]
    block_data = {
        "height": block.height,
        "hash": block.hash,
        "prev_hash": block.prev_hash,
        "timestamp": block.timestamp,
        "nonce": block.nonce,
        "bits": hex(block.bits),
        "merkle_root": block.merkle_root,
        "transactions": [tx.hash for tx in block.transactions]
    }
    return jsonify(block_data), 200

@app.route('/block/hash/<block_hash>', methods=['GET'])
def get_block_by_hash(block_hash):
    if not blockchain:
        return jsonify({"error": "Blockchain not initialized"}), 500
    for block in blockchain.chain:
        if block.hash == block_hash:
            block_data = {
                "height": block.height,
                "hash": block.hash,
                "prev_hash": block.prev_hash,
                "timestamp": block.timestamp,
                "nonce": block.nonce,
                "bits": hex(block.bits),
                "merkle_root": block.merkle_root,
                "transactions": [tx.hash for tx in block.transactions]
            }
            return jsonify(block_data), 200
    return jsonify({"error": "Block not found"}), 404

@app.route('/blocks', methods=['GET'])
def get_blocks():
    if not blockchain:
        return jsonify({"error": "Blockchain not initialized"}), 500
    limit = request.args.get('limit', default=20, type=int)
    offset = request.args.get('offset', default=0, type=int)
    chain = blockchain.chain
    total = len(chain)
    start = max(0, total - offset - limit)
    end = total - offset
    blocks = []
    for b in chain[start:end]:
        blocks.append({
            'height': b.height,
            'hash': b.hash,
            'timestamp': b.timestamp,
            'nonce': b.nonce,
            'tx_count': len(b.transactions)
        })
    return jsonify({
        'blocks': blocks,
        'total': total,
        'limit': limit,
        'offset': offset
    })

@app.route('/latest-blocks', methods=['GET'])
def get_latest_blocks():
    if not blockchain:
        return jsonify({"error": "Blockchain not initialized"}), 500
    limit = request.args.get('limit', default=10, type=int)
    chain = blockchain.chain
    total = len(chain)
    start = max(0, total - limit)
    blocks = []
    for b in chain[start:]:
        blocks.append({
            'height': b.height,
            'hash': b.hash,
            'timestamp': b.timestamp,
            'nonce': b.nonce,
            'tx_count': len(b.transactions)
        })
    return jsonify({'blocks': blocks})

@app.route('/hashrate', methods=['GET'])
def get_hashrate():
    if not blockchain:
        return jsonify({"error": "Blockchain not initialized"}), 500
    chain = blockchain.chain
    if len(chain) < 2:
        return jsonify({'hashrate': 0, 'avg_block_time': 0})
    num_blocks = min(20, len(chain)-1)
    last = chain[-1]
    first = chain[-num_blocks] if num_blocks > 0 else chain[0]
    time_diff = last.timestamp - first.timestamp
    if time_diff <= 0:
        time_diff = 1
    avg_time = time_diff / num_blocks
    target = blockchain.difficulty_target
    hashrate = (target * 2**32) / avg_time
    return jsonify({
        'hashrate': hashrate,
        'avg_block_time': avg_time,
        'difficulty': target
    })

@app.route('/network-stats', methods=['GET'])
def network_stats():
    if not blockchain:
        return jsonify({"error": "Blockchain not initialized"}), 500
    chain = blockchain.chain
    height = len(chain) - 1
    if len(chain) < 2:
        hashrate = 0
        avg_time = 0
    else:
        num_blocks = min(20, len(chain)-1)
        last = chain[-1]
        first = chain[-num_blocks] if num_blocks > 0 else chain[0]
        time_diff = last.timestamp - first.timestamp
        if time_diff <= 0:
            time_diff = 1
        avg_time = time_diff / num_blocks
        target = blockchain.difficulty_target
        hashrate = (target * 2**32) / avg_time

    peers = p2p_server.get_peers_list() if p2p_server else []
    miners = stratum_server.get_miner_count() if stratum_server else 0

    return jsonify({
        'height': height,
        'total_supply': blockchain.total_supply,
        'difficulty_target': blockchain.difficulty_target,
        'current_bits': hex(blockchain.current_bits),
        'utxo_count': len(blockchain.utxo),
        'mempool_size': len(blockchain.mempool),
        'hashrate': hashrate,
        'avg_block_time': avg_time,
        'peer_count': len(peers),
        'peers': peers,
        'miners': miners
    })

@app.route('/mempool', methods=['GET'])
def get_mempool():
    if not blockchain:
        return jsonify({"error": "Blockchain not initialized"}), 500
    tx_list = [tx.hash for tx in blockchain.mempool]
    return jsonify({"mempool": tx_list}), 200

@app.route('/transaction/<tx_hash>', methods=['GET'])
def get_transaction(tx_hash):
    if not blockchain:
        return jsonify({"error": "Blockchain not initialized"}), 500
    tx = blockchain.get_transaction_by_hash(tx_hash)
    if not tx:
        return jsonify({"error": "Transaction not found"}), 404
    block_height = blockchain.get_transaction_block_height(tx_hash)
    confirmations = None
    if block_height is not None:
        confirmations = blockchain.get_block_height() - block_height + 1
    tx_data = {
        'hash': tx.hash,
        'inputs': [{'prev_tx_hash': inp.prev_tx_hash, 'prev_output_index': inp.prev_output_index,
                    'signature': inp.signature.hex() if inp.signature else None} for inp in tx.inputs],
        'outputs': [{'amount': out.amount, 'pubkey_hash': out.pubkey_hash, 'lock_until': out.lock_until} for out in tx.outputs],
        'locktime': tx.locktime,
        'is_coinbase': tx.is_coinbase(),
        'block_height': block_height,
        'confirmations': confirmations
    }
    return jsonify(tx_data), 200

@app.route('/address/<address>/utxo', methods=['GET'])
def get_address_utxo(address):
    if not blockchain:
        return jsonify({"error": "Blockchain not initialized"}), 500
    if len(address) not in (128, 130) or not all(c in '0123456789abcdefABCDEF' for c in address):
        return jsonify({"error": "Invalid address format"}), 400
    height = blockchain.get_block_height()
    utxos = blockchain.get_utxo_for_address(address, height)
    return jsonify({'address': address, 'utxos': utxos}), 200

@app.route('/address/<address>/transactions', methods=['GET'])
def get_address_transactions(address):
    if not blockchain:
        return jsonify({"error": "Blockchain not initialized"}), 500
    if len(address) not in (128, 130) or not all(c in '0123456789abcdefABCDEF' for c in address):
        return jsonify({"error": "Invalid address format"}), 400
    height = blockchain.get_block_height()
    history = blockchain.get_transaction_history(address, height)
    return jsonify({'address': address, 'transactions': history}), 200

@app.route('/history/<address>', methods=['GET'])
def get_history(address):
    return get_address_transactions(address)

# === Отправка транзакции (старый метод) ===
@app.route('/transaction', methods=['POST'])
def send_transaction():
    if not blockchain:
        return jsonify({"error": "Blockchain not initialized"}), 500
    data = request.get_json()
    if not data:
        return jsonify({"error": "Missing JSON body"}), 400

    from_addr = data.get('from')
    to_pubkey = data.get('to_pubkey')
    amount = data.get('amount')
    private_key = data.get('private_key')

    if not all([from_addr, to_pubkey, amount is not None, private_key]):
        return jsonify({"error": "Missing fields: 'from', 'to_pubkey', 'amount', 'private_key'"}), 400

    if len(from_addr) not in (128, 130) or len(to_pubkey) not in (128, 130):
        return jsonify({"error": "Invalid address format (must be 128 or 130 hex chars)"}), 400

    try:
        sk = SigningKey.from_string(bytes.fromhex(private_key), curve=SECP256k1)
        pubkey = sk.get_verifying_key().to_string().hex()
        if pubkey != from_addr:
            return jsonify({"error": "Private key does not match 'from' address"}), 400
    except:
        return jsonify({"error": "Invalid private key"}), 400

    height = blockchain.get_block_height()
    tx = blockchain.create_transaction(from_addr, to_pubkey, amount, height)
    if tx is None:
        return jsonify({"error": "Insufficient funds or invalid UTXO"}), 400

    tx.sign(private_key)
    if blockchain.add_to_mempool(tx):
        return jsonify({"status": "success", "tx_hash": tx.hash}), 200
    else:
        return jsonify({"error": "Transaction already in mempool or invalid"}), 400

@app.route('/broadcast-tx', methods=['POST'])
def broadcast_tx():
    return send_transaction()

@app.route('/mine', methods=['POST'])
def mine_block():
    if not blockchain:
        return jsonify({"error": "Blockchain not initialized"}), 500
    data = request.get_json()
    if not data:
        return jsonify({"error": "Missing JSON body"}), 400
    address = data.get('address')
    if not address:
        return jsonify({"error": "Missing 'address' field"}), 400
    if len(address) not in (128, 130) or not all(c in '0123456789abcdefABCDEF' for c in address):
        return jsonify({"error": "Invalid address format (must be 128 or 130 hex chars)"}), 400

    miner = SimpleMiner(address, blockchain)
    block = miner.mine_block()
    if block:
        return jsonify({
            "status": "success",
            "block_height": block.height,
            "block_hash": block.hash,
            "reward": block.transactions[0].outputs[0].amount
        }), 200
    else:
        return jsonify({"error": "Mining failed"}), 500

@app.route('/peers', methods=['GET'])
def get_peers():
    if not p2p_server:
        return jsonify({"error": "P2P not initialized"}), 500
    return jsonify({"peers": p2p_server.get_peers_list()}), 200

@app.route('/miners', methods=['GET'])
def get_miners():
    if not stratum_server:
        return jsonify({"error": "Stratum not initialized"}), 500
    return jsonify({
        'count': stratum_server.get_miner_count(),
        'addresses': stratum_server.get_miner_addresses()
    })

# === НОВЫЕ ЭНДПОИНТЫ ДЛЯ КОШЕЛЬКА ===
@app.route('/wallet/create', methods=['POST'])
def wallet_create():
    """Создаёт новый кошелёк: генерирует пару ключей и возвращает их"""
    sk = SigningKey.generate(curve=SECP256k1)
    pubkey = sk.get_verifying_key().to_string().hex()
    privkey = sk.to_string().hex()
    return jsonify({
        "address": pubkey,
        "private_key": privkey,
        "message": "Сохраните приватный ключ в безопасном месте!"
    }), 201

@app.route('/wallet/import', methods=['POST'])
def wallet_import():
    """Импортирует кошелёк по приватному ключу, возвращает адрес"""
    data = request.get_json()
    if not data or 'private_key' not in data:
        return jsonify({"error": "Missing 'private_key' field"}), 400
    privkey = data['private_key']
    try:
        sk = SigningKey.from_string(bytes.fromhex(privkey), curve=SECP256k1)
        pubkey = sk.get_verifying_key().to_string().hex()
        return jsonify({
            "address": pubkey,
            "private_key": privkey,
            "message": "Кошелёк импортирован успешно"
        })
    except:
        return jsonify({"error": "Invalid private key"}), 400

@app.route('/wallet/<address>/balance', methods=['GET'])
def wallet_balance(address):
    return get_balance(address)

@app.route('/wallet/<address>/send', methods=['POST'])
def wallet_send(address):
    if not blockchain:
        return jsonify({"error": "Blockchain not initialized"}), 500
    data = request.get_json()
    if not data:
        return jsonify({"error": "Missing JSON body"}), 400

    to_address = data.get('to')
    amount = data.get('amount')
    privkey = data.get('private_key')

    if not all([to_address, amount is not None, privkey]):
        return jsonify({"error": "Missing fields: 'to', 'amount', 'private_key'"}), 400

    if len(address) not in (128, 130) or len(to_address) not in (128, 130):
        return jsonify({"error": "Invalid address format (must be 128 or 130 hex chars)"}), 400

    try:
        sk = SigningKey.from_string(bytes.fromhex(privkey), curve=SECP256k1)
        pubkey = sk.get_verifying_key().to_string().hex()
        if pubkey != address:
            return jsonify({"error": "Private key does not match address"}), 400
    except:
        return jsonify({"error": "Invalid private key"}), 400

    height = blockchain.get_block_height()
    tx = blockchain.create_transaction(address, to_address, amount, height)
    if tx is None:
        return jsonify({"error": "Insufficient funds or invalid UTXO"}), 400

    tx.sign(privkey)
    if blockchain.add_to_mempool(tx):
        return jsonify({"status": "success", "tx_hash": tx.hash}), 200
    else:
        return jsonify({"error": "Transaction already in mempool or invalid"}), 400

def run_api(host='0.0.0.0', port=5000):
    app.run(host=host, port=port, debug=False, threaded=True)
