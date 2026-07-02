"""Tier-2 integration: the real `openkb distill` path with a deterministic fake LLM.

Drives CLI -> make_distill_* -> distill() -> pathway tree end to end, then checks
that every wikilink (child index + sideways `related`) resolves via lint.
"""
import re

import pytest
import yaml
from click.testing import CliRunner
from unittest.mock import patch

from openkb import topic_tree as tt
from openkb.cli import cli
from openkb.lint import list_existing_wiki_targets, strip_ghost_wikilinks
from tests.support.fake_llm import fake_llm

pytestmark = pytest.mark.integration

_WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")


def _kb(tmp_path, n, hierarchy_guidance=True):
    kb = tmp_path / "kb"
    (kb / ".openkb").mkdir(parents=True)
    concepts = kb / "wiki" / "concepts"
    concepts.mkdir(parents=True)
    for i in range(n):
        (concepts / f"concept-{i:02d}.md").write_text(
            f'---\ntype: "Concept"\ndescription: "brief {i}"\n---\n'
            f"# concept-{i:02d}\n\nSee [[concept-00]].\n",
            encoding="utf-8",
        )
    if hierarchy_guidance:
        (kb / "wiki" / "AGENTS.md").write_text(
            "# Wiki Schema\n\n## Hierarchy\nThis KB is about widgets.\n\n## Other\nx\n",
            encoding="utf-8",
        )
    (kb / ".openkb" / "config.yaml").write_text(
        "model: gpt-5.4\ntopic_tree: true\n"
        "hierarchy:\n  target_fanout: 4\n  sideways_links_max: 2\n",
        encoding="utf-8",
    )
    return kb


def _stems(root):
    return {p.stem for p in root.rglob("*.md") if p.name != tt.TOPIC_FILE}


def test_distill_cli_builds_pathway_tree_lint_clean(tmp_path):
    kb = _kb(tmp_path, n=20)
    concepts = kb / "wiki" / "concepts"
    original = _stems(concepts)

    with fake_llm(), patch("openkb.cli._setup_llm_key"):
        res = CliRunner().invoke(cli, ["--kb-dir", str(kb), "distill"])

    assert res.exit_code == 0, res.output
    assert "layer" in res.output.lower()

    # One root, tagged layer 1; every concept preserved.
    root_fm_text = (concepts / tt.TOPIC_FILE).read_text(encoding="utf-8")
    assert yaml.safe_load(re.match(r"^---\n(.*?)\n---\n", root_fm_text, re.DOTALL).group(1))["layer"] == 1
    assert _stems(concepts) == original

    # Multi-layer: at least one subtopic directory was created.
    assert any(d.is_dir() for d in concepts.iterdir())

    # Lint-clean: every wikilink in every wiki file resolves (child + related).
    targets = list_existing_wiki_targets(kb / "wiki")
    for md in (kb / "wiki").rglob("*.md"):
        body = md.read_text(encoding="utf-8")
        for link in _WIKILINK.findall(body):
            _, ghosts = strip_ghost_wikilinks(f"[[{link}]]", targets)
            assert ghosts == [], f"ghost link [[{link}]] in {md}"


def test_distill_twice_is_idempotent_no_loss(tmp_path):
    """Re-distilling rebuilds from the leaves (source of truth); no concept is
    lost or duplicated across a second run."""
    kb = _kb(tmp_path, n=20)
    concepts = kb / "wiki" / "concepts"
    original = _stems(concepts)

    with fake_llm(), patch("openkb.cli._setup_llm_key"):
        first = CliRunner().invoke(cli, ["--kb-dir", str(kb), "distill"])
        after_first = _stems(concepts)
        second = CliRunner().invoke(cli, ["--kb-dir", str(kb), "distill"])

    assert first.exit_code == 0 and second.exit_code == 0, (first.output, second.output)
    assert after_first == original == _stems(concepts)


def test_distill_noop_when_topic_tree_disabled(tmp_path):
    kb = _kb(tmp_path, n=5)
    (kb / ".openkb" / "config.yaml").write_text("model: gpt-5.4\n", encoding="utf-8")
    with fake_llm(), patch("openkb.cli._setup_llm_key"):
        res = CliRunner().invoke(cli, ["--kb-dir", str(kb), "distill"])
    assert res.exit_code == 0
    assert "topic_tree" in res.output
    # tree not built — concepts still flat
    assert not any(d.is_dir() for d in (kb / "wiki" / "concepts").iterdir())
