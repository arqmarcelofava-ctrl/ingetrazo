"""Imagens sintéticas (sem dado de cliente) dos cantos de parede e das
esquadrias de fachada do plugins/bim_proto.py, pelo passe HLR do núcleo — sem GL.

Uso: QT_QPA_PLATFORM=offscreen python docs/images/gerar_imagens_cantos_esquadrias.py"""
import importlib.util, math, os, sys
from pathlib import Path
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QVector3D as V, QImage, QPainter, QPen, QColor, QPolygonF, QFont
from PySide6.QtCore import QPointF, Qt
app = QApplication([])
root = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(root))
spec = importlib.util.spec_from_file_location("bp", root / "plugins/bim_proto.py")
bp = importlib.util.module_from_spec(spec); sys.modules["bp"] = bp; spec.loader.exec_module(bp)
from core.scene import Scene
from core.history import History
from core.section import SectionPlane
from core.camera import OrbitCamera
from core.hlr import hlr_drawing, KIND_CUT, KIND_PROFILE

out = root / "docs/images"


class VP: pass


def novo():
    vp = VP(); vp.scene = Scene(); vp.history = History(vp.scene)
    vp.update = lambda: None; vp.flash_status = lambda *a: None
    return vp


def desenhar(vp, yaw, pitch, centro, dist, corte=None, W=900, H=700, titulo=""):
    sc = vp.scene; sc.section_planes.clear(); sc.set_active_section(None)
    if corte is not None:
        sp = SectionPlane(V(0, 0, corte), V(0, 0, 1)); sc.section_planes.append(sp); sc.set_active_section(sp)
    c = OrbitCamera(); c.perspective = False; c.target = V(*centro); c.distance = dist
    c.yaw, c.pitch = math.radians(yaw), math.radians(pitch)
    d = hlr_drawing(sc, c, hidden=False); s_ = d.segs
    xs = list(s_[:, [0, 2]].ravel()); ys = list(s_[:, [1, 3]].ravel())
    for lp in d.loops: xs += list(lp[:, 0]); ys += list(lp[:, 1])
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys); m = 50
    k = min((W - 2 * m) / (x1 - x0), (H - 2 * m - 30) / (y1 - y0))
    X = lambda x: m + (x - x0) * k; Y = lambda y: H - m - (y - y0) * k
    img = QImage(W, H, QImage.Format_RGB32); img.fill(QColor("white"))
    p = QPainter(img); p.setRenderHint(QPainter.Antialiasing)
    for lp in d.loops:
        p.setPen(Qt.NoPen); p.setBrush(QColor("#3a3f48")); p.drawPolygon(QPolygonF([QPointF(X(a), Y(b)) for a, b in lp]))
    for (a, b, cc, e), kd in zip(s_, d.kinds):
        p.setPen(QPen(QColor("#111"), 1.8 if kd == KIND_PROFILE else 0.7)); p.drawLine(QPointF(X(a), Y(b)), QPointF(X(cc), Y(e)))
    p.setFont(QFont("DejaVu Sans", 12)); p.setPen(QColor("#222")); p.drawText(14, 26, titulo); p.end()
    return img


def lado_a_lado(a, b, nome):
    img = QImage(a.width() + b.width() + 10, max(a.height(), b.height()), QImage.Format_RGB32); img.fill(QColor("white"))
    p = QPainter(img); p.drawImage(0, 0, a); p.drawImage(a.width() + 10, 0, b); p.end(); img.save(str(out / nome))


# --- cantos: um cômodo desenhado pelos eixos, antes e depois de «Unir cantos»
vp = novo()
t = 0.2
bp.create_walls(vp, [
    {"a": [0, 0], "b": [6, 0], "thickness": t}, {"a": [6, 0], "b": [6, 4], "thickness": t},
    {"a": [6, 4], "b": [0, 4], "thickness": t}, {"a": [0, 4], "b": [0, 0], "thickness": t},
    {"a": [3, 0.02], "b": [3, 4], "thickness": 0.1}])        # divisória encostando no meio (T)
cam = dict(yaw=-90, pitch=89.9, centro=(3, 2, 0), dist=20, corte=1.2)
antes = desenhar(vp, titulo="Antes: paredes pelo eixo, cantos abertos", **cam)
n = bp.join_corners(vp)
depois = desenhar(vp, titulo=f"Depois de «Unir cantos» ({n} paredes ajustadas)", **cam)
lado_a_lado(antes, depois, "bim_proto_cantos_antes_depois.png")

# --- esquadrias: fachada com montantes e painéis + guarda-corpo, em axonometria
vp = novo()
bp.create_walls(vp, [{"a": [0, 0], "b": [2, 0], "thickness": 0.2}, {"a": [2, 0], "b": [2, 3], "thickness": 0.2}])
bp.create_frames(vp, [
    {"a": [2.4, 0], "b": [4.4, 0], "divisions": [0.025, 1.0, 1.975], "panel": "mesh"},
    {"a": [4.4, 0], "b": [7.0, 0], "divisions": [0.025, 1.3, 2.575], "panel": "glass"},
    {"a": [7.0, 0], "b": [7.0, 2.0], "depth": 0.04, "panel_thickness": 0.04, "height": 1.1, "ifc": "IfcRailing"}])
