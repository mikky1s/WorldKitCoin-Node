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
```
## 2. Установите зависимости

```bash
pip install -r requirements.txt
```
## 4. Запустите ноду

```bash
python main.py
```
## Нода автоматически:

+ Создаёт генезис-блок (при первом запуске).

+ Запускает Stratum-пул на порту 3333.

+ Запускает P2P-сервер на порту 8333.

+ Запускает HTTP API на порту 5000.

+ Сохраняет состояние в файл blockchain_data.json.

## 5. Проверьте работу
## Откройте в браузере:

```text
http://localhost:5000/info
```
## Пример ответа:

```json
{
  "height": 0,
  "difficulty_target": 115792089237316195423570985008687907853269984665640564039457584007913129639935,
  "current_bits": "0x1f00ffff",
  "utxo_count": 5,
  "mempool_size": 0,
  "total_supply": 125,
  "chain_tip": "..."
}
```
## API Endpoints

| Метод | Путь | Описание | Пример запроса |
|-------|------|----------|----------------|
| `GET` | `/info` | Информация о сети (высота, сложность, UTXO, мемпул) | `curl http://localhost:5000/info` |
| `GET` | `/balance/<address>` | Баланс адреса (доступный, заблокированный, общий) | `curl http://localhost:5000/balance/4b4840a9527ba2ae4947747c5e024ed7484fe486` |
| `GET` | `/block/<height>` | Информация о блоке по его высоте | `curl http://localhost:5000/block/42` |
| `GET` | `/mempool` | Список неподтверждённых транзакций | `curl http://localhost:5000/mempool` |
| `POST` | `/transaction` | Отправка транзакции (требуется JSON с `from`, `to_pubkey`, `amount`, `private_key`) | `curl -X POST http://localhost:5000/transaction -H "Content-Type: application/json" -d '{"from":"...","to_pubkey":"...","amount":10,"private_key":"..."}'` |
| `POST` | `/mine` | Запуск майнинга одного блока (для теста, требует `address` в JSON) | `curl -X POST http://localhost:5000/mine -H "Content-Type: application/json" -d '{"address":"..."}'` |
| `GET` | `/history/<address>` | История транзакций для указанного адреса | `curl http://localhost:5000/history/4b4840a9527ba2ae4947747c5e024ed7484fe486` |

**Примечание:**  
- Все ответы возвращаются в формате JSON.  
- Для `POST /transaction` обязательные поля: `from` (адрес отправителя), `to_pubkey` (публичный ключ получателя), `amount` (целое число), `private_key` (hex-строка).  
- Для `POST /mine` укажите адрес кошелька, на который будет зачислена награда.
