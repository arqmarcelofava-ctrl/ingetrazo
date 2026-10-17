# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""The BIM proto spike plugin (plugins/bim_proto.py): a wall from two points,
its parameters in ``group.ext`` / ``plugin_data``, «Atualizar 3D» as one undo
step — and everything surviving a save and a reopen.

Most tests drive the plugin's logic through a small stand-in viewport (a
scene and a History, no Qt window); the last ones build a real MainWindow
(offscreen) to prove the ``setup(app)`` wiring."""
from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtGui import QVector3D as V  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import core.extensions as extensions  # noqa: E402
from core import bim  # noqa: E402
from core.history import History  # noqa: E402
from core.scene import Scene  # noqa: E402
from formats import igz  # noqa: E402
from tools.base import ToolContext  # noqa: E402
from core.snap import SnapResult  # noqa: E402

_app = QApplication.instance() or QApplication([])

PLUGIN = Path(__file__).resolve().parents[1] / "plugins" / "bim_proto.py"


def _load():
    spec = importlib.util.spec_from_file_location("bim_proto_under_test",
                                                  PLUGIN)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


mod = _load()


class FakeViewport:
    """What the plugin asks of a viewport, and nothing more."""

    def __init__(self) -> None:
        self.scene = Scene()
        self.history = History(self.scene)
        self.flashes: list[str] = []
        self.updates = 0
        self.active_tool = None

    def update(self) -> None:
        self.updates += 1

    def flash_status(self, text, msec=2500) -> None:
        self.flashes.append(text)


@pytest.fixture
def vp():
    return FakeViewport()


def _bbox(group):
    pts = [v.position for v in group.mesh.vertices]
    return (min(p.x() for p in pts), min(p.y() for p in pts),
            min(p.z() for p in pts), max(p.x() for p in pts),
            max(p.y() for p in pts), max(p.z() for p in pts))


def _volume(group):
    return bim.face_set_volume(list(group.mesh.faces))


def _geom(group):
    """The wall's geometry as a comparable value."""
    return sorted((round(v.position.x(), 6), round(v.position.y(), 6),
                   round(v.position.z(), 6)) for v in group.mesh.vertices)


# ---- the wall -------------------------------------------------------------------

def test_a_wall_is_a_closed_solid_with_the_asked_size(vp):
    g = mod.create_wall(vp, (0, 0), (4, 0), thickness=0.2, height=3.0)
    assert g is not None and g in vp.scene.groups
    assert _volume(g) == pytest.approx(4 * 0.2 * 3.0)       # watertight
    x0, y0, z0, x1, y1, z1 = _bbox(g)
    assert (x0, x1) == pytest.approx((0, 4))
    assert (y0, y1) == pytest.approx((-0.1, 0.1))           # centred on the axis
    assert (z0, z1) == pytest.approx((0, 3.0))
    assert g.ifc["class"] == "IfcWall"


def test_a_diagonal_wall_keeps_its_thickness(vp):
    g = mod.create_wall(vp, (0, 0), (3, 4), thickness=0.25, height=2.5)
    assert _volume(g) == pytest.approx(5 * 0.25 * 2.5)


def test_the_base_level_sets_the_height_of_the_wall(vp):
    vp.scene.plugin_data[mod.KEY] = {"levels": [
        {"id": "PB", "name": "PB", "z": 0.0},
        {"id": "P1", "name": "P1", "z": 3.0}]}
    g = mod.create_wall(vp, (0, 0), (2, 0), height=2.8, level="P1",
                        base_offset=0.1)
    _, _, z0, _, _, z1 = _bbox(g)
    assert z0 == pytest.approx(3.1)
    assert z1 == pytest.approx(5.9)


def test_bad_parameters_are_refused_and_leave_nothing(vp):
    assert mod.create_wall(vp, (0, 0), (0, 0)) is None          # no length
    assert mod.create_wall(vp, (0, 0), (1, 0), thickness=0) is None
    assert mod.create_wall(vp, (0, 0), (1, 0), level="nope") is None
    assert vp.scene.groups == []
    assert vp.history.undo_stack == []


def test_the_parameters_live_in_group_ext(vp):
    g = mod.create_wall(vp, (0, 0), (4, 0), thickness=0.2, height=3.0)
    rec = g.ext[mod.KEY]
    assert rec == {"kind": "wall", "a": [0, 0], "b": [4, 0],
                   "thickness": 0.2, "height": 3.0, "level": "PB",
                   "base_offset": 0.0, "openings": []}
    # the project level is in the document's plugin data, too
    assert vp.scene.plugin_data[mod.KEY]["levels"][0]["id"] == "PB"


def test_creating_a_wall_is_one_undo_step(vp):
    mod.create_wall(vp, (0, 0), (4, 0))
    assert len(vp.history.undo_stack) == 1
    assert vp.history.undo()
    assert vp.scene.groups == [] and mod.KEY not in vp.scene.plugin_data
    assert vp.history.redo()
    assert len(vp.scene.groups) == 1 and mod.KEY in vp.scene.plugin_data


# ---- Atualizar 3D ---------------------------------------------------------------

def _two_walls(vp):
    a = mod.create_wall(vp, (0, 0), (4, 0), thickness=0.2, height=3.0)
    b = mod.create_wall(vp, (4, 0), (4, 3), thickness=0.2, height=3.0)
    return a, b


def test_update_3d_rebuilds_from_the_stored_parameters(vp):
    a, _ = _two_walls(vp)
    before = _geom(a)
    a.ext[mod.KEY]["height"] = 2.5                  # edit the parameters…
    assert _geom(a) == before                       # …the 3D has not moved yet
    assert mod.update_3d(vp, [a]) == 1
    assert _bbox(a)[5] == pytest.approx(2.5)
    assert _volume(a) == pytest.approx(4 * 0.2 * 2.5)


def test_update_3d_puts_back_a_wall_the_user_deformed(vp):
    a, _ = _two_walls(vp)
    good = _geom(a)
    a.mesh = mod.Mesh()                             # geometry lost / edited away
    mod.update_3d(vp)
    assert _geom(a) == good


def test_update_3d_follows_a_change_of_level_elevation(vp):
    vp.scene.plugin_data[mod.KEY] = {"levels": [
        {"id": "PB", "name": "PB", "z": 0.0}]}
    a, b = _two_walls(vp)
    vp.scene.plugin_data[mod.KEY]["levels"][0]["z"] = 0.5
    mod.update_3d(vp)
    assert _bbox(a)[2] == pytest.approx(0.5)
    assert _bbox(b)[2] == pytest.approx(0.5)


def test_update_3d_of_many_walls_is_a_single_undo_step(vp):
    a, b = _two_walls(vp)
    old = (_geom(a), _geom(b))
    a.ext[mod.KEY]["height"] = 2.0
    b.ext[mod.KEY]["thickness"] = 0.3
    steps = len(vp.history.undo_stack)
    assert mod.update_3d(vp) == 2
    assert len(vp.history.undo_stack) == steps + 1  # ONE step for both
    new = (_geom(a), _geom(b))
    assert new != old
    assert vp.history.undo()                        # one Ctrl+Z brings back
    assert (_geom(a), _geom(b)) == old              # the geometry of both
    # The in-place edits above were made OUTSIDE any command, so they are
    # not part of the step: only edit_walls() makes edit + rebuild undoable
    # together (next test).
    assert a.ext[mod.KEY]["height"] == 2.0
    assert vp.history.redo()
    assert (_geom(a), _geom(b)) == new


def test_editing_parameters_and_rebuilding_is_one_undo_step(vp):
    a, b = _two_walls(vp)
    old = (_geom(a), _geom(b))
    steps = len(vp.history.undo_stack)
    assert mod.edit_walls(vp, [a, b], height=2.0, thickness=0.3) == 2
    assert len(vp.history.undo_stack) == steps + 1
    assert _bbox(a)[5] == pytest.approx(2.0)
    assert _volume(b) == pytest.approx(3 * 0.3 * 2.0, rel=1e-4)  # vertices weld at ~1e-5
    assert vp.history.undo()
    assert (_geom(a), _geom(b)) == old
    assert a.ext[mod.KEY]["height"] == 3.0          # parameters came back too
    assert b.ext[mod.KEY]["thickness"] == 0.2
    assert vp.history.redo()
    assert a.ext[mod.KEY]["height"] == 2.0
    assert _bbox(a)[5] == pytest.approx(2.0)


def test_a_bad_edit_changes_nothing(vp):
    a, _ = _two_walls(vp)
    old = _geom(a)
    steps = len(vp.history.undo_stack)
    assert mod.edit_walls(vp, [a], height=-1.0) == 0
    assert a.ext[mod.KEY]["height"] == 3.0 and _geom(a) == old
    assert len(vp.history.undo_stack) == steps
    with pytest.raises(TypeError):
        mod.edit_walls(vp, [a], colour="red")


def test_a_record_that_cannot_be_built_rolls_the_whole_update_back(vp):
    a, b = _two_walls(vp)
    a.ext[mod.KEY]["height"] = 2.0                  # valid
    b.ext[mod.KEY]["level"] = "ghost"               # invalid
    old = _geom(a)
    steps = len(vp.history.undo_stack)
    assert mod.update_3d(vp) == 0
    assert vp.history.last_error
    assert len(vp.history.undo_stack) == steps      # nothing landed
    assert _geom(a) == old                          # the good wall untouched


def test_update_3d_ignores_groups_that_are_not_walls(vp):
    from core.group import Group
    other = Group(name="Other")
    other.ext = {"windowizer": {"params": {}}}
    vp.scene.groups.append(other)
    assert mod.update_3d(vp) == 0
    assert other.ext == {"windowizer": {"params": {}}}


def test_update_3d_refuses_while_a_group_is_open(vp):
    a, _ = _two_walls(vp)
    vp.scene.edit_group = a
    try:
        assert mod.update_3d(vp) == 0
    finally:
        vp.scene.edit_group = None


