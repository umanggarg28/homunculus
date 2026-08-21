"""The lint step of the wiki pattern the vault is modelled on.

A knowledge base earns its name through cross-references. Two failure shapes,
and they are different problems: an ORPHAN has no inbound link, so nothing
will ever lead the agent back to it; a DANGLING link names a slug that does
not exist, promising an edge that isn't there.
"""

from homunculus.doctor import audit_memory_links


def _write(root, name, body="", related=None):
    fm = ["---", f"name: {name}", "description: d", "type: project"]
    if related is not None:
        fm.append(f"related: {', '.join(related)}")
    fm.append("---")
    (root / f"{name}.md").write_text("\n".join(fm) + "\n\n" + body, encoding="utf-8")


def test_no_memory_root_is_silent(tmp_path):
    assert audit_memory_links(None) == []
    assert audit_memory_links(tmp_path / "missing") == []


def test_a_well_linked_vault_is_clean(tmp_path):
    _write(tmp_path, "alpha", related=["beta"])
    _write(tmp_path, "beta", related=["alpha"])
    assert audit_memory_links(tmp_path) == []


def test_body_wikilinks_count_as_links(tmp_path):
    _write(tmp_path, "alpha", body="see [[beta]] for more")
    _write(tmp_path, "beta", body="see [[alpha]]")
    assert audit_memory_links(tmp_path) == []


def test_a_mostly_orphaned_vault_reports_once_not_per_entry(tmp_path):
    """The systemic case is one finding. Twenty findings for twenty orphans
    buries the point that nothing is being linked at all."""
    for n in ["a", "b", "c", "d", "e", "f"]:
        _write(tmp_path, n)
    findings = audit_memory_links(tmp_path)
    assert len(findings) == 1
    assert findings[0].subject == "vault"
    assert "6 of 6" in findings[0].detail


def test_a_few_orphans_are_named_individually(tmp_path):
    _write(tmp_path, "hub", related=["spoke1", "spoke2"])
    _write(tmp_path, "spoke1", related=["hub"])
    _write(tmp_path, "spoke2", related=["hub"])
    _write(tmp_path, "lonely")
    findings = audit_memory_links(tmp_path)
    assert [f.subject for f in findings] == ["lonely"]
    assert "no other memory links to this one" in findings[0].detail


def test_dangling_links_are_reported(tmp_path):
    _write(tmp_path, "alpha", related=["beta", "ghost"])
    _write(tmp_path, "beta", related=["alpha"])
    findings = audit_memory_links(tmp_path)
    dangling = [f for f in findings if "do not exist" in f.detail]
    assert len(dangling) == 1
    assert dangling[0].subject == "alpha"
    assert "ghost" in dangling[0].detail


def test_hyphen_underscore_variants_are_the_same_node(tmp_path):
    _write(tmp_path, "alpha_one", related=["beta-two"])
    _write(tmp_path, "beta_two", related=["alpha-one"])
    assert audit_memory_links(tmp_path) == []


def test_index_and_readme_are_not_entries(tmp_path):
    _write(tmp_path, "alpha", related=["beta"])
    _write(tmp_path, "beta", related=["alpha"])
    (tmp_path / "MEMORY.md").write_text("# index\n- [[alpha]]\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# readme\n", encoding="utf-8")
    assert audit_memory_links(tmp_path) == []


def test_a_self_link_does_not_rescue_an_orphan(tmp_path):
    _write(tmp_path, "hub", related=["spoke"])
    _write(tmp_path, "spoke", related=["hub"])
    _write(tmp_path, "narcissus", related=["narcissus"])
    findings = audit_memory_links(tmp_path)
    assert [f.subject for f in findings] == ["narcissus"]


def test_a_link_by_declared_name_is_not_dangling(tmp_path):
    """`user_user_name.md` declares `name: user_name`, and the index shows the
    agent the NAME — so that is what it links by. Resolving on the filename
    alone reported live links as dangling."""
    (tmp_path / "user_user_name.md").write_text(
        "---\nname: user_name\ndescription: d\ntype: user\n---\nbody", encoding="utf-8")
    _write(tmp_path, "alpha", related=["user_name"])
    findings = audit_memory_links(tmp_path)
    assert not [f for f in findings if "do not exist" in f.detail]
