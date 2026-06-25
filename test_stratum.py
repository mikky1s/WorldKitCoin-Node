import socket
import json
import time

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.connect(('127.0.0.1', 3333))

def send(msg):
    s.send((json.dumps(msg) + '\n').encode())

# 1. Subscribe
send({"id": 1, "method": "mining.subscribe", "params": ["test"]})
time.sleep(0.5)

# 2. Authorize
send({"id": 2, "method": "mining.authorize", "params": ["4b4840a9527ba2ae4947747c5e024ed7484fe486", "x"]})
time.sleep(0.5)

# 3. Читаем все сообщения 10 секунд
print("Читаем сообщения от сервера...")
for _ in range(20):
    data = s.recv(4096).decode().strip()
    if data:
        print("Получено:", data)
    else:
        print("Нет данных")
    time.sleep(0.5)

s.close()