"""
Robô Pro de Futebol & Gestão — Interface Streamlit.

Este arquivo cuida só da UI. Banco de dados está em `db.py`, as estratégias
de processamento em `processors.py`, e as métricas do dashboard em
`metrics.py`. Ver o topo de cada um desses arquivos para o detalhe das
melhorias aplicadas.

MELHORIA: `init_db()` agora roda dentro de uma função com `@st.cache_resource`,
então só executa de fato uma vez por sessão do servidor — não a cada
interação do usuário (clique de botão, troca de aba etc.), como acontecia
no script original.
"""

from datetime import date

import pandas as pd
import streamlit as st

import db
from config import SCRIPTS_DISPONIVEIS, CFG_DEFAULT
from metrics import calcular_metricas, formatar_tabela_financeira, colorir_lucro
from processors import processar_cantos, processar_gols, processar_vitoria


st.set_page_config(page_title="Robô Pro de Futebol & Gestão", page_icon="⚽", layout="wide")


@st.cache_resource
def _inicializar_banco():
    db.init_db()
    return True


_inicializar_banco()

st.sidebar.title("⚽ Robô Pro de Futebol")
aba = st.sidebar.radio(
    "Navegação",
    [
        "1. Análise de Arquivos",
        "2. Gerenciar Entradas (Novas)",
        "3. Apostas em Andamento",
        "4. Dashboard Financeiro",
        "5. Parâmetros & Configurações",
    ],
)

configs_atuais = db.carregar_configuracoes()

STATUS_FINALIZADOS = ["Green", "Red", "Anulada"]


def _status_titulo(serie):
    return serie.astype(str).str.strip().str.title()


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

        if f_cantos:
            res = processar_cantos(f_cantos, configs_atuais.get("Cantos (Over 9.5)", CFG_DEFAULT))
            todas_oportunidades.extend(res)

        if f_gols:
            cfg_g25 = configs_atuais.get("Gols (Over 2.5)", CFG_DEFAULT)
            cfg_g15 = configs_atuais.get("Gols (Over 1.5)", CFG_DEFAULT)
            res = processar_gols(f_gols, cfg_g25, cfg_g15)
            todas_oportunidades.extend(res)

        if f_win and f_conf:
            res = processar_vitoria(f_win, f_conf, configs_atuais.get("Vitória / Dominância", CFG_DEFAULT))
            todas_oportunidades.extend(res)

        if todas_oportunidades:
            st.session_state["oportunidades_temp"] = todas_oportunidades
            st.success(f"Foram encontradas {len(todas_oportunidades)} oportunidades no total!")
        else:
            st.session_state["oportunidades_temp"] = []
            st.warning("Nenhuma oportunidade encontrada com os critérios definidos.")

    if "oportunidades_temp" in st.session_state and st.session_state["oportunidades_temp"]:
        st.subheader("📋 Recomendações Unificadas do Dia")
        df_res = pd.DataFrame(st.session_state["oportunidades_temp"])
        st.dataframe(df_res, use_container_width=True)

        if st.button("💾 Enviar Oportunidades para Gestão de Banca", use_container_width=True):
            data_hoje = date.today().isoformat()
            qtd_inserida = db.inserir_oportunidades(st.session_state["oportunidades_temp"], data_hoje)
            qtd_total = len(st.session_state["oportunidades_temp"])
            qtd_duplicada = qtd_total - qtd_inserida

            st.session_state["oportunidades_temp"] = []
            if qtd_duplicada > 0:
                st.success(
                    f"✅ {qtd_inserida} oportunidade(s) enviada(s) com sucesso! "
                    f"{qtd_duplicada} já existiam no banco e foram ignoradas (evitando duplicação)."
                )
            else:
                st.success("✅ Oportunidades enviadas com sucesso! Acesse a aba '2. Gerenciar Entradas' para acompanhar.")

