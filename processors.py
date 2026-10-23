"""
Lógica de processamento das 3 estratégias (Cantos, Gols, Vitória).

MELHORIAS aplicadas neste módulo, na ordem em que aparecem no código:

1. `_tratar_erro_processamento`: um único ponto para logar o traceback completo
   e mostrar uma mensagem útil ao usuário.

2. `processar_gols` deixou de usar `df.iloc[:, 9]`, `df.iloc[:, 16]` etc.
   (acesso por POSIÇÃO de coluna). Agora as colunas são renomeadas por
   posição UMA ÚNICA VEZ, logo no início (`_renomear_colunas_gols`), e o
   resto do código referencia nomes. ATENÇÃO: os nomes semânticos abaixo
   (Goals_Pro_Home, Press_Con_Away etc.) foram INFERIDOS a partir de como as
   posições eram combinadas na fórmula original. Confira contra o cabeçalho
   real do seu arquivo e ajuste COLUNAS_GOLS se necessário.

3. A lógica quase idêntica de Over 2.5 e Over 1.5 foi unificada em
   `_processar_estrategia_gols(df, cfg, thresh, modo)`.

4. O Poisson foi vetorizado com numpy (`_probabilidades_1x2`).

5. Os 4 blocos de montagem de "pick" em `processar_vitoria` foram
   unificados em `_montar_pick`.
"""

import math

import numpy as np
import pandas as pd
import streamlit as st

from config import THRESH_CANTOS, THRESH_GOLS, THRESH_VITORIA
from log_config import logger


# --- TRATAMENTO DE ERRO CENTRALIZADO ---
def _tratar_erro_processamento(e, contexto):
    logger.exception(f"Erro ao processar {contexto}")
    if isinstance(e, (KeyError, ValueError)):
        st.error(f"❌ {contexto}: {e}")
    else:
        st.error(f"❌ Erro inesperado ao processar {contexto}: {type(e).__name__}: {e}")
    with st.expander("🔎 Detalhes técnicos do erro"):
        st.exception(e)
    return []


# --- AUXILIARES DE TEXTO E FILTRO ---
def fix_str(val):
    s = str(val)
    try:
        s = s.encode("latin-1").decode("utf-8")
    except Exception:
        pass
    return s


def filtrar_blacklist(df, col_liga, col_casa, col_fora, cfg):
    terms_copas = [
        r"\bcup\b", r"\bcopa\b", r"\btaça\b", r"\btaca\b", r"\btrofeu\b", r"\btroféu\b",
        r"\bsupercup\b", r"\bsupercopa\b", r"\bshield\b", r"qualif", r"eliminat",
        r"playoff", r"play-off", r"preliminar", r"preliminary",
    ]
    terms_sub = [
        r"\byouth\b", r"\bacademy\b", r"\bacademia\b", r"\bjunior\b", r"\bjúnior\b",
        r"\bu\d{1,2}\b", r"\bsub[- ]?\d{1,2}\b", r"\breserve\b", r"\breserva\b",
    ]
    terms_fem = [
        r"\bwomen\b", r"\bfeminino\b", r"\bfemenino\b", r"\bfem\b", r"\bwom\b",
        r"\bdamallsvenskan\b",
    ]

    blacklist = []
    if not cfg["permitir_copas"]:
        blacklist.extend(terms_copas)
    if not cfg["permitir_sub20"]:
        blacklist.extend(terms_sub)
    if not cfg["permitir_feminino"]:
        blacklist.extend(terms_fem)

    if not blacklist:
        return df

    pattern = "|".join(blacklist)
    mask_liga = df[col_liga].astype(str).str.lower().str.contains(pattern, regex=True, na=False)
    mask_casa = df[col_casa].astype(str).str.lower().str.contains(pattern, regex=True, na=False)
    mask_fora = df[col_fora].astype(str).str.lower().str.contains(pattern, regex=True, na=False)

    return df[~(mask_liga | mask_casa | mask_fora)].copy()


