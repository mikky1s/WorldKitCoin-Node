import socket
import json
import time

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.connect(('127.0.0.1', 3333))

# 1. Отправляем mining.subscribe
s.send(json.dumps({"id":1,"method":"mining.subscribe","params":["test"]}).encode() + b'\n')
time.sleep(0.5)
data = s.recv(4096).decode()
print("Ответ на subscribe:\n", data)

# 2. Отправляем mining.authorize
s.send(json.dumps({"id":2,"method":"mining.authorize","params":["test_address","x"]}).encode() + b'\n')
time.sleep(0.5)
# Читаем все ответы (может быть несколько)
data = s.recv(4096).decode()
print("Ответы после authorize:\n", data)

# 3. Ждём ещё немного и читаем остальное
time.sleep(1)
data = s.recv(4096).decode()
if data:
    print("Дополнительные данные:\n", data)

s.close()