# ---------------------------------------------------------
# ABA 2: GERENCIAR ENTRADAS NOVAS
# ---------------------------------------------------------
elif aba == "2. Gerenciar Entradas (Novas)":
    st.header("🎯 Sugestões Pendentes por Estratégia")
    st.write("Confirme o valor e odd apostados para enviar para **Apostas em Andamento**, ou exclua se não for realizar.")

    df_entradas = db.buscar_entradas_por_status("pendente")

    if df_entradas.empty:
        st.info("Nenhuma sugestão pendente no momento!")
    else:
        for estrategia, grupo in df_entradas.groupby("script_origem"):
            stake_padrao = configs_atuais.get(estrategia, {}).get("stake_padrao", 50.0)

            with st.expander(f"📁 {estrategia} ({len(grupo)} oportunidades)", expanded=True):
                for _, row in grupo.iterrows():
                    score_val = row["score_confianca"] if pd.notnull(row.get("score_confianca")) and str(row.get("score_confianca")).strip() != "" else "N/A"
                    st.markdown(f"##### ⏰ [{row['hora']}] {row['jogo']} - *{row['recomendacao']}*")

                    col_info, col_inputs, col_botoes = st.columns([2.5, 2.5, 1.5])

                    with col_info:
                        st.write(f"**Liga:** {row['liga']}")
                        st.write(f"**Odd Sugerida:** {row['odd_sugerida']:.2f}")
                        st.write(f"🔥 **Score / Confiança:** `{score_val}`")

                    with col_inputs:
                        odd_comprada = st.number_input(
                            "Odd Real Comprada:", value=float(row["odd_sugerida"]), step=0.01, key=f"odd_{row['id']}"
                        )
                        valor_apostado = st.number_input(
                            "Valor Apostado (R$):", value=float(stake_padrao), step=5.0, key=f"val_{row['id']}"
                        )

                    with col_botoes:
                        if st.button("📌 Confirmar Aposta", key=f"conf_{row['id']}", use_container_width=True):
                            db.confirmar_entrada(row["id"], odd_comprada, valor_apostado)
                            st.toast("Aposta enviada para Apostas em Andamento!")
                            st.rerun()

                        if st.button("🗑️ Descartar", key=f"del_{row['id']}", use_container_width=True):
                            db.descartar_entrada(row["id"])
                            st.toast("Entrada descartada!")
                            st.rerun()
                    st.divider()

# ---------------------------------------------------------
# ABA 3: APOSTAS EM ANDAMENTO
# ---------------------------------------------------------
elif aba == "3. Apostas em Andamento":
    st.header("⏳ Apostas Confirmadas (Aguardando Resultado)")

    df_andamento = db.buscar_entradas_por_status("em andamento")

    if df_andamento.empty:
        st.info("Nenhuma aposta em andamento no momento!")
    else:
        for estrategia, grupo in df_andamento.groupby("script_origem"):
            with st.expander(f"📁 {estrategia} ({len(grupo)} apostas ativas)", expanded=True):
                for _, row in grupo.iterrows():
                    score_val = row["score_confianca"] if pd.notnull(row.get("score_confianca")) and str(row.get("score_confianca")).strip() != "" else "N/A"
                    st.markdown(f"##### ⚽ [{row['hora']}] {row['jogo']} - *{row['recomendacao']}*")

                    col_info, col_botoes = st.columns([3, 2])

                    with col_info:
                        st.write(f"**Liga:** {row['liga']}")
                        st.write(f"**Valor Apostado:** R$ {row['valor_apostado']:.2f} | **Odd Comprada:** {row['odd_comprada']:.2f}")
                        st.write(f"🔥 **Score / Confiança:** `{score_val}`")

                    with col_botoes:
                        if st.button("🟢 Green", key=f"green_and_{row['id']}", use_container_width=True):
                            lucro = (row["odd_comprada"] - 1) * row["valor_apostado"]
                            db.registrar_resultado(row["id"], "Green", lucro)
                            st.toast(f"Green registrado com sucesso! (+R$ {lucro:.2f})")
                            st.rerun()

                        if st.button("🔴 Red", key=f"red_and_{row['id']}", use_container_width=True):
                            prejuizo = -row["valor_apostado"]
                            db.registrar_resultado(row["id"], "Red", prejuizo)
                            st.toast(f"Red registrado. (-R$ {row['valor_apostado']:.2f})")
                            st.rerun()

                        if st.button("⚪ Anular / Reembolso", key=f"null_and_{row['id']}", use_container_width=True):
                            db.registrar_resultado(row["id"], "Anulada", 0.0)
                            st.toast("Entrada Anulada!")
                            st.rerun()
                    st.divider()

