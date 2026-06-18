from blockchain import Blockchain
from api import set_blockchain, run_api
from stratum_server import start_stratum
from p2p import P2PServer
import threading
import time
import asyncio
from ecdsa import SigningKey, SECP256k1

def generate_genesis_address():
    sk = SigningKey.generate(curve=SECP256k1)
    pubkey = sk.get_verifying_key().to_string()
    return pubkey.hex(), sk.to_string().hex()

if __name__ == "__main__":
    print("🚀 Starting WorldKitCoin FULL NETWORK (P2P + Stratum + API)...")
    genesis_addr, priv_key = generate_genesis_address()
    print(f"Genesis address: {genesis_addr}")
    print(f"Private key: {priv_key} (сохраните)")

    bc = Blockchain(genesis_addr, load_from_file=True)
    set_blockchain(bc)

    p2p = P2PServer(bc, host='0.0.0.0', port=8333)
    p2p_thread = threading.Thread(target=asyncio.run, args=(p2p.start(),), daemon=True)
    p2p_thread.start()
    print("🌐 P2P сервер запущен на порту 8333")

    stratum_thread = threading.Thread(target=start_stratum, args=(bc,), kwargs={'host': '0.0.0.0', 'port': 3333}, daemon=True)
    stratum_thread.start()
    print("⛏️ Stratum пул запущен на порту 3333")

    api_thread = threading.Thread(target=run_api, kwargs={'host': '0.0.0.0', 'port': 5000}, daemon=True)
    api_thread.start()
    print("🌐 API сервер запущен на порту 5000")

    print("\n✅ ПОЛНАЯ СЕТЬ ЗАПУЩЕНА!")
    print("   - P2P: ноды обмениваются блоками и транзакциями")
    print("   - Stratum: майнеры подключаются и майнят")
    print("   - API: управление и кошельки")
    print("\nPress Ctrl+C to stop.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")