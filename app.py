import os
import re
import math
import sqlite3
import numpy as np
import pandas as pd
import streamlit as st
from datetime import datetime, date

# --- CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(
    page_title="Robô Pro de Futebol & Gestão",
    page_icon="⚽",
    layout="wide"
)

# --- INICIALIZAÇÃO DO BANCO DE DADOS LOCAL ---
def init_db():
    conn = sqlite3.connect("oportunidades.db")
    c = conn.cursor()
    c.execute("""
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
    """)
    
    c.execute("PRAGMA table_info(entradas)")
    cols = [col[1] for col in c.fetchall()]
    if 'placar_ao_vivo' not in cols:
        c.execute("ALTER TABLE entradas ADD COLUMN placar_ao_vivo TEXT DEFAULT 'Aguardando início'")
    if 'score_confianca' not in cols:
        c.execute("ALTER TABLE entradas ADD COLUMN score_confianca TEXT DEFAULT 'N/A'")

    c.execute("""
        CREATE TABLE IF NOT EXISTS configuracoes (
            script_nome TEXT PRIMARY KEY,
            stake_padrao REAL DEFAULT 50.0,
            top_n INTEGER DEFAULT 10,
            permitir_copas INTEGER DEFAULT 0,
            permitir_sub20 INTEGER DEFAULT 0,
            permitir_feminino INTEGER DEFAULT 0
        )
    """)
    scripts = [
        'Cantos (Over 9.5)', 
        'Gols (Over 2.5)', 
        'Gols (Over 1.5)', 
        'Vitória / Dominância (Novo)',
        'Vitória (Versão Antiga - Validação)'
    ]
    for s in scripts:
        c.execute("INSERT OR IGNORE INTO configuracoes (script_nome, stake_padrao, top_n, permitir_copas, permitir_sub20, permitir_feminino) VALUES (?, 50.0, 10, 0, 0, 0)", (s,))
    conn.commit()
    conn.close()

init_db()

# --- AUXILIARES DE PARÂMETROS E TRATAMENTO DE TEXTO ---
def carregar_configuracoes():
    conn = sqlite3.connect("oportunidades.db")
    df_cfg = pd.read_sql_query("SELECT * FROM configuracoes", conn)
    conn.close()
    configs = {}
    for _, row in df_cfg.iterrows():
        configs[row['script_nome']] = {
            'stake_padrao': float(row['stake_padrao']),
            'top_n': int(row['top_n']),
            'permitir_copas': bool(row['permitir_copas']),
            'permitir_sub20': bool(row['permitir_sub20']),
            'permitir_feminino': bool(row['permitir_feminino'])
        }
    return configs

def fix_str(val):
    s = str(val)
    try:
        s = s.encode('latin-1').decode('utf-8')
    except Exception:
        pass
    return s

def filtrar_blacklist(df, col_liga, col_casa, col_fora, cfg):
    terms_copas = [r'\bcup\b', r'\bcopa\b', r'\btaça\b', r'\btaca\b', r'\btrofeu\b', r'\btroféu\b', r'\bsupercup\b', r'\bsupercopa\b', r'\bshield\b', r'qualif', r'eliminat', r'playoff', r'play-off', r'preliminar', r'preliminary']
    terms_sub = [r'\byouth\b', r'\bacademy\b', r'\bacademia\b', r'\bjunior\b', r'\bjúnior\b', r'\bu\d{1,2}\b', r'\bsub[- ]?\d{1,2}\b', r'\breserve\b', r'\breserva\b']
    terms_fem = [r'\bwomen\b', r'\bfeminino\b', r'\bfemenino\b', r'\bfem\b', r'\bwom\b', r'\bdamallsvenskan\b']

    blacklist = []
    if not cfg['permitir_copas']:
        blacklist.extend(terms_copas)
    if not cfg['permitir_sub20']:
        blacklist.extend(terms_sub)
    if not cfg['permitir_feminino']:
        blacklist.extend(terms_fem)

    if not blacklist:
        return df

    pattern = '|'.join(blacklist)
    mask_liga = df[col_liga].astype(str).str.lower().str.contains(pattern, regex=True, na=False)
    mask_casa = df[col_casa].astype(str).str.lower().str.contains(pattern, regex=True, na=False)
    mask_fora = df[col_fora].astype(str).str.lower().str.contains(pattern, regex=True, na=False)

    return df[~(mask_liga | mask_casa | mask_fora)].copy()

# --- CÁLCULO NATIVO DE POISSON ---
def calcular_poisson(k, lambda_val):
    if lambda_val <= 0:
        return 0.0
    return (math.pow(lambda_val, k) * math.exp(-lambda_val)) / math.factorial(k)

# --- FUNÇÕES DE ESTILO ---
def colorir_lucro(val):
    if isinstance(val, (int, float)):
        if val > 0:
            return 'color: #2e7d32; font-weight: bold;'
        elif val < 0:
            return 'color: #c62828; font-weight: bold;'
    return ''

