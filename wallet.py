import json
import os
import time
import logging
from typing import Optional, Dict, List, Tuple
from ecdsa import SigningKey, SECP256k1
from utils import hash256, is_valid_address
from transaction import Transaction, TxIn, TxOut
from blockchain import Blockchain
from config import REWARD, VESTING_PERIODS

logger = logging.getLogger(__name__)

class Wallet:
    def __init__(self, blockchain: Blockchain, wallet_file: str = "wallets.json"):
        self.bc = blockchain
        self.wallet_file = wallet_file
        self.wallets: Dict[str, dict] = {}  # address -> {private_key, public_key}
        self._load_wallets()

    def _load_wallets(self):
        if os.path.exists(self.wallet_file):
            try:
                with open(self.wallet_file, 'r') as f:
                    data = json.load(f)
                    self.wallets = data
                logger.info(f"Загружено {len(self.wallets)} кошельков из {self.wallet_file}")
            except Exception as e:
                logger.error(f"Не удалось загрузить кошельки: {e}")
                self.wallets = {}
        else:
            self.wallets = {}

    def _save_wallets(self):
        try:
            with open(self.wallet_file, 'w') as f:
                json.dump(self.wallets, f, indent=2)
            logger.info(f"Сохранено {len(self.wallets)} кошельков в {self.wallet_file}")
        except Exception as e:
            logger.error(f"Не удалось сохранить кошельки: {e}")

    def generate_wallet(self) -> Tuple[str, str, str]:
        sk = SigningKey.generate(curve=SECP256k1)
        pubkey = sk.get_verifying_key().to_string()
        address = hash256(pubkey).hex()
        priv_hex = sk.to_string().hex()
        pub_hex = pubkey.hex()

        self.wallets[address] = {
            'private_key': priv_hex,
            'public_key': pub_hex,
            'created_at': int(time.time())
        }
        self._save_wallets()
        logger.info(f"Создан новый кошелёк: {address[:8]}...")
        return address, priv_hex, pub_hex

    def import_wallet(self, private_key: str) -> Tuple[str, str]:
        try:
            sk = SigningKey.from_string(bytes.fromhex(private_key), curve=SECP256k1)
            pubkey = sk.get_verifying_key().to_string()
            address = hash256(pubkey).hex()
            pub_hex = pubkey.hex()

            self.wallets[address] = {
                'private_key': private_key,
                'public_key': pub_hex,
                'created_at': int(time.time())
            }
            self._save_wallets()
            logger.info(f"Импортирован кошелёк: {address[:8]}...")
            return address, pub_hex
        except Exception as e:
            logger.error(f"Ошибка импорта ключа: {e}")
            return None, None

    def get_wallet(self, address: str) -> Optional[dict]:
        return self.wallets.get(address)

    def get_balance(self, address: str) -> Tuple[int, int]:
        if not is_valid_address(address):
            return 0, 0
        height = self.bc.get_block_height()
        return self.bc.get_balance(address, height)

    def get_history(self, address: str) -> List[dict]:
        if not is_valid_address(address):
            return []
        height = self.bc.get_block_height()
        return self.bc.get_transaction_history(address, height)

    def get_utxos(self, address: str) -> List[dict]:
        if not is_valid_address(address):
            return []
        height = self.bc.get_block_height()
        return self.bc.get_utxos_for_address(address, height)

    def create_and_send_transaction(self, from_address: str, to_address: str,
                                    amount: int, private_key: str = None) -> Optional[str]:
        if not is_valid_address(from_address) or not is_valid_address(to_address):
            logger.error("Неверный формат адреса")
            return None

        if private_key is None:
            wallet = self.wallets.get(from_address)
            if not wallet:
                logger.error(f"Кошелёк {from_address[:8]}... не найден в хранилище")
                return None
            private_key = wallet['private_key']
            pubkey = wallet['public_key']
        else:
            try:
                sk = SigningKey.from_string(bytes.fromhex(private_key), curve=SECP256k1)
                pubkey = sk.get_verifying_key().to_string().hex()
            except:
                logger.error("Неверный приватный ключ")
                return None

        height = self.bc.get_block_height()
        tx = self.bc.create_transaction(from_address, to_address, amount, height)
        if tx is None:
            logger.error("Недостаточно средств или ошибка создания транзакции")
            return None

        tx.sign(private_key, pubkey)
        if not self.bc.add_to_mempool(tx):
            logger.error("Транзакция не добавлена в мемпул")
            return None

        logger.info(f"Транзакция отправлена: {tx.hash[:16]}...")
        return tx.hash

    def list_wallets(self) -> List[dict]:
        result = []
        for addr, data in self.wallets.items():
            result.append({
                'address': addr,
                'public_key': data['public_key'][:16] + '...',
                'created_at': data['created_at']
            })
        return result
