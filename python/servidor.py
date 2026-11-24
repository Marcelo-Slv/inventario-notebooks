"""Servidor local do painel de inventario.

Usa somente a biblioteca padrao do Python. Serve os arquivos index.html,
css e js e disponibiliza a API consumida pelo front-end.

Executar: py python/servidor.py
"""

from __future__ import annotations

import csv
import io
import json
import mimetypes
import re
import secrets
import sqlite3
import sys
import time
import zipfile
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import banco
import config

BASE_DIR = banco.BASE_DIR
EXTENSOES_PERMITIDAS = {".html", ".css", ".js", ".svg", ".png", ".jpg", ".ico", ".webp"}
DURACAO_SESSAO = 8 * 60 * 60
LIMITE_TENTATIVAS = 5
JANELA_TENTATIVAS = 15 * 60
BLOQUEIO_LOGIN = 15 * 60
SESSOES: dict[str, dict] = {}
TENTATIVAS: dict[str, dict] = {}

CONFIG = config.carregar_config()
COOKIE_SECURE = CONFIG.get("HTTPS", "0") == "1"


def nova_sessao(usuario: dict) -> str:
    token = secrets.token_urlsafe(32)
    SESSOES[token] = {
        "usuario": usuario,
        "expira": time.time() + DURACAO_SESSAO,
    }
    return token


def sessao_atual(handler: BaseHTTPRequestHandler) -> tuple[dict | None, str | None]:
    now = time.time()
    for token in list(SESSOES):
        if SESSOES[token]["expira"] <= now:
            SESSOES.pop(token, None)

    for parte in handler.headers.get("Cookie", "").split(";"):
        nome, separador, valor = parte.strip().partition("=")
        if separador and nome == "sid":
            sessao = SESSOES.get(valor)
            if sessao and sessao["expira"] > now:
                return sessao, valor
            return None, None
    return None, None


