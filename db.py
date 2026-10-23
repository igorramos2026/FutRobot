"""
Camada de acesso ao banco de dados.

MELHORIAS aplicadas aqui:
1. Conexões via context manager (`get_conn`) que garante commit/rollback E
   fechamento da conexão mesmo se uma exceção ocorrer no meio do caminho.
   ATENÇÃO (pegadinha comum): `with sqlite3.connect(...) as conn:` sozinho
   NÃO fecha a conexão automaticamente — só faz commit/rollback. Por isso
   usamos um contextmanager próprio que fecha no `finally`.
2. PRAGMA journal_mode=WAL, que reduz erros de "database is locked" quando
   várias abas/processos acessam o banco ao mesmo tempo.
3. Deduplicação: um índice único em (data_registro, hora, jogo, script_origem)
   evita que a mesma oportunidade seja inserida duas vezes (ex: clique duplo
   em "Enviar para Gestão de Banca", ou reprocessar o mesmo arquivo do dia).
4. Caminho do banco absoluto (vem de config.DB_PATH), não depende mais do
   diretório de onde o Streamlit foi iniciado.
"""

import sqlite3
from contextlib import contextmanager

import pandas as pd

from config import DB_PATH, SCRIPTS_DISPONIVEIS
from log_config import logger


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        c = conn.cursor()
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS entradas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                data_registro TEXT,
                hora TEXT,
                jogo TEXT,
                liga TEXT,
                script_origem TEXT,
                recomendacao TEXT,
                score_confianca TEXT DEFAULT 'N/A',
                odd_sugerida REAL,
                odd_comprada REAL DEFAULT 0.0,
                valor_apostado REAL DEFAULT 0.0,
                lucro_prejuizo REAL DEFAULT 0.0,
                status TEXT DEFAULT 'Pendente',
                placar_ao_vivo TEXT DEFAULT 'Aguardando início'
            )
            """
        )

        # Migrações idempotentes para bancos criados por versões antigas do app
        c.execute("PRAGMA table_info(entradas)")
        cols = [col[1] for col in c.fetchall()]
        if "placar_ao_vivo" not in cols:
            c.execute(
                "ALTER TABLE entradas ADD COLUMN placar_ao_vivo TEXT DEFAULT 'Aguardando início'"
            )
        if "score_confianca" not in cols:
            c.execute(
                "ALTER TABLE entradas ADD COLUMN score_confianca TEXT DEFAULT 'N/A'"
            )

        # Índice único -> protege contra oportunidades duplicadas
        try:
            c.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_entradas_unica
                ON entradas(data_registro, hora, jogo, script_origem)
                """
            )
        except sqlite3.IntegrityError:
            logger.warning(
                "Não foi possível criar o índice único de deduplicação: já existem "
                "registros duplicados na tabela 'entradas'. A proteção automática contra "
                "duplicatas ficará desativada até os duplicados existentes serem limpos."
            )

        c.execute(
            """
            CREATE TABLE IF NOT EXISTS configuracoes (
                script_nome TEXT PRIMARY KEY,
                stake_padrao REAL DEFAULT 50.0,
                top_n INTEGER DEFAULT 10,
                permitir_copas INTEGER DEFAULT 0,
                permitir_sub20 INTEGER DEFAULT 0,
                permitir_feminino INTEGER DEFAULT 0
            )
            """
        )
        for s in SCRIPTS_DISPONIVEIS:
            c.execute(
                """INSERT OR IGNORE INTO configuracoes
                   (script_nome, stake_padrao, top_n, permitir_copas, permitir_sub20, permitir_feminino)
                   VALUES (?, 50.0, 10, 0, 0, 0)""",
                (s,),
            )


def carregar_configuracoes():
    with get_conn() as conn:
        df_cfg = pd.read_sql_query("SELECT * FROM configuracoes", conn)
    configs = {}
    for _, row in df_cfg.iterrows():
        configs[row["script_nome"]] = {
            "stake_padrao": float(row["stake_padrao"]),
            "top_n": int(row["top_n"]),
            "permitir_copas": bool(row["permitir_copas"]),
            "permitir_sub20": bool(row["permitir_sub20"]),
            "permitir_feminino": bool(row["permitir_feminino"]),
        }
    return configs


def salvar_configuracao(script_nome, stake_padrao, top_n, permitir_copas, permitir_sub20, permitir_feminino):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute(
            """
            INSERT OR REPLACE INTO configuracoes
            (script_nome, stake_padrao, top_n, permitir_copas, permitir_sub20, permitir_feminino)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (script_nome, stake_padrao, top_n, int(permitir_copas), int(permitir_sub20), int(permitir_feminino)),
        )


def inserir_oportunidades(oportunidades, data_registro):
    """Insere oportunidades novas, ignorando duplicatas (mesma data/hora/jogo/script).
    Retorna quantas foram efetivamente inseridas."""
    if not oportunidades:
        return 0
    with get_conn() as conn:
        c = conn.cursor()
        antes = c.execute("SELECT COUNT(*) FROM entradas").fetchone()[0]
        sql = """
            INSERT OR IGNORE INTO entradas
            (data_registro, hora, jogo, liga, script_origem, recomendacao, score_confianca, odd_sugerida, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Pendente')
        """
        for item in oportunidades:
            score_str = str(item.get("score_confianca", "N/A"))
            c.execute(
                sql,
                (
                    data_registro,
                    item["hora"],
                    item["jogo"],
                    item["liga"],
                    item["script"],
                    item["recomendacao"],
                    score_str,
                    item["odd"],
                ),
            )
        depois = c.execute("SELECT COUNT(*) FROM entradas").fetchone()[0]
    return depois - antes


def buscar_entradas_por_status(status):
    with get_conn() as conn:
        return pd.read_sql_query(
            "SELECT * FROM entradas WHERE LOWER(TRIM(status)) = ? ORDER BY script_origem ASC, hora ASC",
            conn,
            params=(status.lower(),),
        )


def confirmar_entrada(entrada_id, odd_comprada, valor_apostado):
    with get_conn() as conn:
        conn.execute(
            "UPDATE entradas SET status = 'Em Andamento', odd_comprada = ?, valor_apostado = ? WHERE id = ?",
            (odd_comprada, valor_apostado, entrada_id),
        )


def descartar_entrada(entrada_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM entradas WHERE id = ?", (entrada_id,))


def registrar_resultado(entrada_id, status, lucro_prejuizo):
    with get_conn() as conn:
        conn.execute(
            "UPDATE entradas SET status = ?, lucro_prejuizo = ? WHERE id = ?",
            (status, lucro_prejuizo, entrada_id),
        )


def buscar_historico():
    with get_conn() as conn:
        return pd.read_sql_query(
            "SELECT * FROM entradas WHERE LOWER(TRIM(status)) IN ('green', 'red', 'anulada')",
            conn,
        )


def editar_entrada(entrada_id, status, odd_comprada, valor_apostado, lucro_prejuizo):
    with get_conn() as conn:
        conn.execute(
            """UPDATE entradas SET status = ?, odd_comprada = ?, valor_apostado = ?, lucro_prejuizo = ?
               WHERE id = ?""",
            (status, odd_comprada, valor_apostado, lucro_prejuizo, entrada_id),
        )


def deletar_entrada(entrada_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM entradas WHERE id = ?", (entrada_id,))