# --- SCRIPT 1: CANTOS ---
def processar_cantos(file, cfg):
    CORRECT_COL_NAMES = [
        "Country", "Short", "League", "Hour", "Status", "Home", "ResHome", "ResAway", "Away", "Odds_Over_95",
        "Corners_Pro_Home", "Corners_Pro_Away", "Corners_Con_Home", "Corners_Con_Away",
        "HT_Pro_Home", "HT_Pro_Away", "HT_Con_Home", "HT_Con_Away",
        "Freq_85", "Freq_95", "Freq_105", "Avg_Shots", "Avg_SOT",
        "Poss_Home", "Poss_Away", "Sample_Home", "Sample_Away",
        "APPM_Home", "APPM_Away", "Press_Home", "Press_Away",
        "ShotsCon_Home", "ShotsCon_Away", "Freq_HT45"
    ]
    try:
        df = pd.read_csv(file, sep=';', names=CORRECT_COL_NAMES, skiprows=1, encoding='latin-1')

        numeric_cols = CORRECT_COL_NAMES[9:]
        for col in numeric_cols:
            if df[col].dtype == object:
                df[col] = df[col].astype(str).str.replace('%', '', regex=False).str.replace(',', '.', regex=False)
            df[col] = pd.to_numeric(df[col], errors='coerce')

        cols_checar = ['Corners_Pro_Home', 'Corners_Pro_Away', 'Corners_Con_Home', 'Corners_Con_Away', 'Sample_Home', 'Sample_Away']
        df = df[~(df[cols_checar] == -1).any(axis=1)].copy()

        df = filtrar_blacklist(df, 'League', 'Home', 'Away', cfg)

        df['xCorners_Home'] = (df['Corners_Pro_Home'] * 0.60) + (df['Corners_Con_Away'] * 0.40)
        df['xCorners_Away'] = (df['Corners_Pro_Away'] * 0.40) + (df['Corners_Con_Home'] * 0.60)
        df['Expectativa_Cruzada'] = (df['xCorners_Home'] + df['xCorners_Away']).round(2)
        df['APPM_Total'] = (df['APPM_Home'].fillna(0) + df['APPM_Away'].fillna(0)).round(2)

        df_base = df[(df['Sample_Home'] >= 10) & (df['Sample_Away'] >= 10) & (df['Odds_Over_95'] > 1.0)].copy()
        df_main = df_base[(df_base['Expectativa_Cruzada'] >= 11.50) & (df_base['APPM_Total'] >= 1.00)].sort_values(by='Expectativa_Cruzada', ascending=False).copy()
        
        if cfg['top_n'] > 0:
            df_main = df_main.head(cfg['top_n'])

        resultados = []
        for _, r in df_main.iterrows():
            hora_clean = str(r['Hour'])[-5:] if len(str(r['Hour'])) >= 5 else str(r['Hour'])
            resultados.append({
                'hora': hora_clean,
                'jogo': f"{fix_str(r['Home'])} vs {fix_str(r['Away'])}",
                'liga': f"{fix_str(r['Country'])} - {fix_str(r['League'])}",
                'script': 'Cantos (Over 9.5)',
                'recomendacao': 'Over 9.5 Cantos',
                'score_confianca': f"Exp. Cantos: {r['Expectativa_Cruzada']:.2f}",
                'odd': float(r['Odds_Over_95'])
            })
        return resultados
    except Exception as e:
        st.error(f"Erro ao processar arquivo de Cantos: {e}")
        return []

# --- SCRIPT 2: GOLS ---
def processar_gols(file, cfg_over25, cfg_over15):
    try:
        try:
            df_raw = pd.read_csv(file, sep=None, engine='python', encoding='latin-1')
        except Exception:
            file.seek(0)
            df_raw = pd.read_csv(file, sep=';', encoding='latin-1')

        if df_raw.empty or len(df_raw.columns) < 10:
            return []

        df_raw.columns = [str(c).strip() for c in df_raw.columns]

        cols_tecnicas = list(range(9, min(33, len(df_raw.columns))))
        for col_idx in cols_tecnicas:
            df_raw.iloc[:, col_idx] = (
                df_raw.iloc[:, col_idx]
                .astype(str)
                .str.replace(',', '.')
                .apply(pd.to_numeric, errors='coerce')
            )

        df_clean = df_raw[~(df_raw.iloc[:, cols_tecnicas] == -1).any(axis=1)].copy()
        df_odd = df_clean[df_clean.iloc[:, 9] >= 1.60]   
        df_s5 = df_odd[(df_odd.iloc[:, 16] >= 8) & (df_odd.iloc[:, 17] >= 8)].copy()

        df_s5['SideA_FT'] = df_s5.iloc[:, 10] + df_s5.iloc[:, 13]
        df_s5['SideB_FT'] = df_s5.iloc[:, 11] + df_s5.iloc[:, 12]
        df_s5['Vol_FT'] = df_s5['SideA_FT'] + df_s5['SideB_FT']
        df_s5['Vol_HT'] = (df_s5.iloc[:, 25] + df_s5.iloc[:, 28]) + (df_s5.iloc[:, 26] + df_s5.iloc[:, 27])
        df_s5['Total_Chutes_Proj'] = (df_s5.iloc[:, 29] + df_s5.iloc[:, 32]) + (df_s5.iloc[:, 30] + df_s5.iloc[:, 31])
        df_s5['Chutes_Por_Gol'] = df_s5['Total_Chutes_Proj'] / df_s5['Vol_FT'].replace(0, np.nan)
        df_s5['Barreira_Under'] = df_s5.iloc[:, 24]

        col_liga, col_casa, col_fora = df_s5.columns[2], df_s5.columns[5], df_s5.columns[8]
        resultados = []

        # OVER 2.5
        df_base_25 = filtrar_blacklist(df_s5, col_liga, col_casa, col_fora, cfg_over25)
        df_ht_25 = df_base_25[(df_base_25['Vol_HT'] >= 1.5) & (df_base_25['Total_Chutes_Proj'] >= 15.0) & (df_base_25['Chutes_Por_Gol'] >= 2.7)].copy()
        
        if not df_ht_25.empty:
            s_ft = (((df_ht_25['Vol_FT'] / 2) / 3.5) * 100).clip(upper=100)
            s_press = ((df_ht_25['Total_Chutes_Proj'] / 20.0) * 100).clip(upper=100)
            s_freq = df_ht_25.iloc[:, 18:22].mean(axis=1)
            s_hist = df_ht_25.iloc[:, 23]
            df_ht_25['Score_Elite'] = (s_ft * 0.25) + (s_press * 0.30) + (s_freq * 0.25) + (s_hist * 0.20)
            
            df_score_25 = df_ht_25[df_ht_25['Score_Elite'] >= 60.0].sort_values(by='Score_Elite', ascending=False)
            mask_over25 = (df_score_25['Vol_FT'] >= 5.0) & (df_score_25['SideA_FT'] >= 2.5) & (df_score_25['SideB_FT'] >= 2.5) & (df_score_25['Barreira_Under'] < 42.0)
            df_res_25 = df_score_25[mask_over25].head(cfg_over25['top_n']) if cfg_over25['top_n'] > 0 else df_score_25[mask_over25]

            for _, r in df_res_25.iterrows():
                resultados.append({
                    'hora': str(r.iloc[3])[-5:],
                    'jogo': f"{fix_str(r.iloc[5])} vs {fix_str(r.iloc[8])}",
                    'liga': f"{fix_str(r.iloc[0])} - {fix_str(r.iloc[2])}",
                    'script': 'Gols (Over 2.5)',
                    'recomendacao': 'Over 2.5 Gols',
                    'score_confianca': f"Score Elite: {r['Score_Elite']:.1f}",
                    'odd': float(r.iloc[9])
                })

        # OVER 1.5
        df_base_15 = filtrar_blacklist(df_s5, col_liga, col_casa, col_fora, cfg_over15)
        df_ht_15 = df_base_15[(df_base_15['Vol_HT'] >= 1.5) & (df_base_15['Total_Chutes_Proj'] >= 15.0) & (df_base_15['Chutes_Por_Gol'] >= 2.7)].copy()

        if not df_ht_15.empty:
            s_ft = (((df_ht_15['Vol_FT'] / 2) / 3.5) * 100).clip(upper=100)
            s_press = ((df_ht_15['Total_Chutes_Proj'] / 20.0) * 100).clip(upper=100)
            s_freq = df_ht_15.iloc[:, 18:22].mean(axis=1)
            s_hist = df_ht_15.iloc[:, 23]
            df_ht_15['Score_Elite'] = (s_ft * 0.25) + (s_press * 0.30) + (s_freq * 0.25) + (s_hist * 0.20)
            
            df_score_15 = df_ht_15[df_ht_15['Score_Elite'] >= 60.0].sort_values(by='Score_Elite', ascending=False)
            mask_over25_check = (df_score_15['Vol_FT'] >= 5.0) & (df_score_15['SideA_FT'] >= 2.5) & (df_score_15['SideB_FT'] >= 2.5) & (df_score_15['Barreira_Under'] < 42.0)
            mask_over15 = (df_score_15['Vol_FT'] >= 5.0) & (df_score_15['Barreira_Under'] <= 50.0) & (~mask_over25_check)

            df_res_15 = df_score_15[mask_over15].head(cfg_over15['top_n']) if cfg_over15['top_n'] > 0 else df_score_15[mask_over15]
            for _, r in df_res_15.iterrows():
                resultados.append({
                    'hora': str(r.iloc[3])[-5:],
                    'jogo': f"{fix_str(r.iloc[5])} vs {fix_str(r.iloc[8])}",
                    'liga': f"{fix_str(r.iloc[0])} - {fix_str(r.iloc[2])}",
                    'script': 'Gols (Over 1.5)',
                    'recomendacao': 'Over 1.5 Gols',
                    'score_confianca': f"Score Elite: {r['Score_Elite']:.1f}",
                    'odd': float(r.iloc[9])
                })

        return resultados
    except Exception as e:
        return []