# ---- save, reopen -----------------------------------------------------------------

def test_the_parameters_survive_save_and_reopen(vp, tmp_path):
    vp.scene.plugin_data[mod.KEY] = {"levels": [
        {"id": "PB", "name": "PB", "z": 0.0},
        {"id": "P1", "name": "P1", "z": 3.0}]}
    a = mod.create_wall(vp, (0, 0), (4, 0), thickness=0.2, height=3.0)
    b = mod.create_wall(vp, (4, 0), (4, 3), thickness=0.3, height=2.8,
                        level="P1", base_offset=0.05)
    path = tmp_path / "casa.igz"
    igz.save_scene(vp.scene, path)

    again = Scene()
    igz.load_into(again, path)
    # project data (plugin_data)
    assert again.plugin_data[mod.KEY]["levels"] == \
        vp.scene.plugin_data[mod.KEY]["levels"]
    # per-wall parameters (group.ext)
    got = {g.name: g for g in again.groups}
    assert set(got) == {a.name, b.name}
    assert got[a.name].ext[mod.KEY] == a.ext[mod.KEY]
    assert got[b.name].ext[mod.KEY] == b.ext[mod.KEY]
    assert got[b.name].ifc["class"] == "IfcWall"
    # …and the reopened document is still editable through the parameters:
    vp2 = FakeViewport()
    vp2.scene = again
    vp2.history = History(again)
    got[b.name].ext[mod.KEY]["height"] = 2.0
    assert mod.update_3d(vp2) == 2
    assert _bbox(got[b.name])[5] == pytest.approx(3.05 + 2.0)
    assert _volume(got[a.name]) == pytest.approx(_volume(a))


def test_a_reopened_wall_regenerates_to_the_same_geometry(vp, tmp_path):
    a = mod.create_wall(vp, (1, 2), (5, 6), thickness=0.2, height=3.0)
    path = tmp_path / "w.igz"
    igz.save_scene(vp.scene, path)
    again = Scene()
    igz.load_into(again, path)
    vp2 = FakeViewport()
    vp2.scene, vp2.history = again, History(again)
    g = again.groups[0]
    mod.update_3d(vp2)
    assert _geom(g) == _geom(a)


def test_undo_after_reopen_does_not_touch_the_saved_parameters(vp, tmp_path):
    a = mod.create_wall(vp, (0, 0), (4, 0))
    path = tmp_path / "w.igz"
    igz.save_scene(vp.scene, path)
    again = Scene()
    igz.load_into(again, path)
    vp2 = FakeViewport()
    vp2.scene, vp2.history = again, History(again)
    again.groups[0].ext[mod.KEY]["height"] = 2.0
    mod.update_3d(vp2)
    vp2.history.undo()
    assert again.groups[0].ext[mod.KEY]["height"] == 2.0    # the edit, not the update


# ---- the tool ---------------------------------------------------------------------

def _ctx(vp, x, y, z=0.0):
    return ToolContext(viewport=vp, world=V(x, y, z), screen=None,
                       modifiers=0, snap=SnapResult(V(x, y, z), "none"))


def test_two_clicks_make_a_wall(vp):
    tool = mod.WallTool(app=None)
    tool.thickness, tool.height = 0.2, 3.0
    tool.on_activate(vp)
    tool.on_click(_ctx(vp, 0, 0))
    assert vp.scene.groups == []                   # the first click only anchors
    tool.on_hover(_ctx(vp, 2, 0))
    assert tool.rubber_band_lines() == [(V(0, 0, 0), V(2, 0, 0))]
    tool.on_click(_ctx(vp, 4, 0))
    assert len(vp.scene.groups) == 1
    assert _volume(vp.scene.groups[0]) == pytest.approx(4 * 0.2 * 3.0)
    assert tool.rubber_band_lines() == []          # ready for the next wall


def test_a_typed_length_follows_the_rubber_band(vp):
    tool = mod.WallTool(app=None)
    tool.on_activate(vp)
    tool.on_click(_ctx(vp, 1, 1))
    tool.on_hover(_ctx(vp, 1, 5))                  # pointing +Y
    assert tool.on_value(vp, 2.5) is True
    rec = vp.scene.groups[0].ext[mod.KEY]
    assert rec["a"] == [1, 1] and rec["b"] == pytest.approx([1, 3.5])


def test_a_typed_length_before_the_first_click_is_not_ours(vp):
    tool = mod.WallTool(app=None)
    assert tool.on_value(vp, 3.0) is False
    assert tool.on_value(vp, (1.0, 2.0, 3.0)) is False


def test_escape_drops_the_first_point(vp):
    tool = mod.WallTool(app=None)
    tool.on_click(_ctx(vp, 0, 0))
    tool.on_cancel(vp)
    tool.on_click(_ctx(vp, 3, 0))                  # a NEW first point
    assert vp.scene.groups == []


def test_a_zero_length_second_click_makes_nothing(vp):
    tool = mod.WallTool(app=None)
    tool.on_click(_ctx(vp, 1, 1))
    tool.on_click(_ctx(vp, 1, 1))
    assert vp.scene.groups == []


# ---- inside the real application ----------------------------------------------------

@pytest.fixture
def win(tmp_path, monkeypatch):
    shutil.copy(PLUGIN, tmp_path / "bim_proto.py")
    monkeypatch.setattr(extensions, "plugin_dirs", lambda: [tmp_path])
    from views.main_window import MainWindow
    w = MainWindow()
    yield w
    w._saved_version = w.viewport.scene.version     # no "save?" modal
    w.close()


def _live():
    return next(m for n, m in sys.modules.items()
                if n.endswith("ingetrazo_plugin_bim_proto"))


def test_the_plugin_loads_and_adds_its_menu(win):
    assert "bim_proto" not in getattr(win, "_extension_load_errors", {})
    titles = [a.menu().title() for a in win._ext_menu.actions() if a.menu()]
    assert "BIM proto" in titles
    sub = next(a.menu() for a in win._ext_menu.actions()
               if a.menu() and a.menu().title() == "BIM proto")
    assert [a.text() for a in sub.actions()] == ["Wall", "Update 3D", "Join corners", "Sills", "Door leaves"]
    # WallTool needs `app`, so the loader leaves it out of the one-shot entries
    assert not any(k.startswith("plugin_bim_proto_") for k in win._tools)


def test_the_menu_arms_the_wall_tool_and_clicks_draw_a_wall(win):
    live = _live()
    vp = win.viewport
    sub = next(a.menu() for a in win._ext_menu.actions()
               if a.menu() and a.menu().title() == "BIM proto")
    sub.actions()[0].trigger()                      # Wall
    assert isinstance(vp.active_tool, live.WallTool)
    vp.active_tool.on_click(_ctx(vp, 0, 0))
    vp.active_tool.on_click(_ctx(vp, 3, 0))
    assert len(live.walls(vp.scene)) == 1           # (a new window also holds the scale figure)
    sub.actions()[1].trigger()                      # Update 3D
    assert len(vp.history.undo_stack) == 2          # draw + update
    vp.set_active_tool(None)


def test_the_browser_row_says_what_the_group_is(win):
    live = _live()
    g = live.create_wall(win.viewport, (0, 0), (4, 0), thickness=0.2,
                         height=3.0)
    info = live._outliner_kind(g)
    assert info["type"] == "Wall"
    assert ("Height", "3.00 m") in info["props"]
    from core.group import Group
    assert live._outliner_kind(Group(name="x")) is None


# ---- the project tree panel ---------------------------------------------------------

def test_the_tree_model_groups_walls_by_level_and_lists_views_and_sheets(vp):
    from core.composition import Composicion
    from core.saved_views import SavedView
    vp.scene.plugin_data[mod.KEY] = {"levels": [
        {"id": "P1", "name": "P1", "z": 3.0}, {"id": "PB", "name": "PB", "z": 0.0}]}
    mod.create_wall(vp, (0, 0), (4, 0), level="PB")
    mod.create_wall(vp, (0, 0), (2, 0), level="P1")
    vp.scene.saved_views.append(SavedView("Planta PB"))
    vp.scene.compositions.append(Composicion(name="A-101"))
    levels, views, sheets = mod.project_tree_model(vp.scene)
    assert [lbl for lbl, _ in levels[1]] == ["PB  (+0.00 m)", "P1  (+3.00 m)"]  # by height
    assert [k for k, _ in levels[1][0][1]] == ["Wall 1 — 4.00 m"]
    assert [k for k, _ in levels[1][1][1]] == ["Wall 2 — 2.00 m"]
    assert views[1] == [("Planta PB", [])] and sheets[1] == [("A-101", [])]


def test_the_panel_is_a_tab_of_the_tray_and_follows_the_document(win):
    live = _live()
    dock = win.extension_panels()["extension_bim_proto"]
    tree = dock.widget()
    top = lambda: [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]
    assert top() == ["Levels", "Views", "Sheets"]
    live.create_wall(win.viewport, (0, 0), (3, 0))
    pb = tree.topLevelItem(0).child(0)
    assert pb.childCount() == 1                      # the new wall appeared
    win._on_undo()                      # FRAGILE: the Ctrl+Z slot, which notifies the panels
    assert tree.topLevelItem(0).child(0).childCount() == 0


# ---- importing a plan: rectangles -> walls, in one step -----------------------------

def _rect(cx, cy, length, thick, angle_deg, mirror=False):
    import math
    c, s = math.cos(math.radians(angle_deg)), math.sin(math.radians(angle_deg))
    pts = [(-length / 2, -thick / 2), (length / 2, -thick / 2),
           (length / 2, thick / 2), (-length / 2, thick / 2)]
    out = [(cx + x * c - y * s, cy + x * s + y * c) for x, y in pts]
    return list(reversed(out)) if mirror else out


