import argparse
import threading
import time
import os
import asyncio
import logging
from blockchain import Blockchain
from api import run_api, init_api, app
from block import Block
from utils import bits_to_target, hash256
from config import REWARD, VESTING_PERIODS, NETWORK_NAME, LOG_FILE, LOG_LEVEL
from transaction import Transaction
from p2p import P2PServer
from stratum_server import StratumServer
from ecdsa import SigningKey, SECP256k1

# Настройка логирования
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def generate_keypair():
    sk = SigningKey.generate(curve=SECP256k1)
    pubkey = sk.get_verifying_key().to_string()
    address = hash256(pubkey).hex()
    return address, sk.to_string().hex(), pubkey.hex()

def mine_loop(bc, address, priv_key, pubkey):
    logger.info(f"Майнинг запущен (адрес: {address[:8]}...)")
    while True:
        try:
            last_block = bc.get_last_block()
            new_height = last_block.height + 1
            txs = bc.get_mempool_snapshot(100)
            coinbase = Transaction.create_coinbase(new_height, REWARD, address, VESTING_PERIODS)
            block_txs = [coinbase] + txs
            block = Block(
                height=new_height,
                transactions=block_txs,
                prev_hash=last_block.hash,
                timestamp=int(time.time()),
                bits=bc.current_bits,
                compute_hash=False
            )
            block.merkle_root = block._compute_merkle_root()
            block.hash = block.compute_hash()
            target = bits_to_target(bc.current_bits)
            block.mine(target)
            if bc.add_block(block):
                logger.info(f"Блок {block.height} добавлен!")
            else:
                logger.warning(f"Блок {block.height} отклонён!")
        except Exception as e:
            logger.error(f"Ошибка в майнинге: {e}")
        time.sleep(1)

def mempool_cleaner(bc):
    """Фоновый поток для периодической очистки мемпула."""
    while True:
        time.sleep(60)
        try:
            bc._clean_mempool()
        except Exception as e:
            logger.error(f"Ошибка очистки мемпула: {e}")

def run_p2p_loop(p2p):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(p2p.start())

def connect_to_peer(p2p, host, port):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(p2p.connect_to_peer(host, port))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=f"{NETWORK_NAME} Full Node")
    parser.add_argument('--address', help='Адрес для награды (если не указан, будет сгенерирован)')
    parser.add_argument('--api-port', type=int, default=5000, help='Порт для API')
    parser.add_argument('--p2p-port', type=int, default=8333, help='Порт для P2P')
    parser.add_argument('--stratum-port', type=int, default=3333, help='Порт для Stratum-сервера')
    parser.add_argument('--pool-address', help='Адрес пула для наград майнеров (если не указан, используется --address)')
    parser.add_argument('--data-dir', default='data', help='Папка для данных')
    parser.add_argument('--no-mine', action='store_true', help='Отключить майнинг')
    parser.add_argument('--connect', help='Подключиться к пиру (host:port)')
    parser.add_argument('--certfile', help='Путь к SSL-сертификату (для HTTPS)')
    parser.add_argument('--keyfile', help='Путь к приватному ключу SSL')
    args = parser.parse_args()

    os.makedirs(args.data_dir, exist_ok=True)
    os.chdir(args.data_dir)

    logger.info(f"Запуск ноды (P2P: {args.p2p_port}, API: {args.api_port}, Stratum: {args.stratum_port})")

    if args.address:
        genesis_addr = args.address
        priv_key = None
        pubkey = None
    else:
        genesis_addr, priv_key, pubkey = generate_keypair()
        logger.info(f"Сгенерирован новый адрес: {genesis_addr}")
        # Никогда не логируем приватный ключ!
        # logger.info(f"Приватный ключ: {priv_key} (сохраните!)")
        # logger.info(f"Публичный ключ: {pubkey}")

    # Адрес пула для Stratum (если не указан, используем тот же адрес)
    pool_address = args.pool_address if args.pool_address else genesis_addr

    bc = Blockchain(genesis_addr, load_from_file=True)

    init_api(app, bc)

    api_thread = threading.Thread(
        target=run_api,
        kwargs={
            'host': '0.0.0.0',
            'port': args.api_port,
            'blockchain_instance': bc,
            'certfile': args.certfile,
            'keyfile': args.keyfile
        },
        daemon=True
    )
    api_thread.start()
    logger.info(f"API запущен на порту {args.api_port}" + (" (HTTPS)" if args.certfile else " (HTTP)"))

    p2p = P2PServer(bc, host='0.0.0.0', port=args.p2p_port)
    p2p.load_peers()
    p2p_thread = threading.Thread(target=run_p2p_loop, args=(p2p,), daemon=True)
    p2p_thread.start()
    logger.info(f"P2P запущен на порту {args.p2p_port}")

    # Запускаем Stratum-сервер
    stratum = StratumServer(bc, host='0.0.0.0', port=args.stratum_port, pool_address=pool_address)
    stratum_thread = threading.Thread(target=asyncio.run, args=(stratum.start(),), daemon=True)
    stratum_thread.start()
    logger.info(f"Stratum-сервер запущен на порту {args.stratum_port} (адрес пула: {pool_address[:8]}...)")

    if args.connect:
        host, port = args.connect.split(':')
        port = int(port)
        threading.Thread(target=connect_to_peer, args=(p2p, host, port), daemon=True).start()
        logger.info(f"Подключаемся к пиру {host}:{port}")

    if not args.no_mine:
        mining_thread = threading.Thread(target=mine_loop, args=(bc, genesis_addr, priv_key, pubkey), daemon=True)
        mining_thread.start()
        cleaner_thread = threading.Thread(target=mempool_cleaner, args=(bc,), daemon=True)
        cleaner_thread.start()

    logger.info("Нода запущена!")
    logger.info(f"   API: http{'s' if args.certfile else ''}://127.0.0.1:{args.api_port}")
    logger.info(f"   P2P: 127.0.0.1:{args.p2p_port}")
    logger.info(f"   Stratum: 127.0.0.1:{args.stratum_port}")
    logger.info(f"   Данные: {os.getcwd()}")
    logger.info("Press Ctrl+C to stop.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")