# ===========================================================================
# SCRIPT 1: CANTOS
# ===========================================================================
CORRECT_COL_NAMES_CANTOS = [
    "Country", "Short", "League", "Hour", "Status", "Home", "ResHome", "ResAway", "Away", "Odds_Over_95",
    "Corners_Pro_Home", "Corners_Pro_Away", "Corners_Con_Home", "Corners_Con_Away",
    "HT_Pro_Home", "HT_Pro_Away", "HT_Con_Home", "HT_Con_Away",
    "Freq_85", "Freq_95", "Freq_105", "Avg_Shots", "Avg_SOT",
    "Poss_Home", "Poss_Away", "Sample_Home", "Sample_Away",
    "APPM_Home", "APPM_Away", "Press_Home", "Press_Away",
    "ShotsCon_Home", "ShotsCon_Away", "Freq_HT45",
]


def processar_cantos(file, cfg):
    try:
        df = pd.read_csv(file, sep=";", names=CORRECT_COL_NAMES_CANTOS, skiprows=1, encoding="latin-1")

        numeric_cols = CORRECT_COL_NAMES_CANTOS[9:]
        for col in numeric_cols:
            if df[col].dtype == object:
                df[col] = df[col].astype(str).str.replace("%", "", regex=False).str.replace(",", ".", regex=False)
            df[col] = pd.to_numeric(df[col], errors="coerce")

        cols_checar = ["Corners_Pro_Home", "Corners_Pro_Away", "Corners_Con_Home", "Corners_Con_Away", "Sample_Home", "Sample_Away"]
        df = df[~(df[cols_checar] == -1).any(axis=1)].copy()

        df = filtrar_blacklist(df, "League", "Home", "Away", cfg)

        df["xCorners_Home"] = (df["Corners_Pro_Home"] * 0.60) + (df["Corners_Con_Away"] * 0.40)
        df["xCorners_Away"] = (df["Corners_Pro_Away"] * 0.40) + (df["Corners_Con_Home"] * 0.60)
        df["Expectativa_Cruzada"] = (df["xCorners_Home"] + df["xCorners_Away"]).round(2)
        df["APPM_Total"] = (df["APPM_Home"].fillna(0) + df["APPM_Away"].fillna(0)).round(2)

        th = THRESH_CANTOS
        df_base = df[
            (df["Sample_Home"] >= th["sample_min"])
            & (df["Sample_Away"] >= th["sample_min"])
            & (df["Odds_Over_95"] > 1.0)
        ].copy()
        df_main = df_base[
            (df_base["Expectativa_Cruzada"] >= th["expectativa_cruzada_min"])
            & (df_base["APPM_Total"] >= th["appm_total_min"])
        ].sort_values(by="Expectativa_Cruzada", ascending=False).copy()

        if cfg["top_n"] > 0:
            df_main = df_main.head(cfg["top_n"])

        resultados = []
        for _, r in df_main.iterrows():
            hora_clean = str(r["Hour"])[-5:] if len(str(r["Hour"])) >= 5 else str(r["Hour"])
            resultados.append({
                "hora": hora_clean,
                "jogo": f"{fix_str(r['Home'])} vs {fix_str(r['Away'])}",
                "liga": f"{fix_str(r['Country'])} - {fix_str(r['League'])}",
                "script": "Cantos (Over 9.5)",
                "recomendacao": "Over 9.5 Cantos",
                "score_confianca": f"Exp. Cantos: {r['Expectativa_Cruzada']:.2f}",
                "odd": float(r["Odds_Over_95"]),
            })
        return resultados

    except Exception as e:
        return _tratar_erro_processamento(e, "arquivo de Cantos")


# ===========================================================================
# SCRIPT 2: GOLS
# ===========================================================================
# Nomes inferidos por posição (ver nota no topo do arquivo). Ajuste se não
# baterem com o cabeçalho real do seu CSV de Gols.
COLUNAS_GOLS = [
    "Country", "Short", "League", "Hour", "Status", "Home", "ResHome", "ResAway", "Away",       # 0-8
    "Odds_Over",                                                                                 # 9
    "Goals_Pro_Home", "Goals_Pro_Away", "Goals_Con_Home", "Goals_Con_Away",                       # 10-13
    "Unknown_14", "Unknown_15",                                                                   # 14-15
    "Freq_Home", "Freq_Away",                                                                     # 16-17
    "Freq_Bucket1", "Freq_Bucket2", "Freq_Bucket3", "Freq_Bucket4",                                # 18-21
    "Unknown_22",                                                                                  # 22
    "Hist_Score",                                                                                  # 23
    "Barreira_Under",                                                                               # 24
    "HT_Pro_Home", "HT_Pro_Away", "HT_Con_Home", "HT_Con_Away",                                    # 25-28
    "Press_Pro_Home", "Press_Pro_Away", "Press_Con_Home", "Press_Con_Away",                        # 29-32
]


