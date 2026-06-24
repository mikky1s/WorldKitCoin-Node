# ⛓️ WorldKitCoin – Full-fledged Blockchain in Python

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/mikky1s/worldkitcoin-node/pulls)
[![Tests](https://img.shields.io/badge/tests-92%25-green)](https://github.com/mikky1s/worldkitcoin/actions)

**WorldKitCoin (WKC)** is an implementation of the blockchain in pure Python from scratch. The project includes a full set of components: **P2P network**, **UTXO model**, **difficulty-controlled mining**, **vesting rewards**, **JSON-RPC API**, **Stratum server for ASIC-miners** and **tests**.

---

## 🚀 Features

- 🔗 **Blockchain with Proof‑of‑Work** – SHA‑256, difficulty is adjusted every 10 blocks.
- 💰 **UTXO-model** – like in Bitcoin, with support for vesting (lock‑until).
- ⛏️ **Built-in miner** – CPU‑mining directly from the node (can be disabled).
- 🌐 **P2P network** – block and transaction exchange, IP ban, frequency limit.
- 📡 **Stratum-server** – ASIC miner support (port 3333).
- 🔐 **Cryptography** – ECDSA (secp256k1) for transaction signatures.
- 📦 **Storage** – compressed binary format (msgpack + zlib) with checksum.
- 🧪 **Tests** – >90% coverage (unittest).
- 🖥️ **REST API** – JSON‑RPC, password-protected, with rate‑limiting.

---

📂 Project structure
```.
├── api.py # Flask‑application (JSON‑RPC, /info, /balance etc.)
├── block.py # Block class (hash, merkle‑root, mining)
├── blockchain.py # Main chain logic, UTXO, mempool, forks
├── config.py # All settings (reward, difficulty, ports, etc.)
├── main.py # Entry point – node start, API, P2P, Stratum
├── miner.py # Simple CPU miner
├── p2p.py # P2P server and client (asyncio)
├── stratum_server.py # Stratum server for ASIC
├── transaction.py # TxIn, TxOut, Transaction classes
├── utils.py # Hashes, serialization, working with bits/target
├── tests.py # A set of unit tests (more than 20)
├── wallet.py          # Wallet management (create, import, send)
├── p2p_crypto.py      # SSL certificates and handshake
├── requirements.txt # Dependencies
├── rpc_password.txt # Password for RPC (generated automatically)
└── README.md
```

---

## ⚙️ Installation

### 1. Clone the repository
```bash
git clone https://github.com/yourusername/worldkitcoin.git
cd worldkitcoin
```
## 2. Install dependencies
```bash
pip install -r requirements.txt
```
## 3. (Optional) Configure environment variables
+ RPC_PASSWORD – password for API access (if not set, it is generated and saved in rpc_password.txt).

## 🏁 Launch
```bash
python main.py --address 1234...abcd --pool-address 1234...abcd --stratum-port 3333 --no-checkpoints
```
If no address is specified, a new key will be generated (the private key is not logged).
## ⚙️ Command-line options

| Parameter | Description | Type | Default |
|----------|----------|-----|--------------|
| `--address` | Address for receiving rewards (if not specified, a new key is generated) | `str` | – |
| `--pool-address` | Pool address for mining (used by the Stratum server) | `str` | – |
| `--api-port` | Port for REST API (Flask) | `int` | `5000` |
| `--p2p-port` | Port for P2P network | `int` | `8333` |
| `--stratum-port` | Port for Stratum server (ASIC connection) | `int` | `3333` |
| `--data-dir` | Directory for storing blockchain data | `str` | `data` |
| `--no-mine` | Disable built-in CPU mining | `flag` | `False` (mining is enabled) |
| `--connect` | Connect to the specified peer at startup (format `host:port`) | `str` | – |
| `--certfile` | Path to the SSL certificate for the HTTPS API | `str` | – |
| `--keyfile` | | Path to the SSL private key (in conjunction with `--certfile`) | `str` | – |
| `--no-checkpoints` | Disable checkpoint verification (for development) | `flag` | `False` |
| `--p2p-no-ssl` | Disable SSL for P2P (not recommended) | `flag` | `False` (SSL enabled) |

## 💳 CLI Wallet

The node includes a built‑in wallet manager for working with addresses and transactions directly from the terminal.

| Command | Description |
|---------|-------------|
| `python main.py new` | Generate a new wallet (address + private key) |
| `python main.py import <private_key>` | Import wallet by private key |
| `python main.py list` | Show all saved wallets |
| `python main.py balance <address>` | Show balance (available / locked) |
| `python main.py send <from> <to> <amount>` | Send WKC to another address |
| `python main.py history <address>` | Show transaction history |

**Example:**
```bash
python main.py new
# ✅ New wallet created:
#    Address: 1a2b...
#    Private key: 1234...
#    Public key: abcd...

python main.py balance 1a2b...
# 💰 Balance of address 1a2b...
#    Available: 125 WKC
#    Locked: 0 WKC

python main.py send 1a2b... 3c4d... 10
# ✅ Transaction sent!
#    Tx hash: 9f8e...
```
Important: Private keys are stored in data/wallets.json. Never share this file!

## 📡 API (JSON‑RPC)
RPC is available at http://localhost:5000/rpc (or HTTPS).
Authentication: Basic Auth (login: admin, password from rpc_password.txt).

| Method | Parameters | Description |
|-------|-----------|----------|
| `getblocktemplate` | `address` (string, optional) | Returns a block template for mining. If address is not specified, the default address is used |
| `submitblock` | `{ "template_id": "...", "nonce": ... }` | Sends the found block to the network. You must pass the template_id (from getblocktemplate) and the found nonce. |
Note: All responses are returned in the standard JSON‑RPC 2.0 format.

## getblocktemplate request example
```bash
curl -u admin:$(cat rpc_password.txt) -X POST -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"getblocktemplate","params":[{"address":"1a2b..."}],"id":1}' \
  http://localhost:5000/rpc
```
## Response example
```json
{
  "jsonrpc": "2.0",
  "result": {
    "version": 536870912,
    "prevhash": "8f0f8d4eed5cc7c2d69d189e5fdf1044bbfd1b1253967d07a76f319a0c883450",
    "merkleroot": "b5a7...",
    "timestamp": 1718273945,
    "bits": "1d00ffff",
    "target": "00000000ffff0000000000000000000000000000000000000000000000000000",
    "height": 124,
    "coinbasevalue": 125,
    "template_id": "a1b2c3d4"
  },
  "id": 1
}
```
## Example of a submitblock request
```bash
curl -u admin:$(cat rpc_password.txt) -X POST -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"submitblock","params":[{"template_id":"a1b2c3d4","nonce":987654}],"id":2}' \
  http://localhost:5000/rpc
```
## Example of a successful response
```json
{
  "jsonrpc": "2.0",
  "result": "success",
  "id": 2
}
```
## Error example
```json
{
  "jsonrpc": "2.0",
  "error": {
    "code": -1,
    "message": "Low difficulty"
  },
  "id": 2
}
```
## 📋 REST endpoints (without authentication)
These endpoints are available via HTTP (or HTTPS) and do not require authorization, but have a rate‑limit (100 requests per minute from one IP).

| URL | Method | Description |
|-----|--------|-------------|
| /info | GET | General information about the node (height, complexity) |
| /balance/<address> | GET | Balance of the specified address (available/locked) |
| /history/<address> | GET | Transaction history at |
| /utxos/<address> | GET | The UTXO list for the address |
| /create_transaction | POST | Create and sign a transaction |
| /send_transaction | POST | Send the transaction to the mempool |

Important: The private key for /create_transaction is passed in the request body. Use this endpoint only in a secure environment (such as locally or with HTTPS).

## ⛏️ Mining
##CPU mining
The built-in miner starts automatically (unless --no-mine is specified).
It uses the transaction pool from the mempool and tries to find a block for the specified address.

## ASIC mining (Stratum)
Start a node with --stratum-port 3333 and configure your ASIC miner to the address:

```text
stratum+tcp://<IP_node>:3333
```
The miner must support the Stratum v1. protocol.
After authorization, it will receive tasks (mining.notify) and send solutions (mining.submit).

## 🔗 P2P network
The node automatically connects to other nodes via port 8333.
The list of known peers is stored in known_peers.json.
Supported:

+ Block and transaction exchange

+ Chain synchronization

+ IP ban when limits are exceeded

+ Periodic reconnect

+ To manually connect to a peer:

```bash
python main.py --connect 192.168.1.10:8333
```

## 🧪 Testing
Run all tests:

```bash
python -m unittest tests.py
```
Coverage includes:

+ Serialization/deserialization

+ Signatures and verification

+ Adding blocks

+ Mempool and UTXO operations

+API (partially)

+ P2P (skipped on Windows, but working)

## Technical details
The consensus algorithm
+ Proof‑of‑Work – SHA‑256d, the target is recalculated every 10 blocks.

+ The maximum change in difficulty is 4 times.

+ The block time is 150 seconds (configurable).

## Reward and vesting
+ The reward for the block is 125 WKC.

+ Distributed into 5 parts with lock periods: [0, 4032, 8064, 12096, 16128] blocks.

+ Total supply is 21,000,000 WKC.

## Keeping
+ The blockchain is saved in a binary file blockchain_data.bin using msgpack + zlib + sha256 checksum.

## 🤝 Contribution
The project is open for improvements!
 If you find a bug or want to add a new feature, create an Issue or Pull Request.

Follow these guidelines:

+ Use PEP‑8 code style

+ Document new features

+ Write tests

## 📄 License
The project is distributed under the MIT license. Details in the LICENSE file.

## 🙏 Thanks
 Inspired by Bitcoin and its open ecosystem.

 Uses libraries: ecdsa, cryptography, bip_utils, msgpack, flask, requests.

## If the project is useful to you, give it a ⭐ on GitHub!
 
