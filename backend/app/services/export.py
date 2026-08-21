"""Export in JSON / CSV / Markdown / HTML / GraphML (spec 22)."""

from __future__ import annotations

import csv
import io
import json
import uuid
import xml.etree.ElementTree as ET
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import ExportFormat, ExportScope
from app.services.report import ASSERTION_LABELS, DISCLAIMER, ReportBuilder
from app.services.templates import render_html, render_markdown

MEDIA_TYPES: dict[ExportFormat, str] = {
    ExportFormat.JSON: "application/json",
    ExportFormat.CSV: "text/csv",
    ExportFormat.HTML: "text/html; charset=utf-8",
    ExportFormat.MARKDOWN: "text/markdown; charset=utf-8",
    ExportFormat.GRAPHML: "application/xml",
}

class ExportService:
    def __init__(self, session: AsyncSession, investigation_id: uuid.UUID) -> None:
        self.session = session
        self.investigation_id = investigation_id

    async def export(
        self,
        fmt: ExportFormat,
        scope: ExportScope = ExportScope.INVESTIGATION,
        *,
        entity_ids: list[uuid.UUID] | None = None,
        min_confidence: float = 0.0,
    ) -> tuple[bytes, str, str]:
        """Return ``(content, media_type, filename)``."""
        report = await ReportBuilder(self.session, self.investigation_id).build(min_confidence)
        if entity_ids:
            report = _restrict(report, {str(i) for i in entity_ids})

        slug = _slug(report["investigation"]["name"])
        if fmt is ExportFormat.JSON:
            body = json.dumps(_scoped(report, scope), indent=2, default=str).encode()
        elif fmt is ExportFormat.CSV:
            body = _csv(report, scope).encode()
        elif fmt is ExportFormat.MARKDOWN:
            body = render_markdown(report).encode()
        elif fmt is ExportFormat.HTML:
            body = render_html(report).encode()
        elif fmt is ExportFormat.GRAPHML:
            body = _graphml(report)
        else:  # pragma: no cover - the enum is closed
            raise ValueError(f"unsupported export format: {fmt}")
        return body, MEDIA_TYPES[fmt], f"{slug}-{scope}.{_ext(fmt)}"


def _ext(fmt: ExportFormat) -> str:
    return {"markdown": "md", "graphml": "graphml"}.get(str(fmt), str(fmt))


def _slug(name: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in name.lower())
    return "-".join(filter(None, safe.split("-")))[:60] or "investigation"


def _scoped(report: dict[str, Any], scope: ExportScope) -> dict[str, Any]:
    if scope is ExportScope.ENTITIES:
        return {"investigation": report["investigation"], "entities": report["entities"]}
    if scope is ExportScope.RELATIONSHIPS:
        return {
            "investigation": report["investigation"],
            "relationships": report["relationships"],
        }
    if scope is ExportScope.EVIDENCE:
        return {
            "investigation": report["investigation"],
            "evidence": report["evidence"],
            "disclaimer": report["disclaimer"],
        }
    return report


def _restrict(report: dict[str, Any], keep: set[str]) -> dict[str, Any]:
    entities = [e for e in report["entities"] if e["id"] in keep]
    labels = {e["label"] for e in entities}
    return {
        **report,
        "entities": entities,
        "relationships": [
            r
            for r in report["relationships"]
            if r["source"] in labels or r["target"] in labels
        ],
        "evidence": [e for e in report["evidence"] if e["entity"] in labels],
    }


def _csv(report: dict[str, Any], scope: ExportScope) -> str:
    buffer = io.StringIO()
    if scope is ExportScope.RELATIONSHIPS:
        writer = csv.DictWriter(
            buffer,
            fieldnames=[
                "source", "type", "target", "confidence",
                "assertion", "provider", "why", "evidence_url",
            ],
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(report["relationships"])
    elif scope is ExportScope.EVIDENCE:
        writer = csv.DictWriter(
            buffer,
            fieldnames=[
                "observation_id", "entity", "provider", "kind", "url",
                "observed_at", "confidence", "assertion_label", "excerpt",
            ],
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(report["evidence"])
    else:
        writer = csv.DictWriter(
            buffer,
            fieldnames=[
                "id", "type", "label", "confidence", "sources", "first_seen", "last_seen",
            ],
            extrasaction="ignore",
        )
        writer.writeheader()
        for row in report["entities"]:
            writer.writerow({**row, "sources": ";".join(row["sources"])})
    return buffer.getvalue()


def _graphml(report: dict[str, Any]) -> bytes:
    ns = "http://graphml.graphdrawing.org/xmlns"
    ET.register_namespace("", ns)
    root = ET.Element(f"{{{ns}}}graphml")
    for key_id, name, target, key_type in (
        ("d_label", "label", "node", "string"),
        ("d_type", "type", "node", "string"),
        ("d_conf", "confidence", "node", "double"),
        ("e_type", "type", "edge", "string"),
        ("e_conf", "confidence", "edge", "double"),
        ("e_assert", "assertion", "edge", "string"),
        ("e_why", "why", "edge", "string"),
    ):
        ET.SubElement(
            root,
            f"{{{ns}}}key",
            {"id": key_id, "attr.name": name, "for": target, "attr.type": key_type},
        )
    graph = ET.SubElement(root, f"{{{ns}}}graph", {"id": "G", "edgedefault": "directed"})
    by_label: dict[str, str] = {}
    for entity in report["entities"]:
        by_label[entity["label"]] = entity["id"]
        node = ET.SubElement(graph, f"{{{ns}}}node", {"id": entity["id"]})
        for key_id, value in (
            ("d_label", entity["label"]),
            ("d_type", entity["type"]),
            ("d_conf", f"{entity['confidence']}"),
        ):
            ET.SubElement(node, f"{{{ns}}}data", {"key": key_id}).text = str(value)
    for index, rel in enumerate(report["relationships"]):
        source_id = by_label.get(rel["source"])
        target_id = by_label.get(rel["target"])
        if not source_id or not target_id:
            continue
        edge = ET.SubElement(
            graph,
            f"{{{ns}}}edge",
            {"id": f"e{index}", "source": source_id, "target": target_id},
        )
        for key_id, value in (
            ("e_type", rel["type"]),
            ("e_conf", f"{rel['confidence']}"),
            ("e_assert", rel["assertion"]),
            ("e_why", rel["why"]),
        ):
            ET.SubElement(edge, f"{{{ns}}}data", {"key": key_id}).text = str(value)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


__all__ = ["ASSERTION_LABELS", "DISCLAIMER", "MEDIA_TYPES", "ExportService"]
