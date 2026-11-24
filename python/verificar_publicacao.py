"""Verifica se o projeto esta seguro para publicar no GitHub.

Uso: py python/verificar_publicacao.py

O script marca como problemas:
  - arquivos sensiveis que estao fora do .gitignore;
  - segredos do .env replicados em arquivos que seriam publicados;
  - credenciais antigas deixadas no codigo.
"""

from __future__ import annotations

import fnmatch
import sys
from pathlib import Path

import banco

IGNORADOS = (
    ".env",
    ".env.*",
    "*.xlsx",
    "*.xlsm",
    "*.xls",
    "*.enc",
    "*.key",
    "*.pem",
    "*.pfx",
    "inventario.db",
    "inventario.db-*",
    "dados/",
    "__pycache__/",
    "*.py[cod]",
    "*.log",
)

PUBLICAVEIS = {
    ".html",
    ".css",
    ".js",
    ".py",
    ".md",
    ".txt",
    ".bat",
    ".example",
    ".gitignore",
}

# Montado em partes para que o proprio verificador nao acuse as strings.
SEGREDOS_ANTIGOS = tuple(
    "".join(partes)
    for partes in (
        ("Pro", "fessores1112"),
        ("Pro", "fessores2009"),
        ("Ex", "peed"),
        ("Pro", "player"),
    )
)


def ignorado(caminho: Path) -> bool:
    relativo = caminho.relative_to(banco.BASE_DIR).as_posix()
    partes = relativo.split("/")
    for padrao in IGNORADOS:
        if padrao.endswith("/") and any(fnmatch.fnmatch(parte, padrao[:-1]) for parte in partes[:-1]):
            return True
        if fnmatch.fnmatch(relativo, padrao) or fnmatch.fnmatch(caminho.name, padrao):
            return True
    return False


def segredos_env() -> list[str]:
    caminho = banco.BASE_DIR / ".env"
    if not caminho.exists():
        return []
    valores = []
    for linha in caminho.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" not in linha or linha.lstrip().startswith("#"):
            continue
        chave, valor = linha.split("=", 1)
        if not chave.strip().startswith(("LOGIN_", "SENHA_")):
            continue
        valor = valor.strip().strip("\"'")
        if len(valor) >= 4:
            valores.append(valor)
    return valores


def main() -> int:
    problemas: list[str] = []
    gitignore = banco.BASE_DIR / ".gitignore"
    if not gitignore.exists():
        problemas.append("Falta o arquivo .gitignore.")

    arquivos = [
        caminho
        for caminho in banco.BASE_DIR.rglob("*")
        if caminho.is_file() and banco.BASE_DIR in caminho.parents
    ]

    sensiveis = [
        caminho.relative_to(banco.BASE_DIR).as_posix()
        for caminho in arquivos
        if caminho.suffix.lower() in {".env", ".xlsx", ".xlsm", ".xls", ".enc", ".db", ".key", ".pem", ".pfx"}
        or ".db-" in caminho.name
        or "dados" in caminho.relative_to(banco.BASE_DIR).parts
    ]
    nao_ignorados = [
        nome
        for nome in sensiveis
        if not ignorado(banco.BASE_DIR / nome)
    ]
    if nao_ignorados:
        problemas.append("Arquivos sensiveis fora do .gitignore: " + ", ".join(sorted(nao_ignorados)))

    segredos = segredos_env() + list(SEGREDOS_ANTIGOS)
    for caminho in arquivos:
        if ignorado(caminho):
            continue
        if caminho.suffix.lower() not in PUBLICAVEIS and caminho.name != ".gitignore":
            problemas.append(
                "Arquivo sem extensao publicavel: "
                + caminho.relative_to(banco.BASE_DIR).as_posix()
            )
            continue
        try:
            texto = caminho.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for segredo in segredos:
            if segredo in texto:
                problemas.append(
                    f"Segredo encontrado em {caminho.relative_to(banco.BASE_DIR).as_posix()}: {segredo[:3]}***"
                )

    print("Arquivos sensiveis locais (ignorados pelo Git):", len(sensiveis))
    for nome in sorted(sensiveis):
        print("  -", nome)

    if problemas:
        print("\nBLOQUEADO. Resolva antes de publicar:")
        for problema in problemas:
            print("  !", problema)
        return 1

    print("\nOK. Nenhum segredo visivel nos arquivos publicaveis.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
