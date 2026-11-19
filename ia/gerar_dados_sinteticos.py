"""Gera CSVs SINTÉTICOS no formato do INMET para testar o pipeline sem os dados reais.

As tempestades sintéticas nascem a oeste da região e se deslocam para leste/nordeste,
imitando o padrão típico do RS. NÃO usar estes dados nos resultados do relatório.

Uso: python -m ia.gerar_dados_sinteticos --saida dados/brutos/sinteticos
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from comum.config import ESTACOES_BASE

KM_POR_GRAU = 111.0


def simular(inicio: str, fim: str, semente: int = 42) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(semente)
    tempos = pd.date_range(inicio, fim, freq="h", tz="UTC")
    n = len(tempos)
    codigos = list(ESTACOES_BASE)
    lat = np.array([ESTACOES_BASE[c]["lat"] for c in codigos])
    lon = np.array([ESTACOES_BASE[c]["lon"] for c in codigos])
    alt = rng.uniform(80, 420, len(codigos))

    dia = 2 * np.pi * tempos.hour.to_numpy() / 24
    ano = 2 * np.pi * tempos.dayofyear.to_numpy() / 365.25
    # Sinal sinótico comum (frentes): passeio aleatório suavizado.
    sinotico = pd.Series(rng.normal(0, 0.6, n)).rolling(36, min_periods=1).mean().cumsum()
    sinotico = (sinotico - sinotico.rolling(24 * 10, min_periods=1).mean()).to_numpy() * 2.5

    chuva = rng.exponential(0.3, (n, len(codigos))) * (rng.random((n, len(codigos))) < 0.02)
    queda_p = np.zeros((n, len(codigos)))
    rajada_extra = np.zeros((n, len(codigos)))
    umid_extra = np.zeros((n, len(codigos)))

    n_tempestades = int(n / 24 / 3.5)
    for _ in range(n_tempestades):
        t0 = rng.integers(0, n - 48)
        duracao = rng.integers(10, 30)
        lat0, lon0 = rng.uniform(-31.8, -28.2), rng.uniform(-59.0, -57.5)
        angulo = np.deg2rad(rng.normal(20, 20))  # rumo leste-nordeste
        vel_kmh = rng.uniform(30, 60)
        raio_km = rng.uniform(35, 90)
        intensidade = rng.gamma(2.0, 4.0)
        for k in range(duracao):
            t = t0 + k
            desloc = vel_kmh * k / KM_POR_GRAU
            clat = lat0 + desloc * np.sin(angulo)
            clon = lon0 + desloc * np.cos(angulo) / np.cos(np.deg2rad(clat))
            dx = (lon - clon) * KM_POR_GRAU * np.cos(np.deg2rad(lat))
            dy = (lat - clat) * KM_POR_GRAU
            d = np.hypot(dx, dy)
            fase = np.sin(np.pi * (k + 1) / (duracao + 1))
            chuva[t] += intensidade * fase * np.exp(-(d / raio_km) ** 2)
            # À frente da célula (dx > 0): pressão cai e umidade sobe antes da chuva.
            frente = np.exp(-((d - 1.5 * raio_km) / raio_km) ** 2) * (dx > 0)
            queda_p[t] += 2.5 * fase * (frente + np.exp(-(d / raio_km) ** 2))
            umid_extra[t] += 15 * fase * (frente + np.exp(-(d / raio_km) ** 2))
            rajada_extra[t] += 10 * fase * np.exp(-(d / (0.8 * raio_km)) ** 2)

    saidas = {}
    for j, cod in enumerate(codigos):
        temp = (19 + 7 * np.cos(ano - 0.3) + 5 * np.sin(dia - 2.0)
                - 0.4 * chuva[:, j].clip(0, 10) + rng.normal(0, 0.8, n))
        pressao = 1013 - alt[j] * 0.12 + sinotico + 1.0 * np.sin(2 * dia) - queda_p[:, j] + rng.normal(0, 0.2, n)
        umid = np.clip(70 - 15 * np.sin(dia - 2.0) + umid_extra[:, j] + rng.normal(0, 4, n), 8, 100)
        vento_vel = np.abs(rng.normal(3, 1.2, n)) + rajada_extra[:, j] * 0.35
        df = pd.DataFrame({
            "precip": np.round(np.where(chuva[:, j] > 0.2, chuva[:, j], 0.0), 1),
            "pressao": np.round(pressao, 1), "temp": np.round(temp, 1), "umid": np.round(umid),
            "vento_dir": np.round(rng.uniform(0, 360, n)), "vento_vel": np.round(vento_vel, 1),
            "rajada": np.round(vento_vel * 1.8 + rajada_extra[:, j], 1),
        }, index=tempos)
        lacunas = rng.random(n) < 0.03
        df[lacunas] = np.nan
        df.attrs.update(lat=ESTACOES_BASE[cod]["lat"], lon=ESTACOES_BASE[cod]["lon"], alt=round(alt[j], 1))
        saidas[cod] = df
    return saidas


def _fmt(v: float) -> str:
    return "-9999" if pd.isna(v) else f"{v}".replace(".", ",")


def escrever_formato_inmet(df: pd.DataFrame, cod: str, ano: int, pasta: Path) -> None:
    nome = ESTACOES_BASE[cod]["nome"].upper()
    d = df[df.index.year == ano]
    novo = ano >= 2019
    meta = [
        "REGIAO:;S", "UF:;RS", f"ESTACAO:;{nome}", f"CODIGO (WMO):;{cod}",
        f"LATITUDE:;{_fmt(df.attrs['lat'])}", f"LONGITUDE:;{_fmt(df.attrs['lon'])}",
        f"ALTITUDE:;{_fmt(df.attrs['alt'])}", "DATA DE FUNDACAO:;2006-01-01",
    ]
    cab = ["Data" if novo else "DATA (YYYY-MM-DD)", "Hora UTC" if novo else "HORA (UTC)",
           "PRECIPITAÇÃO TOTAL, HORÁRIO (mm)", "PRESSAO ATMOSFERICA AO NIVEL DA ESTACAO, HORARIA (mB)",
           "TEMPERATURA DO AR - BULBO SECO, HORARIA (°C)", "UMIDADE RELATIVA DO AR, HORARIA (%)",
           "VENTO, DIREÇÃO HORARIA (gr) (° (gr))", "VENTO, RAJADA MAXIMA (m/s)", "VENTO, VELOCIDADE HORARIA (m/s)"]
    linhas = meta + [";".join(cab) + ";"]
    for ts, r in d.iterrows():
        data = ts.strftime("%Y/%m/%d" if novo else "%Y-%m-%d")
        hora = ts.strftime("%H%M UTC" if novo else "%H:%M")
        vals = [r.precip, r.pressao, r.temp, r.umid, r.vento_dir, r.rajada, r.vento_vel]
        linhas.append(";".join([data, hora] + [_fmt(v) for v in vals]) + ";")
    arq = pasta / f"INMET_S_RS_{cod}_{nome.replace(' ', '_')}_01-01-{ano}_A_31-12-{ano}.CSV"
    arq.write_bytes("\n".join(linhas).encode("latin-1"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--saida", default="dados/brutos/sinteticos")
    ap.add_argument("--inicio", default="2015-01-01")
    ap.add_argument("--fim", default="2024-12-31 23:00")
    args = ap.parse_args()
    pasta = Path(args.saida)
    pasta.mkdir(parents=True, exist_ok=True)
    dados = simular(args.inicio, args.fim)
    anos = range(pd.Timestamp(args.inicio).year, pd.Timestamp(args.fim).year + 1)
    for cod, df in dados.items():
        for ano in anos:
            escrever_formato_inmet(df, cod, ano, pasta)
    print(f"CSVs sintéticos gravados em {pasta} ({len(dados)} estações × {len(anos)} anos).")


if __name__ == "__main__":
    main()