@pytest.mark.parametrize("angle", [0, 30, 90, 137, 270])
@pytest.mark.parametrize("mirror", [False, True])
def test_a_rectangle_in_any_orientation_becomes_its_wall(angle, mirror):
    import math
    rec = mod.wall_from_rectangle(_rect(5, 7, 4.0, 0.2, angle, mirror))
    assert rec["thickness"] == pytest.approx(0.2)
    assert math.dist(rec["a"], rec["b"]) == pytest.approx(4.0)
    assert ((rec["a"][0] + rec["b"][0]) / 2, (rec["a"][1] + rec["b"][1]) / 2) \
        == pytest.approx((5, 7))


def test_a_square_pillar_gets_an_axis_and_its_side_as_thickness():
    rec = mod.wall_from_rectangle(_rect(0, 0, 0.4, 0.4, 0))
    assert rec["thickness"] == pytest.approx(0.4)


def test_a_non_rectangular_polygon_is_refused():
    l_shape = [(0, 0), (4, 0), (4, 1), (1, 1), (1, 3), (0, 3)]
    with pytest.raises(mod.BimProtoError):
        mod.wall_from_rectangle(l_shape)
    with pytest.raises(mod.BimProtoError):
        mod.wall_from_rectangle([(0, 0), (1, 1)])


def test_many_walls_are_created_in_one_undo_step(vp):
    recs = [mod.wall_from_rectangle(_rect(i * 2, 0, 1.5, 0.15, 0))
            for i in range(25)]
    made = mod.create_walls(vp, recs, height=2.8)
    assert len(made) == 25 and len(mod.walls(vp.scene)) == 25
    assert len(vp.history.undo_stack) == 1                  # NOT one per wall
    assert _volume(made[0]) == pytest.approx(1.5 * 0.15 * 2.8, rel=1e-4)
    assert vp.history.undo()
    assert vp.scene.groups == [] and mod.KEY not in vp.scene.plugin_data
    assert vp.history.redo()
    assert len(mod.walls(vp.scene)) == 25


def test_one_bad_record_cancels_the_whole_batch(vp):
    good = mod.wall_from_rectangle(_rect(0, 0, 1.5, 0.15, 0))
    bad = {"a": [0, 0], "b": [0, 0], "thickness": 0.15}      # no length
    assert mod.create_walls(vp, [good, bad]) == []
    assert vp.scene.groups == [] and vp.history.undo_stack == []
    assert vp.history.last_error


# ---- openings between aligned wall pieces ---------------------------------------------

def _w(a, b, t=0.15):
    return {"a": list(a), "b": list(b), "thickness": t}


def test_two_aligned_pieces_with_a_door_sized_gap_make_one_opening():
    gaps = mod.wall_gaps([_w((0, 0), (2, 0)), _w((2.9, 0), (5, 0))])
    assert len(gaps) == 1
    g = gaps[0]
    assert g["width"] == pytest.approx(0.9)
    assert g["a"] == pytest.approx([2.0, 0.0]) and g["b"] == pytest.approx([2.9, 0.0])
    assert g["between"] == (0, 1)


def test_the_opening_is_found_whatever_the_direction_and_order():
    for a, b in (((0, 0), (2, 0)), ((2, 0), (0, 0))):
        gaps = mod.wall_gaps([_w((2.9, 0), (5, 0)), _w(a, b)])
        assert len(gaps) == 1 and gaps[0]["width"] == pytest.approx(0.9)


def test_a_diagonal_opening_and_different_thickness():
    import math
    c, s = math.cos(math.radians(37)), math.sin(math.radians(37))
    gaps = mod.wall_gaps([_w((0, 0), (2 * c, 2 * s), 0.12),
                          _w((2.8 * c, 2.8 * s), (5 * c, 5 * s), 0.2)])
    assert len(gaps) == 1
    assert gaps[0]["width"] == pytest.approx(0.8)
    assert gaps[0]["thickness"] == pytest.approx(0.2)


@pytest.mark.parametrize("other", [
    _w((2.9, 0.5), (5, 0.5)),        # parallel but not on the same line
    _w((2.9, 0), (2.9, 3)),          # not parallel
    _w((1.9, 0), (5, 0)),            # overlaps the first piece
    _w((6.5, 0), (9, 0)),            # gap far too big for a door
    _w((2.1, 0), (5, 0)),            # gap too small to be one
])
def test_no_opening_when_the_pieces_do_not_make_one(other):
    assert mod.wall_gaps([_w((0, 0), (2, 0)), other]) == []


def test_a_crossing_wall_inside_the_gap_is_a_junction_not_an_opening():
    tee = [_w((0, 0), (2, 0)), _w((2.9, 0), (5, 0)), _w((2.45, -2), (2.45, 2))]
    assert mod.wall_gaps(tee) == []


def test_a_header_over_the_opening_goes_up_with_base_offset(vp):
    pieces = [_w((0, 0), (2, 0)), _w((2.9, 0), (5, 0))]
    mod.create_walls(vp, pieces, height=2.8)
    g = mod.wall_gaps(pieces)[0]
    head = mod.create_walls(vp, [g], height=0.7, base_offset=2.1)
    _, _, z0, x1, _, z1 = _bbox(head[0])
    assert (z0, z1) == pytest.approx((2.1, 2.8))
    assert (_bbox(head[0])[0], x1) == pytest.approx((2.0, 2.9))
    assert len(vp.history.undo_stack) == 2                 # walls, then headers


def test_a_stub_shorter_than_a_wall_does_not_make_an_opening():
    stub = [_w((0, 0), (2, 0)), _w((2.9, 0), (3.0, 0))]       # 10 cm piece
    assert mod.wall_gaps(stub) == []
    assert len(mod.wall_gaps(stub, min_piece=0.05)) == 1


# ---- walls with real openings -----------------------------------------------------------

def _wall_with(vp, openings, length=6.0, height=2.8, thick=0.2):
    g = mod.create_walls(vp, [{"a": [0, 0], "b": [length, 0], "thickness": thick,
                               "openings": openings}], height=height)
    return g[0]


def test_a_door_is_a_notch_and_the_wall_stays_a_closed_solid(vp):
    g = _wall_with(vp, [{"s0": 2.0, "s1": 2.9, "head": 2.1}])
    expected = (6.0 * 2.8 - 0.9 * 2.1) * 0.2
    assert _volume(g) == pytest.approx(expected, rel=1e-4)         # watertight
    xs = [v.position.x() for v in g.mesh.vertices]
    assert min(xs) == pytest.approx(0) and max(xs) == pytest.approx(6.0)


def test_a_window_is_a_hole_with_its_own_reveals(vp):
    g = _wall_with(vp, [{"s0": 1.0, "s1": 2.5, "sill": 0.9, "head": 2.1}])
    expected = (6.0 * 2.8 - 1.5 * 1.2) * 0.2
    assert _volume(g) == pytest.approx(expected, rel=1e-4)
    assert any(f.hole_loops for f in g.mesh.faces)                 # real holes


def test_several_openings_in_one_wall(vp):
    ops = [{"s0": 0.5, "s1": 1.4, "head": 2.1},
           {"s0": 2.0, "s1": 3.2, "sill": 1.0, "head": 2.2},
           {"s0": 4.0, "s1": 4.8, "head": 2.1}]
    g = _wall_with(vp, ops)
    expected = (6.0 * 2.8 - 0.9 * 2.1 - 1.2 * 1.2 - 0.8 * 2.1) * 0.2
    assert _volume(g) == pytest.approx(expected, rel=1e-4)


def test_a_wall_with_openings_on_a_diagonal_and_a_raised_base(vp):
    vp.scene.plugin_data[mod.KEY] = {"levels": [{"id": "PB", "name": "PB", "z": 1.0}]}
    g = mod.create_walls(vp, [{"a": [0, 0], "b": [3, 4], "thickness": 0.15,
                               "openings": [{"s0": 1.0, "s1": 2.0, "head": 2.1}]}],
                         height=2.8)[0]
    assert _volume(g) == pytest.approx((5.0 * 2.8 - 1.0 * 2.1) * 0.15, rel=1e-4)
    zs = [v.position.z() for v in g.mesh.vertices]
    assert (min(zs), max(zs)) == pytest.approx((1.0, 3.8))


@pytest.mark.parametrize("bad", [
    {"s0": 0.0, "s1": 0.9, "head": 2.1},            # touches the end of the wall
    {"s0": 5.5, "s1": 6.0, "head": 2.1},            # touches the other end
    {"s0": 2.0, "s1": 2.9, "head": 2.8},            # reaches the top
    {"s0": 2.0, "s1": 2.9, "sill": 1.0, "head": 0.9},   # head below sill
    {"s0": 2.0, "s1": 2.9, "sill": -0.1, "head": 2.1},  # below the base
    {"s0": 2.0, "s1": 2.9},                          # no head
])
def test_an_opening_that_does_not_fit_is_refused(vp, bad):
    assert mod.create_walls(vp, [{"a": [0, 0], "b": [6, 0], "thickness": 0.2,
                                  "openings": [bad]}]) == []
    assert vp.scene.groups == [] and vp.history.last_error


def test_overlapping_openings_are_refused(vp):
    ops = [{"s0": 1.0, "s1": 2.0, "head": 2.1}, {"s0": 1.9, "s1": 3.0, "head": 2.1}]
    assert mod.create_walls(vp, [{"a": [0, 0], "b": [6, 0], "thickness": 0.2,
                                  "openings": ops}]) == []


def test_adding_a_door_to_an_existing_wall_is_one_undo_step(vp):
    g = mod.create_walls(vp, [{"a": [0, 0], "b": [6, 0], "thickness": 0.2}], height=2.8)[0]
    solid = _volume(g)
    steps = len(vp.history.undo_stack)
    assert mod.edit_walls(vp, [g], openings=[{"s0": 2, "s1": 2.9, "head": 2.1}]) == 1
    assert len(vp.history.undo_stack) == steps + 1
    assert _volume(g) == pytest.approx(solid - 0.9 * 2.1 * 0.2, rel=1e-4)
    assert vp.history.undo()
    assert _volume(g) == pytest.approx(solid, rel=1e-4)
    assert g.ext[mod.KEY]["openings"] == []
    assert vp.history.redo()
    assert len(g.ext[mod.KEY]["openings"]) == 1


