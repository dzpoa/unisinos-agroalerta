"""Treina e avalia os modelos de nowcasting de chuva.

Experimento central — o valor da rede colaborativa:
  * baseline de persistência (chuva das últimas 3h na própria estação);
  * LightGBM só com atributos LOCAIS;
  * LightGBM com atributos locais + ESTAÇÕES VIZINHAS (a rede "um avisa o outro").

Divisão temporal: treino 2015–2022, validação 2023 (early stopping e escolha do limiar
de decisão), teste 2024 (avaliado uma única vez).

Uso: python -m ia.treinar [--alvos A803 A826 ...]
"""
from __future__ import annotations

import argparse
import json
import warnings

import joblib
import lightgbm as lgb
import matplotlib
import numpy as np
import pandas as pd
from sklearn.metrics import (average_precision_score, brier_score_loss, f1_score,
                             precision_recall_curve, precision_score, recall_score, roc_auc_score)

from comum.config import (ANO_TESTE, ANO_VALIDACAO, ANOS_TREINO, ARQ_HORARIO, DIR_MODELOS,
                          DIR_RELATORIOS, ESTACAO_ALVO, HORIZONTE_H, LIMIAR_MM, estacoes)
from comum.features import montar_alvo, montar_atributos, para_largo

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

# LightGBM >= 4.6 avisa sobre eval_set; o argumento segue válido e compatível com versões antigas.
warnings.filterwarnings("ignore", message=".*eval_set.*deprecated.*")

PARAMS = dict(objective="binary", learning_rate=0.03, num_leaves=31, min_child_samples=50,
              feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0,
              n_estimators=2000, verbose=-1)


def dividir(x: pd.DataFrame, y: pd.Series):
    ok = y.notna()
    x, y = x[ok], y[ok].astype(int)
    ano = x.index.year
    treino = (ano >= ANOS_TREINO[0]) & (ano <= ANOS_TREINO[1])
    return (x[treino], y[treino]), (x[ano == ANO_VALIDACAO], y[ano == ANO_VALIDACAO]), \
           (x[ano == ANO_TESTE], y[ano == ANO_TESTE])


def melhor_limiar(y: pd.Series, score: np.ndarray) -> float:
    """Limiar que maximiza o F1 na validação."""
    prec, rec, lim = precision_recall_curve(y, score)
    f1 = 2 * prec * rec / np.clip(prec + rec, 1e-9, None)
    return float(lim[np.nanargmax(f1[:-1])]) if len(lim) else 0.5


def metricas(y: pd.Series, score: np.ndarray, limiar: float) -> dict:
    pred = (score >= limiar).astype(int)
    return {
        "pr_auc": average_precision_score(y, score),
        "roc_auc": roc_auc_score(y, score),
        "f1": f1_score(y, pred, zero_division=0),
        "precisao": precision_score(y, pred, zero_division=0),
        "recall": recall_score(y, pred, zero_division=0),
        "brier": brier_score_loss(y, np.clip(score, 0, 1)) if score.max() <= 1 else np.nan,
        "limiar": limiar,
        "positivos_teste": int(y.sum()),
        "n_teste": int(len(y)),
    }


def treinar_alvo(largo: pd.DataFrame, alvo: str) -> tuple[list[dict], dict]:
    resultados, curvas = [], {}
    y = montar_alvo(largo, alvo)

    # Baseline de persistência: "vai chover porque está chovendo".
    x_loc = montar_atributos(largo, alvo, usar_vizinhos=False)
    (_, _), (xv, yv), (xt, yt) = dividir(x_loc, y)
    sv, st = xv["local_precip_3h"].fillna(0).to_numpy(), xt["local_precip_3h"].fillna(0).to_numpy()
    lim = melhor_limiar(yv, sv)
    resultados.append({"alvo": alvo, "modelo": "persistencia", **metricas(yt, st, lim)})
    curvas["persistencia"] = (yt, st)

    for config, usar_viz in (("local", False), ("vizinhos", True)):
        x = x_loc if not usar_viz else montar_atributos(largo, alvo, usar_vizinhos=True)
        (xtr, ytr), (xv, yv), (xt, yt) = dividir(x, y)
        modelo = lgb.LGBMClassifier(**PARAMS)
        modelo.fit(xtr, ytr, eval_set=[(xv, yv)], eval_metric="average_precision",
                   callbacks=[lgb.early_stopping(100, verbose=False)])
        pv, pt = modelo.predict_proba(xv)[:, 1], modelo.predict_proba(xt)[:, 1]
        lim = melhor_limiar(yv, pv)
        resultados.append({"alvo": alvo, "modelo": f"lgbm_{config}", **metricas(yt, pt, lim),
                           "arvores": modelo.best_iteration_})
        curvas[f"lgbm_{config}"] = (yt, pt)
        joblib.dump({"modelo": modelo, "atributos": list(x.columns), "limiar": lim,
                     "usar_vizinhos": usar_viz, "alvo": alvo, "limiar_mm": LIMIAR_MM,
                     "horizonte_h": HORIZONTE_H},
                    DIR_MODELOS / f"modelo_{alvo}_{config}.joblib")
        if usar_viz:
            imp = pd.Series(modelo.booster_.feature_importance("gain"), index=x.columns)
            curvas["importancia"] = imp.sort_values(ascending=False)
    return resultados, curvas


