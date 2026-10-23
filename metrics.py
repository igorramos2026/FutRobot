"""
Métricas e formatação do Dashboard Financeiro.

Correção do bug real encontrado: 4 tabelas usavam chaves erradas no
`.style.format()` (ex: 'Investimento (R\\()' em vez de 'Investimento (R$)'),
então a formatação era ignorada silenciosamente pelo pandas.

`formatar_tabela_financeira` monta o dicionário de formatação a partir dos
NOMES DE COLUNA DE VERDADE passados como argumento, então esse tipo de erro
de digitação não pode mais acontecer.
"""


def colorir_lucro(val):
    if isinstance(val, (int, float)):
        if val > 0:
            return "color: #2e7d32; font-weight: bold;"
        elif val < 0:
            return "color: #c62828; font-weight: bold;"
    return ""


def calcular_metricas(df_grupo):
    status_norm = df_grupo["status"].astype(str).str.strip().str.title()
    finalizadas = status_norm.isin(["Green", "Red"])

    total = int(finalizadas.sum())
    greens = int((status_norm == "Green").sum())
    reds = int((status_norm == "Red").sum())
    winrate = (greens / total * 100) if total > 0 else 0.0

    investido = float(df_grupo["valor_apostado"].sum())
    lucro = float(df_grupo["lucro_prejuizo"].sum())
    roi = (lucro / investido * 100) if investido > 0 else 0.0

    return {
        "Entradas": total,
        "Greens": greens,
        "Reds": reds,
        "Assertividade (%)": f"{winrate:.1f}%",
        "Investimento (R$)": investido,
        "Lucro Líquido (R$)": lucro,
        "ROI (%)": roi,
    }


def formatar_tabela_financeira(df, col_dinheiro=None, col_roi=None, col_lucro_colorido=None):
    col_dinheiro = col_dinheiro or []
    col_roi = col_roi or []
    col_lucro_colorido = col_lucro_colorido if col_lucro_colorido is not None else (col_dinheiro + col_roi)

    fmt = {c: "R$ {:.2f}" for c in col_dinheiro}
    fmt.update({c: "{:.1f}%" for c in col_roi})

    styler = df.style.format(fmt)
    if col_lucro_colorido:
        styler = styler.map(colorir_lucro, subset=col_lucro_colorido)
    return styler
