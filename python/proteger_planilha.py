"""Cria ou abre o cofre criptografado da planilha de inventario.

Uso:
  py python/proteger_planilha.py proteger
  py python/proteger_planilha.py desproteger
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

import banco
import config
import seguranca


def localizar_original() -> Path:
    cfg = config.carregar_config()
    configurado = cfg.get("PLANILHA_PATH", "")
    if configurado:
        candidato = Path(configurado)
        if not candidato.is_absolute():
            candidato = banco.BASE_DIR / candidato
        if candidato.exists() and candidato.suffix.lower() != ".enc":
            return candidato.resolve()
    for padrao in ("*.xlsx", "*.xlsm"):
        for item in sorted(banco.BASE_DIR.glob(padrao)):
            if not item.name.startswith("~$"):
                return item.resolve()
    raise FileNotFoundError("Nenhuma planilha XLSX foi encontrada na pasta do projeto.")


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass

    analisador = argparse.ArgumentParser(description="Protege a planilha de inventario.")
    analisador.add_argument("acao", choices=("proteger", "desproteger"))
    analisador.add_argument("origem", nargs="?")
    analisador.add_argument("destino", nargs="?")
    argumentos = analisador.parse_args()

    senha = os.environ.get("SENHA_PLANILHA") or config.carregar_config().get(
        "SENHA_PLANILHA", ""
    )
    if not senha:
        senha = getpass.getpass("Senha da planilha: ")
    if not senha:
        print("Senha vazia. Operacao cancelada.")
        return

    padrao_destino = (
        banco.BASE_DIR / "dados" / "inventario.xlsx.enc"
        if argumentos.acao == "proteger"
        else banco.BASE_DIR / "dados" / "inventario_decrypted.xlsx"
    )

    try:
        if argumentos.acao == "proteger":
            origem = Path(argumentos.origem).resolve() if argumentos.origem else localizar_original()
            destino = Path(argumentos.destino).resolve() if argumentos.destino else padrao_destino
            seguranca.criptografar_arquivo(origem, destino, senha)
            print(f"Cofre criado: {destino}")
            print("Configure PLANILHA_PATH e SENHA_PLANILHA no .env.")
            print("A planilha original em texto aberto continua no disco. Exclua-a depois de validar o cofre.")
        else:
            origem = Path(argumentos.origem).resolve() if argumentos.origem else padrao_destino
            destino = Path(argumentos.destino).resolve() if argumentos.destino else banco.BASE_DIR / "dados" / "inventario_decrypted.xlsx"
            dados = seguranca.descriptografar_arquivo(origem, senha)
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_bytes(dados)
            print(f"Planilha restaurada: {destino}")
            print("Nao envie esse arquivo ao GitHub.")
    except (FileNotFoundError, ValueError, RuntimeError) as erro:
        print(f"Erro: {erro}")
        sys.exit(1)


if __name__ == "__main__":
    main()