def test_openings_survive_save_reopen_and_update_3d(vp, tmp_path):
    g = _wall_with(vp, [{"s0": 2.0, "s1": 2.9, "head": 2.1},
                        {"s0": 4.0, "s1": 5.0, "sill": 0.9, "head": 2.1}])
    path = tmp_path / "porta.igz"
    igz.save_scene(vp.scene, path)
    again = Scene()
    igz.load_into(again, path)
    got = again.groups[0]
    assert got.ext[mod.KEY]["openings"] == g.ext[mod.KEY]["openings"]
    vp2 = FakeViewport()
    vp2.scene, vp2.history = again, History(again)
    got.mesh = mod.Mesh()                                       # geometry lost
    assert mod.update_3d(vp2) == 1
    assert _volume(got) == pytest.approx(_volume(g), rel=1e-6)


def test_merging_the_pieces_around_a_gap_makes_one_wall_with_the_opening():
    pieces = [_w((0, 0), (2, 0)), _w((2.9, 0), (6, 0))]
    gaps = mod.wall_gaps(pieces)
    merged, ignored = mod.merge_gap_walls(pieces, gaps)
    assert ignored == [] and len(merged) == 1
    m = merged[0]
    assert m["a"] == pytest.approx([0, 0]) and m["b"] == pytest.approx([6, 0])
    assert m["openings"] == [{"s0": pytest.approx(2.0), "s1": pytest.approx(2.9),
                              "sill": 0.0, "head": 2.10}]


def test_a_chain_of_three_pieces_and_a_per_gap_head():
    pieces = [_w((0, 0), (2, 0)), _w((2.9, 0), (5, 0)), _w((5.8, 0), (8, 0))]
    gaps = mod.wall_gaps(pieces)
    assert len(gaps) == 2
    gaps[0]["head"] = 2.0
    gaps[1]["head"] = 2.2
    merged, _ = mod.merge_gap_walls(pieces, gaps)
    assert len(merged) == 1
    assert [o["head"] for o in merged[0]["openings"]] == [2.0, 2.2]


def test_pieces_far_apart_in_thickness_are_not_merged():
    pieces = [_w((0, 0), (2, 0), 0.12), _w((2.9, 0), (6, 0), 0.25)]
    gaps = mod.wall_gaps(pieces)
    merged, ignored = mod.merge_gap_walls(pieces, gaps)
    assert len(merged) == 2 and len(ignored) == 1
    assert all(not r.get("openings") for r in merged)


def test_unrelated_pieces_pass_through_untouched():
    pieces = [_w((0, 0), (2, 0)), _w((2.9, 0), (6, 0)), _w((0, 5), (3, 5))]
    merged, _ = mod.merge_gap_walls(pieces, mod.wall_gaps(pieces))
    assert len(merged) == 2
    assert any(r["a"] == [0, 5] and "openings" not in r for r in merged)


def test_overlapping_gaps_around_a_short_piece_become_one_opening():
    # a short stub between two pieces makes the same door show up twice
    pieces = [_w((0, 0), (2, 0)), _w((2.0, 0), (2.4, 0)), _w((3.3, 0), (6, 0))]
    gaps = mod.wall_gaps(pieces, min_piece=0.3)
    two = [dict(g, head=2.1) for g in gaps]
    # force a second, overlapping gap, as the real plan produced
    two.append({"a": [2.0, 0.0], "b": [3.3, 0.0], "width": 1.3, "thickness": 0.15,
                "between": (0, 2), "head": 2.2})
    merged, _ = mod.merge_gap_walls(pieces, two)
    ops = [o for r in merged for o in r.get("openings", [])]
    assert len(ops) == 1
    assert ops[0]["s0"] == pytest.approx(2.0) and ops[0]["s1"] == pytest.approx(3.3)
    assert ops[0]["head"] == 2.2                       # the taller one wins
    assert mod.create_walls(FakeViewport(), merged) != []   # and it builds


# ---- corners: L and T joins ------------------------------------------------------

def _footprint(rec):
    """The wall's footprint as four (x, y) corners."""
    (ax, ay), (bx, by), h = rec["a"], rec["b"], rec["thickness"] / 2
    ln = ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5
    nx, ny = -(by - ay) / ln, (bx - ax) / ln
    return [(ax - nx * h, ay - ny * h), (bx - nx * h, by - ny * h),
            (bx + nx * h, by + ny * h), (ax + nx * h, ay + ny * h)]


def _cover(records, x0, y0, x1, y1, step=0.01):
    """How many footprints cover each sample point, over a window."""
    rects = [_footprint(r) for r in records]
    counts = {}
    nx, ny = int((x1 - x0) / step), int((y1 - y0) / step)
    for i in range(nx):
        for j in range(ny):
            p = (x0 + (i + 0.5) * step, y0 + (j + 0.5) * step)
            n = sum(mod._pip(p, rc) for rc in rects)
            counts[n] = counts.get(n, 0) + 1
    return counts


def test_an_l_corner_is_filled_once_with_no_gap_and_no_overlap():
    a = _w((0, 0), (3.9, 0), 0.2)       # the longer one wins the corner
    b = _w((4, 0.05), (4, 3), 0.1)
    new, joins = mod.join_walls([a, b])
    assert [j["kind"] for j in joins] == ["L"]
    # the exact L: A to the outside face of B, B from the inside face of A
    assert new[0]["b"] == pytest.approx([4.05, 0.0])
    assert new[1]["a"] == pytest.approx([4.0, 0.1])
    c = _cover(new, 3.5, -0.3, 4.4, 0.6)
    assert set(c) == {0, 1}              # never covered twice
    # the corner square (outer corner at (4.05, -0.1)) is solid
    assert mod._pip((4.04, -0.09), _footprint(new[0]))


def test_four_walls_drawn_by_axis_make_a_clean_room():
    t = 0.2
    room = [_w((0, 0), (4, 0), t), _w((4, 0), (4, 3), t),
            _w((4, 3), (0, 3), t), _w((0, 3), (0, 0), t)]
    new, joins = mod.join_walls(room)
    assert len(joins) == 4 and {j["kind"] for j in joins} == {"L"}
    area = sum(mod._len(r) * r["thickness"] for r in new)
    assert area == pytest.approx((4 + t) * (3 + t) - (4 - t) * (3 - t))
    assert set(_cover(new, -0.3, -0.3, 4.3, 3.3, 0.02)) == {0, 1}


def test_a_t_joint_stops_the_branch_at_the_face_of_the_through_wall():
    through = _w((0, 0), (6, 0), 0.2)
    branch = _w((3, 0.02), (3, 4), 0.1)      # ends a hair off the axis
    new, joins = mod.join_walls([through, branch])
    assert [j["kind"] for j in joins] == ["T"]
    assert new[0]["a"] == [0, 0] and new[0]["b"] == [6, 0]   # untouched
    assert new[1]["a"] == pytest.approx([3.0, 0.1])
    assert set(_cover(new, 2.5, -0.3, 3.5, 0.6)) == {0, 1}


def test_walls_that_do_not_meet_are_left_alone():
    parallel = [_w((0, 0), (4, 0)), _w((0, 0.4), (4, 0.4))]
    far = [_w((0, 0), (4, 0)), _w((4.6, 0), (4.6, 3))]       # 0.6 m away
    for recs in (parallel, far):
        new, joins = mod.join_walls(recs)
        assert joins == [] and [r["a"] for r in new] == [r["a"] for r in recs]


def test_only_walls_on_the_same_level_are_joined():
    a = dict(_w((0, 0), (4, 0)), level="PB")
    b = dict(_w((4, 0), (4, 3)), level="P1")
    assert mod.join_walls([a, b])[1] == []


def test_a_door_keeps_its_place_when_its_wall_end_moves():
    a = dict(_w((0, 0), (4, 0), 0.2), openings=[
        {"s0": 1.0, "s1": 1.9, "sill": 0.0, "head": 2.1}])
    b = _w((0, 3), (0, 0.0), 0.1)             # meets A's start: A is the longer
    new, _ = mod.join_walls([a, b])
    shift = new[0]["a"][0] - 0.0              # A's start moved to x = -0.05
    o = new[0]["openings"][0]
    assert shift == pytest.approx(-0.05)
    assert new[0]["a"][0] + o["s0"] == pytest.approx(1.0)    # same world place
    assert new[0]["a"][0] + o["s1"] == pytest.approx(1.9)


def test_join_corners_is_one_undo_step_and_the_volume_is_exact(vp):
    t, h = 0.2, 2.8
    recs = [_w((0, 0), (4, 0), t), _w((4, 0), (4, 3), t),
            _w((4, 3), (0, 3), t), _w((0, 3), (0, 0), t)]
    made = mod.create_walls(vp, recs, height=h)
    before = [_geom(g) for g in made]
    steps = len(vp.history.undo_stack)
    assert mod.join_corners(vp) == 4
    assert len(vp.history.undo_stack) == steps + 1
    vol = sum(_volume(g) for g in made)
    assert vol == pytest.approx(((4 + t) * (3 + t) - (4 - t) * (3 - t)) * h, rel=1e-4)
    assert mod.join_corners(vp) == 0                         # nothing left to join
    vp.history.undo()
    assert [_geom(g) for g in made] == before                # one Ctrl+Z


