"""The full pipeline end to end: demo mode, graph, timeline, search, export, analytics."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET

from app.core.enums import Assertion, ExportFormat, ExportScope
from app.demo.seed import DEMO_DOMAIN, DEMO_EMAIL, DEMO_USERNAME, seed_demo_investigation
from app.graph.analytics import GraphAnalytics
from app.graph.projection import GraphFilters, GraphProjection
from app.services.export import ExportService
from app.services.investigation import InvestigationService
from app.services.report import ReportBuilder
from app.services.search import SearchService
from app.services.timeline import TimelineService


async def test_demo_investigation_builds_a_connected_graph(session, user) -> None:
    investigation = await seed_demo_investigation(session, user.id)
    await session.commit()

    graph = await GraphProjection(session, investigation.id).build()
    stats = graph["stats"]
    assert stats["entities"] >= 15
    assert stats["relationships"] >= 15
    assert stats["sources"] >= 5

    types = {node["type"] for node in graph["nodes"]}
    assert {"username", "social_account", "website", "domain", "email", "repository", "ip"} <= types

    # Every edge must carry an explanation and a provider (spec 39).
    for edge in graph["edges"]:
        assert edge["provider"], edge
        assert edge["why"], f"edge {edge['type']} has no explanation"
        assert edge["assertion"] in {a.value for a in Assertion}


async def test_demo_dedupes_the_same_account_seen_by_two_providers(session, user) -> None:
    investigation = await seed_demo_investigation(session, user.id)
    await session.commit()

    from sqlalchemy import select

    from app.models import Entity

    entities = list(
        (await session.execute(
            select(Entity).where(Entity.investigation_id == investigation.id)
        )).scalars()
    )
    keys = [e.canonical_key for e in entities]
    assert len(keys) == len(set(keys)), "canonical keys must be unique per investigation"

    github = [e for e in entities if e.canonical_key == f"github:user:{DEMO_USERNAME}"]
    assert len(github) == 1
    # The website provider also links this account, so both providers land on one node.
    assert len(github[0].sources) >= 1


async def test_demo_produces_explained_identity_candidates(session, user) -> None:
    investigation = await seed_demo_investigation(session, user.id)
    await session.commit()

    from sqlalchemy import select

    from app.models import IdentityCandidate

    candidates = list(
        (await session.execute(
            select(IdentityCandidate).where(
                IdentityCandidate.investigation_id == investigation.id
            )
        )).scalars()
    )
    assert candidates, "the demo data should correlate the GitHub and Reddit accounts"
    for candidate in candidates:
        assert candidate.reasons, "a candidate without reasons is not explainable"
        assert 0 < candidate.score <= 0.995
        assert candidate.band in ("possible", "probable", "strong", "confirmed")


async def test_search_finds_entities_by_identifier_and_observation(session, user) -> None:
    investigation = await seed_demo_investigation(session, user.id)
    await session.commit()
    service = SearchService(session)

    assert await service.search(investigation.id, DEMO_USERNAME)
    assert await service.search(investigation.id, DEMO_DOMAIN)
    assert await service.search(investigation.id, "graph-pipeline")
    assert not await service.search(investigation.id, "no-such-identifier-anywhere")


async def test_timeline_is_ordered_and_covers_every_event_kind(session, user) -> None:
    investigation = await seed_demo_investigation(session, user.id)
    await session.commit()

    events = await TimelineService(session, investigation.id).build()
    assert len(events) >= 10
    assert events == sorted(events, key=lambda e: e["at"])
    assert {"observation", "relationship", "correlation"} <= {e["kind"] for e in events}


async def test_graph_filters_narrow_the_projection(session, user) -> None:
    investigation = await seed_demo_investigation(session, user.id)
    await session.commit()
    projection = GraphProjection(session, investigation.id)

    everything = await projection.build()
    only_accounts = await projection.build(GraphFilters(entity_types={"social_account"}))
    assert 0 < len(only_accounts["nodes"]) < len(everything["nodes"])
    assert {n["type"] for n in only_accounts["nodes"]} == {"social_account"}

    high_confidence = await projection.build(GraphFilters(min_confidence=0.9))
    assert all(n["confidence"] >= 0.9 for n in high_confidence["nodes"])


async def test_graph_clusters_above_the_threshold(session, user, monkeypatch) -> None:
    investigation = await seed_demo_investigation(session, user.id)
    await session.commit()
    projection = GraphProjection(session, investigation.id)
    monkeypatch.setattr(projection.settings, "graph_cluster_threshold", 3)

    graph = await projection.build()
    assert graph["clustered"] is True
    assert any(node["is_cluster"] for node in graph["nodes"])
    assert graph["clusters"]


async def test_analytics_find_paths_and_structure(session, user) -> None:
    investigation = await seed_demo_investigation(session, user.id)
    await session.commit()
    analytics = GraphAnalytics(session, investigation.id)

    from sqlalchemy import select

    from app.models import Entity

    entities = {
        e.canonical_key: e
        for e in (await session.execute(
            select(Entity).where(Entity.investigation_id == investigation.id)
        )).scalars()
    }
    source = entities[f"username:{DEMO_USERNAME}"]
    target = entities[f"email:{DEMO_EMAIL}"]

    path = await analytics.shortest_path(str(source.id), str(target.id))
    assert len(path) >= 2
    assert path[0].entity_id == str(source.id)
    assert path[-1].entity_id == str(target.id)
    assert all(step.via for step in path[1:]), "every hop must name the relationship"

    ranked = await analytics.centrality("degree", limit=5)
    assert ranked and ranked[0]["score"] > 0
    assert await analytics.connected_components()
    assert await analytics.clusters()
    assert isinstance(await analytics.duplicate_identities(), list)


async def test_report_separates_assertion_classes(session, user) -> None:
    investigation = await seed_demo_investigation(session, user.id)
    await session.commit()

    report = await ReportBuilder(session, investigation.id).build()
    assert report["summary"]["accounts_discovered"] >= 3
    assert report["targets"]
    assert report["evidence"]
    assert report["unverified_claims"], "the demo includes a deliberately unverified hit"
    labels = {item["assertion_label"] for item in report["evidence"]}
    assert "Observed fact" in labels and "Unverified claim" in labels
    assert "not statements of identity" in report["disclaimer"]


async def test_every_export_format_produces_valid_output(session, user) -> None:
    investigation = await seed_demo_investigation(session, user.id)
    await session.commit()
    service = ExportService(session, investigation.id)

    body, media, filename = await service.export(ExportFormat.JSON)
    payload = json.loads(body)
    assert payload["entities"] and payload["relationships"]
    assert media == "application/json" and filename.endswith(".json")

    body, media, _ = await service.export(ExportFormat.CSV, ExportScope.RELATIONSHIPS)
    assert body.decode().splitlines()[0].startswith("source,type,target")

    body, _, _ = await service.export(ExportFormat.MARKDOWN)
    text = body.decode()
    assert "# Investigation report" in text and "## Evidence" in text

    body, _, _ = await service.export(ExportFormat.HTML)
    html = body.decode()
    assert "<!doctype html>" in html and "Observed fact" in html

    body, _, name = await service.export(ExportFormat.GRAPHML)
    root = ET.fromstring(body)
    graph = root.find("{http://graphml.graphdrawing.org/xmlns}graph")
    assert graph is not None
    assert len(graph.findall("{http://graphml.graphdrawing.org/xmlns}node")) > 0
    assert name.endswith(".graphml")


async def test_html_export_escapes_provider_supplied_text(session, user, investigation) -> None:
    """Provider output is untrusted: it must never become markup in a report."""
    from app.evidence.extractor import EntityExtractor
    from app.providers.types import Observation, Target

    hostile = '<script>alert("xss")</script>'
    await EntityExtractor(session, investigation.id).ingest(
        Target(type="username", value="ex", normalized="ex", depth=0),
        [
            Observation(
                provider="stub", kind="social_account", value="ex", confidence=0.9,
                url="https://example.com/ex",
                data={"platform": "Stub", "username": "ex", "display_name": hostile},
                excerpt=hostile,
            )
        ],
    )
    await session.commit()

    body, _, _ = await ExportService(session, investigation.id).export(ExportFormat.HTML)
    html = body.decode()
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html or hostile not in html


async def test_investigation_delete_removes_all_children(session, user) -> None:
    investigation = await seed_demo_investigation(session, user.id)
    await session.commit()
    investigation_id = investigation.id

    from sqlalchemy import func, select

    from app.models import Entity, Relationship

    async def count(model) -> int:
        return int((await session.execute(
            select(func.count(model.id)).where(model.investigation_id == investigation_id)
        )).scalar_one())

    assert await count(Entity) > 0
    await InvestigationService(session).delete(investigation_id)
    await session.commit()
    assert await count(Entity) == 0
    assert await count(Relationship) == 0
