"""
Dashboard visual system
==============================================================================
One place for every colour and chart default, so the whole control tower reads
as a single designed system rather than a pile of default Plotly charts.

The categorical order below is validated, not chosen by eye: the six slots pass
the lightness band, chroma floor, colour-vision-deficiency separation (worst
adjacent pair dE 9.1) and normal-vision separation (worst adjacent dE 19.6) against
the #fcfcfb chart surface. Three of the slots fall below 3:1 contrast on that
surface, which obliges the "relief rule" - every chart in this dashboard is
accompanied by its underlying data table, which satisfies it.

Rules held to throughout:
  * categorical hues are assigned in fixed order and never cycled
  * no chart has two y-axes
  * sequential encoding is one hue, light to dark; never a rainbow
  * status colours are reserved and always ship with a text label, never alone
"""

# ---------------------------------------------------------------------------
# Categorical - assigned in fixed slot order, never cycled
# ---------------------------------------------------------------------------
CATEGORICAL = [
    "#2a78d6",   # 1 blue
    "#eb6834",   # 2 orange
    "#1baf7a",   # 3 aqua
    "#eda100",   # 4 yellow
    "#e87ba4",   # 5 magenta
    "#008300",   # 6 green
]

# ---------------------------------------------------------------------------
# Sequential - one hue, light to dark (utilisation, load intensity)
# ---------------------------------------------------------------------------
SEQUENTIAL_BLUE = [
    [0.00, "#cde2fb"], [0.17, "#9ec5f4"], [0.33, "#6da7ec"],
    [0.50, "#3987e5"], [0.67, "#256abf"], [0.83, "#184f95"], [1.00, "#0d366b"],
]

# Diverging - two poles with a neutral grey midpoint (variance vs plan)
DIVERGING = [
    [0.0, "#0d366b"], [0.25, "#3987e5"], [0.5, "#f0efec"],
    [0.75, "#e34948"], [1.0, "#8f1f1f"],
]

# ---------------------------------------------------------------------------
# Status - reserved, never reused as a series colour, always with a label
# ---------------------------------------------------------------------------
STATUS = {
    "good":     "#0ca30c",
    "warning":  "#fab219",
    "serious":  "#ec835a",
    "critical": "#d03b3b",
}

SEVERITY_COLOUR = {
    "Critical": STATUS["critical"],
    "High":     STATUS["serious"],
    "Medium":   STATUS["warning"],
    "Low":      "#898781",
    "Info":     "#898781",
}

SEVERITY_ICON = {
    "Critical": "🔴", "High": "🟠", "Medium": "🟡", "Low": "⚪", "Info": "⚪",
}

# ---------------------------------------------------------------------------
# Chrome & ink
# ---------------------------------------------------------------------------
SURFACE   = "#fcfcfb"
PLANE     = "#f9f9f7"
INK       = "#0b0b0b"
INK_2     = "#52514e"
MUTED     = "#898781"
GRID      = "#e1e0d9"
AXIS      = "#c3c2b7"

FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'


def layout(title=None, height=340, showlegend=True, **kwargs):
    """Shared Plotly layout - recessive grid, hairline axes, quiet chrome."""
    base = dict(
        template="plotly_white",
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        font=dict(family=FONT, size=12, color=INK_2),
        height=height,
        margin=dict(l=8, r=8, t=44 if title else 16, b=8),
        showlegend=showlegend,
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0,
                    font=dict(size=11, color=INK_2), bgcolor="rgba(0,0,0,0)"),
        hoverlabel=dict(font=dict(family=FONT, size=12), bgcolor="#ffffff",
                        bordercolor=AXIS),
        xaxis=dict(showgrid=False, zeroline=False, linecolor=AXIS, linewidth=1,
                   tickfont=dict(color=MUTED, size=11), title_font=dict(size=11, color=MUTED)),
        yaxis=dict(showgrid=True, gridcolor=GRID, gridwidth=1, zeroline=False,
                   linecolor="rgba(0,0,0,0)",
                   tickfont=dict(color=MUTED, size=11), title_font=dict(size=11, color=MUTED)),
    )
    # Plotly renders a literal "undefined" if a title key is present but empty,
    # so the key is only added when there is actually a title to show.
    if title:
        base["title"] = dict(text=title, font=dict(size=14, color=INK),
                             x=0, xanchor="left", pad=dict(b=10))
    base.update(kwargs)
    return base