def _renomear_colunas_gols(df_raw):
    if len(df_raw.columns) < len(COLUNAS_GOLS):
        faltando = len(COLUNAS_GOLS) - len(df_raw.columns)
        raise ValueError(
            f"o arquivo tem {len(df_raw.columns)} colunas, mas eram esperadas pelo menos "
            f"{len(COLUNAS_GOLS)} (faltam {faltando}). O layout do arquivo de Gols pode ter mudado "
            f"— confira o cabeçalho real contra a lista COLUNAS_GOLS em processors.py."
        )
    df_raw = df_raw.copy()
    df_raw.columns = COLUNAS_GOLS + list(df_raw.columns[len(COLUNAS_GOLS):])
    return df_raw


def _calcular_score_elite(df, thresh):
    df = df.copy()
    s_ft = (((df["Vol_FT"] / 2) / 3.5) * 100).clip(upper=100)
    s_press = ((df["Total_Chutes_Proj"] / 20.0) * 100).clip(upper=100)
    s_freq = df[["Freq_Bucket1", "Freq_Bucket2", "Freq_Bucket3", "Freq_Bucket4"]].mean(axis=1)
    s_hist = df["Hist_Score"]
    df["Score_Elite"] = (
        s_ft * thresh["score_ft_peso"]
        + s_press * thresh["score_press_peso"]
        + s_freq * thresh["score_freq_peso"]
        + s_hist * thresh["score_hist_peso"]
    )
    return df


def _montar_resultados_gols(df, estrategia_nome, recomendacao_nome):
    resultados = []
    for _, r in df.iterrows():
        hora_clean = str(r["Hour"])[-5:] if len(str(r["Hour"])) >= 5 else str(r["Hour"])
        resultados.append({
            "hora": hora_clean,
            "jogo": f"{fix_str(r['Home'])} vs {fix_str(r['Away'])}",
            "liga": f"{fix_str(r['Country'])} - {fix_str(r['League'])}",
            "script": estrategia_nome,
            "recomendacao": recomendacao_nome,
            "score_confianca": f"Score Elite: {r['Score_Elite']:.1f}",
            "odd": float(r["Odds_Over"]),
        })
    return resultados


def _processar_estrategia_gols(df_s5, cfg, thresh, modo):
    """modo: 'over25' ou 'over15'. Une a lógica que antes era duplicada."""
    df_base = filtrar_blacklist(df_s5, "League", "Home", "Away", cfg)
    df_ht = df_base[
        (df_base["Vol_HT"] >= thresh["vol_ht_min"])
        & (df_base["Total_Chutes_Proj"] >= thresh["total_chutes_proj_min"])
        & (df_base["Chutes_Por_Gol"] >= thresh["chutes_por_gol_min"])
    ].copy()
    if df_ht.empty:
        return []

    df_ht = _calcular_score_elite(df_ht, thresh)
    df_score = df_ht[df_ht["Score_Elite"] >= thresh["score_elite_min"]].sort_values(
        by="Score_Elite", ascending=False
    ).copy()
    if df_score.empty:
        return []

    mask_over25 = (
        (df_score["Vol_FT"] >= thresh["over25_vol_ft_min"])
        & (df_score["SideA_FT"] >= thresh["over25_side_min"])
        & (df_score["SideB_FT"] >= thresh["over25_side_min"])
        & (df_score["Barreira_Under"] < thresh["over25_barreira_under_max"])
    )

    if modo == "over25":
        df_final = df_score[mask_over25].copy()
        estrategia_nome, recomendacao_nome = "Gols (Over 2.5)", "Over 2.5 Gols"
    else:
        # Over 1.5: critério mais frouxo, EXCLUINDO quem já entrou no Over 2.5
        mask_over15 = (
            (df_score["Vol_FT"] >= thresh["over25_vol_ft_min"])
            & (df_score["Barreira_Under"] <= thresh["over15_barreira_under_max"])
            & (~mask_over25)
        )
        df_final = df_score[mask_over15].copy()
        estrategia_nome, recomendacao_nome = "Gols (Over 1.5)", "Over 1.5 Gols"

    if cfg["top_n"] > 0:
        df_final = df_final.head(cfg["top_n"])

    return _montar_resultados_gols(df_final, estrategia_nome, recomendacao_nome)


