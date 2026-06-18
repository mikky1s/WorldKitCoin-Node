from flask import Flask, request, jsonify
from blockchain import Blockchain
from miner import SimpleMiner
from ecdsa import SigningKey, SECP256k1

app = Flask(__name__)
blockchain = None

def set_blockchain(bc: Blockchain):
    global blockchain
    blockchain = bc

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
    height = blockchain.get_block_height()
    available, locked = blockchain.get_balance(address, height)
    return jsonify({
        "address": address,
        "available": available,
        "locked": locked,
        "total": available + locked
    }), 200

@app.route('/block/<int:height>', methods=['GET'])
def get_block(height):
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

@app.route('/mempool', methods=['GET'])
def get_mempool():
    if not blockchain:
        return jsonify({"error": "Blockchain not initialized"}), 500
    tx_list = [tx.hash for tx in blockchain.mempool]
    return jsonify({"mempool": tx_list}), 200

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

    # Проверка длины адресов (130 символов для несжатого ключа)
    if len(from_addr) != 130 or len(to_pubkey) != 130:
        return jsonify({"error": "Invalid address format (must be 130 hex chars)"}), 400

    # Проверяем, что из приватного ключа получается адрес from_addr
    try:
        sk = SigningKey.from_string(bytes.fromhex(private_key), curve=SECP256k1)
        pubkey = sk.get_verifying_key().to_string()
        if pubkey.hex() != from_addr:
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

@app.route('/mine', methods=['POST'])
def mine_block():
    if not blockchain:
        return jsonify({"error": "Blockchain not initialized"}), 500
    data = request.get_json()
    print(f"🔍 /mine received data: {data}")
    if not data:
        return jsonify({"error": "Missing JSON body"}), 400
    address = data.get('address')
    if not address:
        return jsonify({"error": "Missing 'address' field"}), 400
    # Исправлено: 130 символов
    if len(address) != 130:
        return jsonify({"error": "Invalid address format (must be 130 hex chars)"}), 400
    try:
        bytes.fromhex(address)
    except:
        return jsonify({"error": "Invalid hex address"}), 400

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

@app.route('/history/<address>', methods=['GET'])
def get_history(address):
    if not blockchain:
        return jsonify({"error": "Blockchain not initialized"}), 500
    height = blockchain.get_block_height()
    history = blockchain.get_transaction_history(address, height)
    return jsonify({"address": address, "history": history}), 200

def run_api(host='0.0.0.0', port=5000):
    app.run(host=host, port=port, debug=False, threaded=True)