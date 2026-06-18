# WorldKitCoin Node

[![GitHub stars](https://img.shields.io/github/stars/mikky1s/worldkitcoin-node)](https://github.com/mikky1s/worldkitcoin-node/stargazers)
[![GitHub license](https://img.shields.io/github/license/mikky1s/worldkitcoin-node)](https://github.com/mikky1s/worldkitcoin-node/blob/main/LICENSE)
[![Telegram](https://img.shields.io/badge/Telegram-@WorldKitCoin-blue)](https://t.me/WorldKitCoin)

**WorldKitCoin Node** – это полноценная нода блокчейна WorldKitCoin с встроенным Stratum-пулом для майнеров. Она обеспечивает работу сети, обрабатывает транзакции, майнит блоки и предоставляет REST API для управления.

---

## Особенности

- **Алгоритм консенсуса:** SHA-256d (Proof-of-Work) – совместим с ASIC-майнерами.
- **Время блока:** 2.5 минуты (в 4 раза быстрее Bitcoin).
- **Максимальная эмиссия:** 21 000 000 WKC.
- **Награда за блок:** 125 WKC с прогрессивным вестингом.
- **Встроенный Stratum-пул** (порт 3333).
- **P2P-сеть** (порт 8333).
- **HTTP API** (порт 5000).
- **UTXO-модель** – как в Bitcoin.
- **Сохранение состояния** на диск (JSON).
- **Динамическая сложность** – автоматическая подстройка.

---

## Требования

- Python 3.8 или новее
- Операционная система: Windows, Linux, macOS
- 1 ГБ свободной оперативной памяти
- 100 МБ свободного места на диске
- Открытые порты: 3333 (Stratum), 5000 (API), 8333 (P2P)

---

## Установка и запуск

### 1. Клонируйте репозиторий

```bash
git clone https://github.com/yourusername/worldkitcoin-node.git
cd worldkitcoin-node
