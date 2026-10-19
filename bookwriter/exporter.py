"""Resa ed export: Markdown, DOCX, PDF, EPUB e wiki/archivio (HTML + JSON).

``write_output`` è il punto unico usato da tutte le modalità per scrivere un libro, e da qui in
avanti passa dal compositore di ``bookwriter.forge``: lo stesso che impagina i libri delle sette
fasi. Non è un dettaglio di implementazione — vuol dire che una bozza esportata dalla schermata
di composizione, una riscrittura e una traduzione escono con la stessa cura tipografica di un
libro preparato per la stampa: misura reale invece dell'A4, margini asimmetrici che tengono conto
della piega, sillabazione, capitoli che aprono in recto, indice, titoli correnti, e la
bibliografia dei capitoli raccolta in una sola in fondo.

``export_wiki`` produce l'archivio navigabile dei nodi e del grafo (uso "archivio" invece di
"libro"). Nessuna dipendenza di sistema: tutto in Python.
"""

from __future__ import annotations

import html as _html
import json
import re
from collections import defaultdict
from pathlib import Path

from sqlmodel import Session, select

from bookwriter.db.project_schema import (
    Edge,
    Node,
    Outline,
    OutlineChapter,
    OutlineItem,
    OutlineSection,
    make_engine,
)
from bookwriter.forge.compose import SUPPORTED_FORMATS, Composed, compose_markdown


def latest_outline_id(db_path: Path) -> int | None:
    with Session(make_engine(db_path)) as ses:
        outline = ses.exec(select(Outline).order_by(Outline.id.desc())).first()
        return outline.id if outline else None


def outline_to_markdown(db_path: Path, outline_id: int) -> str:
    engine = make_engine(db_path)
    with Session(engine) as ses:
        outline = ses.get(Outline, outline_id)
        if outline is None:
            raise ValueError(f"Outline #{outline_id} inesistente.")
        chapters = list(
            ses.exec(
                select(OutlineChapter)
                .where(OutlineChapter.outline_id == outline_id)
                .order_by(OutlineChapter.position)
            )
        )
        lines: list[str] = [f"# {outline.name}", ""]

        def _emit_items(items) -> None:
            for item in items:
                node = ses.get(Node, item.node_id)
                if node and node.raw_text.strip():
                    lines.extend([node.raw_text.strip(), ""])

        for chapter in chapters:
            lines += [f"## {chapter.title}", ""]
            sections = ses.exec(
                select(OutlineSection).where(OutlineSection.chapter_id == chapter.id).order_by(OutlineSection.position)
            ).all()
            if sections:
                for section in sections:
                    lines += [f"### {section.title}", ""]
                    _emit_items(ses.exec(
                        select(OutlineItem).where(OutlineItem.section_id == section.id).order_by(OutlineItem.position)
                    ))
                # Voci senza sezione, se presenti.
                _emit_items(ses.exec(
                    select(OutlineItem)
                    .where(OutlineItem.chapter_id == chapter.id, OutlineItem.section_id.is_(None))
                    .order_by(OutlineItem.position)
                ))
            else:
                _emit_items(ses.exec(
                    select(OutlineItem).where(OutlineItem.chapter_id == chapter.id).order_by(OutlineItem.position)
                ))
    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------- #
#  scrittura output (md / docx / pdf / epub)
# --------------------------------------------------------------------------- #
def compose_output(
    markdown: str,
    out_path: Path,
    fmt: str,
    *,
    title: str = "",
    author: str = "",
    language: str = "it",
    production=None,
    cover: Path | None = None,
) -> Composed:
    """Compone il libro e racconta cos'è uscito (pagine, capitoli, caratteri usati).

    ``production`` è una ``ProductionSettings``: misura del taglio, caratteri, sillabazione,
    marchio. Senza, valgono i valori di serie del compositore — A5, il primo carattere con vere
    corsive che trova sulla macchina, sillabazione dalla lingua.
    """
    if fmt not in SUPPORTED_FORMATS:
        raise ValueError(f"Formato non supportato: {fmt} (usa {'|'.join(SUPPORTED_FORMATS)})")
    return compose_markdown(
        markdown, Path(out_path), fmt,
        title=title,
        author=author or (getattr(production, "author", "") or ""),
        language=language or "it",
        trim=getattr(production, "trim", "a5") or "a5",
        cover=cover,
        overrides=production.overrides() if production is not None else None,
    )


