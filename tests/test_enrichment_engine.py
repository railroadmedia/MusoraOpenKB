"""Unit coverage for the enrichment engine (openkb/agent/synthesizer.py).

Deterministic — injected synthesize/verify callables, no LLM.
"""
import re

import pytest
import yaml

from openkb import topic_tree as tt
from openkb.agent import enricher as E


def _concept(root, stem, brief="b", links=()):
    root.mkdir(parents=True, exist_ok=True)
    body_links = "".join(f"[[{l}]] " for l in links)
    (root / f"{stem}.md").write_text(
        f'---\ntype: "Concept"\ndescription: "{brief}"\n---\n# {stem}\n\n{body_links}\n',
        encoding="utf-8",
    )


def _synth_fm(path):
    return yaml.safe_load(re.match(r"^---\n(.*?)\n---\n", path.read_text(encoding="utf-8"),
                                   re.DOTALL).group(1))


def _synthesize(concept):
    return {
        "elaboration": f"Elab of {concept['stem']}.",
        "inferred": [f"{concept['stem']} is broadly relevant."],
        "examples": [f"example for {concept['stem']}"],
        "see_also": [s for s, _ in concept["neighbors"]],
    }


# --- writer -----------------------------------------------------------------

def test_write_enrichment_md_shape(tmp_path):
    p = tmp_path / "foo.enrich.md"
    written = E.write_enrichment_md(
        p, source_concept="foo", src_hash="abc", verified=True,
        elaboration="An elaboration.", inferred=["Beyond the sources."],
        examples=["ex1"], see_also=["bar"],
    )
    assert written == ["elaboration", "inferred", "examples", "see_also"]
    fm = _synth_fm(p)
    assert fm["type"] == "Enrichment"
    assert fm["source_concept"] == "foo"
    assert fm["source_hash"] == "abc"
    assert fm["provenance"] == "enriched"
    assert fm["verified"] is True
    body = p.read_text(encoding="utf-8")
    assert "> [!inferred] Beyond the sources." in body   # inferred tagged
    assert "- [[bar]]" in body                            # see_also wikilink


def test_write_enrichment_md_omits_empty_sections(tmp_path):
    p = tmp_path / "foo.enrich.md"
    written = E.write_enrichment_md(p, source_concept="foo", src_hash="h", verified=True,
                              elaboration="only this")
    assert written == ["elaboration"]
    assert "## Inferred context" not in p.read_text(encoding="utf-8")


def test_synth_path_and_hash(tmp_path):
    assert E.enrich_path_for(tmp_path / "a" / "foo.md").name == "foo.enrich.md"
    assert E.source_hash("x") == E.source_hash("x") != E.source_hash("y")


# --- engine -----------------------------------------------------------------

def test_enrich_creates_paired_files_without_touching_leaf(tmp_path):
    root = tmp_path / "concepts"
    _concept(root, "foo", "the foo", links=["bar"])
    _concept(root, "bar", "the bar")
    before = (root / "foo.md").read_text(encoding="utf-8")

    stats = E.enrich(root, generate=_synthesize)
    assert stats["created"] == 2 and stats["updated"] == 0
    assert (root / "foo.enrich.md").is_file()
    assert (root / "foo.md").read_text(encoding="utf-8") == before  # leaf untouched
    fm = _synth_fm(root / "foo.enrich.md")
    assert fm["source_concept"] == "foo"
    # neighbor bar surfaced in see_also
    assert "- [[bar]]" in (root / "foo.enrich.md").read_text(encoding="utf-8")


def test_enrich_idempotent_via_source_hash(tmp_path):
    root = tmp_path / "concepts"
    _concept(root, "foo")
    assert E.enrich(root, generate=_synthesize)["created"] == 1
    # second run: unchanged source -> skipped
    stats = E.enrich(root, generate=_synthesize)
    assert stats == {"created": 0, "updated": 0, "skipped": 1, "total": 1}
    # force regenerates
    assert E.enrich(root, generate=_synthesize, force=True)["updated"] == 1


def test_enrich_regenerates_when_concept_changes(tmp_path):
    root = tmp_path / "concepts"
    _concept(root, "foo", "v1")
    E.enrich(root, generate=_synthesize)
    _concept(root, "foo", "v2")  # rewrite -> new hash
    stats = E.enrich(root, generate=_synthesize)
    assert stats["updated"] == 1 and stats["skipped"] == 0


def test_verify_drops_contradictions(tmp_path):
    root = tmp_path / "concepts"
    _concept(root, "foo")

    def synth(concept):
        return {"inferred": ["true thing", "CONTRADICT: false thing", "another true"]}

    def verify(concept, claims):
        return ["contradicts" if "CONTRADICT" in c else "inferred" for c in claims]

    E.enrich(root, generate=synth, verify=verify)
    body = (root / "foo.enrich.md").read_text(encoding="utf-8")
    assert "CONTRADICT" not in body
    assert body.count("> [!inferred]") == 2
    assert _synth_fm(root / "foo.enrich.md")["verified"] is True


def test_unverified_inferred_marks_verified_false(tmp_path):
    root = tmp_path / "concepts"
    _concept(root, "foo")
    E.enrich(root, generate=lambda c: {"inferred": ["x"]}, verify=None)
    assert _synth_fm(root / "foo.enrich.md")["verified"] is False


def test_include_world_knowledge_false_drops_inferred(tmp_path):
    root = tmp_path / "concepts"
    _concept(root, "foo")
    E.enrich(root, generate=_synthesize, include_world_knowledge=False)
    body = (root / "foo.enrich.md").read_text(encoding="utf-8")
    assert "## Inferred context" not in body
    assert _synth_fm(root / "foo.enrich.md")["verified"] is True


def test_only_stems_scopes_work(tmp_path):
    root = tmp_path / "concepts"
    _concept(root, "foo")
    _concept(root, "bar")
    stats = E.enrich(root, generate=_synthesize, only_stems={"foo"})
    assert stats["total"] == 1 and stats["created"] == 1
    assert (root / "foo.enrich.md").is_file()
    assert not (root / "bar.enrich.md").exists()


def test_enrich_ignores_existing_synth_as_input(tmp_path):
    """Synth files must never be treated as concepts to enrich (no foo.enrich.enrich.md)."""
    root = tmp_path / "concepts"
    _concept(root, "foo")
    E.enrich(root, generate=_synthesize)
    E.enrich(root, generate=_synthesize, force=True)
    assert not (root / "foo.enrich.enrich.md").exists()


# --- distill interop --------------------------------------------------------

def test_distill_excludes_synth_and_carries_it_along(tmp_path):
    root = tmp_path / "concepts"
    for i in range(15):
        _concept(root, f"c{i:02d}", f"brief {i}")
    E.enrich(root, generate=_synthesize)  # paired synth for each
    assert len(list(root.glob("*.enrich.md"))) == 15

    def chunk(items):  # deterministic shrink
        return {f"g{i // 4}": [s for s, _ in items[i:i + 4]] for i in range(0, len(items), 4)}

    stats = tt.distill(root, cluster=chunk, summarize=lambda n, s: f"s {n}")
    # synth files were NOT counted as leaves
    assert stats["leaves"] == 15
    # every concept moved into the tree still has its synth co-located
    for cm in root.rglob("c*.md"):
        if cm.name.endswith(".enrich.md"):
            continue
        assert cm.with_name(cm.stem + ".enrich.md").is_file(), f"{cm} lost its synth"
    # and synth files never became tree nodes/leaves themselves
    assert not any(p.stem.endswith(".synth") for p in root.rglob("_topic.md"))
