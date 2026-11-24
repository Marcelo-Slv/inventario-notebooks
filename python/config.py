"""Configuracao local do projeto.

Os segredos ficam no arquivo .env, que esta no .gitignore. Nenhuma credencial
fica gravada no codigo-fonte publicado.
"""

from __future__ import annotations

import os
import secrets
import string
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"

CHAVES_ACESSO = (
    "LOGIN_PROFESSOR",
    "SENHA_PROFESSOR",
    "LOGIN_ADMIN",
    "SENHA_ADMIN",
)


def ler_env() -> dict[str, str]:
    if not ENV_PATH.exists():
        return {}
    dados: dict[str, str] = {}
    for linha in ENV_PATH.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, valor = linha.split("=", 1)
        chave = chave.strip()
        valor = valor.strip()
        if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in ("'", '"'):
            valor = valor[1:-1]
        dados[chave] = valor
    return dados


def carregar_config() -> dict[str, str]:
    arquivo = ler_env()

    def obter(chave: str, padrao: str = "") -> str:
        return os.environ.get(chave, arquivo.get(chave, padrao))

    return {
        "LOGIN_PROFESSOR": obter("LOGIN_PROFESSOR"),
        "SENHA_PROFESSOR": obter("SENHA_PROFESSOR"),
        "LOGIN_ADMIN": obter("LOGIN_ADMIN"),
        "SENHA_ADMIN": obter("SENHA_ADMIN"),
        "PLANILHA_PATH": obter("PLANILHA_PATH"),
        "SENHA_PLANILHA": obter("SENHA_PLANILHA"),
        "HOST": obter("HOST", "127.0.0.1"),
        "PORTA": obter("PORTA", "8000"),
        "HTTPS": obter("HTTPS", "0"),
    }


def escrever_env(dados: dict[str, str]) -> None:
    ordem = (
        "LOGIN_PROFESSOR",
        "SENHA_PROFESSOR",
        "LOGIN_ADMIN",
        "SENHA_ADMIN",
        "PLANILHA_PATH",
        "SENHA_PLANILHA",
        "HOST",
        "PORTA",
        "HTTPS",
    )
    linhas = ["# Arquivo local com segredos. Nao enviar ao GitHub.", ""]
    for chave in ordem:
        linhas.append(f"{chave}={dados.get(chave, '')}")
    ENV_PATH.write_text("\n".join(linhas) + "\n", encoding="utf-8", newline="\n")
    try:
        ENV_PATH.chmod(0o600)
    except OSError:
        pass


def garantir_credenciais(config: dict[str, str] | None = None) -> dict[str, str]:
    """Devolve as credenciais; se nao houver nenhuma, cria um .env local."""
    cfg = config or carregar_config()
    valores = {chave: cfg.get(chave, "") for chave in CHAVES_ACESSO}

    if all(valores.values()):
        return valores
    if any(valores.values()):
        raise ValueError(
            "Preencha LOGIN_PROFESSOR, SENHA_PROFESSOR, LOGIN_ADMIN e SENHA_ADMIN no arquivo .env."
        )

    digitos = string.digits
    novos = {
        "LOGIN_PROFESSOR": "Professor" + "".join(secrets.choice(digitos) for _ in range(5)),
        "SENHA_PROFESSOR": secrets.token_urlsafe(18),
        "LOGIN_ADMIN": "Admin" + "".join(secrets.choice(digitos) for _ in range(5)),
        "SENHA_ADMIN": secrets.token_urlsafe(18),
    }
    escrever_env({**cfg, **novos})
    return novos
