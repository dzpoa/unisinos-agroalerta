"""Painel AgroAlerta — rede colaborativa de alerta de chuva para produtores rurais.

Executar: streamlit run dashboard/app.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import h3
import pandas as pd
import pydeck as pdk
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from comum import db  # noqa: E402
from comum.config import DIR_RELATORIOS, ESTACAO_ALVO, PREFIXO, VARIAVEIS, estacoes  # noqa: E402
from comum.privacidade import celula  # noqa: E402

st.set_page_config(page_title="AgroAlerta", page_icon="🌧️", layout="wide")
EST = estacoes()
CELULAS = {cod: celula(m["lat"], m["lon"]) for cod, m in EST.items()}
COD_POR_CELULA = {v: k for k, v in CELULAS.items()}
NOMES_VARS = {"precip": "Chuva (mm/h)", "pressao": "Pressão (hPa)", "temp": "Temperatura (°C)",
              "umid": "Umidade (%)", "vento_vel": "Vento (m/s)", "rajada": "Rajada (m/s)"}


def _consulta_segura(sql: str, params: tuple = ()) -> pd.DataFrame:
    try:
        return db.consultar(sql, params)
    except Exception:
        return pd.DataFrame()


def ultima_rodada() -> tuple[pd.Timestamp | None, pd.DataFrame]:
    """Previsões da hora simulada mais recente processada pelo motor."""
    ultimo = _consulta_segura("SELECT ts FROM previsoes ORDER BY calculado_em DESC LIMIT 1")
    if ultimo.empty:
        return None, pd.DataFrame()
    ts = ultimo["ts"].iloc[0]
    prev = _consulta_segura("SELECT * FROM previsoes WHERE ts = ? ORDER BY calculado_em", (ts.isoformat(),))
    return ts, prev.drop_duplicates("codigo", keep="last")


def leituras_rede(ts: pd.Timestamp, horas: int = 30) -> pd.DataFrame:
    df = _consulta_segura(f"SELECT ts, celula_h3, {', '.join(VARIAVEIS)} FROM leituras WHERE fonte = 'simulador' "
                          "AND ts >= ? AND ts <= ?", ((ts - pd.Timedelta(hours=horas)).isoformat(), ts.isoformat()))
    if not df.empty:
        df["codigo"] = df["celula_h3"].map(COD_POR_CELULA)
        df["estacao"] = df["codigo"].map(lambda c: EST.get(c, {}).get("nome", "?"))
    return df


def cor(prob: float, alerta: bool) -> list[int]:
    if alerta:
        return [214, 39, 40, 220]
    if prob >= 0.15:
        return [255, 160, 0, 200]
    return [44, 160, 44, 180]


def mapa(prev: pd.DataFrame, chuva_atual: pd.Series) -> pdk.Deck:
    linhas = []
    for cod, meta in EST.items():
        p = prev[prev["codigo"] == cod]
        prob = float(p["probabilidade"].iloc[0]) if not p.empty else 0.0
        alerta = bool(p["alerta"].iloc[0]) if not p.empty else False
        linhas.append({"nome": meta["nome"], "codigo": cod, "lat": meta["lat"], "lon": meta["lon"],
                       "prob": f"{prob:.0%}", "chuva": f"{chuva_atual.get(cod, 0) or 0:.1f} mm",
                       "cor": cor(prob, alerta), "raio": 9000 + 30000 * prob,
                       "hex": h3.cell_to_parent(CELULAS[cod], 5)})
    df = pd.DataFrame(linhas)
    camadas = [
        pdk.Layer("H3HexagonLayer", df, get_hexagon="hex", get_fill_color="cor", opacity=0.25,
                  stroked=True, get_line_color=[80, 80, 80], line_width_min_pixels=1, pickable=False),
        pdk.Layer("ScatterplotLayer", df, get_position="[lon, lat]", get_radius="raio",
                  get_fill_color="cor", pickable=True),
        pdk.Layer("TextLayer", df, get_position="[lon, lat]", get_text="nome", get_size=13,
                  get_pixel_offset=[0, -22], get_color=[30, 30, 30]),
    ]
    return pdk.Deck(layers=camadas, map_style=pdk.map_styles.CARTO_LIGHT,
                    initial_view_state=pdk.ViewState(latitude=-30.0, longitude=-55.4, zoom=6.1),
                    tooltip={"text": "{nome} ({codigo})\nRisco de chuva forte: {prob}\nChuva na última hora: {chuva}"})


st.title("🌧️ AgroAlerta — rede colaborativa de alerta de chuva")
st.caption("Propriedades rurais da Fronteira Oeste e Campanha (RS) compartilham leituras via MQTT; "
           "um modelo de IA prevê chuva forte nas próximas horas e avisa quem está na rota da tempestade.")

aba_mapa, aba_estacao, aba_dispositivo, aba_modelo, aba_privacidade = st.tabs(
    ["Mapa ao vivo", "Série por estação", "Dispositivo ESP32", "Modelo de IA", "Privacidade (LGPD)"])


@st.fragment(run_every="3s")
def painel_ao_vivo():
    ts, prev = ultima_rodada()
    if ts is None:
        st.info("Aguardando dados da rede. Inicie o simulador: `python -m servicos.simulador --modo sintetico`")
        return
    leit = leituras_rede(ts, horas=0)
    chuva = leit.groupby("codigo")["precip"].mean() if not leit.empty else pd.Series(dtype=float)
    em_alerta = prev[prev["alerta"].astype(bool)]
    c1, c2, c3 = st.columns(3)
    c1.metric("Hora observada (UTC)", f"{ts:%d/%m/%Y %H:%M}")
    c2.metric("Estações ativas", f"{leit['celula_h3'].nunique() if not leit.empty else 0}/{len(EST)}")
    c3.metric("Propriedades em alerta", len(em_alerta))
    esquerda, direita = st.columns([3, 2])
    with esquerda:
        st.pydeck_chart(mapa(prev, chuva), height=520)
    with direita:
        st.subheader("Alertas")
        if em_alerta.empty:
            st.success("Nenhum alerta ativo.")
        for _, r in em_alerta.sort_values("probabilidade", ascending=False).iterrows():
            st.error(f"**{EST[r['codigo']]['nome']}** — risco {r['probabilidade']:.0%}: chuva forte esperada "
                     "nas próximas horas. Recolha o maquinário e suspenda a pulverização.")
        tabela = prev.assign(estacao=prev["codigo"].map(lambda c: EST[c]["nome"]),
                             chuva_mm=prev["codigo"].map(chuva))
        st.dataframe(tabela[["estacao", "codigo", "probabilidade", "chuva_mm", "alerta"]]
                     .sort_values("probabilidade", ascending=False), hide_index=True,
                     column_config={"probabilidade": st.column_config.ProgressColumn(
                         "Risco", format="percent", min_value=0, max_value=1)})


with aba_mapa:
    painel_ao_vivo()

with aba_estacao:
    nomes = {f"{m['nome']} ({c})": c for c, m in EST.items()}
    escolha = nomes[st.selectbox("Estação", list(nomes), index=list(nomes.values()).index(ESTACAO_ALVO))]

    @st.fragment(run_every="5s")
    def serie():
        ts, _ = ultima_rodada()
        if ts is None:
            st.info("Sem dados ainda.")
            return
        leit = leituras_rede(ts, horas=48)
        leit = leit[leit["codigo"] == escolha].groupby("ts")[VARIAVEIS].mean()
        prev = _consulta_segura("SELECT ts, probabilidade FROM previsoes WHERE codigo = ? AND ts >= ? AND ts <= ?",
                                (escolha, (ts - pd.Timedelta(hours=48)).isoformat(), ts.isoformat()))
        if not prev.empty:
            st.markdown("**Risco previsto de chuva forte nas próximas horas**")
            st.area_chart(prev.groupby("ts")["probabilidade"].last(), height=180)
        cols = st.columns(2)
        for i, (var, rotulo) in enumerate(NOMES_VARS.items()):
            with cols[i % 2]:
                st.markdown(f"**{rotulo}**")
                (st.bar_chart if var == "precip" else st.line_chart)(leit[var], height=180)
    serie()

with aba_dispositivo:
    st.markdown(f"Leituras do **ESP32 simulado no Wokwi** (tópico `{PREFIXO}/leituras/wokwi/...`). "
                f"O dispositivo recebe o risco pelo tópico `{PREFIXO}/risco/{ESTACAO_ALVO}` e acende o LED quando há alerta.")

    @st.fragment(run_every="3s")
    def dispositivo():
        df = _consulta_segura("SELECT * FROM leituras WHERE fonte = 'wokwi' ORDER BY recebido_em DESC LIMIT 200")
        if df.empty:
            st.info("Nenhuma leitura do ESP32 recebida ainda.")
            return
        ultimo = df.iloc[0]
        c = st.columns(4)
        c[0].metric("Temperatura", f"{ultimo['temp']:.1f} °C")
        c[1].metric("Umidade", f"{ultimo['umid']:.0f} %")
        c[2].metric("Pressão", f"{ultimo['pressao']:.1f} hPa")
        c[3].metric("Última leitura", f"{ultimo['recebido_em']:%H:%M:%S} UTC")
        st.line_chart(df.set_index("recebido_em")[["temp", "umid"]], height=220)
    dispositivo()

with aba_modelo:
    resumo = DIR_RELATORIOS / "resumo.json"
    if not resumo.exists():
        st.info("Treine o modelo: `python -m ia.treinar`")
    else:
        r = json.loads(resumo.read_text(encoding="utf-8"))
        st.markdown(f"Alvo: chuva ≥ **{r['limiar_mm']:g} mm** nas próximas **{r['horizonte_h']} h**. "
                    f"Teste em **{r['ano_teste']}** (dados nunca vistos no treino).")
        pr = r["pr_auc_medio"]
        c = st.columns(3)
        c[0].metric("PR-AUC — persistência", f"{pr.get('persistencia', 0):.3f}")
        c[1].metric("PR-AUC — só local", f"{pr.get('lgbm_local', 0):.3f}")
        c[2].metric("PR-AUC — local + vizinhos", f"{pr.get('lgbm_vizinhos', 0):.3f}",
                    delta=f"{r['ganho_medio_pr_auc_vizinhos_vs_local']:+.3f} com a rede")
        for nome in ("pr_auc_por_estacao.png", f"curva_pr_{ESTACAO_ALVO}.png", f"importancia_{ESTACAO_ALVO}.png"):
            if (DIR_RELATORIOS / nome).exists():
                st.image(str(DIR_RELATORIOS / nome))
        st.dataframe(pd.read_csv(DIR_RELATORIOS / "metricas.csv"), hide_index=True)

with aba_privacidade:
    st.markdown("""
**Privacy by design** — o que sai de cada propriedade:
- **Célula H3** (resolução 7, ~4 km²) no lugar da coordenada GPS;
- **ID rotativo diário** (UUID derivado de segredo + dispositivo + data): não permite rastrear o produtor entre dias;
- **Lista branca de campos** no ingestor: qualquer outro campo é descartado antes de gravar.

Isto é **pseudonimização**, não anonimização (art. 12 da LGPD): em áreas rurais pouco povoadas,
uma célula pode corresponder a uma única propriedade. Ver discussão de riscos residuais no relatório.
""")
    exemplo = _consulta_segura("SELECT * FROM leituras ORDER BY recebido_em DESC LIMIT 1")
    if not exemplo.empty:
        st.markdown("**Última mensagem gravada (exatamente o que o sistema armazena):**")
        st.json(json.loads(exemplo.iloc[0].to_json(date_format="iso")))
