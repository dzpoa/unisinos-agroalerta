"""Acesso ao banco: SQLite (padrão, demo local) ou PostgreSQL/TimescaleDB (via DATABASE_URL).

O código usa placeholders "?"; para PostgreSQL eles são convertidos para "%s".
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

from comum.config import DATABASE_URL

POSTGRES = DATABASE_URL.startswith("postgres")

_TABELAS = {
    "leituras": """
        CREATE TABLE IF NOT EXISTS leituras (
            ts {ts} NOT NULL,
            recebido_em {ts} NOT NULL,
            dispositivo_id TEXT NOT NULL,
            celula_h3 TEXT NOT NULL,
            fonte TEXT NOT NULL,
            precip DOUBLE PRECISION,
            pressao DOUBLE PRECISION,
            temp DOUBLE PRECISION,
            umid DOUBLE PRECISION,
            vento_vel DOUBLE PRECISION,
            vento_dir DOUBLE PRECISION,
            rajada DOUBLE PRECISION
        )""",
    "previsoes": """
        CREATE TABLE IF NOT EXISTS previsoes (
            ts {ts} NOT NULL,
            calculado_em {ts} NOT NULL,
            codigo TEXT NOT NULL,
            celula_h3 TEXT NOT NULL,
            probabilidade DOUBLE PRECISION NOT NULL,
            limiar DOUBLE PRECISION NOT NULL,
            alerta BOOLEAN NOT NULL
        )""",
}
_INDICES = [
    "CREATE INDEX IF NOT EXISTS ix_leituras_celula_ts ON leituras (celula_h3, ts)",
    "CREATE INDEX IF NOT EXISTS ix_previsoes_codigo_ts ON previsoes (codigo, ts)",
]


def _sql(sql: str) -> str:
    return sql.replace("?", "%s") if POSTGRES else sql


@contextmanager
def conexao():
    if POSTGRES:
        import psycopg
        con = psycopg.connect(DATABASE_URL)
    else:
        caminho = DATABASE_URL.removeprefix("sqlite:///")
        Path(caminho).parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(caminho, timeout=30)
        con.execute("PRAGMA journal_mode=WAL")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def criar_esquema() -> None:
    tipo_ts = "TIMESTAMPTZ" if POSTGRES else "TEXT"
    with conexao() as con:
        cur = con.cursor()
        for ddl in _TABELAS.values():
            cur.execute(ddl.format(ts=tipo_ts))
        for ddl in _INDICES:
            cur.execute(ddl)
    if POSTGRES:
        _hipertabelas()


def _hipertabelas() -> None:
    """Converte as tabelas em hypertables do TimescaleDB, se a extensão existir."""
    try:
        with conexao() as con:
            cur = con.cursor()
            cur.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
            for tabela in _TABELAS:
                cur.execute(
                    f"SELECT create_hypertable('{tabela}', 'ts', if_not_exists => TRUE, migrate_data => TRUE)"
                )
    except Exception as erro:  # PostgreSQL puro: segue sem hypertables
        print(f"[db] TimescaleDB indisponível ({erro.__class__.__name__}); usando tabelas comuns.")


def executar(sql: str, parametros: tuple | list = ()) -> None:
    with conexao() as con:
        con.cursor().execute(_sql(sql), parametros)


def executar_varios(sql: str, linhas: list[tuple]) -> None:
    with conexao() as con:
        con.cursor().executemany(_sql(sql), linhas)


def consultar(sql: str, parametros: tuple | list = ()) -> pd.DataFrame:
    with conexao() as con:
        cur = con.cursor()
        cur.execute(_sql(sql), parametros)
        colunas = [c[0] for c in cur.description]
        df = pd.DataFrame(cur.fetchall(), columns=colunas)
    for col in ("ts", "recebido_em", "calculado_em"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], utc=True, format="ISO8601")
    return df
