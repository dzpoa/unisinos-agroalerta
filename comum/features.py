"""Engenharia de atributos — compartilhada entre o treino (ia/) e o motor de alertas (servicos/).

Entrada: DataFrame "largo" horário, índice = timestamp UTC, colunas MultiIndex (codigo, variavel).
Cada atributo no instante t usa apenas informação disponível até t (sem contaminação
(data leakage)); o alvo usa exclusivamente as horas t+1 .. t+HORIZONTE_H.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from comum.config import HORIZONTE_H, LIMIAR_MM


def para_largo(longo: pd.DataFrame) -> pd.DataFrame:
    """Formato longo (ts, codigo, variáveis...) -> largo com grade horária contínua."""
    largo = longo.pivot_table(index="ts", columns="codigo", aggfunc="mean")
    largo.columns = largo.columns.swaplevel(0, 1)
    largo = largo.sort_index(axis=1)
    grade = pd.date_range(largo.index.min(), largo.index.max(), freq="h", tz="UTC")
    return largo.reindex(grade)


def _atributos_estacao(df: pd.DataFrame) -> pd.DataFrame:
    """Atributos de uma estação (df com colunas = variáveis)."""
    p = df["precip"]
    a = pd.DataFrame(index=df.index)
    a["precip_1h"] = p
    a["precip_3h"] = p.rolling(3, min_periods=1).sum()
    a["precip_6h"] = p.rolling(6, min_periods=1).sum()
    a["precip_24h"] = p.rolling(24, min_periods=1).sum()
    a["pressao"] = df["pressao"]
    for h in (1, 3, 6):
        a[f"dp_{h}h"] = df["pressao"] - df["pressao"].shift(h)
    a["temp"] = df["temp"]
    a["dtemp_3h"] = df["temp"] - df["temp"].shift(3)
    a["umid"] = df["umid"]
    a["dumid_3h"] = df["umid"] - df["umid"].shift(3)
    rad = np.deg2rad(df["vento_dir"])
    a["vento_u"] = -df["vento_vel"] * np.sin(rad)  # componente zonal (de onde sopra -> para onde vai)
    a["vento_v"] = -df["vento_vel"] * np.cos(rad)
    a["rajada"] = df["rajada"]
    a["rajada_max_3h"] = df["rajada"].rolling(3, min_periods=1).max()
    return a


VARS_VIZINHO = ["precip_1h", "precip_3h", "precip_6h", "dp_1h", "dp_3h", "umid", "dumid_3h", "rajada_max_3h"]


def montar_atributos(largo: pd.DataFrame, alvo: str, usar_vizinhos: bool) -> pd.DataFrame:
    """Matriz de atributos para prever a chuva na estação `alvo`."""
    codigos = list(dict.fromkeys(largo.columns.get_level_values(0)))
    locais = _atributos_estacao(largo[alvo]).add_prefix("local_")
    partes = [locais]

    if usar_vizinhos:
        precip_3h_viz = []
        for cod in codigos:
            if cod == alvo:
                continue
            viz = _atributos_estacao(largo[cod])[VARS_VIZINHO]
            # Defasagens extras da chuva do vizinho: a célula leva horas para chegar.
            for h in (1, 2, 3, 6):
                viz[f"precip_3h_lag{h}"] = viz["precip_3h"].shift(h)
            precip_3h_viz.append(viz["precip_3h"].rename(cod))
            partes.append(viz.add_prefix(f"{cod}_"))
        tabela = pd.concat(precip_3h_viz, axis=1)
        agregados = pd.DataFrame(index=largo.index)
        agregados["viz_precip_3h_max"] = tabela.max(axis=1)
        agregados["viz_precip_3h_media"] = tabela.mean(axis=1)
        agregados["viz_n_chovendo"] = (tabela >= 1.0).sum(axis=1)
        partes.append(agregados)

    hora, mes = largo.index.hour, largo.index.month
    partes.append(pd.DataFrame({
        "hora_sin": np.sin(2 * np.pi * hora / 24), "hora_cos": np.cos(2 * np.pi * hora / 24),
        "mes_sin": np.sin(2 * np.pi * mes / 12), "mes_cos": np.cos(2 * np.pi * mes / 12),
    }, index=largo.index))
    return pd.concat(partes, axis=1)


def montar_alvo(largo: pd.DataFrame, alvo: str) -> pd.Series:
    """1 se a chuva acumulada em t+1..t+H >= LIMIAR_MM; NaN se faltar dado no horizonte."""
    p = largo[alvo]["precip"]
    futuro = sum(p.shift(-h) for h in range(1, HORIZONTE_H + 1))
    y = (futuro >= LIMIAR_MM).astype(float)
    y[futuro.isna()] = np.nan
    return y.rename("alvo")
