BLOCK_TIME_SEC = 150
DIFFICULTY_ADJUSTMENT_INTERVAL = 10
REWARD = 125
MAX_SUPPLY = 21_000_000
VESTING_PERIODS = [0, 4032, 8064, 12096, 16128]
NETWORK_NAME = "WorldKitCoin"
CURRENCY_SYMBOL = "WKC"

MAX_BLOCK_SIZE = 1_000_000
MAX_SIGOPS = 20_000
CHECKPOINTS = {
    0: "8f0f8d4eed5cc7c2d69d189e5fdf1044bbfd1b1253967d07a76f319a0c883450",
}

RPC_USER = "admin"
RPC_PASSWORD_FILE = "rpc_password.txt"

MAX_MEMPOOL_SIZE = 5000
LOG_FILE = "node.log"
LOG_LEVEL = "INFO"
CACHE_BALANCE_TTL = 10

API_RATE_LIMIT = 100
MAX_TIME_DRIFT = 7200
P2P_BAN_TIME = 600
MAX_MESSAGE_SIZE = 1024 * 1024

MAX_REORG_DEPTH = 6
MAX_MEMPOOL_BYTES = 10 * 1024 * 1024
MAX_PEERS_PER_IP = 5
MAX_PEERS_PER_SUBNET = 20   # защита от Sybil

PEERS_FILE = "known_peers.json"
SKIP_DIFFICULTY_CHECK = True

# ---------- P2P безопасность ----------
P2P_SHARED_SECRET = "my_secret_password_change_me"  # общий секрет для аутентификации
P2P_CERT_DIR = "certs"                              # папка для сертификатов
P2P_SSL_PORT = 8334                                 # можно использовать отдельный порт для SSL
P2P_REQUIRE_AUTH = True                             # требовать handshake
P2P_POW_DIFFICULTY = 0x00ffff0000000000000000000000000000000000000000000000000000000000  # сложность PoW при handshake
