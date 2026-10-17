# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Inspeciona um modelo 3D/2D e desenha planta, elevação e axonometria — sem tela.

Abre o arquivo pelos MESMOS importadores do aplicativo (``formats/``), conta o
que veio, confere a escala e desenha pelo passe de linhas do núcleo
(``core.hlr``, NumPy, sem OpenGL). Serve para olhar um modelo grande no
terminal, num computador sem placa de vídeo, ou numa sessão de nuvem.

    QT_QPA_PLATFORM=offscreen python scripts/inspecionar_modelo.py modelo.glb
    python scripts/inspecionar_modelo.py modelo.skp --saida modelos/saida --corte 1.2
    python scripts/inspecionar_modelo.py modelo.dwg --so-relatorio

Formatos: .igz .glb/.gltf .skp .dae .obj .stl .dxf .dwg (o .dwg precisa do
conversor ``dwg2dxf`` de ``vendor/libredwg``). Nada é escrito fora da pasta de
saída (padrão ``modelos/saida``, ignorada pelo Git) e o arquivo de origem
nunca é alterado. Só símbolos públicos dos importadores; o que não é, leva
``FRAGILE``.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

#: Acima disto o passe de linhas (exato, O(arestas x triângulos)) fica lento.
MAX_FACES_DESENHO = 60_000


def carregar(scene, path: Path) -> str:
    """Abre ``path`` em ``scene`` pelo importador do formato; devolve uma nota."""
    suf = path.suffix.lower()
    if suf == ".igz":
        from formats import igz
        igz.load_into(scene, path)
        return "igz"
    if suf in (".glb", ".gltf"):
        from formats import glb
        glb.load_glb(scene, path)
        return "glb"
    if suf == ".skp":
        from formats import skp
        return "skp (" + str(skp.load_skp(scene, path)) + ")"
    if suf == ".dae":
        from formats import dae
        dae.load_dae(scene, path)
        return "dae"
    if suf == ".obj":
        from formats import obj
        obj.load_obj(scene, path)
        return "obj"
    if suf == ".stl":
        from formats import stl
        stl.load_stl(scene, path)
        return "stl"
    if suf in (".dxf", ".dwg"):
        from formats import dxf_in
        dxf = path
        if suf == ".dwg":
            from formats.dwg_bridge import dwg_to_dxf
            dxf = dwg_to_dxf(path)
        dxf_in.load_dxf(scene, dxf)
        return "dxf" if suf == ".dxf" else "dwg → dxf"
    raise SystemExit(f"formato não suportado: {suf}")


def _todas_as_malhas(scene):
    """``(nome, malha)`` do solto e de cada grupo, em coordenadas de mundo."""
    from core.group import world_mesh
    yield "(solto)", scene.loose_mesh
    for g in scene.groups:
        try:
            yield g.name, world_mesh(g)
        except Exception:  # noqa: BLE001 — um grupo estranho não derruba o relatório
            continue


def relatorio(scene, path: Path, nota: str) -> dict:
    import numpy as np
    faces = arestas = verts = 0
    lo = np.full(3, np.inf)
    hi = np.full(3, -np.inf)
    por_grupo = []
    for nome, malha in _todas_as_malhas(scene):
        pts = np.array([[v.position.x(), v.position.y(), v.position.z()]
                        for v in malha.vertices]) if malha.vertices else None
        faces += len(malha.faces)
        arestas += len(malha.edges)
        verts += len(malha.vertices)
        if pts is not None and len(pts):
            lo = np.minimum(lo, pts.min(0))
            hi = np.maximum(hi, pts.max(0))
        por_grupo.append((nome, len(malha.faces)))
    dim = (hi - lo) if np.isfinite(lo).all() else np.zeros(3)
    print(f"arquivo : {path.name}  ({path.stat().st_size / 1e6:.1f} MB)  via {nota}")
    print(f"conteúdo: {faces} faces, {arestas} arestas, {verts} vértices, "
          f"{len(scene.groups)} grupos, {len(scene.layers)} camadas, "
          f"{len(scene.saved_views)} cenas, {len(scene.compositions)} pranchas")
    if np.isfinite(lo).all():
        print("caixa   : %.2f x %.2f x %.2f m   (x y z; Z para cima)" % tuple(dim))
        print("          de (%.2f, %.2f, %.2f) a (%.2f, %.2f, %.2f)" % (*lo, *hi))
        maior = float(dim.max())
        if not 0.5 <= maior <= 500:
            print(f"ATENÇÃO : maior dimensão = {maior:.3g} m — a unidade do arquivo "
                  "provavelmente não é metro; confira a escala antes de usar medidas.")
    nomes = [ly.name for ly in scene.layers][:20]
    if nomes:
        print("camadas :", ", ".join(nomes) + (" …" if len(scene.layers) > 20 else ""))
    maiores = sorted(por_grupo, key=lambda t: -t[1])[:12]
    print("maiores grupos (faces):")
    for nome, n in maiores:
        print(f"   {n:>8}  {nome}")
    return {"faces": faces, "lo": lo, "hi": hi}


