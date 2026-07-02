"""Tier-2 integration + Tier-4 regression pins for the synthesis layer.

Deterministic (fake LLM), full CLI path plus the distill/remove/lint integrations.
"""
import re

import pytest
import yaml
from click.testing import CliRunner
from unittest.mock import patch

from openkb import topic_tree as tt
from openkb.agent import enricher as E
from openkb.agent.compiler import _remove_doc_from_pages
from openkb.cli import cli
from openkb.lint import find_orphans, list_existing_wiki_targets, strip_ghost_wikilinks
from tests.support.fake_llm import fake_llm

_WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")


def _kb(tmp_path, n, enabled=True):
    kb = tmp_path / "kb"
    (kb / ".openkb").mkdir(parents=True)
    concepts = kb / "wiki" / "concepts"
    concepts.mkdir(parents=True)
    for i in range(n):
        links = "[[concept-00]]" if i else ""
        (concepts / f"concept-{i:02d}.md").write_text(
            f'---\ntype: "Concept"\ndescription: "brief {i}"\n---\n# concept-{i:02d}\n\n{links}\n',
            encoding="utf-8",
        )
    cfg = "model: gpt-5.4\ntopic_tree: true\n"
    if enabled:
        cfg += "enrichment:\n  enabled: true\n"
    (kb / ".openkb" / "config.yaml").write_text(cfg, encoding="utf-8")
    return kb


def _run(kb, *args):
    with fake_llm(), patch("openkb.cli._setup_llm_key"):
        return CliRunner().invoke(cli, ["--kb-dir", str(kb), *args])


def _grounded(concepts):
    return {p.stem for p in concepts.rglob("*.md")
            if p.name != "_topic.md" and not p.name.endswith(".enrich.md")}


# --- Tier 2: CLI enrich -----------------------------------------------------

@pytest.mark.integration
def test_enrich_cli_creates_paired_files_leaf_untouched(tmp_path):
    kb = _kb(tmp_path, 6)
    concepts = kb / "wiki" / "concepts"
    before = {p.name: p.read_text(encoding="utf-8") for p in concepts.glob("*.md")}

    res = _run(kb, "enrich")
    assert res.exit_code == 0, res.output
    assert "Enriched 6" in res.output

    for stem in before:
        # every grounded leaf byte-identical after enrich
        assert (concepts / stem).read_text(encoding="utf-8") == before[stem]
    # one synth per concept, with correct provenance
    synths = list(concepts.glob("*.enrich.md"))
    assert len(synths) == 6
    fm = yaml.safe_load(re.match(r"^---\n(.*?)\n---\n",
                                 synths[0].read_text(encoding="utf-8"), re.DOTALL).group(1))
    assert fm["provenance"] == "enriched" and fm["type"] == "Enrichment"


@pytest.mark.integration
def test_enrich_cli_idempotent(tmp_path):
    kb = _kb(tmp_path, 5)
    assert "Enriched 5" in _run(kb, "enrich").output
    second = _run(kb, "enrich")
    assert "5 unchanged" in second.output


@pytest.mark.integration
def test_enrich_noop_when_disabled(tmp_path):
    kb = _kb(tmp_path, 3, enabled=False)
    res = _run(kb, "enrich")
    assert res.exit_code == 0
    assert "enrichment is not enabled" in res.output
    assert not list((kb / "wiki" / "concepts").glob("*.enrich.md"))


@pytest.mark.integration
def test_enrich_then_distill_grounded_only_synth_travels(tmp_path):
    kb = _kb(tmp_path, 20)
    concepts = kb / "wiki" / "concepts"
    assert _run(kb, "enrich").exit_code == 0
    grounded = _grounded(concepts)

    res = _run(kb, "distill")
    assert res.exit_code == 0, res.output
    # hierarchy built over grounded concepts only; count unchanged
    assert _grounded(concepts) == grounded
    # every relocated concept keeps its paired synth co-located
    for cm in concepts.rglob("*.md"):
        if cm.name.endswith(".enrich.md") or cm.name == "_topic.md":
            continue
        assert cm.with_name(cm.stem + ".enrich.md").is_file()
    # lint-clean: every wikilink (incl. synth see_also + pathway/related) resolves
    targets = list_existing_wiki_targets(kb / "wiki")
    for md in (kb / "wiki").rglob("*.md"):
        for link in _WIKILINK.findall(md.read_text(encoding="utf-8")):
            _, ghosts = strip_ghost_wikilinks(f"[[{link}]]", targets)
            assert ghosts == [], f"ghost [[{link}]] in {md}"


