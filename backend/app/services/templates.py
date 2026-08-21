"""Report rendering.

Presentation only: every value comes from the stored record, and every provider-supplied
string is HTML-escaped before it reaches the page. Kept in its own module because report
markup reads better unwrapped than folded to the project's line limit.
"""

from __future__ import annotations

import html
from typing import Any

from app.core.enums import Assertion

#: Colour per assertion class, so a reader can tell an observed fact from a correlation.
ASSERTION_COLORS = {
    Assertion.OBSERVED: "#3ddc97",
    Assertion.CORRELATED: "#5aa9ff",
    Assertion.INFERRED: "#f5b942",
    Assertion.UNVERIFIED: "#8b93a7",
}


def render_markdown(report: dict[str, Any]) -> str:
    inv = report["investigation"]
    summary = report["summary"]
    lines: list[str] = [
        f"# Investigation report — {inv['name']}",
        "",
        f"> {report['disclaimer']}",
        "",
        f"Generated {report['generated_at']} · status **{inv['status']}**",
        "",
        "## Targets",
        "",
        *[f"- `{t['type']}` **{t['value']}**" for t in report["targets"]],
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---|",
        *[
            f"| {key.replace('_', ' ').title()} | {value} |"
            for key, value in summary.items()
            if not isinstance(value, (dict, list))
        ],
        "",
        "## Correlation analysis",
        "",
    ]
    if report["correlation_analysis"]:
        lines += ["| A | B | Confidence | Band | Reasons |", "|---|---|---|---|---|"]
        lines += [
            f"| {c['a']} | {c['b']} | {c['score']:.0%} | {c['band_label']} | {'; '.join(c['reasons'])} |"
            for c in report["correlation_analysis"]
        ]
    else:
        lines.append("_No identity candidates above the confidence threshold._")

    lines += ["", "## Relationships", "", "| Source | Type | Target | Confidence | Class | Why |", "|---|---|---|---|---|---|"]
    lines += [
        f"| {r['source']} | `{r['type']}` | {r['target']} | {r['confidence']:.0%} | {r['assertion_label']} | {r['why']} |"
        for r in report["relationships"][:200]
    ]

    lines += ["", "## Evidence", ""]
    for item in report["evidence"][:200]:
        lines += [
            f"### {item['entity'] or item['kind']} — {item['provider']}",
            "",
            f"- **Class**: {item['assertion_label']}",
            f"- **Source**: {item['source']}",
            f"- **URL**: {item['url'] or '—'}",
            f"- **Observed at**: {item['observed_at']}",
            f"- **Confidence**: {item['confidence']:.0%}",
            f"- **Evidence hash**: `{(item['sha256'] or ['—'])[0]}`",
            "",
        ]

    if report["unverified_claims"]:
        lines += ["## Unverified claims", ""]
        lines += [
            f"- {c['entity'] or c['kind']} via {c['provider']} — {c['url'] or 'no URL'}"
            for c in report["unverified_claims"]
        ]
    return "\n".join(lines) + "\n"