def desenhar(scene, info, saida: Path, corte: float | None) -> None:
    import numpy as np
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import (QColor, QFont, QImage, QPainter, QPen, QPolygonF,
                               QVector3D)

    from core.camera import OrbitCamera
    from core.hlr import KIND_PROFILE, hlr_drawing
    from core.section import SectionPlane

    lo, hi = info["lo"], info["hi"]
    centro = (lo + hi) / 2
    diag = float(np.linalg.norm(hi - lo)) or 1.0

    def camera(yaw, pitch):
        cam = OrbitCamera()
        cam.perspective = False
        cam.target = QVector3D(*map(float, centro))
        cam.distance = diag * 2
        cam.yaw, cam.pitch = math.radians(yaw), math.radians(pitch)
        return cam

    vistas = [("planta", -90, 89.9, corte), ("frente", -90, 0.0, None),
              ("axonometria", -50, 28, None)]
    saida.mkdir(parents=True, exist_ok=True)
    for nome, yaw, pitch, plano in vistas:
        scene.section_planes.clear()
        scene.set_active_section(None)
        if plano is not None:
            sp = SectionPlane(QVector3D(0, 0, plano), QVector3D(0, 0, 1),
                              name=f"Corte {plano:g} m", symbol="A")
            scene.section_planes.append(sp)
            scene.set_active_section(sp)
        d = hlr_drawing(scene, camera(yaw, pitch), hidden=False)
        if len(d.segs) == 0:
            print(f"  {nome}: sem segmentos")
            continue
        s_ = d.segs
        xs = list(s_[:, [0, 2]].ravel())
        ys = list(s_[:, [1, 3]].ravel())
        for lp in d.loops:
            xs += list(lp[:, 0])
            ys += list(lp[:, 1])
        x0, x1 = float(np.percentile(xs, 0.1)), float(np.percentile(xs, 99.9))
        y0, y1 = float(np.percentile(ys, 0.1)), float(np.percentile(ys, 99.9))
        W, m = 1600, 40
        k = (W - 2 * m) / max(x1 - x0, 1e-6)
        H = int((y1 - y0) * k) + 2 * m + 30
        img = QImage(W, H, QImage.Format_RGB32)
        img.fill(QColor("white"))
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        for lp in d.loops:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#3a3f48"))
            p.drawPolygon(QPolygonF([QPointF(m + (a - x0) * k, H - m - (b - y0) * k)
                                     for a, b in lp]))
        for (a, b, c, e), kd in zip(s_, d.kinds):
            p.setPen(QPen(QColor("#111"), 1.4 if kd == KIND_PROFILE else 0.5))
            p.drawLine(QPointF(m + (a - x0) * k, H - m - (b - y0) * k),
                       QPointF(m + (c - x0) * k, H - m - (e - y0) * k))
        titulo = nome + (f" — corte a {plano:g} m" if plano is not None else "")
        p.setFont(QFont("DejaVu Sans", 12))
        p.setPen(QColor("#222"))
        p.drawText(14, 24, titulo)
        p.end()
        destino = saida / f"{nome}.png"
        img.save(str(destino))
        print(f"  {nome}: {len(s_)} segmentos, {len(d.loops)} anéis de corte → {destino}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("modelo", type=Path)
    ap.add_argument("--saida", type=Path, default=Path("modelos/saida"),
                    help="pasta das imagens (padrão modelos/saida, fora do Git)")
    ap.add_argument("--corte", type=float, default=1.2,
                    help="cota (m) do corte horizontal da planta; 0 = sem corte")
    ap.add_argument("--so-relatorio", action="store_true", help="não desenhar")
    ap.add_argument("--forcar", action="store_true",
                    help=f"desenhar mesmo com mais de {MAX_FACES_DESENHO} faces (lento)")
    a = ap.parse_args()
    if not a.modelo.is_file():
        raise SystemExit(f"arquivo não encontrado: {a.modelo}")

    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841
    from core.scene import Scene
    scene = Scene()
    nota = carregar(scene, a.modelo)
    info = relatorio(scene, a.modelo, nota)
    if a.so_relatorio:
        return 0
    if info["faces"] == 0 and not any(len(m.edges) for _, m in _todas_as_malhas(scene)):
        print("nada para desenhar")
        return 0
    if info["faces"] > MAX_FACES_DESENHO and not a.forcar:
        print(f"{info['faces']} faces: o passe de linhas seria muito lento. "
              "Rode com --forcar, ou exporte só a seleção (paredes, lajes, esquadrias).")
        return 0
    print("desenhando (sem OpenGL):")
    desenhar(scene, info, a.saida, a.corte or None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