def test_joined_corners_survive_save_and_reopen(vp, tmp_path):
    mod.create_walls(vp, [_w((0, 0), (4, 0), 0.2), _w((4, 0), (4, 3), 0.2)])
    mod.join_corners(vp)
    igz.save_scene(vp.scene, tmp_path / "c.igz")
    sc = Scene()
    igz.load_into(sc, tmp_path / "c.igz")
    got = sorted(tuple(map(tuple, (mod.wall_record(g)["a"], mod.wall_record(g)["b"])))
                 for g in mod.walls(sc))
    want = sorted(tuple(map(tuple, (mod.wall_record(g)["a"], mod.wall_record(g)["b"])))
                  for g in mod.walls(vp.scene))
    assert got == want


# ---- facade frames: mullions and panels ------------------------------------------

def _frame(**kw):
    rec = {"a": (0, 0), "b": (3, 0), "height": 2.8, "level": "PB",
           "divisions": [0.025, 1.0, 2.0, 2.975], "mullion": 0.05}
    rec.update(kw)
    return rec


def test_a_frame_is_closed_solids_with_the_expected_volume(vp):
    (g,) = mod.create_frames(vp, [_frame()])
    posts = 4 * 0.05 * 0.06 * 2.8
    panels = (3 - 4 * 0.05) * 0.01 * 2.8
    assert _volume(g) == pytest.approx(posts + panels, rel=1e-4)
    assert g.ifc["class"] == "IfcWindow"
    x0, y0, z0, x1, y1, z1 = _bbox(g)
    assert (x0, x1) == pytest.approx((0, 3)) and (z0, z1) == pytest.approx((0, 2.8))


def test_a_railing_is_a_single_glass_sheet(vp):
    (g,) = mod.create_frames(vp, [{"a": (0, 0), "b": (1.5, 0), "height": 1.1,
                                   "level": "PB", "depth": 0.04,
                                   "panel_thickness": 0.04,
                                   "ifc": "IfcRailing"}])
    assert g.ifc["class"] == "IfcRailing"
    assert _volume(g) == pytest.approx(1.5 * 0.04 * 1.1, rel=1e-4)
    assert g.name.startswith("Railing")


def test_a_frame_without_panels_is_only_mullions(vp):
    (g,) = mod.create_frames(vp, [_frame(panel="none")])
    assert _volume(g) == pytest.approx(4 * 0.05 * 0.06 * 2.8, rel=1e-4)


@pytest.mark.parametrize("bad", [
    {"divisions": [0.01]},                  # the mullion would stick out the end
    {"divisions": [1.0, 1.02]},             # mullions overlap
    {"panel": "wood"},
    {"panel_thickness": 0.2},               # thicker than the frame
    {"ifc": "IfcBanana"},
    {"height": 0}, {"mullion": 0}, {"b": (0, 0)},
])
def test_an_invalid_frame_is_refused_and_nothing_changes(vp, bad):
    assert mod.create_frames(vp, [_frame(**bad)]) == []
    assert vp.scene.groups == [] and vp.history.last_error


def test_frames_are_created_in_one_undo_step_and_update_3d_rebuilds_them(vp):
    mod.create_frames(vp, [_frame(), _frame(a=(0, 5), b=(3, 5))])
    assert len(vp.history.undo_stack) == 1
    g = vp.scene.groups[0]
    good = _geom(g)
    g.mesh = mod.Mesh()                                       # break the geometry
    assert mod.update_3d(vp) == 2
    assert _geom(g) == good
    assert len(vp.history.undo_stack) == 2                    # one step for both
    vp.history.undo()
    assert _geom(g) == []                                     # back to the broken one


def test_frames_survive_save_and_reopen_with_their_parameters(vp, tmp_path):
    mod.create_walls(vp, [_w((0, 0), (3, 0))])
    mod.create_frames(vp, [_frame(a=(0, 1), b=(3, 1), panel="mesh")])
    igz.save_scene(vp.scene, tmp_path / "f.igz")
    sc = Scene()
    igz.load_into(sc, tmp_path / "f.igz")
    (fr,) = mod.frames(sc)
    rec = mod.frame_record(fr)
    assert rec["panel"] == "mesh" and rec["divisions"] == [0.025, 1.0, 2.0, 2.975]
    assert fr.ifc["class"] == "IfcWindow"
    assert _volume(fr) == pytest.approx(_volume(mod.frames(vp.scene)[0]))
    assert len(mod.walls(sc)) == 1                  # walls and frames stay apart


def test_the_tree_and_the_outliner_know_the_frames(vp):
    mod.create_frames(vp, [_frame()])
    levels, _views, _sheets = mod.project_tree_model(vp.scene)
    (pb,) = [lv for lv in levels[1] if lv[0].startswith("PB")]
    assert [label.split(" — ")[0] for label, _ in pb[1]] == ["Frame 1"]
    info = mod._outliner_kind(vp.scene.groups[0])
    assert info["type"] == "Frame" and ("Mullions", "4") in info["props"]


def test_joining_is_the_same_whether_the_ends_fall_short_or_overshoot():
    t = 0.2
    short = [_w((0, 0), (3.9, 0), t), _w((4, 0.3), (4, 3), t)]
    over = [_w((0, 0), (4.1, 0), t), _w((4, -0.1), (4, 3), t)]
    a, _ = mod.join_walls(short)
    b, _ = mod.join_walls(over)
    twice, _ = mod.join_walls(b)
    for x, y in zip(a, b):
        assert x["a"] == pytest.approx(y["a"]) and x["b"] == pytest.approx(y["b"])
    for x, y in zip(b, twice):                       # and joining again changes nothing
        assert x["a"] == pytest.approx(y["a"]) and x["b"] == pytest.approx(y["b"])


def test_a_stub_that_a_join_would_erase_is_left_alone():
    # real plans have short pieces squeezed between two walls: joining must not
    # shorten them to nothing (that crashed on the first real floor plan)
    wall = _w((0, 0), (6, 0), 0.2)
    stub = _w((3, 0.05), (3, 0.11), 0.1)             # 6 cm long, T onto the wall
    new, _ = mod.join_walls([wall, stub])
    assert new[1]["a"] == [3, 0.05] and new[1]["b"] == [3, 0.11]
    mod.normalize_record(dict(new[1], level="PB", height=2.8))   # still valid


def test_the_far_end_of_a_short_piece_does_not_join_what_is_behind_it():
    wall = _w((0, 0), (6, 0), 0.2)
    stub = _w((3, 0.05), (3, 0.25), 0.1)             # 0.2 m long
    new, joins = mod.join_walls([wall, stub])
    assert len(joins) == 1
    assert new[1]["a"] == pytest.approx([3, 0.1]) and new[1]["b"] == [3, 0.25]


def test_a_mullion_touching_the_end_leaves_no_sliver_panel(vp):
    # real data: the post centre is 0.0250000001 from the end, not 0.025
    (g,) = mod.create_frames(vp, [_frame(divisions=[0.0250000001, 1.0, 2.9749999])])
    assert len(g.mesh.faces) == 6 * (3 + 2)           # 3 posts + the 2 real panels
    assert _volume(g) is not None


# ---- rails (travessas) -------------------------------------------------------------

def test_rails_split_the_panels_and_every_piece_stays_a_closed_solid(vp):
    rails = [{"z": 0.0, "height": 0.05}, {"z": 1.0, "height": 0.05},
             {"z": 2.75, "height": 0.05}]
    (g,) = mod.create_frames(vp, [_frame(b=(2, 0), divisions=[0.025, 1.0, 1.975],
                                         rails=rails)])
    cell = 0.925                                 # width between two posts
    posts = 3 * 0.05 * 0.06 * 2.8
    rail_v = 2 * 3 * cell * 0.058 * 0.05         # 2 cells x 3 rails (1 mm recess a face)
    panel_h = 2.8 - 3 * 0.05
    panels = 2 * cell * 0.01 * panel_h
    assert _volume(g) == pytest.approx(posts + rail_v + panels, rel=1e-4)
    assert len(g.mesh.faces) == 6 * (3 + 2 * 3 + 2 * 2)   # posts + rails + 2 panels/cell


@pytest.mark.parametrize("bad", [
    [{"z": 1.0, "height": 0.05}, {"z": 1.02, "height": 0.05}],     # overlap
    [{"z": 2.78, "height": 0.05}],                                 # above the top
    [{"z": 1.0, "height": 0}],
])
def test_bad_rails_are_refused(vp, bad):
    assert mod.create_frames(vp, [_frame(rails=bad)]) == []


# ---- sills (peitoris) ----------------------------------------------------------------

def _window_wall(vp):
    return _wall_with(vp, [
        {"s0": 1.0, "s1": 2.2, "sill": 0.9, "head": 2.1},
        {"s0": 3.5, "s1": 4.4, "sill": 0.0, "head": 2.1}])


def test_a_sill_sits_outside_the_wall_face_under_the_window(vp):
    _window_wall(vp)
    (s,) = mod.create_sills(vp)                       # only the window, not the door
    x0, y0, z0, x1, y1, z1 = _bbox(s)
    assert (x0, x1) == pytest.approx((0.97, 2.23))    # 3 cm overhang each side
    assert (y0, y1) == pytest.approx((0.1, 0.15))     # from the face, 5 cm out
    assert (z0, z1) == pytest.approx((0.87, 0.9))
    assert _volume(s) == pytest.approx(1.26 * 0.05 * 0.03, rel=1e-4)
    assert s.ifc["class"] == "IfcMember" and s.name.startswith("Sill")
    assert mod.wall_record(s) is None and mod.element_record(s)["kind"] == "sill"


def test_a_sill_can_go_to_the_other_side_and_is_one_undo_step(vp):
    _window_wall(vp)
    steps = len(vp.history.undo_stack)
    (s,) = mod.create_sills(vp, side=-1, projection=0.08)
    assert len(vp.history.undo_stack) == steps + 1
    _, y0, _, _, y1, _ = _bbox(s)
    assert (y0, y1) == pytest.approx((-0.18, -0.1))
    vp.history.undo()
    assert s not in vp.scene.groups


def test_no_windows_no_sills(vp):
    mod.create_walls(vp, [_w((0, 0), (4, 0))])
    assert mod.create_sills(vp) == []