def render_html(report: dict[str, Any]) -> str:
    inv = report["investigation"]
    esc = html.escape

    def badge(assertion: str, label: str) -> str:
        color = ASSERTION_COLORS.get(Assertion(assertion), "#8b93a7")
        return f'<span class="badge" style="--c:{color}">{esc(label)}</span>'

    rows = "".join(
        f"<tr><td>{esc(r['source'])}</td><td><code>{esc(r['type'])}</code></td>"
        f"<td>{esc(r['target'])}</td><td>{r['confidence']:.0%}</td>"
        f"<td>{badge(r['assertion'], r['assertion_label'])}</td><td>{esc(r['why'])}</td></tr>"
        for r in report["relationships"][:300]
    )
    def link(url: str | None) -> str:
        if not url:
            return "&mdash;"
        safe = esc(url, quote=True)
        return f'<a href="{safe}" rel="noreferrer noopener nofollow" target="_blank">link</a>'

    evidence = "".join(
        f"<tr><td>{esc(e['entity'] or e['kind'])}</td><td>{esc(e['provider'])}</td>"
        f"<td>{link(e['url'])}</td>"
        f"<td>{esc(e['observed_at'])}</td><td>{e['confidence']:.0%}</td>"
        f"<td>{badge(e['assertion'], e['assertion_label'])}</td></tr>"
        for e in report["evidence"][:300]
    )
    matches = "".join(
        f"<tr><td>{esc(c['a'])}</td><td>{esc(c['b'])}</td><td>{c['score']:.0%}</td>"
        f"<td>{esc(c['band_label'])}</td><td>{esc('; '.join(c['reasons']))}</td></tr>"
        for c in report["correlation_analysis"]
    )
    summary_cards = "".join(
        f'<div class="card"><span class="n">{v}</span><span class="k">{esc(k.replace("_", " "))}</span></div>'
        for k, v in report["summary"].items()
        if not isinstance(v, (dict, list))
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GraphIntel report — {esc(inv['name'])}</title>
<style>
  :root {{ color-scheme: dark; --bg:#0b0e14; --panel:#141922; --line:#232a37; --fg:#e6e9ef; --muted:#8b93a7; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; padding:2rem; background:var(--bg); color:var(--fg);
         font:14px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif; }}
  h1 {{ font-size:1.5rem; margin:0 0 .25rem; }} h2 {{ font-size:1.05rem; margin:2rem 0 .6rem; }}
  .muted {{ color:var(--muted); }}
  .disclaimer {{ border-left:3px solid #f5b942; background:#1b1710; padding:.75rem 1rem; margin:1rem 0; border-radius:4px; }}
  .cards {{ display:flex; flex-wrap:wrap; gap:.6rem; margin:1rem 0; }}
  .card {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:.7rem 1rem; min-width:120px; }}
  .card .n {{ display:block; font-size:1.4rem; font-weight:600; }}
  .card .k {{ display:block; font-size:.72rem; text-transform:uppercase; letter-spacing:.05em; color:var(--muted); }}
  table {{ width:100%; border-collapse:collapse; background:var(--panel); border:1px solid var(--line);
           border-radius:8px; overflow:hidden; font-size:.86rem; }}
  th,td {{ text-align:left; padding:.5rem .7rem; border-bottom:1px solid var(--line); vertical-align:top; }}
  th {{ background:#111722; font-size:.72rem; text-transform:uppercase; letter-spacing:.05em; color:var(--muted); }}
  tr:last-child td {{ border-bottom:none; }}
  code {{ background:#0f141d; padding:.1rem .35rem; border-radius:3px; font-size:.82em; }}
  a {{ color:#5aa9ff; }}
  .badge {{ display:inline-block; padding:.1rem .5rem; border-radius:99px; font-size:.72rem;
            border:1px solid var(--c); color:var(--c); white-space:nowrap; }}
  .legend {{ display:flex; gap:.8rem; flex-wrap:wrap; margin:.5rem 0 0; }}
</style></head><body>
<h1>{esc(inv['name'])}</h1>
<p class="muted">Investigation {esc(inv['id'])} · status {esc(inv['status'])} · generated {esc(report['generated_at'])}</p>
<div class="disclaimer">{esc(report['disclaimer'])}</div>
<div class="legend">
  {badge('observed', 'Observed fact')}{badge('correlated', 'Correlation')}
  {badge('inferred', 'Inference')}{badge('unverified', 'Unverified claim')}
</div>
<h2>Summary</h2><div class="cards">{summary_cards}</div>
<h2>Targets</h2>
<table><tr><th>Type</th><th>Value</th></tr>
{"".join(f"<tr><td><code>{esc(t['type'])}</code></td><td>{esc(t['value'])}</td></tr>" for t in report['targets'])}
</table>
<h2>Correlation analysis</h2>
{f'<table><tr><th>A</th><th>B</th><th>Confidence</th><th>Band</th><th>Reasons</th></tr>{matches}</table>'
 if matches else '<p class="muted">No identity candidates above the confidence threshold.</p>'}
<h2>Relationships</h2>
<table><tr><th>Source</th><th>Type</th><th>Target</th><th>Confidence</th><th>Class</th><th>Why</th></tr>{rows}</table>
<h2>Evidence</h2>
<table><tr><th>Entity</th><th>Provider</th><th>Source URL</th><th>Observed at</th><th>Confidence</th><th>Class</th></tr>{evidence}</table>
</body></html>
"""


