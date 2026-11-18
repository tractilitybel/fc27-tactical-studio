import json
import os

import pytest

from fc27studio.core.document import Document
from fc27studio.core.model import Annotation, Bookmark, Kind, Project
from fc27studio.core import storage


def arrow(frame=10):
    return Annotation(Kind.ARROW, [(0, 0), (100, 50)], frame, frame + 30)


# ------------------------------------------------------------- undo/redo
def test_add_undo_redo():
    d = Document()
    a = arrow()
    d.add_annotation(a)
    assert d.project.annotations == [a]
    assert d.dirty
    d.undo.undo()
    assert d.project.annotations == []
    d.undo.redo()
    assert d.project.annotations == [a]


def test_modify_undo():
    d = Document()
    a = arrow()
    d.add_annotation(a)
    d.modify_annotations([a.id], lambda x: setattr(x, "color", "#ff0000"))
    assert a.color == "#ff0000"
    d.undo.undo()
    assert a.color == "#ffd400"
    d.undo.redo()
    assert a.color == "#ff0000"


def test_interactive_move_undo():
    d = Document()
    a = arrow()
    d.add_annotation(a)
    before = d.snapshot([a.id])
    a.translate(10, 20)
    d.set_annotation_states(before, d.snapshot([a.id]), "Move")
    assert a.points[0] == (10, 20)
    d.undo.undo()
    assert a.points[0] == (0, 0)


def test_merge_key_merges_slider_edits():
    d = Document()
    a = arrow()
    d.add_annotation(a)
    for v in (0.9, 0.8, 0.7):
        d.modify_annotations([a.id], lambda x, v=v: setattr(x, "opacity", v), merge_key="op")
    d.undo.undo()
    assert a.opacity == 1.0


def test_delete_restores_position():
    d = Document()
    anns = [arrow(i) for i in range(3)]
    d.add_annotations(anns)
    d.remove_annotations([anns[1].id])
    assert [x.id for x in d.project.annotations] == [anns[0].id, anns[2].id]
    d.undo.undo()
    assert [x.id for x in d.project.annotations] == [x.id for x in anns]


def test_layer_order():
    d = Document()
    a, b, c = arrow(), arrow(), arrow()
    d.add_annotations([a, b, c])
    d.reorder([a.id], "front")
    assert max(x.z for x in (a, b, c)) == a.z
    d.reorder([a.id], "back")
    assert min(x.z for x in (a, b, c)) == a.z
    d.reorder([a.id], "forward")
    zs = sorted((x.z, x.id) for x in (a, b, c))
    assert zs[1][1] == a.id
    d.undo.undo()
    assert min(x.z for x in (a, b, c)) == a.z


def test_grouping():
    d = Document()
    a, b, c = arrow(), arrow(), arrow()
    d.add_annotations([a, b, c])
    gid = d.group([a.id, b.id])
    assert a.group_id == b.group_id == gid and c.group_id is None
    assert d.expand_groups([a.id]) == {a.id, b.id}
    d.ungroup([a.id, b.id])
    assert a.group_id is None
    d.undo.undo()
    assert a.group_id == gid


def test_clean_state():
    d = Document()
    d.add_annotation(arrow())
    d.mark_clean()
    assert not d.dirty
    d.undo.undo()
    assert d.dirty
    d.undo.redo()
    assert not d.dirty


# ------------------------------------------------------------- bookmarks
def test_bookmark_crud():
    d = Document()
    b = Bookmark(120, "Lost marking", "Defensive Error", tags=["cb"], players=["Van Dijk"])
    d.add_bookmark(b)
    d.update_bookmark(b.id, title="Lost runner", tags=["cb", "box"])
    assert b.title == "Lost runner" and b.tags == ["cb", "box"]
    d.undo.undo()
    assert b.title == "Lost marking" and b.tags == ["cb"]
    d.remove_bookmarks([b.id])
    assert d.project.bookmarks == []
    d.undo.undo()
    assert d.project.bookmarks == [b]


def test_bookmark_search():
    b = Bookmark(1, "High press win", "Pressing", tags=["counter"], players=["Saka"], notes="left side")
    assert b.matches("press saka")
    assert b.matches("LEFT")
    assert not b.matches("goal")


# ------------------------------------------------------------- geometry
def test_hit_testing():
    a = Annotation(Kind.LINE, [(0, 0), (100, 0)], 0, 0, width=4)
    assert a.hit((50, 3), 4)
    assert not a.hit((50, 30), 4)
    c = Annotation(Kind.CIRCLE, [(0, 0), (100, 100)], 0, 0)
    assert c.hit((50, 50), 2)
    assert not c.hit((2, 2), 2)
    z = Annotation(Kind.ZONE, [(0, 0), (100, 0), (50, 100)], 0, 0)
    assert z.hit((50, 30), 2)
    assert not z.hit((5, 90), 2)