def test_a_sill_that_does_not_fit_is_refused(vp):
    _window_wall(vp)
    assert mod.create_sills(vp, overhang=2.0) == []
    assert len(mod.elements(vp.scene)) == 1           # only the wall


# ---- door leaves (folhas de porta) -------------------------------------------------------

def test_a_closed_leaf_fills_the_door_with_a_small_clearance(vp):
    _window_wall(vp)
    (lf,) = mod.create_leaves(vp)                     # only the door
    x0, y0, z0, x1, y1, z1 = _bbox(lf)
    assert (x0, x1) == pytest.approx((3.51, 4.39))
    assert (y0, y1) == pytest.approx((-0.02, 0.02))
    assert (z0, z1) == pytest.approx((0.01, 2.09))
    assert _volume(lf) == pytest.approx(0.88 * 0.04 * 2.08, rel=1e-4)
    assert lf.ifc["class"] == "IfcDoor"


@pytest.mark.parametrize("hinge,side,hx,sign", [
    ("a", 1, 3.51, 1), ("b", 1, 4.39, 1), ("a", -1, 3.51, -1)])
def test_an_open_leaf_swings_about_its_hinge(vp, hinge, side, hx, sign):
    _window_wall(vp)
    (lf,) = mod.create_leaves(vp, angle=90, hinge=hinge, side=side)
    x0, y0, _, x1, y1, _ = _bbox(lf)
    assert (x0, x1) == pytest.approx((hx - 0.02, hx + 0.02))   # still on the hinge line
    swung = (y0, y1)
    assert max(abs(v) for v in swung) == pytest.approx(0.88 + 0.0, abs=0.03)
    assert all(v * sign >= -0.021 for v in swung)              # to the chosen side
    assert _volume(lf) == pytest.approx(0.88 * 0.04 * 2.08, rel=1e-4)


@pytest.mark.parametrize("bad", [{"angle": 200}, {"clearance": 0.6},
                                 {"hinge": "x"}, {"side": 0}])
def test_a_bad_leaf_is_refused_and_nothing_changes(vp, bad):
    _window_wall(vp)
    n = len(vp.scene.groups)
    assert mod.create_leaves(vp, **bad) == []
    assert len(vp.scene.groups) == n


def test_sills_and_leaves_are_rebuilt_by_update_3d_and_survive_reopening(vp, tmp_path):
    _window_wall(vp)
    mod.create_sills(vp)
    mod.create_leaves(vp, angle=45)
    lf = [g for g in vp.scene.groups if mod.element_record(g)["kind"] == "leaf"][0]
    good = _geom(lf)
    lf.mesh = mod.Mesh()
    steps = len(vp.history.undo_stack)
    assert mod.update_3d(vp) == 3                     # wall + sill + leaf
    assert len(vp.history.undo_stack) == steps + 1
    assert _geom(lf) == good
    igz.save_scene(vp.scene, tmp_path / "e.igz")
    sc = Scene()
    igz.load_into(sc, tmp_path / "e.igz")
    kinds = sorted(mod.element_record(g)["kind"] for g in sc.groups)
    assert kinds == ["leaf", "sill", "wall"]
    back = [g for g in sc.groups if mod.element_record(g)["kind"] == "leaf"][0]
    assert mod.element_record(back)["angle"] == 45
    assert _volume(back) == pytest.approx(_volume(lf), rel=1e-4)


# ---- detailed doors: frame, trim, swing arc, sliding glass ------------------------------

DOOR = {"style": "swing", "frame": True, "trim": True, "arc": True,
        "thickness": 0.035, "clearance": 0.002}


def _door_wall(vp, wall_t=0.12, s0=1.0, s1=1.87, head=2.1):
    return mod.create_walls(vp, [{"a": [0, 0], "b": [4, 0], "thickness": wall_t,
                                  "openings": [{"s0": s0, "s1": s1, "sill": 0.0,
                                                "head": head}]}])[0]


def test_a_framed_door_is_closed_solids_with_the_measured_volume(vp):
    _door_wall(vp)
    (d,) = mod.create_leaves(vp, **DOOR)
    D, lt, jw, sd, r, c = 0.12, 0.035, 0.035, 0.010, 1e-3, 0.002
    prof = lt * jw + (D - lt) * (jw + sd)
    jambs = 2 * prof * 2.1
    head = (D - lt) * (0.87 - 2 * (jw + sd)) * (jw + sd - r) \
        + lt * (0.87 - 2 * jw) * (jw - r)
    trims = 4 * (jw - r + 0.025) * 0.012 * (2.1 - jw - r) \
        + 2 * (0.87 + 0.05) * 0.012 * 0.06
    leaf = (0.87 - 2 * jw - 2 * c) * lt * (2.1 - jw - 2 * c)
    assert _volume(d) == pytest.approx(jambs + head + trims + leaf, rel=1e-4)
    assert d.ifc["class"] == "IfcDoor"


def test_the_leaf_closes_against_the_rebate_on_the_swing_side(vp):
    _door_wall(vp)
    (d,) = mod.create_leaves(vp, **DOOR)           # side +1, hinge a, closed
    leaf_pts = [v.position for v in d.mesh.vertices
                if 0.9 < v.position.x() < 1.95 and 0.0 < v.position.z() < 2.1
                and abs(v.position.y() - 0.0425) < 0.0006 + 0.0175]
    ys = sorted({round(p.y(), 4) for p in leaf_pts})
    assert 0.025 in ys and 0.06 in ys              # leaf: D/2 - 0.035 .. D/2


def test_the_swing_arc_is_drawn_as_edges_and_adds_no_faces(vp):
    _door_wall(vp)
    (plain,) = mod.create_leaves(vp, **dict(DOOR, arc=False))
    (arc,) = mod.create_leaves(vp, **DOOR)
    assert len(arc.mesh.edges) - len(plain.mesh.edges) == 24
    assert len(arc.mesh.faces) == len(plain.mesh.faces)


def test_a_door_opened_90_degrees_swings_on_its_hinge_side(vp):
    _door_wall(vp)
    (d,) = mod.create_leaves(vp, **dict(DOOR, angle=90, side=-1, hinge="b",
                                        frame=False, trim=False, arc=False))
    x0, y0, _, x1, y1, _ = _bbox(d)
    assert x0 == pytest.approx(1.87 - 0.002 - 0.0175) and x1 == pytest.approx(1.87 - 0.002 + 0.0175)
    assert y0 == pytest.approx(-0.87 + 0.004, abs=0.03) and y1 <= 0.0176


def test_a_sliding_glass_door_has_a_sash_with_glass(vp):
    _door_wall(vp, wall_t=0.15, s0=1.0, s1=1.7)
    (d,) = mod.create_leaves(vp, style="sliding", thickness=0.035, clearance=0.002,
                             t_offset=-0.0925 + 0.0)
    st, c, gl = 0.05, 0.002, 0.01
    w, h = 0.70 - 2 * c, 2.1 - 2 * c
    expect = 2 * st * h * 0.035 + 2 * (w - 2 * st) * st * (0.035 - 2e-3) \
        + (w - 2 * st) * (h - 2 * st) * gl
    assert _volume(d) == pytest.approx(expect, rel=1e-4)
    _, y0, _, _, y1, _ = _bbox(d)
    assert (y0, y1) == pytest.approx((-0.11, -0.075))      # as measured in the DWG


def test_a_sliding_door_parks_to_the_hinge_side_when_open(vp):
    _door_wall(vp, wall_t=0.15, s0=1.0, s1=1.7)
    (d,) = mod.create_leaves(vp, style="sliding", thickness=0.035, clearance=0.0,
                             open=0.5, hinge="a")
    x0, _, _, x1, _, _ = _bbox(d)
    assert (x0, x1) == pytest.approx((0.65, 1.35))


def test_each_door_can_have_its_own_side_and_type(vp):
    mod.create_walls(vp, [{"a": [0, 0], "b": [8, 0], "thickness": 0.12, "openings": [
        {"s0": 1.0, "s1": 1.87, "sill": 0.0, "head": 2.1},
        {"s0": 4.0, "s1": 4.7, "sill": 0.0, "head": 2.1}]}])
    made = mod.create_leaves(vp, frame=True, clearance=0.002, overrides=lambda w, i, o: (
        {"style": "sliding", "thickness": 0.035} if i == 1 else {"side": -1}))
    recs = [mod.element_record(g) for g in made]
    assert [r["style"] for r in recs] == ["swing", "sliding"]
    assert recs[0]["side"] == -1


@pytest.mark.parametrize("bad", [
    {"style": "revolving"}, {"open": 1.5}, {"frame": True, "jamb_width": 0.5},
    {"frame": True, "thickness": 0.5}, {"style": "sliding", "stile": 0.5},
    {"style": "sliding", "glass_thickness": 0.2}])
def test_bad_door_details_are_refused(vp, bad):
    _door_wall(vp)
    n = len(vp.scene.groups)
    assert mod.create_leaves(vp, **bad) == []
    assert len(vp.scene.groups) == n


def test_detailed_doors_survive_save_reopen_and_update_3d(vp, tmp_path):
    _door_wall(vp)
    (d,) = mod.create_leaves(vp, **DOOR)
    good = _geom(d)
    d.mesh = mod.Mesh()
    mod.update_3d(vp)
    assert _geom(d) == good
    igz.save_scene(vp.scene, tmp_path / "d.igz")
    sc = Scene()
    igz.load_into(sc, tmp_path / "d.igz")
    back = [g for g in sc.groups if mod.element_record(g)["kind"] == "leaf"][0]
    rec = mod.element_record(back)
    assert rec["frame"] and rec["trim"] and rec["arc"] and rec["jamb_width"] == 0.035
    assert _volume(back) == pytest.approx(_volume(d), rel=1e-4)


# ---- library blocks (SketchUp-style components) placed on the openings ------------------

