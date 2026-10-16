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
                   "base_offset": 0.0}
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
    assert [a.text() for a in sub.actions()] == ["Wall", "Update 3D"]
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