def graficos(curvas: dict, alvo: str, nome: str, tabela: pd.DataFrame) -> None:
    rotulos = {"persistencia": "Persistência", "lgbm_local": "LightGBM — só local",
               "lgbm_vizinhos": "LightGBM — local + vizinhos"}
    fig, ax = plt.subplots(figsize=(6.5, 5))
    for chave, rotulo in rotulos.items():
        y, s = curvas[chave]
        p, r, _ = precision_recall_curve(y, s)
        ax.plot(r, p, label=f"{rotulo} (PR-AUC={average_precision_score(y, s):.3f})")
    ax.axhline(curvas["lgbm_local"][0].mean(), ls=":", c="grey", label="Taxa de eventos")
    ax.set(xlabel="Recall", ylabel="Precisão",
           title=f"{nome} ({alvo}) — chuva ≥ {LIMIAR_MM:g} mm em {HORIZONTE_H}h, teste {ANO_TESTE}")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(DIR_RELATORIOS / f"curva_pr_{alvo}.png", dpi=150)
    plt.close(fig)

    top = curvas["importancia"].head(20)[::-1]
    fig, ax = plt.subplots(figsize=(7, 6))
    ax.barh(top.index, top.values / top.values.sum())
    ax.set(xlabel="Importância relativa (ganho)", title=f"Top 20 atributos — {nome} ({alvo})")
    fig.tight_layout()
    fig.savefig(DIR_RELATORIOS / f"importancia_{alvo}.png", dpi=150)
    plt.close(fig)

    est = estacoes()
    piv = tabela.pivot(index="alvo", columns="modelo", values="pr_auc")[list(rotulos)]
    piv = piv.loc[sorted(piv.index, key=lambda c: est[c]["lon"])]  # oeste -> leste
    piv.index = [f"{est[c]['nome']}\n({c})" for c in piv.index]
    ax = piv.rename(columns=rotulos).plot.bar(figsize=(10, 5), rot=0)
    ax.set(ylabel=f"PR-AUC (teste {ANO_TESTE})", xlabel="", title="Ganho da rede colaborativa por estação (oeste → leste)")
    ax.tick_params(axis="x", labelsize=7)
    plt.tight_layout()
    plt.savefig(DIR_RELATORIOS / "pr_auc_por_estacao.png", dpi=150)
    plt.close()


def main() -> None:
    est = estacoes()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--alvos", nargs="*", default=list(est))
    args = ap.parse_args()

    longo = pd.read_parquet(ARQ_HORARIO)
    largo = para_largo(longo)
    disponiveis = set(largo.columns.get_level_values(0))
    DIR_MODELOS.mkdir(parents=True, exist_ok=True)
    DIR_RELATORIOS.mkdir(parents=True, exist_ok=True)

    todas, curvas_alvo = [], None
    for alvo in [a for a in args.alvos if a in disponiveis]:
        print(f"Treinando {est[alvo]['nome']} ({alvo})...")
        resultados, curvas = treinar_alvo(largo, alvo)
        todas += resultados
        if alvo == ESTACAO_ALVO or curvas_alvo is None:
            curvas_alvo = (alvo, curvas)
        for r in resultados:
            print(f"   {r['modelo']:<15} PR-AUC={r['pr_auc']:.3f}  F1={r['f1']:.3f}  "
                  f"precisão={r['precisao']:.3f}  recall={r['recall']:.3f}")

    tabela = pd.DataFrame(todas)
    tabela.round(4).to_csv(DIR_RELATORIOS / "metricas.csv", index=False)
    alvo, curvas = curvas_alvo
    graficos(curvas, alvo, est[alvo]["nome"], tabela)

    resumo = tabela.pivot(index="alvo", columns="modelo", values="pr_auc")
    ganho = (resumo["lgbm_vizinhos"] - resumo["lgbm_local"]).mean()
    (DIR_RELATORIOS / "resumo.json").write_text(json.dumps({
        "limiar_mm": LIMIAR_MM, "horizonte_h": HORIZONTE_H, "ano_teste": ANO_TESTE,
        "pr_auc_medio": resumo.mean().round(4).to_dict(),
        "ganho_medio_pr_auc_vizinhos_vs_local": round(float(ganho), 4),
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nPR-AUC médio por modelo:\n{resumo.mean().round(3).to_string()}")
    print(f"Ganho médio da rede (vizinhos − local): {ganho:+.3f} PR-AUC")


if __name__ == "__main__":
    main()
