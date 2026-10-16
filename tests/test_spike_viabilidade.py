# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Evidence for docs/spike-viabilidade.md: what an extension can do, shown
with public API (and, where it cannot, with the line marked FRAGILE).

Each test backs one claim of the spike's table — if the core changes and a
claim stops being true, the matching test fails and the document needs to
follow."""
from __future__ import annotations

import importlib.util
import math
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtGui import QVector3D as V  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import core.extensions as extensions  # noqa: E402
from core.camera import OrbitCamera  # noqa: E402
from core.composition import Composicion  # noqa: E402
from core.history import (AddSheetCommand, History,  # noqa: E402
                          PlaceSectionPlaneCommand, RotateGroupCommand)
from core.hlr import hlr_drawing  # noqa: E402
from core.saved_views import SavedView  # noqa: E402
from core.scene import Scene  # noqa: E402
from core.section import SectionPlane  # noqa: E402
from formats import igz  # noqa: E402

_app = QApplication.instance() or QApplication([])

PLUGIN = Path(__file__).resolve().parents[1] / "plugins" / "bim_proto.py"
_spec = importlib.util.spec_from_file_location("bim_proto_spike", PLUGIN)
bp = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = bp
_spec.loader.exec_module(bp)


class FakeViewport:
    def __init__(self) -> None:
        self.scene = Scene()
        self.history = History(self.scene)

    def update(self) -> None: ...
    def flash_status(self, text, msec=2500) -> None: ...


# ---- Q3 + Q6 + Q2: scenes, cut plane, sheet and project data travel together ----

def test_views_cut_planes_and_sheets_made_by_code_survive_a_reopen(tmp_path):
    sc = Scene()
    hist = History(sc)
    cut = SectionPlane(V(0, 0, 1.2), V(0, 0, 1), name="Corte PB", symbol="PB")
    hist.execute(PlaceSectionPlaneCommand(cut))
    plan = SavedView("Planta PB", target=(5, 5, 0), distance=30,
                     yaw=-math.pi / 2, pitch=math.radians(89),
                     perspective=False,
                     section={"active": cut.uid, "planes_shown": False,
                              "cuts_shown": True})
    sc.saved_views.append(plan)            # public list; the core panel does not make this undoable either
    sheet = Composicion(name="A-101", paper="A3")
    frame = sheet.default_frame()
    frame.view_key, frame.scale_n = "scene:Planta PB", 50
    sheet.frames.append(frame)
    hist.execute(AddSheetCommand(sheet))
    sc.plugin_data["bim_proto"] = {"views": [
        {"name": "Planta PB", "kind": "plan", "level": "PB"}]}

    path = tmp_path / "p.igz"
    igz.save_scene(sc, path)
    again = Scene()
    igz.load_into(again, path)

    v = again.saved_views[0]
    assert v.name == "Planta PB" and not v.perspective
    assert v.section["active"] == again.section_planes[0].uid
    assert again.section_planes[0].normal.z() == pytest.approx(1.0)
    assert again.compositions[0].frames[0].view_key == "scene:Planta PB"
    assert again.plugin_data["bim_proto"]["views"][0]["level"] == "PB"


def test_a_saved_view_has_no_field_of_its_own_for_an_extension():
    """The extension has to keep «this scene is the plan of level PB»
    elsewhere (plugin_data), keyed by the scene's NAME — a rename in the
    Browser breaks the link unless the extension follows it."""
    names = SavedView.__init__.__code__.co_varnames
    assert "uid" not in names and "ext" not in names and "meta" not in names


def test_applying_a_view_recalls_camera_and_cut():
    sc = Scene()
    cut = SectionPlane(V(0, 0, 1.2), V(0, 0, 1))
    sc.section_planes.append(cut)
    view = SavedView("Planta", target=(1, 2, 0), distance=12, perspective=False,
                     section={"active": cut.uid})
    cam = OrbitCamera()
    view.apply(sc, cam)
    assert cam.perspective is False and cam.distance == 12
    assert sc.active_section() is cut


# ---- Q3: the 2D plan can come from the model, headless ---------------------------

def test_a_plan_cut_at_1_20_m_gives_the_wall_section_rings_without_gl():
    vp = FakeViewport()
    bp.create_wall(vp, (0, 0), (4, 0), thickness=0.2, height=3.0)
    bp.create_wall(vp, (4, 0), (4, 3), thickness=0.2, height=3.0)
    vp.history.execute(PlaceSectionPlaneCommand(
        SectionPlane(V(0, 0, 1.2), V(0, 0, 1), name="PB")))
    cam = OrbitCamera()
    cam.target, cam.distance = V(2, 1.5, 0), 20
    cam.yaw, cam.pitch, cam.perspective = -math.pi / 2, math.radians(89.9), False
    drawing = hlr_drawing(vp.scene, cam)
    assert len(drawing.loops) == 2         # one poché ring per wall
    assert len(drawing.segs) > 0


# ---- Q4: a tool the loader finds is one-shot ----------------------------------------

def test_a_plugin_tool_found_by_the_loader_is_one_shot(tmp_path, monkeypatch):
    (tmp_path / "oneshot.py").write_text(
        "from tools.base import Tool\n"
        "class Ping(Tool):\n"
        "    name = 'Ping'\n"
        "    calls = 0\n"
        "    def on_activate(self, viewport):\n"
        "        type(self).calls += 1\n"
        "    def on_deactivate(self, viewport): pass\n")
    monkeypatch.setattr(extensions, "plugin_dirs", lambda: [tmp_path])
    from views.main_window import MainWindow
    w = MainWindow()
    try:
        key = next(k for k in w._tools if k.startswith("plugin_oneshot_"))
        # FRAGILE: _activate_plugin_tool is the menu's private slot.
        w._activate_plugin_tool(key)
        assert type(w._tools[key]).calls == 1
        assert w.viewport.active_tool is not w._tools[key]
    finally:
        w._saved_version = w.viewport.scene.version
        w.close()


# ---- Q5: a transform does not tell the extension ----------------------------------

def test_rotating_a_wall_leaves_its_parameters_stale_until_update_3d():
    vp = FakeViewport()
    g = bp.create_wall(vp, (0, 0), (4, 0), thickness=0.2, height=3.0)
    vp.history.execute(RotateGroupCommand(g, V(0, 0, 0), V(0, 0, 1), 90))
    assert g.ext[bp.KEY]["b"] == [4, 0]            # the record never heard of it
    xs = [v.position.x() for v in g.mesh.vertices]
    assert max(xs) < 0.2                            # but the geometry turned
    bp.update_3d(vp)                                # the record wins
    assert max(v.position.x() for v in g.mesh.vertices) == pytest.approx(4)
    assert vp.history.undo()                        # and it is one step back
    assert max(v.position.x() for v in g.mesh.vertices) < 0.2


# ---- Q6: PDF of a sheet is reachable only through the composer window ---------------

def test_a_sheet_made_by_code_exports_to_pdf_through_the_composer(tmp_path):
    """FRAGILE: ``MainWindow._ensure_composer`` and ``ComposerWindow.
    export_all_pdf`` are not extension API. A sheet WITHOUT view frames needs
    no GL, so this runs offscreen; a frame needs the GL pipeline (not
    verified here)."""
    from views.main_window import MainWindow
    w = MainWindow()
    try:
        sc = w.viewport.scene
        sheet = Composicion(name="A-101", paper="A3")
        w.viewport.history.execute(AddSheetCommand(sheet))
        out = tmp_path / "pranchas.pdf"
        assert w._ensure_composer().export_all_pdf(str(out)) == []  # FRAGILE
        assert out.stat().st_size > 0
        assert out.read_bytes().startswith(b"%PDF")
        assert sheet in sc.compositions
    finally:
        w._saved_version = w.viewport.scene.version
        w.close()