def test_rotated_hit():
    r = Annotation(Kind.RECT, [(0, 40), (100, 60)], 0, 0, rotation=90)
    assert r.hit((50, 0), 2)   # rotated rect is now vertical
    assert not r.hit((5, 50), 2)


def test_scale_about_anchor():
    a = Annotation(Kind.RECT, [(10, 10), (20, 20)], 0, 0)
    a.scale(2, 2, (10, 10))
    assert a.points == [(10, 10), (30, 30)]


def test_visibility_range():
    a = arrow(10)
    assert a.visible_at(10) and a.visible_at(40)
    assert not a.visible_at(9) and not a.visible_at(41)


# ------------------------------------------------------------- storage
def sample_project():
    p = Project(name="Match 1")
    p.video.path = "/videos/match.mp4"
    p.video.fingerprint = {"frame_count": 150, "width": 320, "height": 180}
    p.annotations.append(arrow())
    p.annotations.append(Annotation(Kind.TEXT, [(5, 5)], 0, 10, text="Press!\nNow"))
    p.bookmarks.append(Bookmark(50, "Goal", "Goal", ["header"], ["Kane"]))
    p.loop_a, p.loop_b = 10, 90
    return p


def test_roundtrip(tmp_path):
    p = sample_project()
    path = str(tmp_path / "m.fc27studio")
    storage.save_project(p, path)
    q = storage.load_project(path)
    assert q.to_dict() == p.to_dict()


def test_backup_created(tmp_path):
    path = str(tmp_path / "m.fc27studio")
    p = sample_project()
    storage.save_project(p, path)
    p.name = "Changed"
    storage.save_project(p, path)
    assert storage.load_project(path + ".bak").name == "Match 1"
    assert storage.load_project(path).name == "Changed"


def test_corrupt_falls_back_to_backup(tmp_path):
    path = str(tmp_path / "m.fc27studio")
    storage.save_project(sample_project(), path)
    storage.save_project(sample_project(), path)
    with open(path, "w") as f:
        f.write("{ not json")
    with pytest.raises(storage.ProjectError):
        storage.load_project(path)
    proj, used_backup = storage.load_with_backup_fallback(path)
    assert used_backup and proj.name == "Match 1"


def test_rejects_foreign_and_newer(tmp_path):
    path = str(tmp_path / "x.fc27studio")
    with open(path, "w") as f:
        json.dump({"hello": 1}, f)
    with pytest.raises(storage.ProjectError):
        storage.load_project(path)
    with open(path, "w") as f:
        json.dump({"format": "fc27studio", "version": 99, "project": {}}, f)
    with pytest.raises(storage.ProjectError, match="newer"):
        storage.load_project(path)


def test_resolve_moved_video(tmp_path):
    pdir = tmp_path / "proj"
    pdir.mkdir()
    (pdir / "match.mp4").write_bytes(b"x")
    p = sample_project()
    p.video.path = "C:\\old\\place\\match.mp4"
    found = storage.resolve_video(p, str(pdir / "m.fc27studio"))
    assert found == str(pdir / "match.mp4")
    p.video.path = "/missing/other.mp4"
    assert storage.resolve_video(p, str(pdir / "m.fc27studio")) is None


def test_relative_path_used(tmp_path):
    vdir = tmp_path / "videos"
    vdir.mkdir()
    (vdir / "a.mp4").write_bytes(b"x")
    p = sample_project()
    p.video.path = str(vdir / "a.mp4")
    ppath = str(tmp_path / "projects" / "m.fc27studio")
    storage.save_project(p, ppath)
    # Move both folders together.
    moved = tmp_path / "moved"
    os.rename(tmp_path / "projects", tmp_path / "p2")
    moved.mkdir()
    os.rename(tmp_path / "p2", moved / "projects")
    os.rename(vdir, moved / "videos")
    q = storage.load_project(str(moved / "projects" / "m.fc27studio"))
    q.video.path = "/gone/a.mp4"
    assert storage.resolve_video(q, str(moved / "projects" / "m.fc27studio")) == \
        str(moved / "videos" / "a.mp4")


def test_recovery(tmp_path):
    rec = storage.Recovery(str(tmp_path / "rec"))
    p = sample_project()
    rec.write(p, "/x/m.fc27studio")
    entries = rec.entries()
    assert len(entries) == 1 and entries[0].project_path == "/x/m.fc27studio"
    assert rec.load(entries[0]).to_dict() == p.to_dict()
    rec.discard(p)
    assert rec.entries() == []
