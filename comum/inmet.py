"""Leitura dos CSVs de dados históricos das estações automáticas do INMET.

Formato (portal.inmet.gov.br/dadoshistoricos): 8 linhas de metadados ("CHAVE:;valor"),
depois a tabela horária separada por ";" com decimal ",", codificação latin-1 e
valores ausentes como -9999. O cabeçalho mudou em 2019 ("DATA (YYYY-MM-DD)"/"HORA (UTC)"
antes; "Data"/"Hora UTC" depois) — ambos são suportados.
"""
from __future__ import annotations

import io
import re
import unicodedata

import numpy as np
import pandas as pd

# Palavras-chave (sem acento, maiúsculas) -> variável padronizada.
MAPA_COLUNAS = {
    "PRECIPITACAO TOTAL": "precip",
    "PRESSAO ATMOSFERICA AO NIVEL DA ESTACAO": "pressao",
    "TEMPERATURA DO AR - BULBO SECO": "temp",
    "UMIDADE RELATIVA DO AR, HORARIA": "umid",
    "VENTO, DIRECAO HORARIA": "vento_dir",
    "VENTO, VELOCIDADE HORARIA": "vento_vel",
    "VENTO, RAJADA MAXIMA": "rajada",
}

# Faixas físicas plausíveis; fora delas o valor vira NaN.
FAIXAS = {
    "precip": (0, 150), "pressao": (850, 1060), "temp": (-15, 50), "umid": (1, 100),
    "vento_dir": (0, 360), "vento_vel": (0, 50), "rajada": (0, 70),
}


def _normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", sem_acento).strip().upper()


def _numero(valor: str) -> float | None:
    try:
        return float(str(valor).strip().replace(",", "."))
    except ValueError:
        return None


def ler_csv_inmet(conteudo: bytes) -> tuple[dict, pd.DataFrame]:
    """Retorna (metadados, DataFrame horário indexado por timestamp UTC)."""
    texto = conteudo.decode("latin-1")
    linhas = texto.splitlines()

    meta: dict = {}
    for linha in linhas[:8]:
        if ":" not in linha:
            continue
        chave, _, valor = linha.partition(":")
        chave, valor = _normalizar(chave), valor.lstrip(";").strip()
        if chave.startswith("CODIGO"):
            meta["codigo"] = valor.upper()
        elif chave.startswith("ESTACAO"):
            meta["nome"] = valor.title()
        elif chave.startswith("LATITUDE"):
            meta["lat"] = _numero(valor)
        elif chave.startswith("LONGITUDE"):
            meta["lon"] = _numero(valor)
        elif chave.startswith("ALTITUDE"):
            meta["alt"] = _numero(valor)

    tabela = pd.read_csv(io.StringIO("\n".join(linhas[8:])), sep=";", decimal=",",
                         dtype=str, na_values=["", "-9999", "-9999,0"])
    tabela.columns = [_normalizar(c) for c in tabela.columns]

    col_data = next(c for c in tabela.columns if c.startswith("DATA"))
    col_hora = next(c for c in tabela.columns if c.startswith("HORA"))
    data = tabela[col_data].str.replace("/", "-", regex=False)
    hora = tabela[col_hora].str.extract(r"(\d{2}):?(\d{2})").agg(":".join, axis=1)
    ts = pd.to_datetime(data + " " + hora, format="%Y-%m-%d %H:%M", utc=True, errors="coerce")

    saida = pd.DataFrame(index=ts)
    for chave, var in MAPA_COLUNAS.items():
        col = next((c for c in tabela.columns if c.startswith(chave)), None)
        valores = (pd.to_numeric(tabela[col].str.replace(",", ".", regex=False), errors="coerce")
                   if col else pd.Series(np.nan, index=tabela.index))
        saida[var] = valores.to_numpy()
    saida = saida[saida.index.notna()]
    saida = saida[~saida.index.duplicated(keep="first")].sort_index()

    for var, (minimo, maximo) in FAIXAS.items():
        saida.loc[(saida[var] < minimo) | (saida[var] > maximo), var] = np.nan
    saida.index.name = "ts"
    return meta, saida
