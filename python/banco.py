"""Camada de dados do inventario.

Le a planilha XLSX (ou o cofre criptografado), grava os registros em SQLite
e disponibiliza as consultas usadas pelo servidor.

Credenciais vem do .env; nenhuma senha fica no codigo-fonte.
"""

from __future__ import annotations

import glob
import io
import re
import sqlite3
import unicodedata
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

import config
import seguranca

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "inventario.db"

NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}

CAMPOS = (
    "etiqueta",
    "numero_serie",
    "localizacao",
    "marca",
    "modelo",
    "estado_fisico",
    "status",
    "carregador",
    "data_levantamento",
    "responsavel_levantamento",
    "observacoes",
)

COLUNAS = {
    "id n etiqueta": "etiqueta",
    "n de serie": "numero_serie",
    "numero de serie": "numero_serie",
    "sala localizacao": "localizacao",
    "localizacao": "localizacao",
    "marca": "marca",
    "modelo": "modelo",
    "estado fisico": "estado_fisico",
    "status funcional": "status",
    "carregador incluso": "carregador",
    "data levantamento": "data_levantamento",
    "responsavel levantamento": "responsavel_levantamento",
    "observacoes": "observacoes",
}

ABAS_IGNORADAS = {
    "instrucoes",
    "etiquetas ds",
    "inventariocompleto",
    "inventario completo",
}

ESTADOS_CONHECIDOS = {
    "bom": "Bom",
    "regular": "Regular",
    "ruim": "Ruim",
    "danificado": "Danificado",
    "nao localizado": "Não localizado",
}

PERFIS_CONFIGURADOS = (
    ("LOGIN_ADMIN", "SENHA_ADMIN", "Administrador", "admin"),
    ("LOGIN_PROFESSOR", "SENHA_PROFESSOR", "Corpo docente", "professor"),
)

STATUS_FUNCIONANDO = "Funcionando"
STATUS_MANUTENCAO = "Aguardando reparo"
STATUS_SEM_INFO = "Sem informação"


def agora() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def chave(texto: object) -> str:
    """Remove acentos e pontuacao para comparar cabecalhos e valores."""
    bruto = str(texto or "").replace("º", "").replace("ª", "")
    sem_acento = unicodedata.normalize("NFKD", bruto)
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    sem_acento = sem_acento.lower().replace("/", " ").replace("-", " ")
    return re.sub(r"\s+", " ", sem_acento).strip()


def limpar(texto: object) -> str:
    return re.sub(r"\s+", " ", str(texto or "")).strip()


def classificar_status(valor: object) -> str:
    texto = chave(valor)
    if not texto:
        return STATUS_SEM_INFO
    if "nao liga" in texto or "nao inicializa" in texto or "desligado" in texto:
        return "Não liga"
    if "defeito" in texto or "com problema" in texto:
        return "Com defeito"
    if "reparo" in texto or "manutencao" in texto:
        return STATUS_MANUTENCAO
    if "funcionando" in texto or "normal" in texto:
        return STATUS_FUNCIONANDO
    return STATUS_SEM_INFO


def classificar_estado(valor: object) -> str:
    texto = limpar(valor)
    if not texto:
        return STATUS_SEM_INFO
    return ESTADOS_CONHECIDOS.get(chave(texto), texto[:60])


def classificar_carregador(valor: object) -> str:
    texto = chave(valor)
    if texto == "sim":
        return "Sim"
    if texto in {"nao", "não", "no"}:
        return "Não"
    return STATUS_SEM_INFO


def data_excel(valor: object) -> str:
    """Converte numeros de data do Excel para ISO; devolve texto inalterado."""
    texto = limpar(valor)
    if not texto:
        return ""
    if re.fullmatch(r"\d{4,6}(\.\d+)?", texto):
        numero = float(texto)
        if 1000 < numero < 80000:
            data = datetime(1899, 12, 30) + timedelta(days=numero)
            return data.strftime("%Y-%m-%d")
    return texto[:30]