def processar_gols(file, cfg_over25, cfg_over15):
    try:
        try:
            df_raw = pd.read_csv(file, sep=None, engine="python", encoding="latin-1")
        except Exception:
            file.seek(0)
            df_raw = pd.read_csv(file, sep=";", encoding="latin-1")

        if df_raw.empty or len(df_raw.columns) < 10:
            st.warning("⚠️ O arquivo de Gols enviado está vazio ou num formato incompatível.")
            return []

        df_raw = _renomear_colunas_gols(df_raw)

        thresh = THRESH_GOLS
        colunas_tecnicas = COLUNAS_GOLS[9:]
        for col in colunas_tecnicas:
            df_raw[col] = (
                df_raw[col].astype(str).str.replace(",", ".", regex=False)
            )
            df_raw[col] = pd.to_numeric(df_raw[col], errors="coerce")

        df_clean = df_raw[~(df_raw[colunas_tecnicas] == -1).any(axis=1)].copy()

        df_odd = df_clean[df_clean["Odds_Over"] >= thresh["odd_min"]]
        df_s5 = df_odd[
            (df_odd["Freq_Home"] >= thresh["freq_min"]) & (df_odd["Freq_Away"] >= thresh["freq_min"])
        ].copy()

        df_s5["SideA_FT"] = df_s5["Goals_Pro_Home"] + df_s5["Goals_Con_Away"]
        df_s5["SideB_FT"] = df_s5["Goals_Pro_Away"] + df_s5["Goals_Con_Home"]
        df_s5["Vol_FT"] = df_s5["SideA_FT"] + df_s5["SideB_FT"]

        df_s5["SideA_HT"] = df_s5["HT_Pro_Home"] + df_s5["HT_Con_Away"]
        df_s5["SideB_HT"] = df_s5["HT_Pro_Away"] + df_s5["HT_Con_Home"]
        df_s5["Vol_HT"] = df_s5["SideA_HT"] + df_s5["SideB_HT"]

        df_s5["Pressao_LadoA"] = df_s5["Press_Pro_Home"] + df_s5["Press_Con_Away"]
        df_s5["Pressao_LadoB"] = df_s5["Press_Pro_Away"] + df_s5["Press_Con_Home"]
        df_s5["Total_Chutes_Proj"] = df_s5["Pressao_LadoA"] + df_s5["Pressao_LadoB"]
        df_s5["Chutes_Por_Gol"] = df_s5["Total_Chutes_Proj"] / df_s5["Vol_FT"].replace(0, np.nan)

        resultados = []
        resultados.extend(_processar_estrategia_gols(df_s5, cfg_over25, thresh, "over25"))
        resultados.extend(_processar_estrategia_gols(df_s5, cfg_over15, thresh, "over15"))
        return resultados

    except Exception as e:
        return _tratar_erro_processamento(e, "arquivo de Gols")


# ===========================================================================
# SCRIPT 3: VITÓRIA (Poisson 1x2 vetorizado)
# ===========================================================================
_MAX_GOALS = THRESH_VITORIA["max_goals_poisson"]
_K_RANGE = np.arange(_MAX_GOALS)
_FATORIAIS = np.array([math.factorial(int(i)) for i in _K_RANGE])
_H_GRID, _A_GRID = np.meshgrid(_K_RANGE, _K_RANGE, indexing="ij")
_MASK_HOME = _H_GRID > _A_GRID
_MASK_DRAW = _H_GRID == _A_GRID
_MASK_AWAY = _H_GRID < _A_GRID