def _block_library(tmp_path, suffix=".igz"):
    """A one-component library: a small asymmetric box (x 0..0.3, y 0..0.1, z 0..2)
    named «PORTA_80», so orientation mistakes show up."""
    from PySide6.QtGui import QMatrix4x4
    from core.group import Group
    mesh = mod.Mesh()
    mod._add_box(mesh, (0, 0), (1, 0), (0, 1), 0.0, 0.3, 0.0, 0.1, 0.0, 2.0)
    g = Group(mesh, name="PORTA_80")
    g.xform = QMatrix4x4()                       # a component instance
    sc = Scene()
    sc.groups.append(g)
    path = tmp_path / f"lib{suffix}"
    if suffix == ".skp":
        from formats import skp_out
        skp_out.save_skp(sc, path)
    else:
        igz.save_scene(sc, path)
    return mod.load_block_library(path)


def _world_bbox(group):
    from core.group import world_mesh
    pts = [v.position for v in world_mesh(group).vertices]
    return (min(p.x() for p in pts), min(p.y() for p in pts), min(p.z() for p in pts),
            max(p.x() for p in pts), max(p.y() for p in pts), max(p.z() for p in pts))


def _one_door(vp, a=(0, 0), b=(4, 0)):
    return mod.create_walls(vp, [{"a": list(a), "b": list(b), "thickness": 0.12,
                                  "openings": [{"s0": 1.5, "s1": 2.5, "sill": 0.0,
                                                "head": 2.1}]}])[0]


@pytest.mark.parametrize("suffix", [".igz", ".skp"])
def test_the_library_comes_back_by_component_name(tmp_path, suffix):
    lib = _block_library(tmp_path, suffix)
    assert list(lib) == ["PORTA_80"] and len(lib["PORTA_80"].mesh.faces) == 6


def test_a_block_lands_on_the_opening_by_the_convention(vp, tmp_path):
    lib = _block_library(tmp_path)
    _one_door(vp)
    (b,) = mod.create_blocks(vp, lib, lambda w, i, o: {"block": "PORTA_80"})
    # origin at the opening centre (s = 2), +X along the wall, +Y to the left of it
    assert _world_bbox(b) == pytest.approx((2.0, 0.0, 0.0, 2.3, 0.1, 2.0))
    assert b.ifc["class"] == "IfcDoor" and b.name.startswith("Block")
    assert b.mesh is lib["PORTA_80"].mesh        # shared: edit the block, edit all doors


def test_flip_and_side_mirror_the_block(vp, tmp_path):
    lib = _block_library(tmp_path)
    _one_door(vp)
    (b,) = mod.create_blocks(vp, lib, lambda w, i, o: {"block": "PORTA_80",
                                                       "flip": True, "side": -1})
    assert _world_bbox(b) == pytest.approx((1.7, -0.1, 0.0, 2.0, 0.0, 2.0))


def test_a_block_follows_a_wall_that_is_not_along_x(vp, tmp_path):
    lib = _block_library(tmp_path)
    _one_door(vp, a=(0, 0), b=(0, 4))            # runs along +Y: left of it is -X
    (b,) = mod.create_blocks(vp, lib, lambda w, i, o: {"block": "PORTA_80"})
    assert _world_bbox(b) == pytest.approx((-0.1, 2.0, 0.0, 0.0, 2.3, 2.0))


def test_blocks_are_one_undo_step_and_a_missing_block_changes_nothing(vp, tmp_path):
    lib = _block_library(tmp_path)
    _one_door(vp)
    n, steps = len(vp.scene.groups), len(vp.history.undo_stack)
    assert mod.create_blocks(vp, lib, lambda w, i, o: {"block": "NAO_EXISTE"}) == []
    assert len(vp.scene.groups) == n and len(vp.history.undo_stack) == steps
    (b,) = mod.create_blocks(vp, lib, lambda w, i, o: {"block": "PORTA_80"})
    assert len(vp.history.undo_stack) == steps + 1
    vp.history.undo()
    assert b not in vp.scene.groups


def test_update_3d_puts_a_moved_block_back_and_blocks_survive_reopening(vp, tmp_path):
    from PySide6.QtGui import QMatrix4x4
    lib = _block_library(tmp_path)
    _one_door(vp)
    (b,) = mod.create_blocks(vp, lib, lambda w, i, o: {"block": "PORTA_80"})
    good = _world_bbox(b)
    b.xform = QMatrix4x4()                        # someone moved it
    assert mod.update_3d(vp, [b]) == 1
    assert _world_bbox(b) == pytest.approx(good)
    igz.save_scene(vp.scene, tmp_path / "b.igz")
    sc = Scene()
    igz.load_into(sc, tmp_path / "b.igz")
    back = [g for g in sc.groups if (mod.element_record(g) or {}).get("kind") == "block"][0]
    assert mod.element_record(back)["block"] == "PORTA_80"
    assert _world_bbox(back) == pytest.approx(good)


# ---- 2D plan with projections (dashed lines of what lies above the cut) ----------------

def _on(seg, x0, x1, y):
    return (abs(seg[1] - y) < 0.011 and abs(seg[3] - y) < 0.011
            and min(seg[0], seg[2]) >= x0 - 0.02 and max(seg[0], seg[2]) <= x1 + 0.02)


def test_the_header_over_a_door_is_projected_dashed_across_the_opening(vp):
    _wall_with(vp, [{"s0": 1.5, "s1": 2.5, "sill": 0.0, "head": 2.1}], length=4.0)
    plan = mod.plan_drawing(vp.scene, 1.2)
    assert len(plan["cut"]) == 2                      # the wall either side of the door
    above = [tuple(map(float, s)) for s in plan["above"]]
    assert above and all(_on(s, 1.5, 2.5, 0.1) or _on(s, 1.5, 2.5, -0.1) for s in above)
    for y in (0.1, -0.1):                             # both faces, most of the opening
        assert sum(abs(s[2] - s[0]) for s in above if _on(s, 1.5, 2.5, y)) > 0.9


def test_a_wall_without_openings_has_no_projection_and_nothing_is_left_changed(vp):
    mod.create_walls(vp, [_w((0, 0), (4, 0), 0.2)])
    planes_before = list(vp.scene.section_planes)
    plan = mod.plan_drawing(vp.scene, 1.2)
    assert len(plan["above"]) == 0 and len(plan["cut"]) == 1
    assert vp.scene.section_planes == planes_before    # the cut was only for the drawing


def test_a_beam_above_the_cut_is_projected_and_a_sill_below_it_is_not(vp):
    from core.group import Group
    mod.create_walls(vp, [_w((0, 0), (4, 0), 0.2)], height=2.8)
    beam = mod.Mesh()
    mod._add_box(beam, (0, 0), (1, 0), (0, 1), 6.0, 8.0, 1.0, 1.4, 2.2, 2.6)
    vp.scene.groups.append(Group(beam, name="viga"))
    low = mod.Mesh()
    mod._add_box(low, (0, 0), (1, 0), (0, 1), 6.0, 7.0, 3.0, 3.3, 0.0, 0.4)
    vp.scene.groups.append(Group(low, name="banco"))
    plan = mod.plan_drawing(vp.scene, 1.2)
    in_beam = [s for s in plan["above"] if 5.95 <= min(s[0], s[2]) and max(s[0], s[2]) <= 8.05
               and 0.95 <= min(s[1], s[3]) and max(s[1], s[3]) <= 1.45]
    assert len(in_beam) >= 4                           # its outline, dashed
    assert not any(5.95 <= min(s[0], s[2]) <= 7.05 and 2.95 <= s[1] <= 3.35 for s in plan["above"])
    assert any(5.95 <= min(s[0], s[2]) <= 7.05 and 2.95 <= s[1] <= 3.35 for s in plan["below"])


def _colours(img, step=2):
    return {img.pixelColor(x, y).name() for x in range(0, img.width(), step)
            for y in range(0, img.height(), step)}


def test_the_plan_renders_in_the_office_standard_by_default(vp):
    _wall_with(vp, [{"s0": 1.5, "s1": 2.5, "sill": 0.0, "head": 2.1}], length=4.0)
    plan = mod.plan_drawing(vp.scene, 1.2)
    img = mod.render_plan(plan, width=800, title="t")
    assert img.width() == 800 and img.height() > 100
    colours = _colours(img)
    assert "#000000" in colours                       # solid black poche, as in the PDF
    assert "#3a3f48" not in colours
    # the projection is dashed: along a row inside the opening the black runs have gaps
    dark = [[img.pixelColor(x, y).red() < 90 for x in range(img.width())]
            for y in range(img.height())]
    gappy = False
    for row in dark:
        runs = sum(1 for a, b in zip(row, row[1:]) if b and not a)
        if 3 <= runs <= 60 and sum(row) < img.width() * 0.5:
            gappy = True
    assert gappy


def test_the_cad_style_keeps_the_blue_grey_poche_and_orange_projection(vp):
    _wall_with(vp, [{"s0": 1.5, "s1": 2.5, "sill": 0.0, "head": 2.1}], length=4.0)
    img = mod.render_plan(mod.plan_drawing(vp.scene, 1.2), width=800, style="cad")
    colours = _colours(img)
    assert "#3a3f48" in colours
    assert any(c.startswith("#b0") or c.startswith("#c") for c in colours)


def test_the_office_style_numbers_are_the_measured_ones():
    st = mod.PLAN_STYLES["ao"]
    assert st["dash"] == (9.8, 4.3) and st["below"] == ("#989898", 0.48)
    assert st["above"] == ("#000000", 0.48) and st["cut_fill"] == "#000000"


def test_an_empty_scene_gives_an_empty_plan(vp):
    plan = mod.plan_drawing(vp.scene)
    assert plan["cut"] == [] and len(plan["below"]) == 0 and len(plan["above"]) == 0
    assert mod.render_plan(plan).height() == 200