# --- SCRIPT 3 (NOVO): NOVO ROBÔ VITÓRIA (POISSON HÍBRIDO) ---
def processar_vitoria_novo(df_clean, cfg):
    try:
        df_clean["Diff_Efficiency"] = df_clean["Efficiency_Home"] - df_clean["Efficiency_Away"]
        df_clean["Diff_Pressure"] = df_clean["Pressure_Home"] - df_clean["Pressure_Away"]

        avg_goals_home = np.maximum(df_clean["Goals_Scored_Home"], 0.5)
        avg_goals_away = np.maximum(df_clean["Goals_Scored_Away"], 0.5)

        mod_home = 1.0 + (df_clean["Diff_Efficiency"] * 0.005) + (df_clean["Diff_Pressure"] * 0.002)
        mod_away = 1.0 - (df_clean["Diff_Efficiency"] * 0.005) - (df_clean["Diff_Pressure"] * 0.002)

        df_clean["xG_Home"] = np.clip(avg_goals_home * mod_home, 0.2, 4.5)
        df_clean["xG_Away"] = np.clip(avg_goals_away * mod_away, 0.2, 4.5)

        picks = []
        max_goals = 8

        for _, r in df_clean.iterrows():
            lambda_h, lambda_a = r["xG_Home"], r["xG_Away"]
            p_home, p_draw, p_away = 0.0, 0.0, 0.0

            for h in range(max_goals):
                p_h = calcular_poisson(h, lambda_h)
                for a in range(max_goals):
                    p_a = calcular_poisson(a, lambda_a)
                    prob = p_h * p_a
                    if h > a: p_home += prob
                    elif h == a: p_draw += prob
                    else: p_away += prob

            total_p = p_home + p_draw + p_away
            if total_p > 0:
                p_home, p_draw, p_away = p_home/total_p, p_draw/total_p, p_away/total_p

            if p_home >= 0.55 and p_draw < 0.25 and r["Odds_Home"] >= 1.50:
                picks.append({'Hour': r['Hour'], 'Home_Team': r['Home_Team'], 'Visitor_Team': r['Visitor_Team'], 'Country': r['Country'], 'League': r['League'], 'Recomendacao': f"Back Vitória: {fix_str(r['Home_Team'])}", 'Score': f"Vitória: {p_home*100:.1f}%", 'Odds': float(r['Odds_Home']), 'Prob_Sort': p_home})
            elif 0.45 <= p_home <= 0.55 and p_draw > 0.25 and r["Odds_Home"] >= 1.50:
                picks.append({'Hour': r['Hour'], 'Home_Team': r['Home_Team'], 'Visitor_Team': r['Visitor_Team'], 'Country': r['Country'], 'League': r['League'], 'Recomendacao': f"DNB: {fix_str(r['Home_Team'])}", 'Score': f"Vitória: {p_home*100:.1f}%", 'Odds': float(r['Odds_Home']), 'Prob_Sort': p_home})

            if p_away >= 0.55 and p_draw < 0.25 and r["Odds_Away"] >= 1.50:
                picks.append({'Hour': r['Hour'], 'Home_Team': r['Home_Team'], 'Visitor_Team': r['Visitor_Team'], 'Country': r['Country'], 'League': r['League'], 'Recomendacao': f"Back Vitória: {fix_str(r['Visitor_Team'])}", 'Score': f"Vitória: {p_away*100:.1f}%", 'Odds': float(r['Odds_Away']), 'Prob_Sort': p_away})
            elif 0.45 <= p_away <= 0.55 and p_draw > 0.25 and r["Odds_Away"] >= 1.50:
                picks.append({'Hour': r['Hour'], 'Home_Team': r['Home_Team'], 'Visitor_Team': r['Visitor_Team'], 'Country': r['Country'], 'League': r['League'], 'Recomendacao': f"DNB: {fix_str(r['Visitor_Team'])}", 'Score': f"Vitória: {p_away*100:.1f}%", 'Odds': float(r['Odds_Away']), 'Prob_Sort': p_away})

        if not picks: return []
        df_picks = pd.DataFrame(picks).sort_values(by="Prob_Sort", ascending=False).drop_duplicates(subset=["Home_Team", "Visitor_Team"])
        top_picks = df_picks.head(cfg['top_n']) if cfg['top_n'] > 0 else df_picks

        res = []
        for _, r in top_picks.iterrows():
            res.append({'hora': str(r['Hour'])[-5:], 'jogo': f"{fix_str(r['Home_Team'])} vs {fix_str(r['Visitor_Team'])}", 'liga': f"{fix_str(r['Country'])} - {fix_str(r['League'])}", 'script': 'Vitória / Dominância (Novo)', 'recomendacao': r['Recomendacao'], 'score_confianca': r['Score'], 'odd': float(r['Odds'])})
        return res
    except:
        return []

