# 🚀 Quick Start Guide for WorldKitCoin

This guide will help you set up your own WorldKitCoin node, mine your first blocks, and send transactions in 5 minutes.

## Prerequisites

- Python 3.9 or higher
- Git (optional, but recommended)
- Basic knowledge of the command line

## Step 1: Clone and install

```bash
git clone https://github.com/yourusername/worldkitcoin.git
cd worldkitcoin
pip install -r requirements.txt
```
## Step 2: Start your node
```bash
python main.py
```
If everything is correct, you will see:
```text
✅ Node started!
   API: http://127.0.0.1:5000
   P2P: 127.0.0.1:8333
   Stratum: 127.0.0.1:3333
   Data: /path/to/data
Press Ctrl+C to stop.
```
## Step 3: Get your address and private key
Your address and private key are saved in data/wallet.txt. Open it:

```bash
cat data/wallet.txt
```
You'll see something like:

```text
Address: 1a2b3c4d...
Private key: 1234...
Public key: abcd...
```
⚠️ Important: Never share your private key! Keep it safe.

## Step 4: Check your balance
Open your browser and go to:

```text
http://127.0.0.1:5000/balance/1a2b3c4d...
```
Replace 1a2b3c4d... with your actual address.

You should see something like:

```json
{
  "address": "1a2b3c4d...",
  "available": 125,
  "locked": 0
}
```
Note: You have 125 WKC because the first block (genesis) gave you a reward.

## Step 5: Create a wallet (optional)
For easier management, create a wallet in the CLI:

```bash
python main.py new
```
This will add your address to data/wallets.json so you can use commands like:

```bash
python main.py balance <address>
python main.py history <address>
python main.py send <from> <to> <amount>
```
## Step 6: Send a transaction
Once you have mined a few blocks and have enough coins, you can send WKC to another address:

```bash
python main.py send 1a2b3c4d... 3c4d5e6f... 10
```
This will send 10 WKC from the first address to the second address.

## Step 7: Stop the node
Press Ctrl+C in the terminal where the node is running.

Next steps
+ 🔗 Connect to other nodes: python main.py --connect <ip>:8333

+ ⛏️ Enable mining on CPU: python main.py (mining is enabled by default)

+ 📡 Set up an ASIC miner: Connect to stratum+tcp://127.0.0.1:3333

+ 🛠️ Explore the API: See the API documentation in README.md

Troubleshooting
Error: Checkpoint mismatch at height 0
This happens when the blockchain data is from a different genesis block. Delete the data folder and restart:

```bash
rm -rf data   # Linux/Mac
rmdir /s data # Windows
python main.py
```
Error: ModuleNotFoundError
Make sure you installed all dependencies:

```bash
pip install -r requirements.txt
```
## Mining is too slow
By default, the node uses normal difficulty (bits=0x1d00ffff). For testing, you can lower the difficulty in blockchain.py (set genesis.bits = 0x1f00ffff) and delete the data folder. Blocks will then be mined in seconds.

Need help?
Check the README.md

Open an Issue

Join our Telegram/Discord (coming soon)
