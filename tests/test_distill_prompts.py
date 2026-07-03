"""Unit tests for the distill guidance/summary hardening (no LLM).

Guards the fix for guidance leaking into distilled summaries: guidance goes in a
system message, summaries are stripped of any echoed preamble, and injected
AGENTS.md guidance excludes human-facing HTML-comment notes.
"""
from openkb import topic_tree_llm as ttl
from openkb.cli import _agents_section


def test_messages_puts_guidance_in_system_role():
    msgs = ttl._messages("Piano KB theme.", "Do the task.")
    assert msgs[0]["role"] == "system" and "Piano KB theme." in msgs[0]["content"]
    assert msgs[-1] == {"role": "user", "content": "Do the task."}


def test_messages_omits_system_when_no_guidance():
    assert ttl._messages("", "task") == [{"role": "user", "content": "task"}]


def test_clean_summary_passes_through_clean_text():
    s = "This is a normal one-paragraph summary of the topic."
    assert ttl._clean_summary(s) == s


def test_clean_summary_strips_hr_separated_preamble():
    leaked = (
        "**Thematic Description (injected prompt text)**\n\n"
        "This KB is themed as follows: blah blah.\n\n"
        "---\n\n"
        "**One-Paragraph Summary of \"scales\"**\n\n"
        "Scales are ordered pitch collections that underpin melody and harmony."
    )
    out = ttl._clean_summary(leaked)
    assert out == "Scales are ordered pitch collections that underpin melody and harmony."
    assert "injected prompt text" not in out


def test_clean_summary_strips_leading_heading_and_bold_label():
    out = ttl._clean_summary("# scales\n\n**Summary**\n\nThe real summary body.")
    assert out == "The real summary body."


def test_agents_section_strips_html_comment_notes(tmp_path):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "AGENTS.md").write_text(
        "# Wiki Schema\n\n"
        "## Hierarchy\n"
        "<!-- Editing note: not sent to the model. Replace below. -->\n"
        "Organize around piano learning areas: technique, theory, rhythm.\n\n"
        "## Other\nx\n",
        encoding="utf-8",
    )
    g = _agents_section(wiki, "Hierarchy")
    assert "Editing note" not in g and "not sent to the model" not in g
    assert "piano learning areas" in g
