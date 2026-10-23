"""
Configuração central do Robô Pro de Futebol.

MELHORIA: antes, o caminho do banco era relativo ("oportunidades.db"), o que
faz o app apontar para bancos diferentes dependendo de onde o Streamlit é
executado. Agora o caminho é absoluto, baseado na localização deste arquivo.

MELHORIA: os limiares (thresholds) de cada estratégia, que antes apareciam
"soltos" no meio da lógica de processamento (ex: `>= 11.50`, `Score_Elite >= 60.0`),
foram centralizados aqui. Facilita calibrar as estratégias sem caçar números
mágicos espalhados pelo código, e documenta o que cada um significa.
"""

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "oportunidades.db")
LOG_PATH = os.path.join(BASE_DIR, "robo_futebol.log")

SCRIPTS_DISPONIVEIS = [
    "Cantos (Over 9.5)",
    "Gols (Over 2.5)",
    "Gols (Over 1.5)",
    "Vitória / Dominância",
]

CFG_DEFAULT = {
    "stake_padrao": 50.0,
    "top_n": 10,
    "permitir_copas": False,
    "permitir_sub20": False,
    "permitir_feminino": False,
}

# --- LIMIARES DA ESTRATÉGIA DE CANTOS ---
THRESH_CANTOS = {
    "sample_min": 10,
    "expectativa_cruzada_min": 11.50,
    "appm_total_min": 1.00,
}

# --- LIMIARES DA ESTRATÉGIA DE GOLS (Over 2.5 / Over 1.5) ---
THRESH_GOLS = {
    "odd_min": 1.60,
    "freq_min": 8,  # frequência mínima exigida nas duas colunas de "Freq"
    "vol_ht_min": 1.5,
    "total_chutes_proj_min": 15.0,
    "chutes_por_gol_min": 2.7,
    "score_elite_min": 60.0,
    # pesos do Score_Elite (devem somar 1.0)
    "score_ft_peso": 0.25,
    "score_press_peso": 0.30,
    "score_freq_peso": 0.25,
    "score_hist_peso": 0.20,
    "over25_vol_ft_min": 5.0,
    "over25_side_min": 2.5,
    "over25_barreira_under_max": 42.0,
    "over15_barreira_under_max": 50.0,
}

# --- LIMIARES DA ESTRATÉGIA DE VITÓRIA (Poisson 1x2) ---
THRESH_VITORIA = {
    "games_min": 8,
    "odds_min": 1.50,
    "p_forte_min": 0.55,
    "p_draw_max_forte": 0.25,
    "p_dnb_min": 0.45,
    "p_dnb_max": 0.55,
    "p_draw_min_dnb": 0.25,
    "max_goals_poisson": 8,
}
