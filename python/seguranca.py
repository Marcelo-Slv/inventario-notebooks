"""Funcoes de seguranca: hashes de senha e cofre criptografado da planilha.

O cofre usa AES-GCM com chave derivada por PBKDF2-HMAC-SHA256. A biblioteca
cryptography so e exigida quando o cofre e usado; o restante do sistema
funciona com a biblioteca padrao.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from pathlib import Path

try:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    CRIPTOGRAFIA_DISPONIVEL = True
except ImportError:
    CRIPTOGRAFIA_DISPONIVEL = False

PBKDF2_ITERACOES = 600_000
MAGICO = b"PTI1"


def gerar_hash_senha(senha: str) -> tuple[str, str, int]:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", senha.encode("utf-8"), salt, PBKDF2_ITERACOES, dklen=32
    )
    return digest.hex(), salt.hex(), PBKDF2_ITERACOES


def verificar_senha(senha: str, hash_hex: str, salt_hex: str, iteracoes: int) -> bool:
    try:
        digest = hashlib.pbkdf2_hmac(
            "sha256", senha.encode("utf-8"), bytes.fromhex(salt_hex), int(iteracoes), dklen=32
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), hash_hex)


def _exigir_cryptography() -> None:
    if not CRIPTOGRAFIA_DISPONIVEL:
        raise RuntimeError(
            "Instale cryptography para usar a planilha criptografada: py -m pip install -r requirements.txt"
        )


def _derivar_chave(senha: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=PBKDF2_ITERACOES,
    )
    return kdf.derive(senha.encode("utf-8"))


def criptografar_bytes(dados: bytes, senha: str) -> bytes:
    _exigir_cryptography()
    if not senha:
        raise ValueError("Defina SENHA_PLANILHA antes de criptografar.")
    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(12)
    chave = _derivar_chave(senha, salt)
    cifrado = AESGCM(chave).encrypt(nonce, dados, None)
    return MAGICO + salt + nonce + cifrado


def descriptografar_bytes(conteudo: bytes, senha: str) -> bytes:
    _exigir_cryptography()
    if not conteudo.startswith(MAGICO):
        raise ValueError("Arquivo protegido com formato invalido.")
    if len(conteudo) < len(MAGICO) + 16 + 12 + 16:
        raise ValueError("Arquivo protegido incompleto.")
    salt = conteudo[len(MAGICO) : len(MAGICO) + 16]
    nonce = conteudo[len(MAGICO) + 16 : len(MAGICO) + 28]
    cifrado = conteudo[len(MAGICO) + 28 :]
    chave = _derivar_chave(senha, salt)
    try:
        return AESGCM(chave).decrypt(nonce, cifrado, None)
    except Exception as erro:
        raise ValueError("Senha da planilha incorreta ou arquivo corrompido.") from erro


def criptografar_arquivo(origem: Path, destino: Path, senha: str) -> Path:
    destino.parent.mkdir(parents=True, exist_ok=True)
    cifrado = criptografar_bytes(origem.read_bytes(), senha)
    destino.write_bytes(cifrado)
    try:
        destino.chmod(0o600)
    except OSError:
        pass
    return destino


def descriptografar_arquivo(origem: Path, senha: str) -> bytes:
    return descriptografar_bytes(origem.read_bytes(), senha)