# ---------------------------------------------------------
# ABA 4: DASHBOARD FINANCEIRO
# ---------------------------------------------------------
elif aba == "4. Dashboard Financeiro":
    st.header("📊 Painel de Desempenho Financeiro")

    df_hist = db.buscar_historico()

    if df_hist.empty:
        st.info("Nenhuma aposta finalizada no histórico para gerar métricas.")
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
            estrategias_disponiveis = ["Todas as Estratégias"] + list(df_hist["script_origem"].unique())
            est_selecionada = st.selectbox("Estratégia:", estrategias_disponiveis)

        df_hist["data_registro"] = df_hist["data_registro"].fillna(hoje.isoformat())
        df_hist["data_dt"] = pd.to_datetime(df_hist["data_registro"], errors="coerce").dt.date
        df_hist["data_dt"] = df_hist["data_dt"].fillna(hoje)

        mask_data = (df_hist["data_dt"] >= d_inicio) & (df_hist["data_dt"] <= d_fim)
        df_filtrado = df_hist[mask_data].copy()

        if est_selecionada != "Todas as Estratégias":
            df_filtrado = df_filtrado[df_filtrado["script_origem"] == est_selecionada].copy()

        st.divider()

        if df_filtrado.empty:
            st.warning("Nenhum resultado registrado para o período ou estratégia selecionada.")
        else:
            metricas_gerais = calcular_metricas(df_filtrado)

            df_dd_calc = df_filtrado.sort_values("data_registro").copy()
            df_dd_calc["Lucro_Acum"] = df_dd_calc["lucro_prejuizo"].cumsum()
            df_dd_calc["Pico"] = df_dd_calc["Lucro_Acum"].cummax()
            df_dd_calc["Drawdown"] = df_dd_calc["Lucro_Acum"] - df_dd_calc["Pico"]
            max_drawdown = df_dd_calc["Drawdown"].min() if not df_dd_calc.empty else 0.0

            c1, c2, c3, c4, c5, c6 = st.columns(6)
            c1.metric("Entradas", metricas_gerais["Entradas"])
            c2.metric("🟢 G / 🔴 R", f"{metricas_gerais['Greens']} / {metricas_gerais['Reds']}")
            c3.metric("Assertividade", metricas_gerais["Assertividade (%)"])
            c4.metric("Investido", f"R$ {metricas_gerais['Investimento (R$)']:.2f}")
            c5.metric(
                "Lucro Líquido", f"R$ {metricas_gerais['Lucro Líquido (R$)']:.2f}",
                delta=f"{metricas_gerais['ROI (%)']:.1f}% ROI",
            )
            c6.metric("Max Drawdown", f"R$ {max_drawdown:.2f}")

            st.divider()

            st.subheader("📈 Evolução Financeira Acumulada")
            df_chart = df_filtrado.groupby("data_registro")["lucro_prejuizo"].sum().reset_index()
            df_chart["Lucro_Acumulado"] = df_chart["lucro_prejuizo"].cumsum()
            st.line_chart(df_chart.set_index("data_registro")["Lucro_Acumulado"])

            st.divider()

            # --- DESEMPENHO POR FAIXA DE ODD ---
            st.subheader("🎯 Desempenho por Faixa de Odd")
            bins = [1.0, 1.5, 1.75, 2.0, 2.5, 50.0]
            labels = ["Até 1.50", "1.51 - 1.75", "1.76 - 2.00", "2.01 - 2.50", "Acima de 2.50"]
            df_filtrado["Faixa_Odd"] = pd.cut(df_filtrado["odd_comprada"], bins=bins, labels=labels, right=True, include_lowest=True)

            linhas_odd = []
            for faixa, group in df_filtrado.groupby("Faixa_Odd", observed=False):
                if group.empty:
                    continue
                m = calcular_metricas(group)
                if m["Entradas"] == 0:
                    continue
                m["Faixa de Odd"] = faixa
                linhas_odd.append(m)

            df_odd_perf = pd.DataFrame(linhas_odd)
            if not df_odd_perf.empty:
                cols_ordem = ["Faixa de Odd", "Entradas", "Greens", "Reds", "Assertividade (%)", "Investimento (R$)", "Lucro Líquido (R$)", "ROI (%)"]
                df_odd_perf = df_odd_perf[cols_ordem]
                st.dataframe(
                    formatar_tabela_financeira(df_odd_perf, col_dinheiro=["Investimento (R$)", "Lucro Líquido (R$)"], col_roi=["ROI (%)"]),
                    use_container_width=True,
                )
            else:
                st.info("Dados insuficientes para análise por faixa de odd.")

            st.divider()

            # --- DESEMPENHO POR DIA DA SEMANA ---
            st.subheader("📅 Desempenho por Dia da Semana")
            dias_map = {0: "Segunda-feira", 1: "Terça-feira", 2: "Quarta-feira", 3: "Quinta-feira", 4: "Sexta-feira", 5: "Sábado", 6: "Domingo"}
            ordem_dias = list(dias_map.values())

            df_filtrado["Dia da Semana"] = pd.to_datetime(df_filtrado["data_registro"]).dt.dayofweek.map(dias_map)

            linhas_semana = []
            for dia, group in df_filtrado.groupby("Dia da Semana"):
                m = calcular_metricas(group)
                m["Dia da Semana"] = dia
                linhas_semana.append(m)

            df_semana_perf = pd.DataFrame(linhas_semana)
            if not df_semana_perf.empty:
                df_semana_perf["ordem"] = df_semana_perf["Dia da Semana"].map(lambda x: ordem_dias.index(x) if x in ordem_dias else 99)
                df_semana_perf = df_semana_perf.sort_values("ordem").drop(columns="ordem")
                cols_ordem = ["Dia da Semana", "Entradas", "Greens", "Reds", "Assertividade (%)", "Investimento (R$)", "Lucro Líquido (R$)", "ROI (%)"]
                df_semana_perf = df_semana_perf[cols_ordem]
                st.dataframe(
                    formatar_tabela_financeira(df_semana_perf, col_dinheiro=["Investimento (R$)", "Lucro Líquido (R$)"], col_roi=["ROI (%)"]),
                    use_container_width=True,
                )
            else:
                st.info("Dados insuficientes para análise por dia da semana.")

            st.divider()

            # --- DESEMPENHO DETALHADO POR DIA ---
            st.subheader("📅 Desempenho Detalhado por Dia")
            linhas_dia = []
            for dt, group in df_filtrado.groupby("data_registro", sort=False):
                m = calcular_metricas(group)
                m["Data"] = dt
                m["Apostas"] = m.pop("Entradas")
                m["Lucro do Dia (R$)"] = m.pop("Lucro Líquido (R$)")
                linhas_dia.append(m)

            df_diario = pd.DataFrame(linhas_dia)
            if not df_diario.empty:
                cols_ordem = ["Data", "Apostas", "Greens", "Reds", "Assertividade (%)", "Investimento (R$)", "Lucro do Dia (R$)", "ROI (%)"]
                df_diario = df_diario[cols_ordem]
                st.dataframe(
                    formatar_tabela_financeira(df_diario, col_dinheiro=["Investimento (R$)", "Lucro do Dia (R$)"], col_roi=["ROI (%)"], col_lucro_colorido=["Lucro do Dia (R$)"]),
                    use_container_width=True,
                )

            st.divider()

            # --- RESULTADO DIÁRIO POR PROJETO / ESTRATÉGIA ---
            st.subheader("📁 Resultado Diário por Projeto / Estratégia")
            grp_proj = df_filtrado.groupby(["data_registro", "script_origem"]).agg(
                lucro=("lucro_prejuizo", "sum"), investido=("valor_apostado", "sum")
            ).reset_index()

            pivot_lucro = grp_proj.pivot_table(
                index="data_registro", columns="script_origem", values="lucro", aggfunc="first", fill_value=0.0
            ).reset_index()
            pivot_lucro.rename(columns={"data_registro": "Data"}, inplace=True)

            if len(pivot_lucro.columns) > 1:
                cols_num_proj = [c for c in pivot_lucro.columns if c != "Data"]
                st.dataframe(
                    formatar_tabela_financeira(pivot_lucro, col_dinheiro=cols_num_proj, col_lucro_colorido=cols_num_proj),
                    use_container_width=True,
                )
            else:
                st.dataframe(pivot_lucro, use_container_width=True)

            st.divider()

            # --- PERFORMANCE COMPARATIVA GLOBAL ---
            st.subheader("📊 Performance Comparativa Global das Estratégias")
            linhas_estr = []
            for script, group in df_filtrado.groupby("script_origem"):
                m = calcular_metricas(group)
                m["Estratégia"] = script
                linhas_estr.append(m)

            df_perf = pd.DataFrame(linhas_estr)
            if not df_perf.empty:
                cols_ordem = ["Estratégia", "Entradas", "Greens", "Reds", "Assertividade (%)", "Investimento (R$)", "Lucro Líquido (R$)", "ROI (%)"]
                df_perf = df_perf[cols_ordem]
                st.dataframe(
                    formatar_tabela_financeira(df_perf, col_dinheiro=["Investimento (R$)", "Lucro Líquido (R$)"], col_roi=["ROI (%)"]),
                    use_container_width=True,
                )

            st.divider()

            with st.expander("🛠️ Corrigir ou Excluir uma Entrada Registrada"):
                opcoes_editar = {
                    f"ID {r['id']} | {r['data_registro']} - {r['jogo']} [{r['script_origem']}] - Status: {r['status']}": r["id"]
                    for _, r in df_filtrado.iterrows()
                }
                sel_label = st.selectbox("Selecione a Aposta para Editar:", list(opcoes_editar.keys()))
                id_selecionado = opcoes_editar[sel_label]
                row_edit = df_filtrado[df_filtrado["id"] == id_selecionado].iloc[0]

                ec1, ec2, ec3, ec4 = st.columns(4)
                novo_status = ec1.selectbox(
                    "Novo Status:", ["Green", "Red", "Anulada"],
                    index=["Green", "Red", "Anulada"].index(row_edit["status"]) if row_edit["status"] in ["Green", "Red", "Anulada"] else 0,
                )
                nova_odd = ec2.number_input("Nova Odd Comprada:", value=float(row_edit["odd_comprada"]), step=0.01)
                novo_valor = ec3.number_input("Novo Valor Apostado (R$):", value=float(row_edit["valor_apostado"]), step=5.0)

                if ec4.button("💾 Salvar Alterações", use_container_width=True):
                  novo_lucro = (nova_odd - 1) * novo_valor if novo_status == "Green" else (-novo_valor if novo_status == "Red" else 0.0)
                    db.editar_entrada(id_selecionado, novo_status, nova_odd, novo_valor, novo_lucro)
                    st.success("✅ Aposta atualizada com sucesso!")
                    st.rerun()

                if ec4.button("🗑️ Deletar Registro", use_container_width=True):
                    db.deletar_entrada(id_selecionado)
                    st.success("🗑️ Aposta removida do histórico!")
                    st.rerun()

            st.subheader("📋 Histórico Detalhado do Período Selecionado")
            cols_exibir = ["id", "data_registro", "hora", "jogo", "script_origem", "recomendacao", "score_confianca", "odd_comprada", "valor_apostado", "lucro_prejuizo", "status"]
            cols_existentes = [c for c in cols_exibir if c in df_filtrado.columns]
            df_historico_view = df_filtrado[cols_existentes].copy()

            st.dataframe(
                df_historico_view.style.format({
                    "odd_comprada": "{:.2f}",
                    "valor_apostado": "R$ {:.2f}",
                    "lucro_prejuizo": "R$ {:.2f}",
                }).map(colorir_lucro, subset=["lucro_prejuizo"]),
                use_container_width=True,
            )