# --- SCRIPT 3 (ANTIGO): ANÁLISE ORIGINAL ---
def processar_vitoria_antigo(df_clean, cfg):
    try:
        df_old = df_clean.copy()
        df_old["Diff_Efficiency"] = df_old["Efficiency_Home"] - df_old["Efficiency_Away"]
        df_old["Diff_Pressure"] = df_old["Pressure_Home"] - df_old["Pressure_Away"]
        df_old["Score_Dominance_Home"] = (df_old["Diff_Efficiency"] * 0.6) + (df_old["Diff_Pressure"] * 0.4) + (df_old["Win_Pct_Home"] * 0.2)
        df_old["Score_Dominance_Away"] = (-df_old["Diff_Efficiency"] * 0.6) + (-df_old["Diff_Pressure"] * 0.4) + (df_old["Win_Pct_Away"] * 0.2)

        picks = []
        for _, r in df_old.iterrows():
            if r["Score_Dominance_Home"] >= 25.0 and r["H2H_Win_Pct_Home"] >= 40.0 and r["Odds_Home"] >= 1.50:
                picks.append({'Hour': r['Hour'], 'Home_Team': r['Home_Team'], 'Visitor_Team': r['Visitor_Team'], 'Country': r['Country'], 'League': r['League'], 'Recomendacao': f"Back Vitória: {fix_str(r['Home_Team'])}", 'Score': f"Dom.: {r['Score_Dominance_Home']:.1f}", 'Odds': float(r['Odds_Home']), 'Score_Sort': r['Score_Dominance_Home']})
            elif r["Score_Dominance_Away"] >= 25.0 and r["H2H_Win_Pct_Away"] >= 40.0 and r["Odds_Away"] >= 1.50:
                picks.append({'Hour': r['Hour'], 'Home_Team': r['Home_Team'], 'Visitor_Team': r['Visitor_Team'], 'Country': r['Country'], 'League': r['League'], 'Recomendacao': f"Back Vitória: {fix_str(r['Visitor_Team'])}", 'Score': f"Dom.: {r['Score_Dominance_Away']:.1f}", 'Odds': float(r['Odds_Away']), 'Score_Sort': r['Score_Dominance_Away']})

        if not picks: return []
        df_picks = pd.DataFrame(picks).sort_values(by="Score_Sort", ascending=False).drop_duplicates(subset=["Home_Team", "Visitor_Team"])
        top_picks = df_picks.head(cfg['top_n']) if cfg['top_n'] > 0 else df_picks

        res = []
        for _, r in top_picks.iterrows():
            res.append({'hora': str(r['Hour'])[-5:], 'jogo': f"{fix_str(r['Home_Team'])} vs {fix_str(r['Visitor_Team'])}", 'liga': f"{fix_str(r['Country'])} - {fix_str(r['League'])}", 'script': 'Vitória (Versão Antiga - Validação)', 'recomendacao': r['Recomendacao'], 'score_confianca': r['Score'], 'odd': float(r['Odds'])})
        return res
    except:
        return []

# --- CARREGADOR UNIFICADO DO ROBÔ DE VITÓRIA ---
def carregar_dados_vitoria(win_file, conf_file, cfg):
    df_win = pd.read_csv(win_file, sep=";", encoding='latin-1')
    df_conf = pd.read_csv(conf_file, sep=";", encoding='latin-1')

    col_names_win = ["Country", "Short", "League", "Hour", "Status", "Home_Team", "Result_Home", "Result_Visitor", "Visitor_Team", "Odds_Home", "Odds_Away", "Win_Pct_Home", "Win_Pct_Away", "Efficiency_Home", "Efficiency_Away", "Games_Home", "Games_Away", "ExG", "Pressure_Home", "Pressure_Away", "Attacks_Min_Home", "Attacks_Min_Away", "Shots_Scored_Home", "Shots_Scored_Away", "Shots_Conceded_Home", "Shots_Conceded_Away", "ShotsOnTarget_Scored_Home", "ShotsOnTarget_Scored_Away", "ShotsOnTarget_Conceded_Home", "ShotsOnTarget_Conceded_Away", "Possession_Home", "Possession_Away", "Goals_Scored_Home", "Goals_Scored_Away", "Goals_Conceded_Home", "Goals_Conceded_Away"]
    df_win.columns = col_names_win[: len(df_win.columns)]

    col_names_conf = ["Country", "Short", "League", "Hour", "Status", "Home_Team", "Result_Home", "Result_Visitor", "Visitor_Team", "H2H_Games", "H2H_Win_Pct_Home", "H2H_Win_Pct_Away"]
    df_conf.columns = col_names_conf[: len(df_conf.columns)]

    merged = pd.merge(df_win, df_conf[["Home_Team", "Visitor_Team", "League", "Hour", "H2H_Games", "H2H_Win_Pct_Home", "H2H_Win_Pct_Away"]], on=["Home_Team", "Visitor_Team", "League", "Hour"], how="inner")
    num_cols = [c for c in merged.columns if c not in ["Country", "Short", "League", "Hour", "Status", "Home_Team", "Visitor_Team"]]
    for col in num_cols:
        if merged[col].dtype == object:
            merged[col] = merged[col].astype(str).str.replace('%', '', regex=False).str.replace(',', '.', regex=False)
        merged[col] = pd.to_numeric(merged[col], errors="coerce")

    merged = merged[~(merged[["Efficiency_Home", "Efficiency_Away", "Games_Home", "Games_Away", "Goals_Scored_Home", "Goals_Scored_Away"]] == -1).any(axis=1)].copy()
    merged[num_cols] = merged[num_cols].fillna(0.0)

    df_clean = merged[(merged["Games_Home"] >= 8) & (merged["Games_Away"] >= 8)].copy()
    return filtrar_blacklist(df_clean, 'League', 'Home_Team', 'Visitor_Team', cfg)

