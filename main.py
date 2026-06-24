import argparse
import threading
import time
import os
import asyncio
import logging
from blockchain import Blockchain
from api import run_api, init_api, app
from block import Block
from utils import bits_to_target, hash256, is_valid_address
from config import REWARD, VESTING_PERIODS, NETWORK_NAME, LOG_FILE, LOG_LEVEL
from transaction import Transaction
from p2p import P2PServer
from stratum_server import StratumServer
from ecdsa import SigningKey, SECP256k1
from p2p_crypto import generate_self_signed_cert
from wallet import Wallet

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

    # Основные параметры ноды
    parser.add_argument('--address', help='Адрес для награды (если не указан, генерируется)')
    parser.add_argument('--api-port', type=int, default=5000, help='Порт для API')
    parser.add_argument('--p2p-port', type=int, default=8333, help='Порт для P2P')
    parser.add_argument('--stratum-port', type=int, default=3333, help='Порт для Stratum')
    parser.add_argument('--pool-address', help='Адрес пула (для Stratum)')
    parser.add_argument('--data-dir', default='data', help='Папка для данных')
    parser.add_argument('--no-mine', action='store_true', help='Отключить майнинг')
    parser.add_argument('--connect', help='Подключиться к пиру (host:port)')
    parser.add_argument('--certfile', help='Путь к SSL-сертификату для API (HTTPS)')
    parser.add_argument('--keyfile', help='Путь к приватному ключу SSL для API')
    parser.add_argument('--p2p-no-ssl', action='store_true', help='Отключить SSL для P2P (не рекомендуется)')
    parser.add_argument('--no-checkpoints', action='store_true', help='Отключить проверку чекпоинтов (для разработки)')

    # Подкоманды для кошелька
    subparsers = parser.add_subparsers(dest='wallet_cmd', help='Команды управления кошельком')

    # wallet new
    parser_new = subparsers.add_parser('new', help='Создать новый кошелёк')

    # wallet import <privkey>
    parser_import = subparsers.add_parser('import', help='Импортировать кошелёк по приватному ключу')
    parser_import.add_argument('privkey', help='Приватный ключ (hex)')

    # wallet list
    parser_list = subparsers.add_parser('list', help='Показать все сохранённые кошельки')

    # wallet balance <address>
    parser_balance = subparsers.add_parser('balance', help='Показать баланс адреса')
    parser_balance.add_argument('address', help='Адрес')

    # wallet send <from> <to> <amount>
    parser_send = subparsers.add_parser('send', help='Отправить транзакцию')
    parser_send.add_argument('from_address', help='Адрес отправителя')
    parser_send.add_argument('to_address', help='Адрес получателя')
    parser_send.add_argument('amount', type=int, help='Сумма в монетах (WKC)')

    # wallet history <address>
    parser_history = subparsers.add_parser('history', help='Показать историю транзакций адреса')
    parser_history.add_argument('address', help='Адрес')

    args = parser.parse_args()

    # Если вызвана команда кошелька — выполняем её и завершаем работу
    if args.wallet_cmd:
        os.makedirs(args.data_dir, exist_ok=True)
        os.chdir(args.data_dir)

        # Для команд кошелька всегда отключаем проверку чекпоинтов
        if not os.path.exists("blockchain.db"):
            # Если блокчейн не существует, создаём новый с временным адресом
            tmp_addr, _, _ = generate_keypair()
            bc = Blockchain(tmp_addr, load_from_file=False, verify_checkpoints=False)
        else:
            # Загружаем существующий блокчейн, чекпоинты не проверяем
            bc = Blockchain("0"*64, load_from_file=True, verify_checkpoints=False)
            bc.current_bits = 0x1d00ffff

        wallet = Wallet(bc, wallet_file="wallets.json")

        if args.wallet_cmd == 'new':
            addr, priv, pub = wallet.generate_wallet()
            print(f"\n✅ Новый кошелёк создан:")
            print(f"   Адрес: {addr}")
            print(f"   Приватный ключ: {priv}")
            print(f"   Публичный ключ: {pub}")
            print("⚠️  Сохраните приватный ключ в надёжном месте!")

        elif args.wallet_cmd == 'import':
            addr, pub = wallet.import_wallet(args.privkey)
            if addr:
                print(f"\n✅ Кошелёк импортирован:")
                print(f"   Адрес: {addr}")
                print(f"   Публичный ключ: {pub}")
            else:
                print("❌ Ошибка импорта. Проверьте приватный ключ.")

        elif args.wallet_cmd == 'list':
            wallets = wallet.list_wallets()
            if not wallets:
                print("Нет сохранённых кошельков.")
            else:
                print(f"\n📋 Сохранённые кошельки ({len(wallets)}):")
                for w in wallets:
                    print(f"   {w['address'][:8]}... | создан: {time.ctime(w['created_at'])}")

        elif args.wallet_cmd == 'balance':
            if not is_valid_address(args.address):
                print("❌ Неверный адрес")
                exit(1)
            avail, locked = wallet.get_balance(args.address)
            print(f"\n💰 Баланс адреса {args.address[:8]}...")
            print(f"   Доступно: {avail} WKC")
            print(f"   Заблокировано: {locked} WKC")

        elif args.wallet_cmd == 'send':
            if args.from_address not in wallet.wallets:
                print(f"❌ Адрес {args.from_address[:8]}... не найден в хранилище кошельков.")
                print("   Используйте 'import' для добавления.")
            else:
                tx_hash = wallet.create_and_send_transaction(
                    args.from_address, args.to_address, args.amount
                )
                if tx_hash:
                    print(f"\n✅ Транзакция отправлена!")
                    print(f"   Хеш: {tx_hash}")
                else:
                    print("❌ Ошибка отправки транзакции")

        elif args.wallet_cmd == 'history':
            if not is_valid_address(args.address):
                print("❌ Неверный адрес")
                exit(1)
            history = wallet.get_history(args.address)
            if not history:
                print(f"Нет транзакций для адреса {args.address[:8]}...")
            else:
                print(f"\n📜 История адреса {args.address[:8]}...")
                for tx in history[:10]:  # показываем последние 10
                    confirmations = tx['confirmations']
                    status = "✅ подтверждён" if confirmations > 0 else "⏳ неподтверждён"
                    print(f"   {tx['tx_hash'][:16]}... | сумма: {tx['amount']} | блок: {tx['block_height']} | {status}")

        exit(0)

    # ------------------------------------------------------------------
    # Если команда кошелька не вызвана — запускаем полноценную ноду
    # ------------------------------------------------------------------

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
        with open("wallet.txt", "w") as f:
            f.write(f"Address: {genesis_addr}\nPrivate key: {priv_key}\nPublic key: {pubkey}\n")
        logger.warning("Ключи сохранены в wallet.txt. НЕ УДАЛЯЙТЕ!")

    pool_address = args.pool_address if args.pool_address else genesis_addr
    bc = Blockchain(genesis_addr, load_from_file=True, verify_checkpoints=not args.no_checkpoints)

    init_api(app, bc)

    # Запуск API в отдельном потоке
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

    # Запуск P2P
    use_ssl = not args.p2p_no_ssl
    p2p = P2PServer(bc, host='0.0.0.0', port=args.p2p_port, use_ssl=use_ssl, cert_dir=os.path.join(args.data_dir, "certs"))
    p2p.load_peers()
    p2p_thread = threading.Thread(target=run_p2p_loop, args=(p2p,), daemon=True)
    p2p_thread.start()
    logger.info(f"P2P запущен на порту {args.p2p_port} (SSL={use_ssl})")

    # Запуск Stratum
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
    logger.info(f"   P2P: 127.0.0.1:{args.p2p_port} (SSL={use_ssl})")
    logger.info(f"   Stratum: 127.0.0.1:{args.stratum_port}")
    logger.info(f"   Данные: {os.getcwd()}")
    logger.info("Press Ctrl+C to stop.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