def write_output(markdown: str, out_path: Path, fmt: str, **kwargs) -> Path:
    """Scrive il Markdown nel formato richiesto: ``md`` | ``docx`` | ``pdf`` | ``epub``."""
    return compose_output(markdown, Path(out_path), fmt, **kwargs).path


def markdown_to_pdf(markdown: str, out_path: Path, **kwargs) -> Path:
    """PDF impaginato dal Markdown. Scorciatoia storica per ``write_output(..., "pdf")``."""
    return write_output(markdown, Path(out_path), "pdf", **kwargs)


def export_outline(db_path: Path, outline_id: int, out_path: Path, fmt: str = "md", **kwargs) -> Path:
    """Esporta l'outline (resa verbatim) in md/docx/pdf/epub."""
    return write_output(outline_to_markdown(db_path, outline_id), out_path, fmt, **kwargs)


# --------------------------------------------------------------------------- #
#  wiki / archivio (HTML navigabile + JSON del grafo)
# --------------------------------------------------------------------------- #
def _stable_hue(text: str) -> int:
    return sum(ord(c) for c in text) % 360


def _graph_svg(nodes, edges, *, max_nodes: int = 150) -> tuple[str, int]:
    """Grafo SVG inline (layout networkx). Ritorna (svg, nodi_disegnati)."""
    import networkx as nx

    if not nodes:
        return "", 0
    graph = nx.Graph()
    graph.add_nodes_from(n.id for n in nodes)
    for e in edges:
        if e.src_node in graph and e.dst_node in graph:
            graph.add_edge(e.src_node, e.dst_node)
    if graph.number_of_nodes() > max_nodes:  # tieni i più connessi
        keep = {nid for nid, _ in sorted(graph.degree, key=lambda x: -x[1])[:max_nodes]}
        graph = graph.subgraph(keep).copy()
    if graph.number_of_nodes() == 0:
        return "", 0

    pos = nx.spring_layout(graph, seed=42)
    xs = [p[0] for p in pos.values()]
    ys = [p[1] for p in pos.values()]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    w, h = 960, 560

    def sx(x: float) -> float:
        return 20 + (x - minx) / (maxx - minx + 1e-9) * (w - 40)

    def sy(y: float) -> float:
        return 20 + (y - miny) / (maxy - miny + 1e-9) * (h - 40)

    nmap = {n.id: n for n in nodes}
    parts = [f'<svg viewBox="0 0 {w} {h}" width="100%" style="max-width:960px;border:1px solid #ddd;border-radius:8px;background:#fafafa">']
    for a, b in graph.edges():
        parts.append(f'<line x1="{sx(pos[a][0]):.1f}" y1="{sy(pos[a][1]):.1f}" x2="{sx(pos[b][0]):.1f}" y2="{sy(pos[b][1]):.1f}" stroke="#ccd" stroke-width="1"/>')
    for nid in graph.nodes():
        node = nmap.get(nid)
        theme = (node.themes or ["?"])[0] if node and node.themes else "?"
        label = _html.escape((node.summary or "")[:60]) if node else ""
        parts.append(
            f'<a href="#n{nid}"><circle cx="{sx(pos[nid][0]):.1f}" cy="{sy(pos[nid][1]):.1f}" r="6" '
            f'fill="hsl({_stable_hue(theme)},60%,55%)"><title>{label}</title></circle></a>'
        )
    parts.append("</svg>")
    return "".join(parts), graph.number_of_nodes()


