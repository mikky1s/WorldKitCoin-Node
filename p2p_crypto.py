import os
import ssl
import hashlib
import struct
import time
from typing import Tuple
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.backends import default_backend

def generate_self_signed_cert(cert_dir: str, common_name: str = "worldkitcoin-node") -> Tuple[str, str]:
    os.makedirs(cert_dir, exist_ok=True)
    cert_path = os.path.join(cert_dir, "cert.pem")
    key_path = os.path.join(cert_dir, "key.pem")
    if os.path.exists(cert_path) and os.path.exists(key_path):
        return cert_path, key_path

    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import serialization
    import datetime

    # Генерация ключа
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend()
    )

    # Создание сертификата
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "WorldKitCoin"),
        x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
    ])
    cert = x509.CertificateBuilder().subject_name(
        subject
    ).issuer_name(
        issuer
    ).public_key(
        private_key.public_key()
    ).serial_number(
        x509.random_serial_number()
    ).not_valid_before(
        datetime.datetime.utcnow()
    ).not_valid_after(
        datetime.datetime.utcnow() + datetime.timedelta(days=365)
    ).add_extension(
        x509.SubjectAlternativeName([x509.DNSName("localhost")]),
        critical=False,
    ).sign(private_key, hashes.SHA256(), default_backend())

    # Запись ключа и сертификата
    with open(key_path, "wb") as f:
        f.write(private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        ))
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))

    return cert_path, key_path

def create_ssl_context(certfile: str, keyfile: str) -> ssl.SSLContext:
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain(certfile, keyfile)
    context.set_ciphers('ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM:DHE+CHACHA20:!aNULL:!MD5:!DSS')
    context.options |= ssl.OP_NO_SSLv2 | ssl.OP_NO_SSLv3 | ssl.OP_NO_TLSv1 | ssl.OP_NO_TLSv1_1
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context

def create_client_ssl_context(certfile: str, keyfile: str, server_hostname: str = None) -> ssl.SSLContext:
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    if certfile and keyfile:
        context.load_cert_chain(certfile, keyfile)
    context.check_hostname = False  # в P2P проверяем по ключу, а не по DNS
    context.verify_mode = ssl.CERT_NONE  # самоподписанные, проверяем отдельно
    context.set_ciphers('ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM:DHE+CHACHA20:!aNULL:!MD5:!DSS')
    context.options |= ssl.OP_NO_SSLv2 | ssl.OP_NO_SSLv3 | ssl.OP_NO_TLSv1 | ssl.OP_NO_TLSv1_1
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context

def verify_shared_secret(secret: str, challenge: bytes, response: bytes) -> bool:
    expected = hashlib.sha256(secret.encode() + challenge).digest()
    return expected == response

def generate_challenge() -> bytes:
    return os.urandom(32)

def solve_pow(challenge: bytes, difficulty: int) -> Tuple[int, bytes]:
    nonce = 0
    while True:
        data = challenge + struct.pack('<Q', nonce)
        digest = hashlib.sha256(data).digest()
        if int.from_bytes(digest, 'big') < difficulty:
            return nonce, digest
        nonce += 1

def verify_pow(challenge: bytes, nonce: int, difficulty: int) -> bool:
    data = challenge + struct.pack('<Q', nonce)
    digest = hashlib.sha256(data).digest()
    return int.from_bytes(digest, 'big') < difficulty