# --- NAVEGAÇÃO STREAMLIT ---
st.sidebar.title("⚽ Robô Pro de Futebol")
aba = st.sidebar.radio("Navegação", ["1. Análise de Arquivos", "2. Gerenciar Entradas (Novas)", "3. Apostas em Andamento", "4. Dashboard Financeiro", "5. Parâmetros & Configurações"])

configs_atuais = carregar_configuracoes()

# ---------------------------------------------------------
# ABA 1: UPLOAD E PROCESSAMENTO DE DADOS
# ---------------------------------------------------------
if aba == "1. Análise de Arquivos":
    st.header("📥 Upload dos Arquivos PackBall")
    c1, c2 = st.columns(2)
    with c1:
        f_cantos = st.file_uploader("Arquivo de Cantos", key="cantos")
        f_gols = st.file_uploader("Arquivo de Over Gols", key="gols")
    with c2:
        f_win = st.file_uploader("Arquivo Back Win", key="win")
        f_conf = st.file_uploader("Arquivo Back Confronto H2H", key="conf")

    if st.button("🚀 Processar Oportunidades", use_container_width=True):
        todas_oportunidades = []
        cfg_default = {'top_n': 10, 'permitir_copas': False, 'permitir_sub20': False, 'permitir_feminino': False}

        if f_cantos:
            todas_oportunidades.extend(processar_cantos(f_cantos, configs_atuais.get('Cantos (Over 9.5)', cfg_default)))
        if f_gols:
            todas_oportunidades.extend(processar_gols(f_gols, configs_atuais.get('Gols (Over 2.5)', cfg_default), configs_atuais.get('Gols (Over 1.5)', cfg_default)))
        if f_win and f_conf:
            cfg_win_novo = configs_atuais.get('Vitória / Dominância (Novo)', cfg_default)
            df_vitoria_base = carregar_dados_vitoria(f_win, f_conf, cfg_win_novo)
            
            todas_oportunidades.extend(processar_vitoria_novo(df_vitoria_base, cfg_win_novo))
            
            # Robô Antigo roda silenciosamente para a "gaveta" de validação
            st.session_state['vitoria_sombra_temp'] = processar_vitoria_antigo(df_vitoria_base, configs_atuais.get('Vitória (Versão Antiga - Validação)', cfg_default))

        if todas_oportunidades:
            st.session_state['oportunidades_temp'] = todas_oportunidades
            st.success(f"Foram encontradas {len(todas_oportunidades)} oportunidades reais de aposta!")
        else:
            st.session_state['oportunidades_temp'] = []
            st.warning("Nenhuma oportunidade encontrada.")

    if 'oportunidades_temp' in st.session_state and st.session_state['oportunidades_temp']:
        st.subheader("📋 Recomendações Unificadas para Entradas")
        st.dataframe(pd.DataFrame(st.session_state['oportunidades_temp']), use_container_width=True)

        if st.button("💾 Enviar Oportunidades para Gestão de Banca", use_container_width=True):
            conn = sqlite3.connect("oportunidades.db")
            c = conn.cursor()
            data_hoje = date.today().isoformat()
            
            sql_insert = "INSERT INTO entradas (data_registro, hora, jogo, liga, script_origem, recomendacao, score_confianca, odd_sugerida, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Pendente')"
            for item in st.session_state['oportunidades_temp']:
                c.execute(sql_insert, (data_hoje, item['hora'], item['jogo'], item['liga'], item['script'], item['recomendacao'], str(item.get('score_confianca', 'N/A')), item['odd']))
            
            if 'vitoria_sombra_temp' in st.session_state:
                sql_sombra = "INSERT INTO entradas (data_registro, hora, jogo, liga, script_origem, recomendacao, score_confianca, odd_sugerida, odd_comprada, valor_apostado, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Em Andamento (Sombra)')"
                stake_antiga = configs_atuais.get('Vitória (Versão Antiga - Validação)', {}).get('stake_padrao', 50.0)
                for item in st.session_state['vitoria_sombra_temp']:
                    c.execute(sql_sombra, (data_hoje, item['hora'], item['jogo'], item['liga'], item['script'], item['recomendacao'], str(item.get('score_confianca', 'N/A')), item['odd'], item['odd'], stake_antiga))

            conn.commit()
            conn.close()
            st.session_state['oportunidades_temp'] = []
            st.session_state['vitoria_sombra_temp'] = []
            st.success("✅ Oportunidades enviadas com sucesso!")