class Handler(BaseHTTPRequestHandler):
    server_version = "InventarioTI/1.0"

    def log_message(self, formato: str, *args) -> None:
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), formato % args))

    def _cabecalhos_seguranca(self) -> list[tuple[str, str]]:
        return [
            ("X-Content-Type-Options", "nosniff"),
            ("X-Frame-Options", "DENY"),
            ("Referrer-Policy", "same-origin"),
            ("Cross-Origin-Opener-Policy", "same-origin"),
            ("Cross-Origin-Resource-Policy", "same-origin"),
            ("Permissions-Policy", "geolocation=(), microphone=(), camera=()"),
            ("X-Permitted-Cross-Domain-Policies", "none"),
            *(
                [("Strict-Transport-Security", "max-age=31536000; includeSubDomains")]
                if COOKIE_SECURE
                else []
            ),
            (
                "Content-Security-Policy",
                "default-src 'self'; base-uri 'self'; form-action 'self'; "
                "script-src 'self'; connect-src 'self'; img-src 'self' data:; "
                "style-src 'self' 'unsafe-inline'; object-src 'none'; frame-ancestors 'none'",
            ),
        ]

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        self._json(code, {"erro": message or "Erro na requisicao."})

    def _json(self, status: int, objeto: object, headers: list[tuple[str, str]] | None = None) -> None:
        corpo = json.dumps(objeto, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        for chave, valor in [*self._cabecalhos_seguranca(), *(headers or [])]:
            self.send_header(chave, valor)
        self.end_headers()
        try:
            self.wfile.write(corpo)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _bytes(self, status: int, corpo: bytes, tipo: str, headers: list[tuple[str, str]] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        for chave, valor in [*self._cabecalhos_seguranca(), *(headers or [])]:
            self.send_header(chave, valor)
        self.end_headers()
        try:
            self.wfile.write(corpo)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _corpo_json(self) -> dict:
        try:
            tamanho = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            tamanho = 0
        if tamanho <= 0:
            return {}
        if tamanho > 1_000_000:
            raise ValueError("Corpo muito grande.")
        if not self.headers.get("Content-Type", "").lower().startswith("application/json"):
            raise ValueError("Content-Type invalido.")
        dados = json.loads(self.rfile.read(tamanho).decode("utf-8"))
        if not isinstance(dados, dict):
            raise ValueError("Corpo da requisicao invalido.")
        return dados

    def _origem_valida(self) -> bool:
        origem = self.headers.get("Origin")
        if origem and urlparse(origem).netloc.lower() != self.headers.get("Host", "").lower():
            return False
        destino = self.headers.get("Sec-Fetch-Site", "")
        if destino and destino not in {"same-origin", "none"}:
            return False
        return True

    def _exigir_login(self, perfil: str | None = None) -> dict | None:
        sessao, _ = sessao_atual(self)
        if sessao is None:
            self._json(401, {"erro": "Entre na area restrita para realizar esta acao."})
            return None
        if perfil == "admin" and sessao["usuario"]["perfil"] != "admin":
            self._json(403, {"erro": "Esta acao esta disponivel apenas para o administrador."})
            return None
        return sessao["usuario"]

    def _ip_cliente(self) -> str:
        return self.client_address[0] if self.client_address else "desconhecido"

    def _login_bloqueado(self) -> bool:
        agora = time.time()
        for ip, registro in list(TENTATIVAS.items()):
            if registro.get("bloqueado_ate", 0) <= agora and registro.get("inicio", 0) + JANELA_TENTATIVAS <= agora:
                TENTATIVAS.pop(ip, None)
        registro = TENTATIVAS.get(self._ip_cliente())
        return bool(registro and registro.get("bloqueado_ate", 0) > agora)

    def _registrar_falha_login(self) -> None:
        agora = time.time()
        ip = self._ip_cliente()
        registro = TENTATIVAS.setdefault(ip, {"inicio": agora, "tentativas": 0, "bloqueado_ate": 0})
        if registro["inicio"] + JANELA_TENTATIVAS <= agora:
            registro.update({"inicio": agora, "tentativas": 0, "bloqueado_ate": 0})
        registro["tentativas"] += 1
        if registro["tentativas"] >= LIMITE_TENTATIVAS:
            registro["bloqueado_ate"] = agora + BLOQUEIO_LOGIN

    def _limpar_falhas_login(self) -> None:
        TENTATIVAS.pop(self._ip_cliente(), None)

    def _cookie_sessao(self, token: str, expira: int) -> str:
        seguro = "; Secure" if COOKIE_SECURE else ""
        return f"sid={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={expira}{seguro}"

    def _arquivo_estatico(self, caminho: str) -> bool:
        relativo = unquote(caminho).lstrip("/")
        if not relativo:
            relativo = "index.html"
        alvo = (BASE_DIR / relativo).resolve()
        if alvo not in (BASE_DIR,) and BASE_DIR not in alvo.parents:
            self._json(403, {"erro": "Acesso negado."})
            return True
        if alvo.is_dir():
            alvo = alvo / "index.html"
        if not alvo.is_file() or alvo.suffix.lower() not in EXTENSOES_PERMITIDAS:
            return False
        tipo = mimetypes.guess_type(alvo.name)[0] or "application/octet-stream"
        if alvo.suffix.lower() == ".js":
            tipo = "text/javascript; charset=utf-8"
        elif alvo.suffix.lower() == ".css":
            tipo = "text/css; charset=utf-8"
        self._bytes(200, alvo.read_bytes(), tipo, [("Cache-Control", "no-cache")])
        return True

    def _exportar_csv(self) -> None:
        itens = banco.listar_equipamentos()
        buffer = io.StringIO()
        escritor = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
        escritor.writerow(
            [
                "Etiqueta",
                "Numero de Serie",
                "Localizacao",
                "Marca",
                "Modelo",
                "Estado Fisico",
                "Status Funcional",
                "Carregador Incluso",
                "Data do Levantamento",
                "Responsavel do Levantamento",
                "Observacoes",
                "Origem",
                "Ultima Alteracao",
            ]
        )
        def seguro(valor: object) -> str:
            texto = str(valor or "")
            if texto and texto[0] in "=+-@\t\r":
                return "'" + texto
            return texto

        for item in itens:
            escritor.writerow([seguro(item[campo]) for campo in (
                "etiqueta", "numero_serie", "localizacao", "marca", "modelo",
                "estado_fisico", "status", "carregador", "data_levantamento",
                "responsavel_levantamento", "observacoes", "origem", "modificacao",
            )])
        conteudo = ("\ufeff" + buffer.getvalue()).encode("utf-8")
        self._bytes(
            200,
            conteudo,
            "text/csv; charset=utf-8",
            [("Content-Disposition", 'attachment; filename="inventario_notebooks.csv"')],
        )

    def do_GET(self) -> None:
        url = urlparse(self.path)
        caminho = url.path.rstrip("/") or "/"
        consulta = parse_qs(url.query)

        if not self._origem_valida():
            self._json(403, {"erro": "Origem da requisicao nao autorizada."})
            return

        if caminho == "/api/sessao":
            sessao, _ = sessao_atual(self)
            self._json(
                200,
                {
                    "autenticado": sessao is not None,
                    "usuario": sessao["usuario"] if sessao else None,
                },
            )
            return

        if caminho == "/api/dashboard":
            self._json(200, banco.obter_dashboard())
            return

        if caminho == "/api/equipamentos":
            filtros = {
                "q": consulta.get("q", [""])[0],
                "status": consulta.get("status", [""])[0],
                "localizacao": consulta.get("localizacao", [""])[0],
                "origem": consulta.get("origem", [""])[0],
            }
            itens = banco.listar_equipamentos(filtros)
            self._json(200, {"itens": itens, "total": len(itens)})
            return

        if caminho in {"/api/exportar.csv", "/api/exportar.xlsx"}:
            if self._exigir_login("admin") is None:
                return
            if caminho.endswith(".csv"):
                self._exportar_csv()
            else:
                nome = datetime.now().strftime("inventario_notebooks_%Y-%m-%d.xlsx")
                self._bytes(
                    200,
                    banco.exportar_xlsx(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    [("Content-Disposition", f'attachment; filename="{nome}"')],
                )
            return

        if caminho.startswith("/api/"):
            self._json(404, {"erro": "Endereco nao encontrado."})
            return

        if self._arquivo_estatico(caminho):
            return

        self.send_error(404, "Arquivo nao encontrado")

    def do_POST(self) -> None:
        caminho = urlparse(self.path).path.rstrip("/") or "/"
        if not self._origem_valida():
            self._json(403, {"erro": "Origem da requisicao nao autorizada."})
            return

        try:
            dados = self._corpo_json()
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            self._json(400, {"erro": "Dados invalidos."})
            return

        if caminho == "/api/login":
            if self._login_bloqueado():
                self._json(
                    429,
                    {"erro": "Muitas tentativas. Aguarde alguns minutos antes de tentar novamente."},
                )
                return
            usuario = banco.verificar_login(dados.get("usuario", ""), dados.get("senha", ""))
            if usuario is None:
                self._registrar_falha_login()
                self._json(401, {"erro": "Usuario ou senha incorretos."})
                return
            self._limpar_falhas_login()
            token = nova_sessao(usuario)
            self._json(
                200,
                {"autenticado": True, "usuario": usuario},
                [("Set-Cookie", self._cookie_sessao(token, DURACAO_SESSAO))],
            )
            return

        if caminho == "/api/logout":
            _, token = sessao_atual(self)
            if token:
                SESSOES.pop(token, None)
            seguro = "; Secure" if COOKIE_SECURE else ""
            self._json(
                200,
                {"autenticado": False},
                [("Set-Cookie", f"sid=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0{seguro}")],
            )
            return

        if caminho == "/api/equipamentos":
            if self._exigir_login() is None:
                return
            try:
                item = banco.salvar_equipamento(dados)
            except (ValueError, sqlite3.Error):
                self._json(400, {"erro": "Nao foi possivel salvar o equipamento. Verifique os dados informados."})
                return
            self._json(201, {"item": item, "mensagem": "Equipamento cadastrado."})
            return

        if caminho == "/api/importar":
            if self._exigir_login("admin") is None:
                return
            try:
                resultado = banco.importar_planilha()
            except (FileNotFoundError, ValueError, OSError, RuntimeError, zipfile.BadZipFile):
                self._json(400, {"erro": "Nao foi possivel importar a planilha. Verifique o arquivo protegido e a senha configurada."})
                return
            self._json(200, {"resultado": resultado, "mensagem": "Planilha reimportada com sucesso."})
            return

        self._json(404, {"erro": "Endereco nao encontrado."})

    def do_PUT(self) -> None:
        caminho = urlparse(self.path).path.rstrip("/") or "/"
        if not self._origem_valida():
            self._json(403, {"erro": "Origem da requisicao nao autorizada."})
            return
        if self._exigir_login() is None:
            return
        match = re.fullmatch(r"/api/equipamentos/(\d+)", caminho)
        if not match:
            self._json(404, {"erro": "Endereco nao encontrado."})
            return
        try:
            dados = self._corpo_json()
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            self._json(400, {"erro": "Dados invalidos."})
            return
        try:
            item = banco.salvar_equipamento(dados, int(match.group(1)))
        except LookupError:
            self._json(404, {"erro": "Equipamento nao encontrado."})
            return
        except (ValueError, sqlite3.Error):
            self._json(400, {"erro": "Nao foi possivel salvar o equipamento. Verifique os dados informados."})
            return
        self._json(200, {"item": item, "mensagem": "Equipamento atualizado."})

    def do_DELETE(self) -> None:
        caminho = urlparse(self.path).path.rstrip("/") or "/"
        if not self._origem_valida():
            self._json(403, {"erro": "Origem da requisicao nao autorizada."})
            return
        if self._exigir_login("admin") is None:
            return
        match = re.fullmatch(r"/api/equipamentos/(\d+)", caminho)
        if not match:
            self._json(404, {"erro": "Endereco nao encontrado."})
            return
        if not banco.excluir_equipamento(int(match.group(1))):
            self._json(404, {"erro": "Equipamento nao encontrado."})
            return
        self._json(200, {"mensagem": "Equipamento removido."})


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass

    cfg = config.carregar_config()
    resultado = banco.inicializar()
    host = cfg.get("HOST", "127.0.0.1")
    porta = int(cfg.get("PORTA", "8000"))
    servidor = ThreadingHTTPServer((host, porta), Handler)
    servidor.daemon_threads = True

    print("Inventario de Notebooks | Patrimonio TI")
    print(f"Registros no banco: {resultado['total']}")
    if resultado.get("arquivo"):
        print(f"Planilha de origem: {resultado['arquivo']}")
    print(f"Acesse: http://{host}:{porta}")
    print("Pressione Ctrl+C para encerrar.")

    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor encerrado.")
    finally:
        servidor.server_close()


if __name__ == "__main__":
    main()
