"""Lê os CSVs do INMET (soltos ou dentro dos ZIPs anuais) e gera a base horária consolidada.

Uso: python -m ia.preparar_dados [--entrada dados/brutos]
Saídas: dados/processados/horario.parquet, estacoes.json e relatorios/cobertura_dados.csv
"""
from __future__ import annotations

import argparse
import json
import re
import zipfile
from pathlib import Path

import pandas as pd

from comum.config import (ARQ_ESTACOES, ARQ_HORARIO, DIR_PROCESSADOS, DIR_RELATORIOS,
                          ESTACOES_BASE, VARIAVEIS)
from comum.inmet import ler_csv_inmet

PADRAO_CODIGO = re.compile(r"_(A\d{3})_", re.IGNORECASE)


def _arquivos(entrada: Path):
    """Gera (nome, bytes) dos CSVs das estações configuradas, inclusive dentro de ZIPs."""
    for caminho in sorted(entrada.rglob("*")):
        if caminho.suffix.lower() == ".csv":
            yield caminho.name, caminho.read_bytes()
        elif caminho.suffix.lower() == ".zip":
            with zipfile.ZipFile(caminho) as zf:
                for membro in zf.namelist():
                    cod = PADRAO_CODIGO.search(Path(membro).name)
                    if membro.lower().endswith(".csv") and cod and cod.group(1).upper() in ESTACOES_BASE:
                        yield Path(membro).name, zf.read(membro)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--entrada", default="dados/brutos")
    args = ap.parse_args()

    blocos, metadados = [], {}
    for nome, conteudo in _arquivos(Path(args.entrada)):
        cod = PADRAO_CODIGO.search(nome)
        if not cod or cod.group(1).upper() not in ESTACOES_BASE:
            continue
        meta, df = ler_csv_inmet(conteudo)
        codigo = meta.get("codigo") or cod.group(1).upper()
        metadados[codigo] = {k: meta.get(k) for k in ("nome", "lat", "lon", "alt")}
        blocos.append(df.assign(codigo=codigo))
        print(f"  {nome}: {len(df)} linhas")

    if not blocos:
        raise SystemExit(f"Nenhum CSV das estações {list(ESTACOES_BASE)} encontrado em {args.entrada}.")

    longo = pd.concat(blocos).reset_index()
    longo = longo.drop_duplicates(["ts", "codigo"]).sort_values(["codigo", "ts"])
    DIR_PROCESSADOS.mkdir(parents=True, exist_ok=True)
    longo.to_parquet(ARQ_HORARIO, index=False)
    ARQ_ESTACOES.write_text(json.dumps(metadados, ensure_ascii=False, indent=2), encoding="utf-8")

    # Cobertura: % de horas com dado válido por estação e variável.
    DIR_RELATORIOS.mkdir(parents=True, exist_ok=True)
    cobertura = longo.groupby("codigo")[VARIAVEIS].apply(lambda g: g.notna().mean() * 100).round(1)
    cobertura.insert(0, "inicio", longo.groupby("codigo")["ts"].min().dt.date)
    cobertura.insert(1, "fim", longo.groupby("codigo")["ts"].max().dt.date)
    cobertura.to_csv(DIR_RELATORIOS / "cobertura_dados.csv")
    faltando = sorted(set(ESTACOES_BASE) - set(metadados))
    print(f"\nBase horária: {len(longo):,} linhas, {len(metadados)} estações -> {ARQ_HORARIO}")
    print(cobertura.to_string())
    if faltando:
        print(f"\nATENÇÃO: estações sem dados: {faltando}")


if __name__ == "__main__":
    main()