# ---------------------------------------------------------
# ABA 5: PARÂMETROS & CONFIGURAÇÕES
# ---------------------------------------------------------
elif aba == "5. Parâmetros & Configurações":
    st.header("⚙️ Parâmetros de Entrada por Projeto")
    st.write("Ajuste as regras de filtragem, limite de recomendações e stake fixa padrão para cada estratégia.")

    for script in SCRIPTS_DISPONIVEIS:
        cfg = configs_atuais.get(script, CFG_DEFAULT)

        with st.expander(f"🛠️ Parâmetros do Projeto: {script}", expanded=False):
            col_cfg1, col_cfg2 = st.columns(2)

            with col_cfg1:
                nova_stake = st.number_input("Stake Fixa Padrão (R$):", value=float(cfg["stake_padrao"]), step=5.0, key=f"cfg_stake_{script}")
                novo_top_n = st.number_input("Limite de Retornos (Top N jogos):", value=int(cfg["top_n"]), min_value=1, max_value=100, step=1, key=f"cfg_top_{script}")

            with col_cfg2:
                st.write("**Filtros de Ligas Aceitas:**")
                perm_copas = st.checkbox("Incluir Copas e Torneios Eliminatórios", value=bool(cfg["permitir_copas"]), key=f"cfg_copas_{script}")
                perm_sub20 = st.checkbox("Incluir Ligas Sub-20 / Sub-23 / Formação", value=bool(cfg["permitir_sub20"]), key=f"cfg_sub_{script}")
                perm_fem = st.checkbox("Incluir Jogos de Futebol Feminino", value=bool(cfg["permitir_feminino"]), key=f"cfg_fem_{script}")

            if st.button(f"💾 Salvar Parâmetros para {script}", key=f"btn_save_cfg_{script}", use_container_width=True):
                db.salvar_configuracao(script, nova_stake, novo_top_n, perm_copas, perm_sub20, perm_fem)
                st.toast(f"Parâmetros de '{script}' atualizados com sucesso!")
                st.rerun()
