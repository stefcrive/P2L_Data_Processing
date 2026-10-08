from pathlib import Path
import json
import plotly.graph_objects as go
import plotly.io as pio

root = Path(__file__).resolve().parents[2]
pio.defaults.plotlyjs = str(root / "apps/web/node_modules/plotly.js/dist/plotly.min.js")
traces = [
    go.Scatter(x=[0, 1, 2, 3], y=[-.6, -.65, -.61, -.64], name="Outliers detectados · δ¹³C", mode="markers", marker=dict(symbol="square", color="#fde725", size=7)),
    go.Scatter(x=[0, 3], y=[-.63, -.63], name="Média", mode="lines", line=dict(color="#334155", width=1.5)),
    go.Scatter(x=[0, 3], y=[-.75, -.75], name="Valor verdadeiro", mode="lines", line=dict(color="#7c3aed", dash="dash", width=1.5)),
    go.Scatter(x=[0, 3], y=[-.60, -.60], name="Média ±1σ", mode="lines", line=dict(color="#c38835", dash="dot", width=1.5)),
]
categories = [("Fora da faixa de validade", "diamond"), ("Análises com falha", "triangle-down"), ("Outliers estatísticos", "square"), ("Observações sem sinalização", "circle")]
for stage, title in [("before", "Antes da correção residual"), ("after", "Resultados corrigidos")]:
    stage_categories = [categories[0], categories[-1]] if stage == "before" else categories
    for index, (name, symbol) in enumerate(stage_categories):
        traces.append(go.Scatter(x=[None], y=[None], mode="markers", name=f"{title}<br>{name}" if index == 0 else name,
            legendgroup=stage,
            marker=dict(size=7, color="#c4c4c4" if stage == "before" else "#31688e", symbol=f"{symbol}-open" if stage == "before" else symbol)))
for width, columns in [(780, 3), (1100, 4)]:
    figure = go.Figure(traces)
    figure.update_layout(width=width, height=440, margin=dict(l=54, r=18, t=28, b=42, pad=0),
        font=dict(family="Segoe UI", size=11, color="#475569"), paper_bgcolor="white", plot_bgcolor="white",
        legend=dict(orientation="h", x=0, xanchor="left", y=1.01, yanchor="bottom", font=dict(size=9),
            title=dict(text="Cor: δ¹⁸O/¹⁶O Mean", side="top", font=dict(size=9)),
            traceorder="normal", itemsizing="trace", tracegroupgap=0,
            borderwidth=0, entrywidth=.96/columns, entrywidthmode="fraction"),
        xaxis=dict(title="Sequência analítica da sessão", gridcolor="#e8edf1"),
        yaxis=dict(title="δ¹³C / ‰ VPDB", gridcolor="#e8edf1"))
    prefix = Path(__file__).with_name(f"legend-{columns}-columns")
    figure.write_image(str(prefix.with_suffix(".png")))
    figure.write_image(str(prefix.with_suffix(".svg")))
    print(json.dumps(dict(width=width, columns=columns, image=str(prefix.with_suffix(".png")))))