@pytest.mark.integration
def test_synth_files_not_flagged_as_orphans(tmp_path):
    kb = _kb(tmp_path, 5)
    _run(kb, "enrich")
    orphans = find_orphans(kb / "wiki")
    assert not [o for o in orphans if o.endswith(".synth")]


# --- Tier 4: regression pins ------------------------------------------------

@pytest.mark.regression
def test_regression_enrich_never_mutates_grounded_leaf(tmp_path):
    root = tmp_path / "concepts"
    root.mkdir(parents=True)
    (root / "foo.md").write_text(
        '---\ntype: "Concept"\ndescription: "d"\n---\n# foo\n', encoding="utf-8")
    original = (root / "foo.md").read_bytes()
    E.enrich(root, generate=lambda c: {"elaboration": "x", "inferred": ["y"]},
             verify=lambda c, claims: ["inferred"] * len(claims))
    assert (root / "foo.md").read_bytes() == original


@pytest.mark.regression
def test_regression_verify_drops_contradiction(tmp_path):
    root = tmp_path / "concepts"
    root.mkdir(parents=True)
    (root / "foo.md").write_text(
        '---\ntype: "Concept"\ndescription: "d"\n---\n# foo\n', encoding="utf-8")
    E.enrich(
        root,
        generate=lambda c: {"inferred": ["ok claim", "CONTRADICT bad claim"]},
        verify=lambda c, claims: ["contradicts" if "CONTRADICT" in x else "inferred" for x in claims],
    )
    assert "CONTRADICT" not in (root / "foo.enrich.md").read_text(encoding="utf-8")


@pytest.mark.regression
def test_regression_remove_concept_removes_paired_synth(tmp_path):
    wiki = tmp_path / "wiki"
    concepts = wiki / "concepts"
    concepts.mkdir(parents=True)
    # a concept whose only source is doc "d"
    (concepts / "foo.md").write_text(
        '---\ntype: "Concept"\ndescription: "d"\nsources: [summaries/d.md]\n---\n# foo\n',
        encoding="utf-8")
    (concepts / "foo.enrich.md").write_text(
        '---\ntype: Enrichment\nsource_concept: foo\n---\n# foo (enrichment)\n', encoding="utf-8")

    out = _remove_doc_from_pages(wiki, "d", page_dir="concepts")
    assert "foo" in out["deleted"]
    assert not (concepts / "foo.md").exists()
    assert not (concepts / "foo.enrich.md").exists()  # paired synth cleaned up


@pytest.mark.regression
def test_regression_distill_ignores_synth_files(tmp_path):
    root = tmp_path / "concepts"
    root.mkdir(parents=True)
    for i in range(12):
        (root / f"c{i:02d}.md").write_text(
            f'---\ntype: "Concept"\ndescription: "b{i}"\n---\n# c{i:02d}\n', encoding="utf-8")
        (root / f"c{i:02d}.enrich.md").write_text(
            f'---\ntype: Enrichment\nsource_concept: c{i:02d}\n---\n# c{i:02d} (enrichment)\n',
            encoding="utf-8")
    stats = tt.distill(root, cluster=lambda items: {
        f"g{i // 4}": [s for s, _ in items[i:i + 4]] for i in range(0, len(items), 4)
    }, summarize=lambda n, s: "s")
    assert stats["leaves"] == 12  # synth files not counted as leaves
    # no topic node was named after a synth file
    for tp in root.rglob("_topic.md"):
        fm = yaml.safe_load(re.match(r"^---\n(.*?)\n---\n",
                                     tp.read_text(encoding="utf-8"), re.DOTALL).group(1))
        assert all(not c.endswith(".synth") for c in fm.get("children", []))
