import os
import re
import sqlite3
import numpy as np
import pandas as pd
import streamlit as st
import requests
from difflib import SequenceMatcher
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
            odd_sugerida REAL,
            odd_comprada REAL DEFAULT 0.0,
            valor_apostado REAL DEFAULT 0.0,
            lucro_prejuizo REAL DEFAULT 0.0,
            status TEXT DEFAULT 'Pendente',
            placar_ao_vivo TEXT DEFAULT 'Não consultado'
        )
    """)
    
    c.execute("PRAGMA table_info(entradas)")
    cols = [col[1] for col in c.fetchall()]
    if 'placar_ao_vivo' not in cols:
        c.execute("ALTER TABLE entradas ADD COLUMN placar_ao_vivo TEXT DEFAULT 'Não consultado'")

    c.execute("""
        CREATE TABLE IF NOT EXISTS configuracoes (
            script_nome TEXT PRIMARY KEY,
            stake_padrao REAL DEFAULT 50.0,
            top_n INTEGER DEFAULT 10,
            permitir_copas INTEGER DEFAULT 0,
            permitir_sub20 INTEGER DEFAULT 0,
            permitir_feminino INTEGER DEFAULT 0,
            api_key TEXT DEFAULT '0643621bd1msh310745680e8ac73p10a756jsn4dff9e95dfcf'
        )
    """)
    
    c.execute("PRAGMA table_info(configuracoes)")
    cfg_cols = [col[1] for col in c.fetchall()]
    if 'api_key' not in cfg_cols:
        c.execute("ALTER TABLE configuracoes ADD COLUMN api_key TEXT DEFAULT '0643621bd1msh310745680e8ac73p10a756jsn4dff9e95dfcf'")

    scripts = [
        'Cantos (Over 9.5)', 
        'Gols (Over 2.5)', 
        'Gols (Over 1.5)', 
        'Vitória / Dominância'
    ]
    for s in scripts:
        c.execute("INSERT OR IGNORE INTO configuracoes (script_nome, stake_padrao, top_n, permitir_copas, permitir_sub20, permitir_feminino, api_key) VALUES (?, 50.0, 10, 0, 0, 0, '0643621bd1msh310745680e8ac73p10a756jsn4dff9e95dfcf')", (s,))
    conn.commit()
    conn.close()

init_db()

# --- INTEGRAÇÃO COM API-FOOTBALL & FALLBACK ---
def carregar_api_key():
    conn = sqlite3.connect("oportunidades.db")
    c = conn.cursor()
    c.execute("SELECT api_key FROM configuracoes LIMIT 1")
    res = c.fetchone()
    conn.close()
    if res and res[0]:
        return res[0]
    return "0643621bd1msh310745680e8ac73p10a756jsn4dff9e95dfcf"

def obter_placar_api_football(nome_jogo):
    api_key = carregar_api_key()
    
    # 1. TENTATIVA VIA API-FOOTBALL
    if api_key and len(api_key.strip()) > 10:
        try:
            parts = nome_jogo.split(" vs ")
            if len(parts) >= 2:
                home_team, away_team = parts[0].strip(), parts[1].strip()

                url = "https://api-football-v1.p.rapidapi.com/v3/fixtures"
                hoje = date.today().isoformat()
                headers = {
                    "x-rapidapi-key": api_key.strip(),
                    "x-rapidapi-host": "api-football-v1.p.rapidapi.com"
                }
                params = {"date": hoje}

                response = requests.get(url, headers=headers, params=params, timeout=6)
                if response.status_code == 200:
                    data = response.json()
                    fixtures = data.get("response", [])

                    melhor_match = None
                    maior_sim = 0.0

                    for fix in fixtures:
                        api_home = fix["teams"]["home"]["name"]
                        api_away = fix["teams"]["away"]["name"]

                        sim_home = SequenceMatcher(None, home_team.lower(), api_home.lower()).ratio()
                        sim_away = SequenceMatcher(None, away_team.lower(), api_away.lower()).ratio()
                        media_sim = (sim_home + sim_away) / 2

                        if media_sim > maior_sim and media_sim > 0.40:
                            maior_sim = media_sim
                            melhor_match = fix

                    if melhor_match:
                        status_short = melhor_match["fixture"]["status"]["short"]
                        elapsed = melhor_match["fixture"]["status"]["elapsed"]
                        goals_home = melhor_match["goals"]["home"]
                        goals_away = melhor_match["goals"]["away"]

                        gols_str = f"{goals_home if goals_home is not None else 0} x {goals_away if goals_away is not None else 0}"
                        tempo_str = f"{elapsed}'" if elapsed else status_short

                        # Tentar obter estatísticas de escanteios se disponível
                        fixture_id = melhor_match["fixture"]["id"]
                        cantos_str = ""
                        try:
                            url_stats = "https://api-football-v1.p.rapidapi.com/v3/fixtures/statistics"
                            res_stats = requests.get(url_stats, headers=headers, params={"fixture": fixture_id}, timeout=4)
                            if res_stats.status_code == 200:
                                stats_data = res_stats.json().get("response", [])
                                tot_cantos = 0
                                encontrou = False
                                for team_stat in stats_data:
                                    for item in team_stat.get("statistics", []):
                                        if item.get("type") == "Corner Kicks":
                                            val = item.get("value")
                                            if val is not None:
                                                tot_cantos += int(val)
                                                encontrou = True
                                if encontrou:
                                    cantos_str = f" | 🚩 Cantos: {tot_cantos}"
                        except Exception:
                            pass

                        return f"⚽ {gols_str} ({tempo_str}){cantos_str}"
        except Exception:
            pass

    # 2. FALLBACK AUTOMÁTICO (Caso a API dê 403 ou falhe)
    try:
        query = f"{nome_jogo} placar ao vivo flashscore"
        url_search = f"https://www.google.com/search?q={requests.utils.quote(query)}&hl=pt-BR"
        headers_web = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36'
        }
        res_web = requests.get(url_search, headers=headers_web, timeout=5)
        if res_web.status_code == 200:
            match = re.search(r'(\d+)\s*[-xX–]\s*(\d+)', res_web.text)
            if match:
                return f"⚽ {match.group(1)} x {match.group(2)} (Em Andamento)"
    except Exception:
        pass

    return "⚠️ Jogo não localizado no momento"

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
            'permitir_feminino': bool(row['permitir_feminino']),
            'api_key': str(row.get('api_key', '0643621bd1msh310745680e8ac73p10a756jsn4dff9e95dfcf'))
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

# --- LÓGICA DO SCRIPT 1: CANTOS ---
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
        mask_error = df.isin([-1, -1.0, '-1', '-1.0', ' -1'])
        df = df[~mask_error.any(axis=1)].copy()

        numeric_cols = CORRECT_COL_NAMES[9:]
        for col in numeric_cols:
            if df[col].dtype == object:
                df[col] = df[col].astype(str).str.replace('%', '', regex=False).str.replace(',', '.', regex=False)
            df[col] = pd.to_numeric(df[col], errors='coerce')

        df = df.dropna(subset=numeric_cols).copy()
        df = filtrar_blacklist(df, 'League', 'Home', 'Away', cfg)

        df['xCorners_Home'] = (df['Corners_Pro_Home'] * 0.60) + (df['Corners_Con_Away'] * 0.40)
        df['xCorners_Away'] = (df['Corners_Pro_Away'] * 0.40) + (df['Corners_Con_Home'] * 0.60)
        df['Expectativa_Cruzada'] = (df['xCorners_Home'] + df['xCorners_Away']).round(2)
        df['APPM_Total'] = (df['APPM_Home'] + df['APPM_Away']).round(2)

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
                'odd': float(r['Odds_Over_95'])
            })
        return resultados
    except Exception as e:
        st.error(f"Erro ao processar arquivo de Cantos: {e}")
        return []

# --- LÓGICA DO SCRIPT 2: GOLS ---
def processar_gols(file, cfg_over25, cfg_over15):
    try:
        try:
            df_raw = pd.read_csv(file, sep=None, engine='python', encoding='latin-1')
        except Exception:
            file.seek(0)
            df_raw = pd.read_csv(file, sep=';', encoding='latin-1')

        if df_raw.empty or len(df_raw.columns) < 10:
            st.warning("⚠️ O arquivo de Gols enviado está vazio ou num formato incompatível.")
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

        df_clean = df_raw.dropna(subset=[df_raw.columns[9], df_raw.columns[10], df_raw.columns[16], df_raw.columns[23]])
        df_clean = df_clean[~(df_clean.iloc[:, cols_tecnicas] == -1).any(axis=1)]

        df_odd = df_clean[df_clean.iloc[:, 9] >= 1.60]   
        df_s5 = df_odd[(df_odd.iloc[:, 16] >= 8) & (df_odd.iloc[:, 17] >= 8)].copy()

        df_s5['SideA_FT'] = df_s5.iloc[:, 10] + df_s5.iloc[:, 13]
        df_s5['SideB_FT'] = df_s5.iloc[:, 11] + df_s5.iloc[:, 12]
        df_s5['Vol_FT'] = df_s5['SideA_FT'] + df_s5['SideB_FT']

        df_s5['SideA_HT'] = df_s5.iloc[:, 25] + df_s5.iloc[:, 28]
        df_s5['SideB_HT'] = df_s5.iloc[:, 26] + df_s5.iloc[:, 27]
        df_s5['Vol_HT'] = df_s5['SideA_HT'] + df_s5['SideB_HT']

        df_s5['Pressao_LadoA'] = df_s5.iloc[:, 29] + df_s5.iloc[:, 32]
        df_s5['Pressao_LadoB'] = df_s5.iloc[:, 30] + df_s5.iloc[:, 31]
        df_s5['Total_Chutes_Proj'] = df_s5['Pressao_LadoA'] + df_s5['Pressao_LadoB']
        df_s5['Chutes_Por_Gol'] = df_s5['Total_Chutes_Proj'] / df_s5['Vol_FT']
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
            
            df_score_25 = df_ht_25[df_ht_25['Score_Elite'] >= 60.0].sort_values(by='Score_Elite', ascending=False).copy()
            mask_over25 = (df_score_25['Vol_FT'] >= 5.0) & (df_score_25['SideA_FT'] >= 2.5) & (df_score_25['SideB_FT'] >= 2.5) & (df_score_25['Barreira_Under'] < 42.0)
            
            df_res_25 = df_score_25[mask_over25].copy()
            if cfg_over25['top_n'] > 0:
                df_res_25 = df_res_25.head(cfg_over25['top_n'])

            for _, r in df_res_25.iterrows():
                hora_clean = str(r.iloc[3])[-5:]
                resultados.append({
                    'hora': hora_clean,
                    'jogo': f"{fix_str(r.iloc[5])} vs {fix_str(r.iloc[8])}",
                    'liga': f"{fix_str(r.iloc[0])} - {fix_str(r.iloc[2])}",
                    'script': 'Gols (Over 2.5)',
                    'recomendacao': 'Over 2.5 Gols',
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
            
            df_score_15 = df_ht_15[df_ht_15['Score_Elite'] >= 60.0].sort_values(by='Score_Elite', ascending=False).copy()
            mask_over25_check = (df_score_15['Vol_FT'] >= 5.0) & (df_score_15['SideA_FT'] >= 2.5) & (df_score_15['SideB_FT'] >= 2.5) & (df_score_15['Barreira_Under'] < 42.0)
            mask_over15 = (df_score_15['Vol_FT'] >= 5.0) & (df_score_15['Barreira_Under'] <= 50.0) & (~mask_over25_check)

            df_res_15 = df_score_15[mask_over15].copy()
            if cfg_over15['top_n'] > 0:
                df_res_15 = df_res_15.head(cfg_over15['top_n'])

            for _, r in df_res_15.iterrows():
                hora_clean = str(r.iloc[3])[-5:]
                resultados.append({
                    'hora': hora_clean,
                    'jogo': f"{fix_str(r.iloc[5])} vs {fix_str(r.iloc[8])}",
                    'liga': f"{fix_str(r.iloc[0])} - {fix_str(r.iloc[2])}",
                    'script': 'Gols (Over 1.5)',
                    'recomendacao': 'Over 1.5 Gols',
                    'odd': float(r.iloc[9])
                })

        return resultados
    except Exception as e:
        st.error(f"Erro ao processar arquivo de Gols: {e}")
        return []

# --- LÓGICA DO SCRIPT 3: VITÓRIA ---
def processar_vitoria(win_file, conf_file, cfg):
    try:
        df_win = pd.read_csv(win_file, sep=";", encoding='latin-1')
        df_conf = pd.read_csv(conf_file, sep=";", encoding='latin-1')

        col_names_win = [
            "Country", "Short", "League", "Hour", "Status", "Home_Team", "Result_Home", "Result_Visitor", "Visitor_Team",
            "Odds_Home", "Odds_Away", "Win_Pct_Home", "Win_Pct_Away", "Efficiency_Home", "Efficiency_Away",
            "Games_Home", "Games_Away", "ExG", "Pressure_Home", "Pressure_Away", "Attacks_Min_Home", "Attacks_Min_Away",
            "Shots_Scored_Home", "Shots_Scored_Away", "Shots_Conceded_Home", "Shots_Conceded_Away",
            "ShotsOnTarget_Scored_Home", "ShotsOnTarget_Scored_Away", "ShotsOnTarget_Conceded_Home", "ShotsOnTarget_Conceded_Away",
            "Possession_Home", "Possession_Away", "Goals_Scored_Home", "Goals_Scored_Away", "Goals_Conceded_Home", "Goals_Conceded_Away"
        ]
        df_win.columns = col_names_win[: len(df_win.columns)]

        col_names_conf = [
            "Country", "Short", "League", "Hour", "Status", "Home_Team", "Result_Home", "Result_Visitor", "Visitor_Team",
            "H2H_Games", "H2H_Win_Pct_Home", "H2H_Win_Pct_Away"
        ]
        df_conf.columns = col_names_conf[: len(df_conf.columns)]

        merged = pd.merge(
            df_win,
            df_conf[["Home_Team", "Visitor_Team", "League", "Hour", "H2H_Games", "H2H_Win_Pct_Home", "H2H_Win_Pct_Away"]],
            on=["Home_Team", "Visitor_Team", "League", "Hour"],
            how="inner"
        )

        num_cols = [c for c in merged.columns if c not in ["Country", "Short", "League", "Hour", "Status", "Home_Team", "Visitor_Team"]]
        for col in num_cols:
            merged[col] = pd.to_numeric(merged[col], errors="coerce")

        df_clean = merged[(merged["Games_Home"] >= 8) & (merged["Games_Away"] >= 8)].copy()
        df_clean = filtrar_blacklist(df_clean, 'League', 'Home_Team', 'Visitor_Team', cfg)

        df_clean["Diff_Efficiency"] = df_clean["Efficiency_Home"] - df_clean["Efficiency_Away"]
        df_clean["Diff_WinPct"] = df_clean["Win_Pct_Home"] - df_clean["Win_Pct_Away"]
        df_clean["Net_Goal_Dominance"] = (df_clean["Goals_Scored_Home"] - df_clean["Goals_Conceded_Home"]) - (df_clean["Goals_Scored_Away"] - df_clean["Goals_Conceded_Away"])
        df_clean["Net_Shots_OnTarget"] = (df_clean["ShotsOnTarget_Scored_Home"] - df_clean["ShotsOnTarget_Conceded_Home"]) - (df_clean["ShotsOnTarget_Scored_Away"] - df_clean["ShotsOnTarget_Conceded_Away"])
        df_clean["Diff_Pressure"] = df_clean["Pressure_Home"] - df_clean["Pressure_Away"]
        df_clean["Diff_Attacks"] = df_clean["Attacks_Min_Home"] - df_clean["Attacks_Min_Away"]
        df_clean["Diff_Possession"] = df_clean["Possession_Home"] - df_clean["Possession_Away"]
        df_clean["Net_Total_Shots"] = (df_clean["Shots_Scored_Home"] - df_clean["Shots_Conceded_Home"]) - (df_clean["Shots_Scored_Away"] - df_clean["Shots_Conceded_Away"])
        df_clean["Diff_H2H"] = df_clean["H2H_Win_Pct_Home"] - df_clean["H2H_Win_Pct_Away"]

        df_clean["Enhanced_Dominance_Score"] = (
            0.20 * df_clean["Diff_Efficiency"] + 0.10 * df_clean["Diff_WinPct"] +
            0.20 * (df_clean["Net_Goal_Dominance"] * 20) + 0.15 * (df_clean["Net_Shots_OnTarget"] * 10) +
            0.10 * df_clean["Diff_Pressure"] + 0.05 * (df_clean["Diff_Attacks"] * 50) +
            0.05 * df_clean["Diff_Possession"] + 0.05 * (df_clean["Net_Total_Shots"] * 5) + 0.10 * df_clean["Diff_H2H"]
        )

        df_clean["Prob_Home"] = 1 / (1 + np.exp(-0.035 * df_clean["Enhanced_Dominance_Score"]))
        df_clean["Prob_Away"] = 1 / (1 + np.exp(0.035 * df_clean["Enhanced_Dominance_Score"]))

        home_picks = df_clean[df_clean["Odds_Home"] >= 1.50].copy()
        home_picks["Pick_Team"] = home_picks["Home_Team"]
        home_picks["Odds"] = home_picks["Odds_Home"]
        home_picks["Prob"] = home_picks["Prob_Home"]

        away_picks = df_clean[df_clean["Odds_Away"] >= 1.50].copy()
        away_picks["Pick_Team"] = away_picks["Visitor_Team"]
        away_picks["Odds"] = away_picks["Odds_Away"]
        away_picks["Prob"] = away_picks["Prob_Away"]

        all_picks = pd.concat([home_picks, away_picks]).sort_values(by="Prob", ascending=False)
        
        top_n = cfg['top_n'] if cfg['top_n'] > 0 else 10
        top_picks = all_picks.head(top_n).copy()

        resultados = []
        for _, r in top_picks.iterrows():
            hora_clean = str(r['Hour'])[-5:] if len(str(r['Hour'])) >= 5 else str(r['Hour'])
            resultados.append({
                'hora': hora_clean,
                'jogo': f"{fix_str(r['Home_Team'])} vs {fix_str(r['Visitor_Team'])}",
                'liga': f"{fix_str(r['Country'])} - {fix_str(r['League'])}",
                'script': 'Vitória / Dominância',
                'recomendacao': f"Vitória: {fix_str(r['Pick_Team'])}",
                'odd': float(r['Odds'])
            })
        return resultados
    except Exception as e:
        st.error(f"Erro ao processar arquivos de Vitória: {e}")
        return []

# --- INTERFACE PRINCIPAL DO STREAMLIT ---
st.sidebar.title("⚽ Robô Pro de Futebol")
aba = st.sidebar.radio(
    "Navegação", 
    [
        "1. Análise de Arquivos", 
        "2. Gerenciar Entradas (Novas)", 
        "3. Apostas em Andamento", 
        "4. Dashboard Financeiro",
        "5. Parâmetros & Configurações"
    ]
)

configs_atuais = carregar_configuracoes()

# ---------------------------------------------------------
# ABA 1: UPLOAD E PROCESSAMENTO DE DADOS
# ---------------------------------------------------------
if aba == "1. Análise de Arquivos":
    st.header("📥 Upload dos Arquivos PackBall")
    st.write("Envie os arquivos CSV do dia para gerar as oportunidades com base nos seus parâmetros configurados.")

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
            res = processar_cantos(f_cantos, configs_atuais.get('Cantos (Over 9.5)', cfg_default))
            todas_oportunidades.extend(res)

        if f_gols:
            cfg_g25 = configs_atuais.get('Gols (Over 2.5)', cfg_default)
            cfg_g15 = configs_atuais.get('Gols (Over 1.5)', cfg_default)
            res = processar_gols(f_gols, cfg_g25, cfg_g15)
            todas_oportunidades.extend(res)

        if f_win and f_conf:
            res = processar_vitoria(f_win, f_conf, configs_atuais.get('Vitória / Dominância', cfg_default))
            todas_oportunidades.extend(res)

        if todas_oportunidades:
            st.session_state['oportunidades_temp'] = todas_oportunidades
            st.success(f"Foram encontradas {len(todas_oportunidades)} oportunidades no total!")
        else:
            st.session_state['oportunidades_temp'] = []
            st.warning("Nenhuma oportunidade encontrada com os critérios definidos.")

    if 'oportunidades_temp' in st.session_state and st.session_state['oportunidades_temp']:
        st.subheader("📋 Recomendações Unificadas do Dia")
        df_res = pd.DataFrame(st.session_state['oportunidades_temp'])
        st.dataframe(df_res, use_container_width=True)

        if st.button("💾 Enviar Oportunidades para Gestão de Banca", use_container_width=True):
            conn = sqlite3.connect("oportunidades.db")
            c = conn.cursor()
            data_hoje = date.today().isoformat()
            sql_insert = "INSERT INTO entradas (data_registro, hora, jogo, liga, script_origem, recomendacao, odd_sugerida, status) VALUES (?, ?, ?, ?, ?, ?, ?, 'Pendente')"
            for item in st.session_state['oportunidades_temp']:
                c.execute(sql_insert, (data_hoje, item['hora'], item['jogo'], item['liga'], item['script'], item['recomendacao'], item['odd']))
            conn.commit()
            conn.close()
            st.session_state['oportunidades_temp'] = []
            st.success("✅ Oportunidades enviadas com sucesso! Acesse a aba '2. Gerenciar Entradas' para acompanhar.")

# ---------------------------------------------------------
# ABA 2: GERENCIAR ENTRADAS NOVAS (COM PLACAR AO VIVO)
# ---------------------------------------------------------
elif aba == "2. Gerenciar Entradas (Novas)":
    st.header("🎯 Sugestões Pendentes por Estratégia")
    st.write("Confirme o valor e odd apostados para enviar para **Apostas em Andamento**, ou exclua se não for realizar.")

    conn = sqlite3.connect("oportunidades.db")
    df_entradas = pd.read_sql_query("SELECT * FROM entradas WHERE LOWER(TRIM(status)) = 'pendente' ORDER BY script_origem ASC, hora ASC", conn)

    if not df_entradas.empty:
        if st.button("🔄 Atualizar Placares Ao Vivo", key="btn_update_pend", use_container_width=True):
            with st.spinner("Consultando dados dos jogos em tempo real..."):
                c = conn.cursor()
                for _, r_p in df_entradas.iterrows():
                    placar = obter_placar_api_football(r_p['jogo'])
                    c.execute("UPDATE entradas SET placar_ao_vivo = ? WHERE id = ?", (placar, r_p['id']))
                conn.commit()
                st.toast("Placares atualizados!")
                st.rerun()

    if df_entradas.empty:
        st.info("Nenhuma sugestão pendente no momento!")
    else:
        for estrategia, grupo in df_entradas.groupby('script_origem'):
            stake_padrao = configs_atuais.get(estrategia, {}).get('stake_padrao', 50.0)
            
            with st.expander(f"📁 {estrategia} ({len(grupo)} oportunidades)", expanded=True):
                for idx, row in grupo.iterrows():
                    placar_str = row.get('placar_ao_vivo', 'Não consultado')
                    st.markdown(f"##### ⏰ [{row['hora']}] {row['jogo']} - *{row['recomendacao']}*")
                    st.caption(f"📺 **Status em Tempo Real:** {placar_str}")
                    
                    col_info, col_inputs, col_botoes = st.columns([2.5, 2.5, 1.5])

                    with col_info:
                        st.write(f"**Liga:** {row['liga']}")
                        st.write(f"**Odd Sugerida:** {row['odd_sugerida']:.2f}")

                    with col_inputs:
                        key_odd = f"odd_{row['id']}"
                        key_val = f"val_{row['id']}"
                        odd_comprada = st.number_input("Odd Real Comprada:", value=float(row['odd_sugerida']), step=0.01, key=key_odd)
                        valor_apostado = st.number_input("Valor Apostado (R$):", value=float(stake_padrao), step=5.0, key=key_val)

                    with col_botoes:
                        key_conf = f"conf_{row['id']}"
                        key_del = f"del_{row['id']}"
                        
                        btn_confirmar = st.button("📌 Confirmar Aposta", key=key_conf, use_container_width=True)
                        if btn_confirmar:
                            c = conn.cursor()
                            sql_update = "UPDATE entradas SET status = 'Em Andamento', odd_comprada = ?, valor_apostado = ? WHERE id = ?"
                            c.execute(sql_update, (odd_comprada, valor_apostado, row['id']))
                            conn.commit()
                            st.toast("Aposta enviada para Apostas em Andamento!")
                            st.rerun()

                        btn_descartar = st.button("🗑️ Descartar", key=key_del, use_container_width=True)
                        if btn_descartar:
                            c = conn.cursor()
                            sql_delete = "DELETE FROM entradas WHERE id = ?"
                            c.execute(sql_delete, (row['id'],))
                            conn.commit()
                            st.toast("Entrada descartada!")
                            st.rerun()
                    st.divider()
    conn.close()

# ---------------------------------------------------------
# ABA 3: APOSTAS EM ANDAMENTO (COM PLACAR AO VIVO)
# ---------------------------------------------------------
elif aba == "3. Apostas em Andamento":
    st.header("⏳ Apostas Confirmadas (Aguardando Resultado)")

    conn = sqlite3.connect("oportunidades.db")
    df_andamento = pd.read_sql_query("SELECT * FROM entradas WHERE LOWER(TRIM(status)) = 'em andamento' ORDER BY script_origem ASC, hora ASC", conn)

    if not df_andamento.empty:
        if st.button("🔄 Atualizar Placares Ao Vivo", key="btn_update_and", use_container_width=True):
            with st.spinner("Consultando dados dos jogos em tempo real..."):
                c = conn.cursor()
                for _, r_a in df_andamento.iterrows():
                    placar = obter_placar_api_football(r_a['jogo'])
                    c.execute("UPDATE entradas SET placar_ao_vivo = ? WHERE id = ?", (placar, r_a['id']))
                conn.commit()
                st.toast("Placares atualizados!")
                st.rerun()

    if df_andamento.empty:
        st.info("Nenhuma aposta em andamento no momento!")
    else:
        for estrategia, grupo in df_andamento.groupby('script_origem'):
            with st.expander(f"📁 {estrategia} ({len(grupo)} apostas ativas)", expanded=True):
                for idx, row in grupo.iterrows():
                    placar_str = row.get('placar_ao_vivo', 'Não consultado')
                    st.markdown(f"##### ⚽ [{row['hora']}] {row['jogo']} - *{row['recomendacao']}*")
                    st.caption(f"📺 **Status em Tempo Real:** {placar_str}")
                    
                    col_info, col_botoes = st.columns([3, 2])

                    with col_info:
                        st.write(f"**Liga:** {row['liga']}")
                        st.write(f"**Valor Apostado:** R$ {row['valor_apostado']:.2f} | **Odd Comprada:** {row['odd_comprada']:.2f}")

                    with col_botoes:
                        key_green = f"green_and_{row['id']}"
                        key_red = f"red_and_{row['id']}"
                        key_null = f"null_and_{row['id']}"

                        btn_green = st.button("🟢 Green", key=key_green, use_container_width=True)
                        if btn_green:
                            lucro = (row['odd_comprada'] - 1) * row['valor_apostado']
                            c = conn.cursor()
                            sql_g = "UPDATE entradas SET status = 'Green', lucro_prejuizo = ? WHERE id = ?"
                            c.execute(sql_g, (lucro, row['id']))
                            conn.commit()
                            st.toast(f"Green registrado com sucesso! (+R$ {lucro:.2f})")
                            st.rerun()

                        btn_red = st.button("🔴 Red", key=key_red, use_container_width=True)
                        if btn_red:
                            prejuizo = -row['valor_apostado']
                            c = conn.cursor()
                            sql_r = "UPDATE entradas SET status = 'Red', lucro_prejuizo = ? WHERE id = ?"
                            c.execute(sql_r, (prejuizo, row['id']))
                            conn.commit()
                            st.toast(f"Red registrado. (-R$ {row['valor_apostado']:.2f})")
                            st.rerun()

                        btn_null = st.button("⚪ Anular / Reembolso", key=key_null, use_container_width=True)
                        if btn_null:
                            c = conn.cursor()
                            sql_n = "UPDATE entradas SET status = 'Anulada', lucro_prejuizo = 0 WHERE id = ?"
                            c.execute(sql_n, (row['id'],))
                            conn.commit()
                            st.toast("Entrada Anulada!")
                            st.rerun()
                    st.divider()
    conn.close()

# ---------------------------------------------------------
# ABA 4: DASHBOARD FINANCEIRO
# ---------------------------------------------------------
elif aba == "4. Dashboard Financeiro":
    st.header("📊 Painel de Desempenho Financeiro")

    conn = sqlite3.connect("oportunidades.db")
    df_hist = pd.read_sql_query("SELECT * FROM entradas WHERE LOWER(TRIM(status)) IN ('green', 'red', 'anulada')", conn)

    if df_hist.empty:
        st.info("Nenhuma aposta finalizada no histórico para gerar métricas.")
        conn.close()
    else:
        st.subheader("🔍 Filtros de Análise")
        f1, f2 = st.columns(2)

        with f1:
            opcao_data = st.selectbox("Período:", ["Hoje", "Últimos 7 dias", "Mês Atual", "Todo o Histórico", "Personalizado"])
            hoje = date.today()

            if opcao_data == "Hoje":
                d_inicio, d_fim = hoje, hoje
            elif opcao_data == "Últimos 7 dias":
                d_inicio, d_fim = hoje - pd.Timedelta(days=7), hoje
            elif opcao_data == "Mês Atual":
                d_inicio, d_fim = date(hoje.year, hoje.month, 1), hoje
            elif opcao_data == "Todo o Histórico":
                d_inicio, d_fim = date(2020, 1, 1), hoje
            else:
                d_inicio = st.date_input("Data Inicial:", hoje)
                d_fim = st.date_input("Data Final:", hoje)

        with f2:
            estrategias_disponiveis = ["Todas as Estratégias"] + list(df_hist['script_origem'].unique())
            est_selecionada = st.selectbox("Estratégia:", estrategias_disponiveis)

        df_hist['data_registro'] = df_hist['data_registro'].fillna(hoje.isoformat())
        df_hist['data_dt'] = pd.to_datetime(df_hist['data_registro'], errors='coerce').dt.date
        df_hist['data_dt'] = df_hist['data_dt'].fillna(hoje)

        mask_data = (df_hist['data_dt'] >= d_inicio) & (df_hist['data_dt'] <= d_fim)
        df_filtrado = df_hist[mask_data].copy()

        if est_selecionada != "Todas as Estratégias":
            df_filtrado = df_filtrado[df_filtrado['script_origem'] == est_selecionada].copy()

        st.divider()

        if df_filtrado.empty:
            st.warning("Nenhum resultado registrado para o período ou estratégia selecionada.")
        else:
            total_apostas = len(df_filtrado[df_filtrado['status'].str.strip().str.title().isin(['Green', 'Red'])])
            greens = len(df_filtrado[df_filtrado['status'].str.strip().str.title() == 'Green'])
            reds = len(df_filtrado[df_filtrado['status'].str.strip().str.title() == 'Red'])
            winrate = (greens / total_apostas * 100) if total_apostas > 0 else 0

            total_investido = df_filtrado['valor_apostado'].sum()
            lucro_total = df_filtrado['lucro_prejuizo'].sum()
            roi = (lucro_total / total_investido * 100) if total_investido > 0 else 0

            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("Entradas Finalizadas", total_apostas)
            c2.metric("🟢 Greens / 🔴 Reds", f"{greens} / {reds}")
            c3.metric("Assertividade", f"{winrate:.1f}%")
            c4.metric("Total Investido", f"R$ {total_investido:.2f}")
            c5.metric("Lucro Líquido", f"R$ {lucro_total:.2f}", delta=f"{roi:.1f}% ROI")

            st.divider()

            st.subheader("📈 Evolução Financeira Acumulada")
            df_chart = df_filtrado.groupby('data_registro')['lucro_prejuizo'].sum().reset_index()
            df_chart['Lucro_Acumulado'] = df_chart['lucro_prejuizo'].cumsum()
            st.line_chart(df_chart.set_index('data_registro')['Lucro_Acumulado'])

            st.divider()

            st.subheader("📅 Desempenho Detalhado por Dia")
            diario_list = []
            for dt, group in df_filtrado.groupby('data_registro', sort=False):
                tot_d = len(group[group['status'].str.strip().str.title().isin(['Green', 'Red'])])
                g_d = len(group[group['status'].str.strip().str.title() == 'Green'])
                r_d = len(group[group['status'].str.strip().str.title() == 'Red'])
                wr_d = (g_d / tot_d * 100) if tot_d > 0 else 0
                inv_d = group['valor_apostado'].sum()
                luc_d = group['lucro_prejuizo'].sum()
                roi_d = (luc_d / inv_d * 100) if inv_d > 0 else 0.0
                
                diario_list.append({
                    'Data': dt,
                    'Apostas': tot_d,
                    'Greens': g_d,
                    'Reds': r_d,
                    'Assertividade (%)': f"{wr_d:.1f}%",
                    'Investido (R$)': f"R$ {inv_d:.2f}",
                    'Lucro do Dia (R$)': f"R$ {luc_d:.2f}",
                    'ROI (%)': f"{roi_d:.1f}%"
                })
            st.dataframe(pd.DataFrame(diario_list), use_container_width=True)

            st.divider()

            st.subheader("📁 Resultado Diário por Projeto / Estratégia")
            grp_proj = df_filtrado.groupby(['data_registro', 'script_origem']).agg(
                lucro=('lucro_prejuizo', 'sum'),
                investido=('valor_apostado', 'sum')
            ).reset_index()

            def calc_res_text(row):
                luc = row['lucro']
                inv = row['investido']
                r_pct = (luc / inv * 100) if inv > 0 else 0.0
                return f"R$ {luc:.2f} ({r_pct:.1f}%)"

            grp_proj['res_formatado'] = grp_proj.apply(calc_res_text, axis=1)
            pivot_proj = grp_proj.pivot_table(
                index='data_registro',
                columns='script_origem',
                values='res_formatado',
                aggfunc='first',
                fill_value="R$ 0.00 (0.0%)"
            ).reset_index()
            pivot_proj.rename(columns={'data_registro': 'Data'}, inplace=True)
            st.dataframe(pivot_proj, use_container_width=True)

            st.divider()

            st.subheader("📊 Performance Comparativa Global das Estratégias")
            perf_list = []
            for script, group in df_filtrado.groupby('script_origem'):
                tot = len(group[group['status'].str.strip().str.title().isin(['Green', 'Red'])])
                g = len(group[group['status'] == 'Green'])
                r = len(group[group['status'] == 'Red'])
                wr = (g / tot * 100) if tot > 0 else 0
                inv = group['valor_apostado'].sum()
                luc = group['lucro_prejuizo'].sum()
                roi_script = (luc / inv * 100) if inv > 0 else 0
                perf_list.append({
                    'Estratégia': script,
                    'Entradas': tot,
                    'Greens': g,
                    'Reds': r,
                    'Assertividade (%)': f"{wr:.1f}%",
                    'Investimento (R$)': f"R$ {inv:.2f}",
                    'Lucro Líquido (R$)': f"R$ {luc:.2f}",
                    'ROI (%)': f"{roi_script:.1f}%"
                })
            st.dataframe(pd.DataFrame(perf_list), use_container_width=True)

            st.divider()

            with st.expander("🛠️ Corrigir ou Excluir uma Entrada Registrada"):
                opcoes_editar = {
                    f"ID {r['id']} | {r['data_registro']} - {r['jogo']} [{r['script_origem']}] - Status: {r['status']}": r['id'] 
                    for _, r in df_filtrado.iterrows()
                }
                sel_label = st.selectbox("Selecione a Aposta para Editar:", list(opcoes_editar.keys()))
                id_selecionado = opcoes_editar[sel_label]
                row_edit = df_filtrado[df_filtrado['id'] == id_selecionado].iloc[0]

                ec1, ec2, ec3, ec4 = st.columns(4)
                novo_status = ec1.selectbox("Novo Status:", ["Green", "Red", "Anulada"], index=["Green", "Red", "Anulada"].index(row_edit['status']) if row_edit['status'] in ["Green", "Red", "Anulada"] else 0)
                nova_odd = ec2.number_input("Nova Odd Comprada:", value=float(row_edit['odd_comprada']), step=0.01)
                novo_valor = ec3.number_input("Novo Valor Apostado (R$):", value=float(row_edit['valor_apostado']), step=5.0)

                btn_salvar = ec4.button("💾 Salvar Alterações", use_container_width=True)
                btn_deletar_hist = ec4.button("🗑️ Deletar Registro", use_container_width=True)

                if btn_salvar:
                    novo_lucro = (nova_odd - 1) * novo_valor if novo_status == "Green" else (-novo_valor if novo_status == "Red" else 0.0)
                    c = conn.cursor()
                    sql_edit = "UPDATE entradas SET status = ?, odd_comprada = ?, valor_apostado = ?, lucro_prejuizo = ? WHERE id = ?"
                    c.execute(sql_edit, (novo_status, nova_odd, novo_valor, novo_lucro, id_selecionado))
                    conn.commit()
                    st.success("✅ Aposta atualizada com sucesso!")
                    st.rerun()

                if btn_deletar_hist:
                    c = conn.cursor()
                    sql_del_hist = "DELETE FROM entradas WHERE id = ?"
                    c.execute(sql_del_hist, (id_selecionado,))
                    conn.commit()
                    st.success("🗑️ Aposta removida do histórico!")
                    st.rerun()

            st.subheader("📋 Histórico Detalhado do Período Selecionado")
            st.dataframe(df_filtrado[['id', 'data_registro', 'hora', 'jogo', 'script_origem', 'recomendacao', 'odd_comprada', 'valor_apostado', 'lucro_prejuizo', 'status']], use_container_width=True)
        conn.close()

# ---------------------------------------------------------
# ABA 5: PARÂMETROS & CONFIGURAÇÕES
# ---------------------------------------------------------
elif aba == "5. Parâmetros & Configurações":
    st.header("⚙️ Parâmetros de Entrada e Integrações")
    st.write("Ajuste as regras de filtragem, limite de recomendações, stake fixa padrão e chave da API-Football.")

    api_key_atual = carregar_api_key()
    
    with st.expander("🔑 Configuração da API-Football (RapidAPI)", expanded=True):
        nova_key = st.text_input("Chave X-RapidAPI-Key:", value=api_key_atual, type="password")
        if st.button("💾 Salvar Chave API", use_container_width=True):
            conn = sqlite3.connect("oportunidades.db")
            c = conn.cursor()
            c.execute("UPDATE configuracoes SET api_key = ?", (nova_key,))
            conn.commit()
            conn.close()
            st.toast("Chave API salva com sucesso!")
            st.rerun()

    st.divider()

    scripts_disponiveis = [
        'Cantos (Over 9.5)',
        'Gols (Over 2.5)',
        'Gols (Over 1.5)',
        'Vitória / Dominância'
    ]

    for script in scripts_disponiveis:
        cfg = configs_atuais.get(script, {
            'stake_padrao': 50.0,
            'top_n': 10,
            'permitir_copas': False,
            'permitir_sub20': False,
            'permitir_feminino': False
        })

        with st.expander(f"🛠️ Parâmetros do Projeto: {script}", expanded=False):
            col_cfg1, col_cfg2 = st.columns(2)

            with col_cfg1:
                nova_stake = st.number_input(f"Stake Fixa Padrão (R$):", value=float(cfg['stake_padrao']), step=5.0, key=f"cfg_stake_{script}")
                novo_top_n = st.number_input(f"Limite de Retornos (Top N jogos):", value=int(cfg['top_n']), min_value=1, max_value=100, step=1, key=f"cfg_top_{script}")

            with col_cfg2:
                st.write("**Filtros de Ligas Aceitas:**")
                perm_copas = st.checkbox("Incluir Copas e Torneios Eliminatórios", value=bool(cfg['permitir_copas']), key=f"cfg_copas_{script}")
                perm_sub20 = st.checkbox("Incluir Ligas Sub-20 / Sub-23 / Formação", value=bool(cfg['permitir_sub20']), key=f"cfg_sub_{script}")
                perm_fem = st.checkbox("Incluir Jogos de Futebol Feminino", value=bool(cfg['permitir_feminino']), key=f"cfg_fem_{script}")

            if st.button(f"💾 Salvar Parâmetros para {script}", key=f"btn_save_cfg_{script}", use_container_width=True):
                conn = sqlite3.connect("oportunidades.db")
                c = conn.cursor()
                sql_save_cfg = """
                    INSERT OR REPLACE INTO configuracoes (script_nome, stake_padrao, top_n, permitir_copas, permitir_sub20, permitir_feminino, api_key)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """
                c.execute(sql_save_cfg, (script, nova_stake, novo_top_n, int(perm_copas), int(perm_sub20), int(perm_fem), nova_key if 'nova_key' in locals() else api_key_atual))
                conn.commit()
                conn.close()
                st.toast(f"Parâmetros de '{script}' atualizados com sucesso!")
                st.rerun()
