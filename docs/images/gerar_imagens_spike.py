"""Gera as imagens do spike (planta cortada e axonometria das paredes do
plugins/bim_proto.py) pelo passe HLR do núcleo — sem GL.

Uso: QT_QPA_PLATFORM=offscreen python docs/images/gerar_imagens_spike.py"""
import importlib.util, math, sys
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QVector3D as V, QImage, QPainter, QPen, QColor, QPolygonF, QFont, QBrush
from PySide6.QtCore import QPointF, Qt
app = QApplication([])
root = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(root))
spec = importlib.util.spec_from_file_location("bp", root/"plugins/bim_proto.py")
bp = importlib.util.module_from_spec(spec); sys.modules["bp"] = bp; spec.loader.exec_module(bp)
from core.scene import Scene
from core.history import History, PlaceSectionPlaneCommand
from core.section import SectionPlane
from core.camera import OrbitCamera
from core.hlr import hlr_drawing, KIND_CUT, KIND_PROFILE, KIND_HIDDEN

class VP: pass
vp = VP(); vp.scene = Scene(); vp.history = History(vp.scene)
vp.update = lambda: None; vp.flash_status = lambda *a: None
vp.scene.plugin_data[bp.KEY] = {"levels": [{"id": "PB", "name": "PB", "z": 0.0}]}
# a small L-shaped room: 4 walls
pts = [(0, 0), (6, 0), (6, 4), (0, 4)]
for i in range(4):
    bp.create_wall(vp, pts[i], pts[(i+1) % 4], thickness=0.2, height=2.8)
out = root/"docs/images"

def camera(plan):
    c = OrbitCamera(); c.perspective = False
    if plan:
        c.target = V(3, 2, 0); c.distance = 20; c.yaw = -math.pi/2; c.pitch = math.radians(89.9)
    else:
        c.target = V(3, 2, 1.4); c.distance = 25; c.yaw = math.radians(-50); c.pitch = math.radians(28)
    return c

def render(name, plan, W=1000, H=760, title=""):
    sc = vp.scene
    sc.section_planes.clear()
    if plan:
        cut = SectionPlane(V(0, 0, 1.2), V(0, 0, 1), name="Corte PB 1,20", symbol="PB")
        vp.history.execute(PlaceSectionPlaneCommand(cut))
    d = hlr_drawing(sc, camera(plan), hidden=False)
    segs = d.segs; xs = list(segs[:, [0, 2]].ravel()); ys = list(segs[:, [1, 3]].ravel())
    for lp in d.loops:
        xs += list(lp[:, 0]); ys += list(lp[:, 1])
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    m = 120; s = min((W-2*m)/(x1-x0), (H-2*m-30)/(y1-y0))
    X = lambda x: m + (x-x0)*s + (W-2*m-(x1-x0)*s)/2
    Y = lambda y: H-m - (y-y0)*s - (H-2*m-30-(y1-y0)*s)/2
    img = QImage(W, H, QImage.Format_RGB32); img.fill(QColor("#ffffff"))
    p = QPainter(img); p.setRenderHint(QPainter.Antialiasing)
    for lp in d.loops:
        poly = QPolygonF([QPointF(X(a), Y(b)) for a, b in lp])
        p.setPen(Qt.NoPen); p.setBrush(QColor("#3a3f48")); p.drawPolygon(poly)
    for (a, b, c, e), k in zip(segs, d.kinds):
        w = 2.6 if k == KIND_CUT else 1.8 if k == KIND_PROFILE else 0.9
        p.setPen(QPen(QColor("#111"), w)); p.drawLine(QPointF(X(a), Y(b)), QPointF(X(c), Y(e)))
    f = QFont("DejaVu Sans", 13); p.setFont(f); p.setPen(QColor("#222"))
    p.drawText(20, 30, title)
    if plan:  # label each wall axis from its stored parameters
        import numpy as np
        wp = np.concatenate([d.world[:, 0, :2], d.world[:, 1, :2]])
        cp = np.concatenate([segs[:, :2], segs[:, 2:]])
        A = np.c_[wp, np.ones(len(wp))]
        M, *_ = np.linalg.lstsq(A, cp, rcond=None)        # world xy -> camera uv
        cam = lambda x, y: np.array([x, y, 1.0]) @ M
        p.setFont(QFont("DejaVu Sans", 11)); p.setPen(QColor("#b04a00"))
        for g in bp.walls(sc):
            r = g.ext[bp.KEY]; ax, ay = r["a"]; bx, by = r["b"]
            L = math.hypot(bx-ax, by-ay)
            nx, ny = -(by-ay)/L, (bx-ax)/L
            # push the label 0.45 m away from the room's centre (3, 2)
            mx, my = (ax+bx)/2, (ay+by)/2
            sgn = 1 if (mx-3)*nx + (my-2)*ny > 0 else -1
            u, v = cam(mx + sgn*nx*0.6, my + sgn*ny*0.6)
            p.drawText(QPointF(X(u)-22, Y(v)+5), f"{L:.2f} m")
    p.end(); img.save(str(out/f"{name}.png"))
    # SVG
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}"><rect width="100%" height="100%" fill="#fff"/>']
    for lp in d.loops:
        svg.append('<polygon fill="#3a3f48" points="' + " ".join(f"{X(a):.1f},{Y(b):.1f}" for a, b in lp) + '"/>')
    for (a, b, c, e), k in zip(segs, d.kinds):
        w = 2.6 if k == KIND_CUT else 1.8 if k == KIND_PROFILE else 0.9
        svg.append(f'<line x1="{X(a):.1f}" y1="{Y(b):.1f}" x2="{X(c):.1f}" y2="{Y(e):.1f}" stroke="#111" stroke-width="{w}"/>')
    svg.append(f'<text x="20" y="30" font-family="sans-serif" font-size="14">{title}</text></svg>')
    (out/f"{name}.svg").write_text("\n".join(svg), encoding="utf-8")
    print(name, len(segs), "segs", len(d.loops), "rings")

render("bim_proto_planta_pb", True, title="Planta PB — corte a 1,20 m (4 paredes, esp. 0,20 m)")
sc = vp.scene; sc.section_planes.clear()
render("bim_proto_axonometria", False, title="Axonometria — mesmas 4 paredes (h = 2,80 m)")