# ---------------------------------------------------------
# ABA 2: GERENCIAR ENTRADAS NOVAS
# ---------------------------------------------------------
elif aba == "2. Gerenciar Entradas (Novas)":
    st.header("🎯 Sugestões Pendentes por Estratégia")
    conn = sqlite3.connect("oportunidades.db")
    df_entradas = pd.read_sql_query("SELECT * FROM entradas WHERE LOWER(TRIM(status)) = 'pendente' ORDER BY script_origem ASC, hora ASC", conn)

    if df_entradas.empty:
        st.info("Nenhuma sugestão pendente no momento!")
    else:
        for estrategia, grupo in df_entradas.groupby('script_origem'):
            stake_padrao = configs_atuais.get(estrategia, {}).get('stake_padrao', 50.0)
            with st.expander(f"📁 {estrategia} ({len(grupo)} oportunidades)", expanded=True):
                for idx, row in grupo.iterrows():
                    st.markdown(f"##### ⏰ [{row['hora']}] {row['jogo']} - *{row['recomendacao']}*")
                    col_info, col_inputs, col_botoes = st.columns([2.5, 2.5, 1.5])
                    with col_info:
                        st.write(f"**Liga:** {row['liga']}")
                        st.write(f"**Odd Sugerida:** {row['odd_sugerida']:.2f} | **Confiança:** `{row['score_confianca']}`")
                    with col_inputs:
                        odd_comprada = st.number_input("Odd Real Comprada:", value=float(row['odd_sugerida']), step=0.01, key=f"odd_{row['id']}")
                        valor_apostado = st.number_input("Valor Apostado (R$):", value=float(stake_padrao), step=5.0, key=f"val_{row['id']}")
                    with col_botoes:
                        if st.button("📌 Confirmar", key=f"conf_{row['id']}", use_container_width=True):
                            conn.execute("UPDATE entradas SET status = 'Em Andamento', odd_comprada = ?, valor_apostado = ? WHERE id = ?", (odd_comprada, valor_apostado, row['id']))
                            conn.commit()
                            st.toast("Aposta ativada!")
                            st.rerun()
                        if st.button("🗑️ Descartar", key=f"del_{row['id']}", use_container_width=True):
                            conn.execute("DELETE FROM entradas WHERE id = ?", (row['id'],))
                            conn.commit()
                            st.rerun()
                    st.divider()
    conn.close()

# ---------------------------------------------------------
# ABA 3: APOSTAS EM ANDAMENTO
# ---------------------------------------------------------
elif aba == "3. Apostas em Andamento":
    st.header("⏳ Apostas Confirmadas (Aguardando Resultado)")
    conn = sqlite3.connect("oportunidades.db")
    df_andamento = pd.read_sql_query("SELECT * FROM entradas WHERE LOWER(TRIM(status)) = 'em andamento' ORDER BY script_origem ASC, hora ASC", conn)

    if df_andamento.empty:
        st.info("Nenhuma aposta real em andamento.")
    else:
        for estrategia, grupo in df_andamento.groupby('script_origem'):
            with st.expander(f"📁 {estrategia} ({len(grupo)} ativas)", expanded=True):
                for idx, row in grupo.iterrows():
                    st.markdown(f"##### ⚽ [{row['hora']}] {row['jogo']} - *{row['recomendacao']}*")
                    col_info, col_botoes = st.columns([3, 2])
                    with col_info:
                        st.write(f"**Liga:** {row['liga']}")
                        st.write(f"**Apostado:** R$ {row['valor_apostado']:.2f} | **Odd:** {row['odd_comprada']:.2f}")
                    with col_botoes:
                        if st.button("🟢 Green", key=f"g_{row['id']}", use_container_width=True):
                            lucro = (row['odd_comprada'] - 1) * row['valor_apostado']
                            conn.execute("UPDATE entradas SET status = 'Green', lucro_prejuizo = ? WHERE id = ?", (lucro, row['id']))
                            conn.execute("UPDATE entradas SET status = 'Green', lucro_prejuizo = (odd_comprada - 1) * valor_apostado WHERE jogo = ? AND status = 'Em Andamento (Sombra)'", (row['jogo'],))
                            conn.commit()
                            st.toast("Green!")
                            st.rerun()
                        if st.button("🔴 Red", key=f"r_{row['id']}", use_container_width=True):
                            conn.execute("UPDATE entradas SET status = 'Red', lucro_prejuizo = ? WHERE id = ?", (-row['valor_apostado'], row['id']))
                            conn.execute("UPDATE entradas SET status = 'Red', lucro_prejuizo = -valor_apostado WHERE jogo = ? AND status = 'Em Andamento (Sombra)'", (row['jogo'],))
                            conn.commit()
                            st.toast("Red!")
                            st.rerun()
                        if st.button("⚪ Anular", key=f"n_{row['id']}", use_container_width=True):
                            conn.execute("UPDATE entradas SET status = 'Anulada', lucro_prejuizo = 0 WHERE id = ?", (row['id'],))
                            conn.execute("UPDATE entradas SET status = 'Anulada', lucro_prejuizo = 0 WHERE jogo = ? AND status = 'Em Andamento (Sombra)'", (row['jogo'],))
                            conn.commit()
                            st.rerun()
                    st.divider()
    conn.close()

