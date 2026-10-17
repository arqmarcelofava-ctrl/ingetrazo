"""Foto do painel «BIM» dentro da janela REAL do IngeTrazo (Qt sem tela), com dados de teste.

O 3D não desenha sem placa de vídeo; no lugar dele entra a planta de teste gerada pela
própria extensão (montagem, dita na imagem).

Uso: QT_QPA_PLATFORM=offscreen python docs/images/gerar_painel_bim.py"""
import os, shutil, sys, tempfile
from pathlib import Path
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
root = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(root))
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication
app = QApplication([])
import core.extensions as extensions
from core.i18n import set_language
set_language("pt-BR")
tmp = Path(tempfile.mkdtemp()); shutil.copy(root / "plugins/bim_proto.py", tmp / "bim_proto.py")
extensions.plugin_dirs = lambda: [tmp]
from views.main_window import MainWindow
w = MainWindow(); w.resize(1500, 950)
live = next(m for n, m in sys.modules.items() if n.endswith("ingetrazo_plugin_bim_proto"))
vp = w.viewport
live.create_walls(vp, [
    {"a": [0, 0], "b": [8, 0], "thickness": 0.2, "openings": [{"s0": 5.0, "s1": 5.87, "sill": 0.0, "head": 2.1}]},
    {"a": [8, 0], "b": [8, 5], "thickness": 0.2}, {"a": [8, 5], "b": [0, 5], "thickness": 0.2},
    {"a": [0, 5], "b": [0, 0], "thickness": 0.2}, {"a": [4.6, 0], "b": [4.6, 5], "thickness": 0.1,
     "openings": [{"s0": 3.0, "s1": 3.87, "sill": 0.0, "head": 2.1}]}])
live.join_corners(vp)
live.create_leaves(vp, auto_swing=True, frame=True, trim=True, arc=True, thickness=0.035, clearance=0.002)
live.update_rooms(vp)
for g, nm in zip(live.rooms(vp.scene), ("Sala", "Quarto")):
    g.name = nm
vp.scene.version += 1
vp.sceneVersionChanged.emit(vp.scene.version)
w.show(); app.processEvents()
dock = w.extension_panels()["extension_bim_proto"]
dock.raise_(); app.processEvents()
panel = dock.widget()
img = w.grab().toImage().convertToFormat(QImage.Format_RGB32)
plan = live.render_plan(live.plan_drawing(vp.scene, 1.2), width=1400, labels=True,
                        title="Planta a 1,20 m (gerada pela extensão)")
r = vp.mapTo(w, vp.rect().topLeft()); rect = QRect(r.x(), r.y(), vp.width(), vp.height())
p = QPainter(img); p.fillRect(rect, QColor("white"))
p.drawImage(rect.x() + 10, rect.y() + 10, plan.scaled(rect.width() - 20, rect.height() - 60, Qt.KeepAspectRatio, Qt.SmoothTransformation))
p.setPen(QColor("#666")); p.setFont(QFont("DejaVu Sans", 9))
p.drawText(rect.x() + 12, rect.bottom() - 12, "montagem: sem placa de vídeo o 3D não desenha aqui; a planta foi gerada à parte")
p.end()
out = root / "docs/images"
img.save(str(out / "bim_proto_janela_painel.png"))
panel.grab().save(str(out / "bim_proto_painel_botoes.png"))
w._saved_version = vp.scene.version; w.close(); print("ok")