def colour_for(index):
    """Fixed-order slot assignment. Beyond the palette, fold into a neutral."""
    return CATEGORICAL[index] if index < len(CATEGORICAL) else MUTED


def series_colours(names):
    """Stable colour per named entity - identity never depends on rank or filter."""
    return {n: colour_for(i) for i, n in enumerate(sorted(names))}


# ---------------------------------------------------------------------------
CSS = f"""
<style>
  .block-container {{ padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1500px; }}
  h1, h2, h3 {{ color: {INK}; letter-spacing: -0.01em; }}
  .ct-sub {{ color: {MUTED}; font-size: 0.86rem; margin-top: -0.5rem;
             margin-bottom: 1.1rem; }}
  .ct-block {{ display:inline-block; background:{PLANE}; border:1px solid {GRID};
               color:{INK_2}; font-size:0.72rem; padding:2px 9px; border-radius:99px;
               margin-bottom:0.6rem; letter-spacing:0.02em; }}
  .ct-steps {{ display:flex; flex-wrap:wrap; gap:6px; margin:-0.4rem 0 1.1rem 0; }}
  .ct-step {{ background:{SURFACE}; border:1px solid {GRID}; color:{INK_2};
              font-size:0.73rem; padding:3px 10px; border-radius:5px;
              white-space:nowrap; }}
  .ct-card {{ background:{SURFACE}; border:1px solid {GRID}; border-radius:10px;
              padding:14px 16px; height:100%; }}
  .ct-card .lab {{ color:{MUTED}; font-size:0.72rem; text-transform:uppercase;
                   letter-spacing:0.05em; }}
  .ct-card .val {{ color:{INK}; font-size:1.55rem; font-weight:600; line-height:1.25;
                   margin-top:2px; }}
  .ct-card .sub {{ color:{MUTED}; font-size:0.75rem; margin-top:1px; }}
  .ct-note {{ background:{PLANE}; border-left:3px solid {CATEGORICAL[0]};
              padding:11px 14px; border-radius:0 8px 8px 0; color:{INK_2};
              font-size:0.86rem; margin:0.6rem 0 1rem 0; }}
  .ct-warn {{ background:#fdf6e7; border-left:3px solid {STATUS['warning']};
              padding:11px 14px; border-radius:0 8px 8px 0; color:{INK_2};
              font-size:0.86rem; margin:0.6rem 0 1rem 0; }}
  .ct-crit {{ background:#fbeceb; border-left:3px solid {STATUS['critical']};
              padding:11px 14px; border-radius:0 8px 8px 0; color:{INK_2};
              font-size:0.86rem; margin:0.6rem 0 1rem 0; }}
  .ct-good {{ background:#eaf7ea; border-left:3px solid {STATUS['good']};
              padding:11px 14px; border-radius:0 8px 8px 0; color:{INK_2};
              font-size:0.86rem; margin:0.6rem 0 1rem 0; }}
  section[data-testid="stSidebar"] {{ background:{PLANE}; }}
  div[data-testid="stMetricValue"] {{ font-size:1.4rem; }}
  .stDataFrame {{ border:1px solid {GRID}; border-radius:8px; }}
</style>
"""


def card(label, value, sub=""):
    return (f'<div class="ct-card"><div class="lab">{label}</div>'
            f'<div class="val">{value}</div>'
            f'<div class="sub">{sub}</div></div>')