# ---------------------------------------------------------
# ABA 4: DASHBOARD FINANCEIRO (COMPLETO E ANALÍTICO)
# ---------------------------------------------------------
elif aba == "4. Dashboard Financeiro":
    st.header("📊 Painel de Desempenho Analítico (Seu Dinheiro Real)")

    conn = sqlite3.connect("oportunidades.db")
    df_hist_full = pd.read_sql_query("SELECT * FROM entradas WHERE LOWER(TRIM(status)) IN ('green', 'red', 'anulada')", conn)

    if df_hist_full.empty:
        st.info("Nenhuma aposta finalizada no histórico para gerar métricas.")
        conn.close()
    else:
        # Filtros no topo
        st.subheader("🔍 Filtros de Análise")
        f1, f2 = st.columns(2)

        with f1:
            opcao_data = st.selectbox("Período:", ["Mês Atual", "Hoje", "Últimos 7 dias", "Todo o Histórico"])
            hoje = date.today()
            if opcao_data == "Hoje": d_inicio, d_fim = hoje, hoje
            elif opcao_data == "Últimos 7 dias": d_inicio, d_fim = hoje - pd.Timedelta(days=7), hoje
            elif opcao_data == "Mês Atual": d_inicio, d_fim = date(hoje.year, hoje.month, 1), hoje
            else: d_inicio, d_fim = date(2020, 1, 1), hoje

        with f2:
            estrategias_reais = [e for e in df_hist_full['script_origem'].unique() if e != 'Vitória (Versão Antiga - Validação)']
            est_selecionada = st.selectbox("Estratégia:", ["Todas as Estratégias Reais"] + estrategias_reais)

        # Tratamento de Datas
        df_hist_full['data_registro'] = df_hist_full['data_registro'].fillna(hoje.isoformat())
        df_hist_full['data_dt'] = pd.to_datetime(df_hist_full['data_registro'], errors='coerce').dt.date
        df_hist_full['data_dt'] = df_hist_full['data_dt'].fillna(hoje)

        # Filtrando Data
        mask_data = (df_hist_full['data_dt'] >= d_inicio) & (df_hist_full['data_dt'] <= d_fim)
        df_filtrado_data = df_hist_full[mask_data].copy()

        # Separando os dados reais dos dados do robô sombra
        df_real = df_filtrado_data[df_filtrado_data['script_origem'] != 'Vitória (Versão Antiga - Validação)'].copy()
        df_sombra = df_filtrado_data[df_filtrado_data['script_origem'] == 'Vitória (Versão Antiga - Validação)'].copy()

        # Filtrando Estratégia no Dashboard Principal
        if est_selecionada != "Todas as Estratégias Reais":
            df_real = df_real[df_real['script_origem'] == est_selecionada]

        st.divider()

        if df_real.empty:
            st.warning("Nenhum dado real registrado para os filtros selecionados.")
        else:
            # --- 1. KPIs Principais ---
            df_resolvido = df_real[df_real['status'].str.strip().str.title().isin(['Green', 'Red'])]
            total_apostas = len(df_resolvido)
            greens = len(df_resolvido[df_resolvido['status'].str.strip().str.title() == 'Green'])
            reds = total_apostas - greens
            winrate = (greens / total_apostas * 100) if total_apostas > 0 else 0
            total_investido = df_real['valor_apostado'].sum()
            lucro_total = df_real['lucro_prejuizo'].sum()
            roi = (lucro_total / total_investido * 100) if total_investido > 0 else 0

            # Cálculo de Drawdown
            df_real_dd = df_real.sort_values('data_registro').copy()
            df_real_dd['Lucro_Acum'] = df_real_dd['lucro_prejuizo'].cumsum()
            df_real_dd['Pico'] = df_real_dd['Lucro_Acum'].cummax()
            df_real_dd['Drawdown'] = df_real_dd['Lucro_Acum'] - df_real_dd['Pico']
            max_drawdown = df_real_dd['Drawdown'].min() if not df_real_dd.empty else 0.0

            c1, c2, c3, c4, c5, c6 = st.columns(6)
            c1.metric("Total Entradas", total_apostas)
            c2.metric("🟢 Green / 🔴 Red", f"{greens} / {reds}")
            c3.metric("Assertividade", f"{winrate:.1f}%")
            c4.metric("Valor Investido", f"R$ {total_investido:.2f}")
            c5.metric("Lucro Líquido", f"R$ {lucro_total:.2f}", delta=f"{roi:.1f}% ROI")
            c6.metric("Max Drawdown", f"R$ {max_drawdown:.2f}")
            st.divider()

            # --- 2. Gráficos de Evolução (Layout com 2 colunas) ---
            g_col1, g_col2 = st.columns(2)
            with g_col1:
                st.markdown("**📈 Evolução Acumulada do Lucro/Prejuízo**")
                df_evolucao = df_real.groupby('data_registro')['lucro_prejuizo'].sum().cumsum()
                st.line_chart(df_evolucao)

            with g_col2:
                st.markdown("**📊 Resultado Financeiro Diário (R$)**")
                df_diario = df_real.groupby('data_registro')['lucro_prejuizo'].sum()
                st.bar_chart(df_diario)
            
            st.divider()

            # --- 3. Tabelas Analíticas Avançadas (Estratégias e Ligas) ---
            t_col1, t_col2 = st.columns(2)
            
            with t_col1:
                st.subheader("🛠️ Desempenho por Estratégia")
                perf_estrategia = []
                for script, group in df_real.groupby('script_origem'):
                    g_res = group[group['status'].str.strip().str.title().isin(['Green', 'Red'])]
                    tot = len(g_res)
                    g = len(g_res[g_res['status'].str.strip().str.title() == 'Green'])
                    inv = group['valor_apostado'].sum()
                    luc = group['lucro_prejuizo'].sum()
                    perf_estrategia.append({
                        'Estratégia': script,
                        'Entradas': tot,
                        'Winrate': f"{(g/tot*100):.1f}%" if tot > 0 else "0.0%",
                        'Lucro Líquido': luc,
                        'ROI': f"{(luc/inv*100):.1f}%" if inv > 0 else "0.0%"
                    })
                df_perf_estr = pd.DataFrame(perf_estrategia)
                st.dataframe(df_perf_estr.style.format({'Lucro Líquido': 'R$ {:.2f}'}).map(colorir_lucro, subset=['Lucro Líquido']), use_container_width=True)

            with t_col2:
                st.subheader("🌍 Desempenho por Liga (Top Ligas)")
                perf_ligas = []
                for liga, group in df_real.groupby('liga'):
                    g_res = group[group['status'].str.strip().str.title().isin(['Green', 'Red'])]
                    tot = len(g_res)
                    g = len(g_res[g_res['status'].str.strip().str.title() == 'Green'])
                    luc = group['lucro_prejuizo'].sum()
                    if tot > 0:
                        perf_ligas.append({
                            'Liga': liga,
                            'Jogos': tot,
                            'Winrate': f"{(g/tot*100):.0f}%",
                            'Lucro': luc
                        })
                df_perf_ligas = pd.DataFrame(perf_ligas).sort_values('Lucro', ascending=False)
                st.dataframe(df_perf_ligas.style.format({'Lucro': 'R$ {:.2f}'}).map(colorir_lucro, subset=['Lucro']), use_container_width=True)

            st.divider()

            # --- 4. Análise por Faixa de Odd e Dia da Semana ---
            o_col1, o_col2 = st.columns(2)
            
            with o_col1:
                st.subheader("🎯 Desempenho por Faixa de Odd")
                bins = [1.0, 1.49, 1.79, 2.19, 3.0, 10.0]
                labels = ['Até 1.49', '1.50 a 1.79', '1.80 a 2.19', '2.20 a 3.00', '3.01+']
                df_odds = df_real.copy()
                df_odds['Faixa_Odd'] = pd.cut(df_odds['odd_comprada'], bins=bins, labels=labels, right=True)
                
                perf_odds = []
                for faixa, group in df_odds.groupby('Faixa_Odd', observed=False):
                    g_res = group[group['status'].str.strip().str.title().isin(['Green', 'Red'])]
                    tot = len(g_res)
                    g = len(g_res[g_res['status'].str.strip().str.title() == 'Green'])
                    luc = group['lucro_prejuizo'].sum()
                    if tot > 0:
                        perf_odds.append({'Faixa de Odd': faixa, 'Entradas': tot, 'Winrate': f"{(g/tot*100):.1f}%", 'Lucro': luc})
                
                df_perf_odds = pd.DataFrame(perf_odds)
                if not df_perf_odds.empty:
                    st.dataframe(df_perf_odds.style.format({'Lucro': 'R$ {:.2f}'}).map(colorir_lucro, subset=['Lucro']), use_container_width=True)

            with o_col2:
                st.subheader("📅 Histórico Bruto Recente")
                cols_exibir = ['data_registro', 'jogo', 'odd_comprada', 'lucro_prejuizo', 'status']
                df_hist_view = df_real[cols_exibir].sort_values('data_registro', ascending=False).head(20)
                st.dataframe(df_hist_view.style.format({'odd_comprada': '{:.2f}', 'lucro_prejuizo': 'R$ {:.2f}'}).map(colorir_lucro, subset=['lucro_prejuizo']), use_container_width=True)

        # ---------------------------------------------------------------------------------
        # GAVETA ISOLADA: CONFERÊNCIA DO ROBÔ ANTIGO (APENAS PARA CONSULTA)
        # ---------------------------------------------------------------------------------
        st.markdown("


", unsafe_allow_html=True)
with st.expander("👀 Conferência Simples: Como estaria o Robô Antigo de Vitória? (Apenas Leitura)", expanded=False):
st.write("Estes dados não interferem nas métricas financeiras acima. São apenas os resultados que o código original de vitória teria alcançado em sombra no mesmo período.")

        if df_sombra.empty:
            st.info("Nenhuma entrada do Robô Antigo registrada neste período.")
        else:
            s_tot = len(df_sombra[df_sombra['status'].str.strip().str.title().isin(['Green', 'Red'])])
            s_greens = len(df_sombra[df_sombra['status'].str.strip().str.title() == 'Green'])
            s_lucro = df_sombra['lucro_prejuizo'].sum()
            s_wr = (s_greens / s_tot * 100) if s_tot > 0 else 0

            s_c1, s_c2, s_c3 = st.columns(3)
            s_c1.metric("Entradas Validadas", s_tot)
            s_c2.metric("Assertividade Antiga", f"{s_wr:.1f}%")
            s_c3.metric("Lucro Hipotético", f"R$ {s_lucro:.2f}")

            st.write("**Histórico de Jogos do Robô Antigo:**")
            st.dataframe(
                df_sombra[['data_registro', 'jogo', 'recomendacao', 'odd_comprada', 'lucro_prejuizo', 'status']].style.format({
                    'odd_comprada': '{:.2f}', 'lucro_prejuizo': 'R$ {:.2f}'
                }).map(colorir_lucro, subset=['lucro_prejuizo']),
                use_container_width=True
            )
conn.close()
---------------------------------------------------------
ABA 5: PARÂMETROS & CONFIGURAÇÕES
---------------------------------------------------------
elif aba == "5. Parâmetros & Configurações":
st.header("⚙️ Parâmetros de Entrada por Projeto")
scripts_disp = [
'Cantos (Over 9.5)',
'Gols (Over 2.5)',
'Gols (Over 1.5)',
'Vitória / Dominância (Novo)',
'Vitória (Versão Antiga - Validação)'
]

for script in scripts_disp:
    cfg = configs_atuais.get(script, {
        'stake_padrao': 50.0, 
        'top_n': 10, 
        'permitir_copas': False, 
        'permitir_sub20': False, 
        'permitir_feminino': False
    })
    with st.expander(f"🛠️ Parâmetros: {script}", expanded=False):
        col_cfg1, col_cfg2 = st.columns(2)
        with col_cfg1:
            nova_stake = st.number_input(f"Stake (R$):", value=float(cfg['stake_padrao']), step=5.0, key=f"stake_{script}")
            novo_top_n = st.number_input(f"Top N:", value=int(cfg['top_n']), min_value=1, step=1, key=f"top_{script}")
        with col_cfg2:
            perm_copas = st.checkbox("Incluir Copas", value=bool(cfg['permitir_copas']), key=f"copas_{script}")
            perm_sub20 = st.checkbox("Incluir Sub-20", value=bool(cfg['permitir_sub20']), key=f"sub_{script}")
            perm_fem = st.checkbox("Incluir Feminino", value=bool(cfg['permitir_feminino']), key=f"fem_{script}")
        
        if st.button(f"💾 Salvar {script}", key=f"save_{script}", use_container_width=True):
            conn = sqlite3.connect("oportunidades.db")
            conn.execute("""
                INSERT OR REPLACE INTO configuracoes 
                (script_nome, stake_padrao, top_n, permitir_copas, permitir_sub20, permitir_feminino) 
                VALUES (?, ?, ?, ?, ?, ?)
            """, (script, nova_stake, novo_top_n, int(perm_copas), int(perm_sub20), int(perm_fem)))
            conn.commit()
            conn.close()
            st.toast(f"Parâmetros salvos para {script}!")
            st.rerun()