# ---- swing rule: the door opens AGAINST the wall ----------------------------------------

def _room_corner(door_at=(1.0, 1.87), perp_x=0.6, perp_len=3.0, perp_dir=1, level="PB"):
    """A wall along +x with a door, and a perpendicular wall at ``perp_x``
    running ``perp_len`` m to +y (perp_dir 1) or -y (-1)."""
    wall = dict(_w((0, 0), (5, 0), 0.12), level=level,
                openings=[{"s0": door_at[0], "s1": door_at[1], "sill": 0.0, "head": 2.1}])
    perp = dict(_w((perp_x, 0), (perp_x, perp_dir * perp_len), 0.12), level=level)
    return [wall, perp]


def test_the_hinge_goes_to_the_corner_side_and_the_leaf_opens_to_the_wall():
    got = mod.door_swing(_room_corner(), 0, 0)
    assert got["hinge"] == "a" and got["side"] == 1          # corner is at the start, wall goes +y
    assert got["wall"] == 1 and got["gap"] == pytest.approx(1.0 - 0.6 - 0.06)


def test_the_rule_mirrors_with_the_wall_on_the_other_side():
    assert mod.door_swing(_room_corner(perp_dir=-1), 0, 0)["side"] == -1
    recs = _room_corner(perp_x=2.4)                            # corner after the door
    got = mod.door_swing(recs, 0, 0)
    assert got["hinge"] == "b" and got["side"] == 1


def test_a_wall_too_far_or_too_short_or_alone_gives_no_rule():
    assert mod.door_swing(_room_corner(perp_x=-0.5), 0, 0) is None   # 1.5 m away
    assert mod.door_swing(_room_corner(perp_len=0.3), 0, 0) is None  # cannot receive the leaf
    assert mod.door_swing(_room_corner()[:1], 0, 0) is None          # nothing beside it
    other_level = _room_corner(level="PB")
    other_level[1]["level"] = "P1"
    assert mod.door_swing(other_level, 0, 0) is None


def test_two_candidate_walls_are_reported_and_the_nearest_corner_wins():
    recs = _room_corner() + [dict(_w((2.4, 0), (2.4, -3), 0.12), level="PB")]
    got = mod.door_swing(recs, 0, 0)
    assert (got["hinge"], got["side"]) == ("a", 1)             # 0.34 m beats 0.51 m
    assert [(a["hinge"], a["side"]) for a in got["alternatives"]] == [("b", -1)]
    assert not got["ambiguous"]
    even = _room_corner(perp_x=0.6) + [dict(_w((2.27, 0), (2.27, -3), 0.12), level="PB")]
    assert mod.door_swing(even, 0, 0)["ambiguous"]             # the rule alone cannot decide


def test_auto_swing_fills_the_side_and_an_override_still_wins(vp):
    wall, perp = _room_corner()
    mod.create_walls(vp, [wall, perp])
    (lf,) = mod.create_leaves(vp, [g for g in vp.scene.groups
                                   if mod.wall_record(g)["openings"]], auto_swing=True)
    rec = mod.element_record(lf)
    assert (rec["hinge"], rec["side"]) == ("a", 1)
    (lf2,) = mod.create_leaves(vp, [g for g in vp.scene.groups
                                    if (mod.element_record(g) or {}).get("openings")],
                               auto_swing=True,
                               overrides=lambda w, i, o: {"side": -1})
    assert mod.element_record(lf2)["side"] == -1 and mod.element_record(lf2)["hinge"] == "a"


# ---- rooms (ambientes): detected from the walls, with name and area --------------------

def _wall_rec(a, b, t=0.2):
    return {"kind": "wall", "a": list(a), "b": list(b), "thickness": t, "level": "PB"}


def _box_room(extra=()):
    return [_wall_rec((0, 0), (6, 0)), _wall_rec((6, 0), (6, 4)),
            _wall_rec((6, 4), (0, 4)), _wall_rec((0, 4), (0, 0))] + list(extra)


def test_a_closed_box_is_one_room_with_the_inside_area():
    (r,) = mod.detect_rooms(_box_room())
    assert r["area"] == pytest.approx((6 - 0.2) * (4 - 0.2), rel=0.005)
    assert len(r["outline"]) == 4 and r["holes"] == []
    assert mod._ring_area([tuple(p) for p in r["outline"]]) > 0          # counter-clockwise


def test_a_partition_splits_the_room_and_a_pillar_is_a_hole():
    recs = _box_room([_wall_rec((3, 0), (3, 4), 0.1),
                      _wall_rec((1.5, 1.5), (1.5, 1.9), 0.4)])
    left, right = sorted(mod.detect_rooms(recs), key=lambda r: r["outline"][0][0])
    assert right["area"] == pytest.approx((6 - 0.1 - 3.05) * 3.8, rel=0.005)
    assert left["area"] == pytest.approx((2.95 - 0.1) * 3.8 - 0.4 * 0.4, rel=0.005)
    assert len(left["holes"]) == 1 and len(right["holes"]) == 0


def test_a_door_does_not_join_rooms_but_a_gap_with_no_wall_does():
    door = _box_room([dict(_wall_rec((3, 0), (3, 4), 0.1),
                           openings=[{"s0": 1.5, "s1": 2.4, "sill": 0.0, "head": 2.1}])])
    assert len(mod.detect_rooms(door)) == 2                # the wall covers its door
    leak = _box_room([_wall_rec((3, 0), (3, 1.4), 0.1), _wall_rec((3, 2.4), (3, 4), 0.1)])
    assert len(mod.detect_rooms(leak)) == 1                # no piece in the gap: it is one space
    closed = mod.detect_rooms(leak, extra=[_wall_rec((3, 1.4), (3, 2.4), 0.1)])
    assert len(closed) == 2


def test_a_frame_closes_the_room_like_a_wall():
    frame = {"kind": "frame", "a": [3, 1.4], "b": [3, 2.4], "depth": 0.06, "level": "PB",
             "height": 2.8, "divisions": [], "panel": "glass", "panel_thickness": 0.01,
             "mullion": 0.05, "base_offset": 0.0, "ifc": "IfcWindow", "rails": []}
    leak = _box_room([_wall_rec((3, 0), (3, 1.4), 0.1), _wall_rec((3, 2.4), (3, 4), 0.1), frame])
    assert len(mod.detect_rooms(leak)) == 2


def test_the_outside_and_tiny_regions_are_not_rooms():
    assert len(mod.detect_rooms(_box_room())) == 1         # not the street around it
    tiny = _box_room([_wall_rec((0.1, 0.8), (1.2, 0.8)), _wall_rec((1.2, 0.8), (1.2, 0.1))])
    assert all(r["area"] >= 1.0 for r in mod.detect_rooms(tiny))
    assert mod.detect_rooms([]) == []


def test_rooms_are_named_by_the_hints_inside_them():
    recs = _box_room([_wall_rec((3, 0), (3, 4), 0.1)])
    found = sorted(mod.detect_rooms(recs), key=lambda r: r["outline"][0][0])
    names = mod.name_rooms(found, [(1, 1, "Cozinha"), (1.2, 2, "Cozinha"), (1, 3, "Banheiro"),
                                   (-5, -5, "Fora")])
    assert names == ["Cozinha", None]


def test_rooms_become_floor_zones_in_one_undo_step_with_the_area_table(vp):
    found = mod.detect_rooms(_box_room([_wall_rec((3, 0), (3, 4), 0.1),
                                        _wall_rec((1.5, 1.5), (1.5, 1.9), 0.4)]))
    steps = len(vp.history.undo_stack)
    made = mod.create_rooms(vp, found, names=["Sala", "Quarto"])
    assert len(made) == 2 and len(vp.history.undo_stack) == steps + 1
    assert all(g.ifc["class"] == "IfcSpace" for g in made)
    for g in made:
        rec = mod.room_record(g)
        assert _volume(g) == pytest.approx(rec["area"] * 0.01, rel=1e-3)      # closed slab
    rows = dict(mod.area_table(vp.scene))
    assert set(rows) == {"Sala", "Quarto"} and all("," in v for v in rows.values())
    vp.history.undo()
    assert mod.rooms(vp.scene) == []


def test_rooms_survive_reopening_and_update_3d_and_show_in_the_tree(vp, tmp_path):
    found = mod.detect_rooms(_box_room())
    (g,) = mod.create_rooms(vp, found, names=["Sala"])
    good = _geom(g)
    g.mesh = mod.Mesh()
    assert mod.update_3d(vp, [g]) == 1 and _geom(g) == good
    igz.save_scene(vp.scene, tmp_path / "r.igz")
    sc = Scene()
    igz.load_into(sc, tmp_path / "r.igz")
    (back,) = mod.rooms(sc)
    assert back.name == "Sala" and mod.room_record(back)["area"] == pytest.approx(found[0]["area"])
    levels, _v, _s = mod.project_tree_model(vp.scene)
    (pb,) = [lv for lv in levels[1] if lv[0].startswith("PB")]
    assert any(label.startswith("Sala — ") and label.endswith(" m²") for label, _ in pb[1])
    assert mod._outliner_kind(g)["type"] == "Room"


def test_the_plan_labels_the_rooms(vp):
    mod.create_walls(vp, [_w((0, 0), (6, 0), 0.2), _w((6, 0), (6, 4), 0.2),
                          _w((6, 4), (0, 4), 0.2), _w((0, 4), (0, 0), 0.2)])
    mod.create_rooms(vp, mod.detect_rooms([mod.wall_record(g) for g in mod.walls(vp.scene)]),
                     names=["Sala"])
    plan = mod.plan_drawing(vp.scene, 1.2)
    assert plan["rooms"][0]["name"] == "Sala" and plan["rooms"][0]["area"] > 20
    plain = mod.render_plan(plan, width=800)
    labelled = mod.render_plan(plan, width=800, labels=True)
    assert plain.size() == labelled.size() and plain != labelled