img = desenhar(vp, yaw=-35, pitch=22, centro=(3.5, 1, 1.4), dist=30, titulo="Esquadrias (montantes + painéis) e guarda-corpo, como grupos IFC", W=800, H=620)
img.save(str(out / "bim_proto_esquadrias.png"))
# --- peitoril e folha de porta: parede com uma janela e uma porta aberta a 70°
vp = novo()
(w,) = bp.create_walls(vp, [{"a": [0, 0], "b": [5, 0], "thickness": 0.2, "openings": [
    {"s0": 0.8, "s1": 2.2, "sill": 0.9, "head": 2.1}, {"s0": 3.2, "s1": 4.1, "sill": 0.0, "head": 2.1}]}])
bp.create_sills(vp, projection=0.08, side=-1)
bp.create_leaves(vp, angle=70, hinge="a", side=-1)
img = desenhar(vp, yaw=-30, pitch=18, centro=(2.5, 0, 1.2), dist=30, titulo="Janela com peitoril e porta com folha aberta a 70°", W=700, H=560)
img.save(str(out / "bim_proto_peitoril_folha.png"))
# --- portas detalhadas (medidas lidas de um DWG real): marco com rebaixo, guarnição,
# folha de 35 mm, arco de giro; e porta de correr de vidro
vp = novo()
bp.create_walls(vp, [{"a": [0, 0], "b": [5, 0], "thickness": 0.12, "openings": [
    {"s0": 0.6, "s1": 1.47, "sill": 0.0, "head": 2.1}, {"s0": 3.2, "s1": 3.9, "sill": 0.0, "head": 2.1}]}])
bp.create_leaves(vp, frame=True, trim=True, arc=True, thickness=0.035, clearance=0.002, side=-1, hinge="b",
                 overrides=lambda w, i, o: {"style": "sliding", "t_offset": 0.0, "arc": False, "frame": False, "trim": False} if i == 1 else None)
img = desenhar(vp, yaw=-90, pitch=89.9, centro=(2.5, -0.5, 0), dist=20, corte=1.2, W=1000, H=420,
               titulo="Planta a 1,20 m: marco com rebaixo, guarnição, folha e arco de giro; porta de correr")
img.save(str(out / "bim_proto_portas_planta.png"))
img = desenhar(vp, yaw=-25, pitch=22, centro=(2.5, 0, 1.0), dist=30, W=1000, H=620,
               titulo="Porta de abrir detalhada e porta de correr de vidro")
img.save(str(out / "bim_proto_portas_axo.png"))
# --- planta 2D com projeções: o tracejado é o que está ACIMA do corte (verga sobre a
# porta, viga), como na planta do AutoCAD
from core.i18n import set_language
set_language("pt-BR")
from core.group import Group
vp = novo()
bp.create_walls(vp, [{"a": [0, 0], "b": [6, 0], "thickness": 0.15, "openings": [
    {"s0": 1.0, "s1": 1.87, "sill": 0.0, "head": 2.1}, {"s0": 3.5, "s1": 4.9, "sill": 0.9, "head": 2.1}]},
    {"a": [6, 0], "b": [6, 4], "thickness": 0.15}], height=2.8)
bp.create_leaves(vp, frame=True, trim=True, arc=True, thickness=0.035, clearance=0.002,
                 overrides=lambda w, i, o: {"hinge": "b", "side": 1})
bp.create_sills(vp, projection=0.06, side=1)
viga = bp.Mesh()
bp._add_box(viga, (0, 0), (1, 0), (0, 1), 0.0, 6.0, 3.85, 4.0, 2.4, 2.8)       # viga junto à parede
vp.scene.groups.append(Group(viga, name="viga"))
armario = bp.Mesh()
bp._add_box(armario, (0, 0), (1, 0), (0, 1), 2.0, 3.2, 3.6, 4.0, 1.5, 2.2)      # armário alto
vp.scene.groups.append(Group(armario, name="armário"))
bp.render_plan(bp.plan_drawing(vp.scene, 1.2), width=1100, title="Planta a 1,20 m: corte, abaixo do corte e projeção (tracejado)").save(str(out / "bim_proto_planta_projecoes.png"))
# --- paredes curvas, contínua e retângulo (as opções do botão Parede)
set_language("pt-BR")
vp = novo()
bp.create_box_walls(vp, (0, 0), (7, 5), thickness=0.2)                       # retângulo: 4 paredes unidas
bp.create_wall(vp, (7, 1.0), (7, 4.0), bulge=-1.4, thickness=0.2)             # curva: janela saliente (baía)
bp.create_wall(vp, (2.5, 0), (2.5, 5), bulge=0.9, thickness=0.12)             # curva: divisória em arco
bp.create_wall_chain(vp, [(9.5, 0), (13, 0), (13, 3), (11, 5)], thickness=0.2)  # contínua: 3 trechos unidos
bp.create_wall_chain(vp, [(9.5, 6), (13, 6), (13, 9)], bulges=[0, 1.2], thickness=0.2)  # contínua com um trecho curvo
img = desenhar(vp, yaw=-90, pitch=89.9, centro=(6.5, 4.5, 0), dist=40, corte=1.2, W=1100, H=800,
               titulo="Parede reta, curva, contínua e retângulo: planta a 1,20 m")
img.save(str(out / "bim_proto_paredes_curvas_planta.png"))
img = desenhar(vp, yaw=-40, pitch=28, centro=(6.5, 4.5, 1.4), dist=40, W=1100, H=800,
               titulo="As mesmas paredes em 3D (curvas são sólidos fechados)")
img.save(str(out / "bim_proto_paredes_curvas_3d.png"))
print("ok")