def conectar() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def criar_tabelas() -> None:
    with conectar() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                login TEXT NOT NULL UNIQUE COLLATE NOCASE,
                nome TEXT NOT NULL,
                perfil TEXT NOT NULL CHECK (perfil IN ('professor', 'admin')),
                salt TEXT NOT NULL,
                hash TEXT NOT NULL,
                iteracoes INTEGER NOT NULL DEFAULT 120000
            );

            CREATE TABLE IF NOT EXISTS equipamentos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                etiqueta TEXT NOT NULL DEFAULT '',
                numero_serie TEXT NOT NULL DEFAULT '',
                localizacao TEXT NOT NULL DEFAULT '',
                marca TEXT NOT NULL DEFAULT '',
                modelo TEXT NOT NULL DEFAULT '',
                estado_fisico TEXT NOT NULL DEFAULT 'Sem informação',
                status TEXT NOT NULL DEFAULT 'Sem informação',
                carregador TEXT NOT NULL DEFAULT 'Sem informação',
                data_levantamento TEXT NOT NULL DEFAULT '',
                responsavel_levantamento TEXT NOT NULL DEFAULT '',
                observacoes TEXT NOT NULL DEFAULT '',
                origem TEXT NOT NULL DEFAULT '',
                modificacao TEXT NOT NULL DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_equip_status ON equipamentos(status);
            CREATE INDEX IF NOT EXISTS idx_equip_local ON equipamentos(localizacao);
            CREATE INDEX IF NOT EXISTS idx_equip_origem ON equipamentos(origem);

            CREATE TABLE IF NOT EXISTS metadados (
                chave TEXT PRIMARY KEY,
                valor TEXT NOT NULL
            );
            """
        )
        colunas = {linha[1] for linha in conn.execute("PRAGMA table_info(usuarios)")}
        if "iteracoes" not in colunas:
            conn.execute(
                "ALTER TABLE usuarios ADD COLUMN iteracoes INTEGER NOT NULL DEFAULT 120000"
            )


def sincronizar_usuarios(credenciais: dict[str, str]) -> None:
    """Grava apenas hashes das credenciais configuradas no .env local."""
    with conectar() as conn:
        for chave_login, chave_senha, nome, perfil in PERFIS_CONFIGURADOS:
            login = limpar(credenciais.get(chave_login))[:120]
            senha = credenciais.get(chave_senha, "")
            if not login or not senha:
                raise ValueError(f"Credencial ausente no .env: {chave_login}/{chave_senha}")
            hash_hex, salt, iteracoes = seguranca.gerar_hash_senha(senha)
            conn.execute(
                """
                INSERT INTO usuarios (login, nome, perfil, salt, hash, iteracoes)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(login) DO UPDATE SET
                    nome = excluded.nome,
                    perfil = excluded.perfil,
                    salt = excluded.salt,
                    hash = excluded.hash,
                    iteracoes = excluded.iteracoes
                """,
                (login, nome, perfil, salt, hash_hex, iteracoes),
            )


def verificar_login(login: str, senha: str) -> dict | None:
    with conectar() as conn:
        linha = conn.execute(
            "SELECT * FROM usuarios WHERE login = ? COLLATE NOCASE", (limpar(login),)
        ).fetchone()
        if linha is None:
            return None
        if not seguranca.verificar_senha(
            senha, linha["hash"], linha["salt"], linha["iteracoes"]
        ):
            return None
        if linha["iteracoes"] < seguranca.PBKDF2_ITERACOES:
            hash_hex, salt, iteracoes = seguranca.gerar_hash_senha(senha)
            conn.execute(
                "UPDATE usuarios SET hash = ?, salt = ?, iteracoes = ? WHERE id = ?",
                (hash_hex, salt, iteracoes, linha["id"]),
            )
        return {
            "id": linha["id"],
            "login": linha["login"],
            "nome": linha["nome"],
            "perfil": linha["perfil"],
        }


def localizar_planilha() -> Path | None:
    cfg = config.carregar_config()
    configurado = cfg.get("PLANILHA_PATH", "")
    if configurado:
        candidato = Path(configurado)
        if not candidato.is_absolute():
            candidato = BASE_DIR / candidato
        if candidato.is_file():
            return candidato.resolve()

    candidatos: list[Path] = []
    for padrao in ("*.xlsx", "*.xlsm", "*.xls", "*.enc", "dados/*.enc"):
        candidatos.extend(Path(p) for p in glob.glob(str(BASE_DIR / padrao)))
    candidatos = [
        p
        for p in candidatos
        if p.is_file() and not p.name.startswith("~$") and p.suffix.lower() != ".xls"
    ]
    if not candidatos:
        return None
    return max(
        candidatos,
        key=lambda p: (("invent" in p.stem.lower()) or ("etiquet" in p.stem.lower()), p.stat().st_size),
    )


def valor_celula(celula: ET.Element, compartilhadas: list[str]) -> str:
    tipo = celula.get("t")
    if tipo == "inlineStr":
        return "".join(no.text or "" for no in celula.findall(".//m:t", NS))
    no = celula.find("m:v", NS)
    if no is None:
        return ""
    bruto = no.text or ""
    if tipo == "s":
        try:
            return compartilhadas[int(bruto)]
        except (ValueError, IndexError):
            return ""
    if tipo == "b":
        return "Sim" if bruto == "1" else "Não"
    return bruto


def caminho_interno(destino: str) -> str:
    alvo = destino.lstrip("/")
    return alvo if alvo.startswith("xl/") else "xl/" + alvo


def abas_do_arquivo(arquivo: zipfile.ZipFile) -> list[tuple[str, str]]:
    workbook = ET.fromstring(arquivo.read("xl/workbook.xml"))
    rels = ET.fromstring(arquivo.read("xl/_rels/workbook.xml.rels"))
    mapa = {rel.get("Id"): rel.get("Target") for rel in rels}
    abas = []
    for aba in workbook.findall(".//m:sheet", NS):
        destino = mapa.get(aba.get("{" + NS["r"] + "}id"), "")
        if destino:
            abas.append((aba.get("name") or "Aba", caminho_interno(destino)))
    return abas


def compartilhadas_do_arquivo(arquivo: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in arquivo.namelist():
        return []
    raiz = ET.fromstring(arquivo.read("xl/sharedStrings.xml"))
    return [
        "".join(no.text or "" for no in item.findall(".//m:t", NS))
        for item in raiz.findall("m:si", NS)
    ]


def linhas_da_aba(arquivo: zipfile.ZipFile, destino: str, compartilhadas: list[str], origem: str) -> list[dict]:
    raiz = ET.fromstring(arquivo.read(destino))
    linhas = raiz.findall(".//m:sheetData/m:row", NS)
    indice_cabecalho = None
    colunas: dict[str, str] = {}

    for posicao, linha in enumerate(linhas[:12]):
        encontradas: dict[str, str] = {}
        for celula in linha.findall("m:c", NS):
            referencia = celula.get("r") or ""
            letras = re.match(r"([A-Z]+)", referencia)
            if not letras:
                continue
            titulo = chave(valor_celula(celula, compartilhadas))
            campo = COLUNAS.get(titulo)
            if campo:
                encontradas[letras.group(1)] = campo
        if "etiqueta" in encontradas.values() and "status" in encontradas.values():
            indice_cabecalho = posicao
            colunas = encontradas
            break

    if indice_cabecalho is None:
        return []

    registros = []
    for linha in linhas[indice_cabecalho + 1 :]:
        valores = {campo: "" for campo in CAMPOS}
        for celula in linha.findall("m:c", NS):
            referencia = celula.get("r") or ""
            letras = re.match(r"([A-Z]+)", referencia)
            campo = colunas.get(letras.group(1)) if letras else None
            if campo:
                valores[campo] = valor_celula(celula, compartilhadas)

        if not any(limpar(valores[campo]) for campo in CAMPOS):
            continue

        registro = {
            "etiqueta": limpar(valores["etiqueta"])[:80],
            "numero_serie": limpar(valores["numero_serie"])[:120],
            "localizacao": limpar(valores["localizacao"])[:120],
            "marca": limpar(valores["marca"])[:80],
            "modelo": limpar(valores["modelo"])[:80],
            "estado_fisico": classificar_estado(valores["estado_fisico"]),
            "status": classificar_status(valores["status"]),
            "carregador": classificar_carregador(valores["carregador"]),
            "data_levantamento": data_excel(valores["data_levantamento"]),
            "responsavel_levantamento": limpar(valores["responsavel_levantamento"])[:120],
            "observacoes": limpar(valores["observacoes"])[:1000],
            "origem": origem[:120],
            "modificacao": agora(),
        }
        # A planilha tem linhas reservadas apenas com o codigo da etiqueta.
        # Elas nao representam equipamentos levantados e por isso sao ignoradas.
        sem_levantamento = (
            not registro["numero_serie"]
            and registro["estado_fisico"] == STATUS_SEM_INFO
            and registro["status"] == STATUS_SEM_INFO
            and not registro["observacoes"]
        )
        if sem_levantamento:
            continue
        registros.append(registro)
    return registros


def abrir_planilha(caminho: Path) -> zipfile.ZipFile:
    if caminho.suffix.lower() == ".enc":
        senha = config.carregar_config().get("SENHA_PLANILHA", "")
        if not senha:
            raise ValueError("Defina SENHA_PLANILHA no .env para abrir o cofre da planilha.")
        return zipfile.ZipFile(io.BytesIO(seguranca.descriptografar_arquivo(caminho, senha)))
    return zipfile.ZipFile(caminho)


def importar_planilha(caminho: Path | None = None) -> dict:
    arquivo = caminho or localizar_planilha()
    if arquivo is None or not arquivo.exists():
        raise FileNotFoundError("Nenhuma planilha de inventario foi encontrada.")

    nome_amigavel = arquivo.name[:-4] if arquivo.suffix.lower() == ".enc" else arquivo.name

    with abrir_planilha(arquivo) as pacote:
        compartilhadas = compartilhadas_do_arquivo(pacote)
        registros: list[dict] = []
        abas_usadas: list[str] = []
        for nome, destino in abas_do_arquivo(pacote):
            if chave(nome) in ABAS_IGNORADAS:
                continue
            encontrados = linhas_da_aba(pacote, destino, compartilhadas, nome)
            if encontrados:
                abas_usadas.append(nome)
                registros.extend(encontrados)

    if not registros:
        raise ValueError("A planilha nao continha registros de inventario.")

    with conectar() as conn:
        conn.execute("BEGIN")
        try:
            conn.execute("DELETE FROM equipamentos")
            conn.executemany(
                """
                INSERT INTO equipamentos (
                    etiqueta, numero_serie, localizacao, marca, modelo,
                    estado_fisico, status, carregador, data_levantamento,
                    responsavel_levantamento, observacoes, origem, modificacao
                ) VALUES (
                    :etiqueta, :numero_serie, :localizacao, :marca, :modelo,
                    :estado_fisico, :status, :carregador, :data_levantamento,
                    :responsavel_levantamento, :observacoes, :origem, :modificacao
                )
                """,
                registros,
            )
            conn.executemany(
                "INSERT OR REPLACE INTO metadados (chave, valor) VALUES (?, ?)",
                (
                    ("ultima_importacao", agora()),
                    ("planilha_origem", nome_amigavel),
                    ("total_importado", str(len(registros))),
                    ("abas_importadas", " | ".join(abas_usadas)),
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    return {
        "total": len(registros),
        "arquivo": nome_amigavel,
        "abas": abas_usadas,
    }


def obter_metadados() -> dict:
    with conectar() as conn:
        linhas = conn.execute("SELECT chave, valor FROM metadados").fetchall()
    return {linha["chave"]: linha["valor"] for linha in linhas}


def inicializar() -> dict:
    cfg = config.carregar_config()
    credenciais = config.garantir_credenciais(cfg)
    criar_tabelas()
    sincronizar_usuarios(credenciais)
    with conectar() as conn:
        total = conn.execute("SELECT COUNT(*) FROM equipamentos").fetchone()[0]
    if total == 0:
        try:
            return importar_planilha()
        except (FileNotFoundError, ValueError, zipfile.BadZipFile, RuntimeError):
            return {"total": 0, "arquivo": "", "abas": []}
    return {
        "total": total,
        "arquivo": obter_metadados().get("planilha_origem", ""),
        "abas": [],
    }


def listar_equipamentos(filtros: dict | None = None) -> list[dict]:
    filtros = filtros or {}
    condicoes: list[str] = []
    parametros: list[object] = []

    busca = limpar(filtros.get("q"))[:120]
    if busca:
        condicoes.append(
            "(etiqueta LIKE ? OR numero_serie LIKE ? OR marca LIKE ? "
            "OR modelo LIKE ? OR localizacao LIKE ? OR observacoes LIKE ?)"
        )
        parametros.extend([f"%{busca}%"] * 6)
    for chave_filtro, coluna in (
        ("status", "status"),
        ("localizacao", "localizacao"),
        ("origem", "origem"),
    ):
        valor = limpar(filtros.get(chave_filtro))[:120]
        if valor:
            condicoes.append(f"{coluna} = ?")
            parametros.append(valor)

    where = " WHERE " + " AND ".join(condicoes) if condicoes else ""
    with conectar() as conn:
        linhas = conn.execute(
            f"SELECT * FROM equipamentos{where} ORDER BY origem, etiqueta, id", parametros
        ).fetchall()
    return [dict(linha) for linha in linhas]


def obter_equipamento(identificador: int) -> dict | None:
    with conectar() as conn:
        linha = conn.execute(
            "SELECT * FROM equipamentos WHERE id = ?", (identificador,)
        ).fetchone()
    return dict(linha) if linha else None


def salvar_equipamento(dados: dict, identificador: int | None = None) -> dict:
    valores = {campo: limpar(dados.get(campo))[:1000] for campo in CAMPOS}
    valores["origem"] = limpar(dados.get("origem"))[:120]
    valores["status"] = classificar_status(valores["status"])
    valores["estado_fisico"] = classificar_estado(valores["estado_fisico"])
    valores["carregador"] = classificar_carregador(valores["carregador"])
    valores["data_levantamento"] = data_excel(valores["data_levantamento"])
    valores["modificacao"] = agora()

    with conectar() as conn:
        if identificador is None:
            valores["origem"] = valores["origem"] or "Cadastro manual"
            cursor = conn.execute(
                """
                INSERT INTO equipamentos (
                    etiqueta, numero_serie, localizacao, marca, modelo,
                    estado_fisico, status, carregador, data_levantamento,
                    responsavel_levantamento, observacoes, origem, modificacao
                ) VALUES (
                    :etiqueta, :numero_serie, :localizacao, :marca, :modelo,
                    :estado_fisico, :status, :carregador, :data_levantamento,
                    :responsavel_levantamento, :observacoes, :origem, :modificacao
                )
                """,
                valores,
            )
            identificador = cursor.lastrowid
        else:
            existente = conn.execute(
                "SELECT origem FROM equipamentos WHERE id = ?", (identificador,)
            ).fetchone()
            if existente is None:
                raise LookupError("Equipamento nao encontrado.")
            valores["origem"] = existente["origem"] or "Cadastro manual"
            conn.execute(
                """
                UPDATE equipamentos SET
                    etiqueta = :etiqueta,
                    numero_serie = :numero_serie,
                    localizacao = :localizacao,
                    marca = :marca,
                    modelo = :modelo,
                    estado_fisico = :estado_fisico,
                    status = :status,
                    carregador = :carregador,
                    data_levantamento = :data_levantamento,
                    responsavel_levantamento = :responsavel_levantamento,
                    observacoes = :observacoes,
                    modificacao = :modificacao
                WHERE id = :id
                """,
                {**valores, "id": identificador},
            )
    return obter_equipamento(identificador)


def excluir_equipamento(identificador: int) -> bool:
    with conectar() as conn:
        cursor = conn.execute("DELETE FROM equipamentos WHERE id = ?", (identificador,))
    return cursor.rowcount > 0


def obter_dashboard() -> dict:
    with conectar() as conn:
        resumo = conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status = 'Funcionando' THEN 1 ELSE 0 END) AS funcionando,
                SUM(CASE WHEN status IN ('Com defeito', 'Não liga', 'Aguardando reparo')
                         THEN 1 ELSE 0 END) AS nao_funcionando,
                SUM(CASE WHEN status = 'Aguardando reparo' THEN 1 ELSE 0 END) AS manutencao,
                SUM(CASE WHEN status = 'Sem informação' THEN 1 ELSE 0 END) AS sem_informacao,
                SUM(CASE WHEN status = 'Funcionando' AND carregador = 'Sim'
                         AND estado_fisico IN ('Bom', 'Regular') THEN 1 ELSE 0 END) AS disponiveis,
                SUM(CASE WHEN carregador = 'Sim' THEN 1 ELSE 0 END) AS com_carregador
            FROM equipamentos
            """
        ).fetchone()
        por_status = conn.execute(
            "SELECT status AS nome, COUNT(*) AS quantidade FROM equipamentos "
            "GROUP BY status ORDER BY quantidade DESC, nome"
        ).fetchall()
        por_estado = conn.execute(
            "SELECT estado_fisico AS nome, COUNT(*) AS quantidade FROM equipamentos "
            "GROUP BY estado_fisico ORDER BY quantidade DESC, nome"
        ).fetchall()
        por_marca = conn.execute(
            "SELECT marca AS nome, COUNT(*) AS quantidade FROM equipamentos "
            "GROUP BY marca ORDER BY quantidade DESC, nome LIMIT 8"
        ).fetchall()
        por_local = conn.execute(
            """
            SELECT
                localizacao AS nome,
                COUNT(*) AS total,
                SUM(CASE WHEN status = 'Funcionando' THEN 1 ELSE 0 END) AS funcionando,
                SUM(CASE WHEN status = 'Funcionando' AND carregador = 'Sim'
                         AND estado_fisico IN ('Bom', 'Regular') THEN 1 ELSE 0 END) AS disponiveis,
                SUM(CASE WHEN status IN ('Com defeito', 'Não liga', 'Aguardando reparo')
                         THEN 1 ELSE 0 END) AS indisponiveis,
                SUM(CASE WHEN status = 'Sem informação' THEN 1 ELSE 0 END) AS sem_informacao
            FROM equipamentos
            GROUP BY localizacao
            ORDER BY total DESC, nome
            """
        ).fetchall()

    resultado = {chave: int(valor or 0) for chave, valor in dict(resumo).items()}
    resultado["porStatus"] = [dict(linha) for linha in por_status]
    resultado["porEstado"] = [dict(linha) for linha in por_estado]
    resultado["porMarca"] = [dict(linha) for linha in por_marca]
    resultado["porLocal"] = [dict(linha) for linha in por_local]
    resultado["metadados"] = obter_metadados()
    resultado["geradoEm"] = agora()
    return resultado