def export_wiki(db_path: Path, out_dir: Path) -> dict[str, Path]:
    """Esporta i nodi e il grafo come archivio: ``graph.json`` + ``wiki.html`` navigabile."""
    out_dir.mkdir(parents=True, exist_ok=True)
    engine = make_engine(db_path)
    with Session(engine) as ses:
        nodes = list(ses.exec(select(Node)))
        edges = list(ses.exec(select(Edge)))

    data = {
        "nodes": [
            {
                "id": n.id, "summary": n.summary, "text": n.raw_text, "type": n.type,
                "themes": n.themes, "entities": n.entities, "date": n.event_date,
            }
            for n in nodes
        ],
        "edges": [{"src": e.src_node, "dst": e.dst_node, "type": e.type, "weight": e.weight} for e in edges],
    }
    json_path = out_dir / "graph.json"
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    nmap = {n.id: n for n in nodes}
    related: dict[int, list[int]] = defaultdict(list)
    for e in edges:
        related[e.src_node].append(e.dst_node)
        related[e.dst_node].append(e.src_node)

    cards = []
    for n in nodes:
        themes = "".join(f'<span class="t">{_html.escape(t)}</span>' for t in (n.themes or []))
        links, seen = [], set()
        for oid in related.get(n.id, []):
            if oid in seen or oid not in nmap:
                continue
            seen.add(oid)
            other = nmap[oid]
            links.append(f'<a href="#n{oid}">{_html.escape((other.summary or other.raw_text)[:50])}</a>')
        rel = " · ".join(links) or "—"
        date = f' <span class="d">{_html.escape(n.event_date)}</span>' if n.event_date else ""
        search = _html.escape((f"{n.summary} {n.raw_text} " + " ".join(n.themes or [])).lower())
        cards.append(
            f'<section class="node" id="n{n.id}" data-text="{search}">'
            f'<h3>{_html.escape(n.summary or "(senza sintesi)")}{date}</h3>'
            f'<div class="themes">{themes}</div>'
            f"<p>{_html.escape(n.raw_text)}</p>"
            f'<div class="rel">Collegati: {rel}</div></section>'
        )

    _svg, _drawn = _graph_svg(nodes, edges)
    html_doc = (
        '<!doctype html><html lang="it"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Archivio</title><style>"
        "body{font-family:system-ui,Arial,sans-serif;max-width:900px;margin:0 auto;padding:1rem;color:#222}"
        "h1{border-bottom:2px solid #444}.node{border:1px solid #ddd;border-radius:8px;padding:.8rem 1rem;margin:.8rem 0}"
        ".themes .t{background:#eef;border-radius:10px;padding:.1rem .5rem;margin-right:.3rem;font-size:.8rem}"
        ".rel{font-size:.85rem;color:#555;margin-top:.4rem}.d{color:#888;font-weight:normal;font-size:.8rem}"
        "#q{width:100%;padding:.6rem;font-size:1rem;margin:.5rem 0;box-sizing:border-box}a{color:#25c}"
        "</style></head><body>"
        f"<h1>Archivio ({len(nodes)} nodi · {len(edges)} collegamenti)</h1>"
        + (f'<h2>Grafo</h2>{_svg}<p style="font-size:.85rem;color:#777">Grafo dei {_drawn} nodi più connessi (clic per saltare alla scheda).</p>' if _svg else "")
        + '<input id="q" placeholder="Cerca nell\'archivio…" oninput="f()">'
        + "".join(cards)
        + "<script>function f(){var q=document.getElementById('q').value.toLowerCase();"
        "document.querySelectorAll('.node').forEach(function(n){"
        "n.style.display=n.dataset.text.indexOf(q)>=0?'':'none';});}</script>"
        "</body></html>"
    )
    html_path = out_dir / "wiki.html"
    html_path.write_text(html_doc, encoding="utf-8")
    return {"html": html_path, "json": json_path}


# --------------------------------------------------------------------------- #
#  nomi dei file esportati
# --------------------------------------------------------------------------- #
_LANG_TAIL = re.compile(r"[ _-]([A-Za-z]{2,3})$")


def _slug_part(text: str) -> str:
    """Pezzo di nome file leggibile: niente spazi né caratteri vietati, accenti conservati."""
    cleaned = re.sub(r"[\/:*?\"<>|]+", "", (text or "").strip())
    return re.sub(r"\s+", "-", cleaned).strip("-_")


def export_filename(
    base: str,
    *,
    style: str = "",
    language: str = "",
    words: int | None = None,
    qualifier: str = "",
    ext: str = "docx",
) -> str:
    """Nome che descrive il contenuto: ``base_stile_LINGUA_lunghezza.ext``.

    Ogni variante prodotta (stile diverso, lingua diversa, lunghezza diversa) ottiene così un
    nome diverso e non sovrascrive le altre. Se ``base`` finisce già con una sigla di lingua
    (``appunti_prosa_IT``) quella viene sostituita, non accodata.
    """
    stem = _slug_part(base) or "libro"
    if language and _LANG_TAIL.search(stem):
        stem = _LANG_TAIL.sub("", stem)
    parts = [stem]
    for extra in (qualifier, style):
        piece = _slug_part(extra)
        if piece:
            parts.append(piece)
    if language:
        parts.append(language.upper())
    if words:
        parts.append(f"{round(words / 1000)}k" if words >= 1000 else str(words))
    return "_".join(parts) + f".{ext.lstrip('.')}"