def calcular_poisson(k, lambda_val):
    """Mantido por compatibilidade/uso avulso."""
    if lambda_val <= 0:
        return 0.0
    return (math.pow(lambda_val, k) * math.exp(-lambda_val)) / math.factorial(k)


def _probabilidades_1x2(lambda_h, lambda_a):
    """Calcula P(vitória casa), P(empate), P(vitória fora) via matriz de
    Poisson conjunta, vetorizado com numpy."""
    pmf_h = np.exp(-lambda_h) * (lambda_h ** _K_RANGE) / _FATORIAIS if lambda_h > 0 else np.zeros(_MAX_GOALS)
    pmf_a = np.exp(-lambda_a) * (lambda_a ** _K_RANGE) / _FATORIAIS if lambda_a > 0 else np.zeros(_MAX_GOALS)
    matriz = np.outer(pmf_h, pmf_a)

    p_home = matriz[_MASK_HOME].sum()
    p_draw = matriz[_MASK_DRAW].sum()
    p_away = matriz[_MASK_AWAY].sum()

    total = p_home + p_draw + p_away
    if total > 0:
        p_home, p_draw, p_away = p_home / total, p_draw / total, p_away / total
    return p_home, p_draw, p_away


def _montar_pick(r, time_pick, odds, p_time, p_draw, tipo):
    rec = (
        f"Back Vitória: {fix_str(time_pick)}"
        if tipo == "forte"
        else f"Empate Anula (DNB / AH 0.0): {fix_str(time_pick)}"
    )
    return {
        "Hour": r["Hour"], "Home_Team": r["Home_Team"], "Visitor_Team": r["Visitor_Team"],
        "Country": r["Country"], "League": r["League"], "Pick_Team": time_pick,
        "Recomendacao": rec,
        "Score": f"Vitória: {p_time * 100:.1f}% | Empate: {p_draw * 100:.1f}%",
        "Odds": float(odds),
        "Prob_Sort": p_time,
    }