COLUNAS_EXPORTACAO = (
    ("Etiqueta", "etiqueta", 18),
    ("Numero de serie", "numero_serie", 24),
    ("Localizacao", "localizacao", 24),
    ("Marca", "marca", 16),
    ("Modelo", "modelo", 20),
    ("Estado fisico", "estado_fisico", 18),
    ("Status funcional", "status", 20),
    ("Carregador incluso", "carregador", 20),
    ("Data do levantamento", "data_levantamento", 22),
    ("Responsavel do levantamento", "responsavel_levantamento", 28),
    ("Observacoes", "observacoes", 50),
    ("Origem", "origem", 24),
    ("Ultima alteracao", "modificacao", 22),
)


def _celula_texto(referencia: str, valor: object, estilo: int = 0) -> str:
    texto = escape(str(valor or ""))
    atributo_estilo = f' s="{estilo}"' if estilo else ""
    return f'<c r="{referencia}" t="inlineStr"{atributo_estilo}><is><t>{texto}</t></is></c>'


def exportar_xlsx() -> bytes:
    """Monta uma planilha XLSX valida com os dados atuais do banco."""
    itens = listar_equipamentos()
    letras = [chr(65 + indice) for indice in range(len(COLUNAS_EXPORTACAO))]
    ultima = letras[-1]
    ultima_linha = len(itens) + 1

    linhas = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
        f'<dimension ref="A1:{ultima}{ultima_linha}"/>',
        '<sheetViews><sheetView workbookViewId="0">'
        '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
        '</sheetView></sheetViews>',
        "<cols>"
        + "".join(
            f'<col min="{indice + 1}" max="{indice + 1}" width="{largura}" customWidth="1"/>'
            for indice, (_, _, largura) in enumerate(COLUNAS_EXPORTACAO)
        )
        + "</cols>",
        "<sheetData>",
    ]

    cabecalho = "".join(
        _celula_texto(f"{letra}1", titulo, 1)
        for letra, (titulo, _, _) in zip(letras, COLUNAS_EXPORTACAO)
    )
    linhas.append(f'<row r="1" ht="22" customHeight="1">{cabecalho}</row>')

    for numero_linha, item in enumerate(itens, start=2):
        celulas = "".join(
            _celula_texto(f"{letra}{numero_linha}", item[campo])
            for letra, (_, campo, _) in zip(letras, COLUNAS_EXPORTACAO)
        )
        linhas.append(f'<row r="{numero_linha}">{celulas}</row>')

    linhas.append("</sheetData>")
    linhas.append(f'<autoFilter ref="A1:{ultima}{ultima_linha}"/>')
    linhas.append("</worksheet>")

    tipos = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        "</Types>"
    )
    rels_raiz = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Inventario" sheetId="1" r:id="rId1"/></sheets>'
        "</workbook>"
    )
    rels_workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        "</Relationships>"
    )
    estilos = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="2">'
        '<font><sz val="11"/><name val="Calibri"/></font>'
        '<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>'
        "</fonts>"
        '<fills count="3">'
        '<fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FF1D4ED8"/><bgColor indexed="64"/></patternFill></fill>'
        "</fills>"
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="2">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>'
        "</cellXfs>"
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        "</styleSheet>"
    )

    pacote = {
        "[Content_Types].xml": tipos,
        "_rels/.rels": rels_raiz,
        "xl/workbook.xml": workbook,
        "xl/_rels/workbook.xml.rels": rels_workbook,
        "xl/styles.xml": estilos,
        "xl/worksheets/sheet1.xml": "".join(linhas),
    }

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as arquivo:
        for nome, conteudo in pacote.items():
            arquivo.writestr(nome, conteudo.encode("utf-8"))
    return buffer.getvalue()