def processar_vitoria(win_file, conf_file, cfg):
    try:
        df_win = pd.read_csv(win_file, sep=";", encoding="latin-1")
        df_conf = pd.read_csv(conf_file, sep=";", encoding="latin-1")

        col_names_win = [
            "Country", "Short", "League", "Hour", "Status", "Home_Team", "Result_Home", "Result_Visitor", "Visitor_Team",
            "Odds_Home", "Odds_Away", "Win_Pct_Home", "Win_Pct_Away", "Efficiency_Home", "Efficiency_Away",
            "Games_Home", "Games_Away", "ExG", "Pressure_Home", "Pressure_Away", "Attacks_Min_Home", "Attacks_Min_Away",
            "Shots_Scored_Home", "Shots_Scored_Away", "Shots_Conceded_Home", "Shots_Conceded_Away",
            "ShotsOnTarget_Scored_Home", "ShotsOnTarget_Scored_Away", "ShotsOnTarget_Conceded_Home", "ShotsOnTarget_Conceded_Away",
            "Possession_Home", "Possession_Away", "Goals_Scored_Home", "Goals_Scored_Away", "Goals_Conceded_Home", "Goals_Conceded_Away",
        ]
        df_win.columns = col_names_win[: len(df_win.columns)]

        col_names_conf = [
            "Country", "Short", "League", "Hour", "Status", "Home_Team", "Result_Home", "Result_Visitor", "Visitor_Team",
            "H2H_Games", "H2H_Win_Pct_Home", "H2H_Win_Pct_Away",
        ]
        df_conf.columns = col_names_conf[: len(df_conf.columns)]

        merged = pd.merge(
            df_win,
            df_conf[["Home_Team", "Visitor_Team", "League", "Hour", "H2H_Games", "H2H_Win_Pct_Home", "H2H_Win_Pct_Away"]],
            on=["Home_Team", "Visitor_Team", "League", "Hour"],
            how="inner",
        )

        num_cols = [c for c in merged.columns if c not in ["Country", "Short", "League", "Hour", "Status", "Home_Team", "Visitor_Team"]]
        for col in num_cols:
            if merged[col].dtype == object:
                merged[col] = merged[col].astype(str).str.replace("%", "", regex=False).str.replace(",", ".", regex=False)
            merged[col] = pd.to_numeric(merged[col], errors="coerce")

        cols_vit_checar = ["Efficiency_Home", "Efficiency_Away", "Games_Home", "Games_Away", "Goals_Scored_Home", "Goals_Scored_Away"]
        merged = merged[~(merged[cols_vit_checar] == -1).any(axis=1)].copy()
        merged[num_cols] = merged[num_cols].fillna(0.0)

        th = THRESH_VITORIA
        df_clean = merged[(merged["Games_Home"] >= th["games_min"]) & (merged["Games_Away"] >= th["games_min"])].copy()
        df_clean = filtrar_blacklist(df_clean, "League", "Home_Team", "Visitor_Team", cfg)

        df_clean["Diff_Efficiency"] = df_clean["Efficiency_Home"] - df_clean["Efficiency_Away"]
        df_clean["Diff_Pressure"] = df_clean["Pressure_Home"] - df_clean["Pressure_Away"]

        avg_goals_home = np.maximum(df_clean["Goals_Scored_Home"], 0.5)
        avg_goals_away = np.maximum(df_clean["Goals_Scored_Away"], 0.5)

        mod_home = 1.0 + (df_clean["Diff_Efficiency"] * 0.005) + (df_clean["Diff_Pressure"] * 0.002)
        mod_away = 1.0 - (df_clean["Diff_Efficiency"] * 0.005) - (df_clean["Diff_Pressure"] * 0.002)

        df_clean["xG_Home"] = np.clip(avg_goals_home * mod_home, 0.2, 4.5)
        df_clean["xG_Away"] = np.clip(avg_goals_away * mod_away, 0.2, 4.5)

        picks = []
        for _, r in df_clean.iterrows():
            p_home, p_draw, p_away = _probabilidades_1x2(r["xG_Home"], r["xG_Away"])

            if p_home >= th["p_forte_min"] and p_draw < th["p_draw_max_forte"] and r["Odds_Home"] >= th["odds_min"]:
                picks.append(_montar_pick(r, r["Home_Team"], r["Odds_Home"], p_home, p_draw, "forte"))
            elif th["p_dnb_min"] <= p_home <= th["p_dnb_max"] and p_draw > th["p_draw_min_dnb"] and r["Odds_Home"] >= th["odds_min"]:
                picks.append(_montar_pick(r, r["Home_Team"], r["Odds_Home"], p_home, p_draw, "dnb"))

            if p_away >= th["p_forte_min"] and p_draw < th["p_draw_max_forte"] and r["Odds_Away"] >= th["odds_min"]:
                picks.append(_montar_pick(r, r["Visitor_Team"], r["Odds_Away"], p_away, p_draw, "forte"))
            elif th["p_dnb_min"] <= p_away <= th["p_dnb_max"] and p_draw > th["p_draw_min_dnb"] and r["Odds_Away"] >= th["odds_min"]:
                picks.append(_montar_pick(r, r["Visitor_Team"], r["Odds_Away"], p_away, p_draw, "dnb"))

        if not picks:
            return []

        df_picks = pd.DataFrame(picks)
        all_picks = df_picks.sort_values(by="Prob_Sort", ascending=False)
        all_picks = all_picks.drop_duplicates(subset=["Home_Team", "Visitor_Team"], keep="first")

        top_n = cfg["top_n"] if cfg["top_n"] > 0 else 10
        top_picks = all_picks.head(top_n).copy()

        resultados = []
        for _, r in top_picks.iterrows():
            hora_clean = str(r["Hour"])[-5:] if len(str(r["Hour"])) >= 5 else str(r["Hour"])
            resultados.append({
                "hora": hora_clean,
                "jogo": f"{fix_str(r['Home_Team'])} vs {fix_str(r['Visitor_Team'])}",
                "liga": f"{fix_str(r['Country'])} - {fix_str(r['League'])}",
                "script": "Vitória / Dominância",
                "recomendacao": r["Recomendacao"],
                "score_confianca": r["Score"],
                "odd": float(r["Odds"]),
            })
        return resultados

    except Exception as e:
        return _tratar_erro_processamento(e, "arquivos de Vitória")
