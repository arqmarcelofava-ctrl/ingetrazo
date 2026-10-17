# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""BIM proto — spike de viabilidade de um BIM leve feito só com extensões.

Prova, com o mínimo, três coisas (ver docs/spike-viabilidade.md):

* uma ferramenta de desenho que cria uma PAREDE a partir de dois pontos, com
  espessura, altura e nível de base (clique, clique; ou clique e comprimento
  digitado no VCB);
* os parâmetros ficam em ``group.ext["bim_proto"]`` (um registro por parede,
  salvo no .igz) e os níveis em ``plugin_data["bim_proto"]`` (dado do projeto
  inteiro, via ``ExtensionApp.document_data``);
* «Atualizar 3D» regenera a geometria de cada parede a partir desses
  parâmetros — todas as paredes, um único passo de desfazer.

A geometria é DERIVADA: o registro manda. Se a parede for movida com
Mover/Girar/Escalar, Atualizar 3D a devolve para onde os parâmetros dizem
(o núcleo não avisa a extensão de transformações; ver o documento do spike).

Só símbolos públicos (nenhum nome com ``_`` do núcleo é usado aqui). Os que
são públicos mas FICAM FORA do contrato documentado (``docs/plugins.md`` e
``views/extension_api.py``) levam a marca ``FRAGILE`` no ponto de uso: se o
núcleo os mudar, este arquivo quebra antes do contrato.
"""
from __future__ import annotations

import copy
import math

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QVector3D
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
                               QGridLayout, QLabel, QMessageBox, QTextEdit,
                               QToolButton, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from core import bim
# FRAGILE: Group e Mesh são públicos, mas o contrato documentado só fala de
# ``group.ext``; o precedente é examples/extensions/windowizer.py.
from core.group import Group
from core.history import Command
from core.mesh import Mesh
from core.scene import Scene
from tools.base import AxisMagnet, Tool, ToolContext

KEY = "bim_proto"

#: Valores iniciais da parede (metros).
DEFAULTS = {"thickness": 0.15, "height": 2.8}
#: Nível que existe sempre, para a primeira parede ter onde pousar.
DEFAULT_LEVELS = [{"id": "PB", "name": "PB", "z": 0.0}]

_EPS = 1e-9
#: Comprimento mínimo (m) que uma parede ainda tem depois de unir cantos.
_MIN_JOIN_LENGTH = 0.02
#: Menor largura (m) de painel entre dois montantes; abaixo disso é fresta de
#: arredondamento (um montante que encosta na ponta) e não vira painel.
_MIN_PANEL = 1e-3
#: A travessa é 1 mm mais rasa, por face, que o montante: assim as peças
#: encostam sem dividir vértices e cada uma continua um sólido fechado.
_RAIL_RECESS = 1e-3

_TEXTS = {
    "pt-BR": {
        "Wall": "Parede", "Wall (BIM proto)": "Parede (BIM proto)",
        "Update 3D": "Atualizar 3D",
        "BIM proto": "BIM proto",
        "Click the wall's first point": "Clique no primeiro ponto da parede",
        "Click the wall's second point, or type its length":
        "Clique no segundo ponto da parede ou digite o comprimento",
        "Draw a wall from two points, with thickness, height and base level.":
        "Desenha uma parede a partir de dois pontos, com espessura, altura "
        "e nível de base.",
        "Rebuild the walls' 3D from their stored parameters.":
        "Regenera o 3D das paredes a partir dos parâmetros guardados.",
        "{n} wall(s) updated": "{n} parede(s) atualizada(s)",
        "No BIM proto walls in the document.":
        "Não há paredes do BIM proto no documento.",
        "Levels": "Pavimentos", "Views": "Vistas", "Sheets": "Pranchas",
        "Project": "Projeto",
        "Join corners": "Unir cantos",
        "Sill": "Peitoril", "Door leaf": "Folha de porta",
        "Sills": "Peitoris (janelas)", "Door leaves": "Folhas de porta",
        "Block": "Bloco", "Room": "Ambiente",
        "Draw": "Desenhar", "Openings": "Aberturas", "Rooms": "Ambientes",
        "Plan 2D": "Planta 2D", "Elements": "Elementos",
        "Doors": "Portas", "Swing": "Giro", "Detect rooms": "Detectar",
        "Area table": "Áreas", "Generate plan…": "Gerar planta…",
        "Import DWG": "Importar DWG", "Block library": "Biblioteca",
        "Soon": "Em breve", "Plan to import": "Planta a importar",
        "Layers with walls": "Camadas com paredes", "Wall height (m)": "Altura das paredes (m)",
        "Door height (m)": "Altura das portas (m)", "Put the plan corner at 0,0": "Pôr o canto da planta em 0,0",
        "Open DWG or DXF": "Abrir DWG ou DXF",
        "Plan {n}: {count} pieces (y {y0:.0f} to {y1:.0f} m)": "Planta {n}: {count} peças (y de {y0:.0f} a {y1:.0f} m)",
        "Imported: {walls} walls, {openings} door openings, {headers} headers": "Importado: {walls} paredes, {openings} vãos de porta, {headers} vergas",
        "Import failed: {why}": "Falha ao importar: {why}", "Cut at (m)": "Corte a (m)", "Style": "Estilo",
        "Office": "Escritório", "CAD": "CAD", "Room names": "Nomes dos ambientes",
        "{n} room(s) detected": "{n} ambiente(s) detectado(s)",
        "No closed rooms found.": "Nenhum ambiente fechado encontrado.",
        "Plan saved: {path}": "Planta salva: {path}",
        "No rooms yet.": "Ainda não há ambientes.",
        "Estimated net area (m²)": "Área útil estimada (m²)",
        "cut": "corte", "below the cut": "abaixo do corte",
        "projection (above the cut)": "projeção (acima do corte)",
        "Frame": "Esquadria", "Railing": "Guarda-corpo",
        "{n} wall(s) joined": "{n} parede(s) unida(s)",
        "No wall corners to join.": "Não há cantos de parede para unir.",
        "Close the group you are editing, then run this again.":
        "Feche o grupo que está editando e repita.",
    },
}


def _t(text: str, **kw) -> str:
    from core.i18n import current_language
    out = _TEXTS.get(current_language(), {}).get(text, text)
    return out.format(**kw) if kw else out


class BimProtoError(Exception):
    """A parameter record that cannot be turned into geometry."""


# ---------------------------------------------------------------------------
# Dados: níveis (projeto inteiro) e registro da parede (por grupo)
# ---------------------------------------------------------------------------

def project_data(scene) -> dict:
    """Os dados do projeto desta extensão — ``scene.plugin_data[KEY]``,
    uma cópia. Sempre com ``levels``."""
    raw = scene.plugin_data.get(KEY)
    data = copy.deepcopy(raw) if isinstance(raw, dict) else {}
    if not isinstance(data.get("levels"), list) or not data["levels"]:
        data["levels"] = copy.deepcopy(DEFAULT_LEVELS)
    return data


def level_z(scene, level_id: str) -> float:
    """Elevação (m) do nível ``level_id``."""
    for lv in project_data(scene)["levels"]:
        if lv.get("id") == level_id:
            return float(lv["z"])
    raise BimProtoError(f"unknown level {level_id!r}")


def wall_record(group):
    """O registro de parede de ``group`` (``group.ext[KEY]``) ou ``None``."""
    ext = getattr(group, "ext", None)
    rec = ext.get(KEY) if isinstance(ext, dict) else None
    if isinstance(rec, dict) and rec.get("kind") == "wall":
        return rec
    return None


def normalize_record(rec: dict) -> dict:
    """Valida e limpa um registro; levanta :class:`BimProtoError`.

    ``a`` e ``b`` são pontos (x, y) em metros; ``base_offset`` é a folga
    sobre o nível de base."""
    try:
        a = [float(v) for v in rec["a"]][:2]
        b = [float(v) for v in rec["b"]][:2]
        out = {
            "kind": "wall",
            "a": a, "b": b,
            "thickness": float(rec["thickness"]),
            "height": float(rec["height"]),
            "level": str(rec["level"]),
            "base_offset": float(rec.get("base_offset", 0.0)),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise BimProtoError(f"bad wall record: {exc}") from exc
    if len(a) != 2 or len(b) != 2:
        raise BimProtoError("a and b need two coordinates")
    if not all(math.isfinite(v) for v in a + b + [
            out["thickness"], out["height"], out["base_offset"]]):
        raise BimProtoError("non-finite value in the wall record")
    if out["thickness"] <= _EPS or out["height"] <= _EPS:
        raise BimProtoError("thickness and height must be positive")
    length = math.hypot(b[0] - a[0], b[1] - a[1])
    if length <= _EPS:
        raise BimProtoError("the wall has no length")
    out["openings"] = _normalize_openings(rec.get("openings"), length,
                                          out["height"])
    return out


def _normalize_openings(raw, length: float, height: float) -> list:
    """Aberturas da parede, em coordenadas LOCAIS: ``s0``/``s1`` ao longo do
    eixo (0 = ponto ``a``), ``sill`` (peitoril) e ``head`` (topo do vão) em
    metros acima da base. ``sill`` 0 é uma porta. Cada abertura tem de caber
    inteira na parede, sem encostar nas pontas nem no topo e sem se sobrepor
    às outras; senão levanta :class:`BimProtoError`."""
    out = []
    for o in raw or []:
        try:
            s0, s1 = sorted((float(o["s0"]), float(o["s1"])))
            sill = float(o.get("sill", 0.0))
            head = float(o["head"])
        except (KeyError, TypeError, ValueError) as exc:
            raise BimProtoError(f"bad opening: {exc}") from exc
        if not all(math.isfinite(v) for v in (s0, s1, sill, head)):
            raise BimProtoError("non-finite value in an opening")
        if s0 <= _EPS or s1 >= length - _EPS or s1 - s0 <= _EPS:
            raise BimProtoError("an opening must lie inside the wall's length")
        if sill < 0 or head <= sill or head >= height - _EPS:
            raise BimProtoError("an opening needs 0 <= sill < head < height")
        out.append({"s0": s0, "s1": s1, "sill": sill, "head": head})
    out.sort(key=lambda o: o["s0"])
    for prev, nxt in zip(out, out[1:]):
        if nxt["s0"] < prev["s1"] + _EPS:
            raise BimProtoError("openings overlap")
    return out


# ---------------------------------------------------------------------------
# Geometria
# ---------------------------------------------------------------------------

def wall_mesh(rec: dict, base_z: float) -> Mesh:
    """Prisma fechado da parede: planta retangular (eixo a→b, centrada,
    ``thickness``) extrudada de ``base_z`` a ``base_z + height``; todas as
    normais para fora. Coordenadas de mundo (Z para cima)."""
    ax, ay = rec["a"]
    bx, by = rec["b"]
    dx, dy = bx - ax, by - ay
    length = math.hypot(dx, dy)
    nx, ny = -dy / length, dx / length          # esquerda do eixo
    h = rec["thickness"] / 2.0
    if rec.get("openings"):
        return _wall_mesh_with_openings(rec, base_z, length)
    foot = [(ax - nx * h, ay - ny * h), (bx - nx * h, by - ny * h),
            (bx + nx * h, by + ny * h), (ax + nx * h, ay + ny * h)]  # anti-horário
    z0, z1 = base_z, base_z + rec["height"]
    low = [QVector3D(x, y, z0) for x, y in foot]
    top = [QVector3D(x, y, z1) for x, y in foot]
    mesh = Mesh()
    mesh.add_face(list(reversed(low)))                       # fundo, normal -Z
    mesh.add_face(top)                                       # topo, normal +Z
    for i in range(4):
        j = (i + 1) % 4
        mesh.add_face([low[i], low[j], top[j], top[i]])      # lado, para fora
    return mesh


def _newell(loop):
    """Normal (não unitária) de um laço 3D de tuplas."""
    nx = ny = nz = 0.0
    for i in range(len(loop)):
        c, n = loop[i], loop[(i + 1) % len(loop)]
        nx += (c[1] - n[1]) * (c[2] + n[2])
        ny += (c[2] - n[2]) * (c[0] + n[0])
        nz += (c[0] - n[0]) * (c[1] + n[1])
    return nx, ny, nz


def _pip(pt, poly) -> bool:
    """Ponto dentro de um polígono 2D (par-ímpar)."""
    x, y = pt
    inside = False
    for i in range(len(poly)):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % len(poly)]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def _wall_mesh_with_openings(rec: dict, base_z: float, length: float) -> Mesh:
    """Parede com portas e janelas de verdade: o PERFIL da elevação (s ao longo
    do eixo, z para cima) é um retângulo com entalhes (portas, que chegam ao
    chão) e furos (janelas), extrudado na espessura. As faces das duas
    pontas do perfil levam os furos como ``hole_loops``; os batentes são os
    quadriláteros das bordas. Todas as normais para fora do sólido."""
    ax, ay = rec["a"]
    ux, uy = (rec["b"][0] - ax) / length, (rec["b"][1] - ay) / length
    nx, ny = -uy, ux
    half = rec["thickness"] / 2.0
    height = rec["height"]
    outline = [(0.0, 0.0)]
    holes = []
    for o in rec["openings"]:
        if o["sill"] <= _EPS:                       # porta: entalhe no pé
            outline += [(o["s0"], 0.0), (o["s0"], o["head"]),
                        (o["s1"], o["head"]), (o["s1"], 0.0)]
        else:                                       # janela: furo
            holes.append([(o["s0"], o["sill"]), (o["s1"], o["sill"]),
                          (o["s1"], o["head"]), (o["s0"], o["head"])])
    outline += [(length, 0.0), (length, height), (0.0, height)]

    def at(pt, t):
        s_, z_ = pt
        return (ax + ux * s_ + nx * t, ay + uy * s_ + ny * t, base_z + z_)

    def oriented(loop3, want):
        n = _newell(loop3)
        return loop3 if sum(a * b for a, b in zip(n, want)) > 0 else loop3[::-1]

    def vec(loop3):
        return [QVector3D(*p) for p in loop3]

    mesh = Mesh()
    for t, want in ((half, (nx, ny, 0.0)), (-half, (-nx, -ny, 0.0))):
        outer = oriented([at(p, t) for p in outline], want)
        inner = [oriented([at(p, t) for p in hole], tuple(-w for w in want))
                 for hole in holes]
        mesh.add_face(vec(outer), [vec(hl) for hl in inner])

    def material(pt) -> bool:
        return _pip(pt, outline) and not any(_pip(pt, hl) for hl in holes)

    for loop in [outline] + holes:
        for i in range(len(loop)):
            p, q = loop[i], loop[(i + 1) % len(loop)]
            ds, dz = q[0] - p[0], q[1] - p[1]
            size = math.hypot(ds, dz)
            if size <= _EPS:
                continue
            ps, pz = dz / size, -ds / size             # perpendicular no perfil
            probe = ((p[0] + q[0]) / 2 + ps * 1e-4, (p[1] + q[1]) / 2 + pz * 1e-4)
            if material(probe):                        # apontava para dentro
                ps, pz = -ps, -pz
            want = (ux * ps, uy * ps, pz)
            quad = [at(p, -half), at(q, -half), at(q, half), at(p, half)]
            mesh.add_face(vec(oriented(quad, want)))
    return mesh


def build_wall_group(scene, rec: dict, name: str | None = None) -> Group:
    """Um grupo novo (ainda fora de ``scene.groups``) com a parede de ``rec``."""
    rec = normalize_record(rec)
    base_z = level_z(scene, rec["level"]) + rec["base_offset"]
    g = Group(wall_mesh(rec, base_z), name=name or _next_name(scene))
    bim.tag_group(g, "IfcWall", g.name)
    g.ext = {KEY: rec}
    return g


def _next_name(scene, what: str = "Wall") -> str:
    kind = {"Wall": "wall", "Frame": "frame", "Railing": "frame",
            "Sill": "sill", "Door leaf": "leaf", "Block": "block",
            "Room": "room"}[what]
    n = sum(1 for g in scene.groups
            if (element_record(g) or {}).get("kind") == kind) + 1
    taken = {g.name for g in scene.groups}
    while f"{_t(what)} {n}" in taken:
        n += 1
    return f"{_t(what)} {n}"


def regenerate_wall(scene, group) -> None:
    """Refaz a malha de ``group`` a partir do SEU registro. A malha nova
    substitui a antiga (a antiga fica no comando, para o desfazer); nome,
    camada e etiqueta IFC do grupo ficam."""
    rec = wall_record(group)
    if rec is None:
        raise BimProtoError(f"«{group.name}» is not a BIM proto wall")
    rec = normalize_record(rec)
    base_z = level_z(scene, rec["level"]) + rec["base_offset"]
    # FRAGILE: troca ``group.mesh`` por outro objeto Mesh (em vez de editar a
    # malha no lugar). Comandos do núcleo guardam o grupo, não a malha, mas
    # nenhum contrato diz que a identidade da malha é estável.
    group.mesh = wall_mesh(rec, base_z)
    group.xform = None          # malha em coordenadas de mundo, sem matriz
    group.children = []
    group.ext = dict(group.ext or {}, **{KEY: rec})


def walls(scene) -> list:
    return [g for g in scene.groups if wall_record(g) is not None]


# ---------------------------------------------------------------------------
# Esquadrias: montantes + painéis (fachada)
# ---------------------------------------------------------------------------

#: Valores iniciais da esquadria (metros).
FRAME_DEFAULTS = {"depth": 0.06, "mullion": 0.05, "panel": "glass",
                  "panel_thickness": 0.01, "ifc": "IfcWindow"}


def frame_record(group):
    """O registro de esquadria de ``group`` (``group.ext[KEY]``) ou ``None``."""
    ext = getattr(group, "ext", None)
    rec = ext.get(KEY) if isinstance(ext, dict) else None
    if isinstance(rec, dict) and rec.get("kind") == "frame":
        return rec
    return None


def normalize_frame(rec: dict) -> dict:
    """Valida e limpa o registro de uma esquadria; levanta :class:`BimProtoError`.

    Eixo ``a``→``b`` (x, y em metros), ``depth`` (espessura do montante),
    ``height``, ``level``/``base_offset`` como na parede. ``divisions`` são as
    posições (s, ao longo do eixo; 0 = ``a``) dos CENTROS dos montantes, de
    largura ``mullion``; entre dois montantes vai um painel (``panel``:
    ``"glass"``, ``"mesh"`` ou ``"none"``) de espessura ``panel_thickness``,
    centrado na profundidade. ``ifc`` é a classe IFC do grupo."""
    try:
        out = {
            "kind": "frame",
            "a": [float(v) for v in rec["a"]][:2],
            "b": [float(v) for v in rec["b"]][:2],
            "depth": float(rec.get("depth", FRAME_DEFAULTS["depth"])),
            "height": float(rec["height"]),
            "level": str(rec["level"]),
            "base_offset": float(rec.get("base_offset", 0.0)),
            "mullion": float(rec.get("mullion", FRAME_DEFAULTS["mullion"])),
            "panel": str(rec.get("panel", FRAME_DEFAULTS["panel"])),
            "panel_thickness": float(rec.get(
                "panel_thickness", FRAME_DEFAULTS["panel_thickness"])),
            "ifc": str(rec.get("ifc", FRAME_DEFAULTS["ifc"])),
            "divisions": sorted(float(v) for v in rec.get("divisions") or []),
            "rails": sorted(({"z": float(r["z"]), "height": float(r["height"])}
                             for r in rec.get("rails") or []),
                            key=lambda r: r["z"]),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise BimProtoError(f"bad frame record: {exc}") from exc
    a, b = out["a"], out["b"]
    if len(a) != 2 or len(b) != 2:
        raise BimProtoError("a and b need two coordinates")
    nums = a + b + [out["depth"], out["height"], out["base_offset"],
                    out["mullion"], out["panel_thickness"]] + out["divisions"] \
        + [v for r in out["rails"] for v in (r["z"], r["height"])]
    if not all(math.isfinite(v) for v in nums):
        raise BimProtoError("non-finite value in the frame record")
    if out["depth"] <= _EPS or out["height"] <= _EPS \
            or out["mullion"] <= _EPS or out["panel_thickness"] <= _EPS:
        raise BimProtoError("depth, height, mullion and panel thickness "
                            "must be positive")
    if out["panel"] not in ("glass", "mesh", "none"):
        raise BimProtoError("panel must be 'glass', 'mesh' or 'none'")
    if out["panel_thickness"] > out["depth"] + _EPS:
        raise BimProtoError("the panel cannot be thicker than the frame")
    if out["ifc"] not in bim.IFC_CLASSES:
        raise BimProtoError(f"unknown IFC class {out['ifc']!r}")
    length = math.hypot(b[0] - a[0], b[1] - a[1])
    if length <= _EPS:
        raise BimProtoError("the frame has no length")
    half = out["mullion"] / 2.0
    prev_end = 0.0
    for c in out["divisions"]:
        if c < half - _EPS or c > length - half + _EPS:
            raise BimProtoError("a mullion must lie inside the frame's length")
        if c - half < prev_end - _EPS:
            raise BimProtoError("mullions overlap")
        prev_end = c + half
    top = 0.0
    for r in out["rails"]:
        if r["height"] <= _EPS or r["z"] < top - _EPS:
            raise BimProtoError("rails need a positive height and must not "
                                "overlap")
        top = r["z"] + r["height"]
    if top > out["height"] + _EPS:
        raise BimProtoError("a rail must lie inside the frame's height")
    if out["rails"] and out["panel"] != "none" \
            and out["panel_thickness"] >= out["depth"] - 2 * _RAIL_RECESS:
        raise BimProtoError("with rails the panel must be thinner than the "
                            "frame")
    return out


def _add_prism(mesh, foot, z0, z1) -> None:
    """Prisma fechado sobre a planta ``foot`` (lista de (x, y), ANTI-HORÁRIA)
    de ``z0`` a ``z1``; todas as normais para fora."""
    low = [QVector3D(x, y, z0) for x, y in foot]
    top = [QVector3D(x, y, z1) for x, y in foot]
    mesh.add_face(list(reversed(low)))
    mesh.add_face(top)
    for i in range(len(foot)):
        j = (i + 1) % len(foot)
        mesh.add_face([low[i], low[j], top[j], top[i]])


def _add_box(mesh, origin, u, n, s0, s1, t0, t1, z0, z1) -> None:
    """Prisma fechado: ``s`` ao longo de ``u``, ``t`` ao longo de ``n`` (esquerda
    de ``u``), de ``z0`` a ``z1``; todas as normais para fora."""
    foot = [(s0, t0), (s1, t0), (s1, t1), (s0, t1)]           # anti-horário
    _add_prism(mesh, [(origin[0] + u[0] * s + n[0] * t,
                       origin[1] + u[1] * s + n[1] * t) for s, t in foot],
               z0, z1)


def frame_mesh(rec: dict, base_z: float) -> Mesh:
    """Montantes (prismas ``mullion`` x ``depth``) nas ``divisions`` e, entre
    eles, painéis finos de ``panel_thickness``; tudo de ``base_z`` a
    ``base_z + height``. ``rails`` (``z``, ``height`` acima da base) são
    travessas de montante a montante, e os painéis ficam nos intervalos entre
    elas. Cada peça é um sólido fechado e separado (nada invade nada), todos
    num só grupo."""
    ax, ay = rec["a"]
    bx, by = rec["b"]
    length = math.hypot(bx - ax, by - ay)
    u = ((bx - ax) / length, (by - ay) / length)
    n = (-u[1], u[0])
    z0, z1 = base_z, base_z + rec["height"]
    d2, m2 = rec["depth"] / 2.0, rec["mullion"] / 2.0
    p2 = rec["panel_thickness"] / 2.0
    mesh = Mesh()
    cursor = 0.0
    cells = []
    for c in rec["divisions"]:
        _add_box(mesh, (ax, ay), u, n, c - m2, c + m2, -d2, d2, z0, z1)
        cells.append((cursor, c - m2))
        cursor = c + m2
    cells.append((cursor, length))
    # intervalos de altura livres entre as travessas (onde cabe painel)
    spans, z = [], 0.0
    for r in rec["rails"]:
        if r["z"] - z > _MIN_PANEL:
            spans.append((z, r["z"]))
        z = r["z"] + r["height"]
    if rec["height"] - z > _MIN_PANEL:
        spans.append((z, rec["height"]))
    for s0, s1 in cells:
        if s1 - s0 <= _MIN_PANEL:
            continue
        for r in rec["rails"]:                       # travessa: de montante a montante
            _add_box(mesh, (ax, ay), u, n, s0, s1, -d2 + _RAIL_RECESS,
                     d2 - _RAIL_RECESS, z0 + r["z"], z0 + r["z"] + r["height"])
        if rec["panel"] != "none":
            for za, zb in spans:
                _add_box(mesh, (ax, ay), u, n, s0, s1, -p2, p2, z0 + za, z0 + zb)
    return mesh


def build_frame_group(scene, rec: dict, name: str | None = None) -> Group:
    """Um grupo novo (ainda fora de ``scene.groups``) com a esquadria de ``rec``."""
    rec = normalize_frame(rec)
    base_z = level_z(scene, rec["level"]) + rec["base_offset"]
    g = Group(frame_mesh(rec, base_z),
              name=name or _next_name(scene, "Railing" if rec["ifc"]
                                      == "IfcRailing" else "Frame"))
    bim.tag_group(g, rec["ifc"], g.name)
    g.ext = {KEY: rec}
    return g


def regenerate_frame(scene, group) -> None:
    """Como :func:`regenerate_wall`, para uma esquadria."""
    rec = frame_record(group)
    if rec is None:
        raise BimProtoError(f"«{group.name}» is not a BIM proto frame")
    rec = normalize_frame(rec)
    base_z = level_z(scene, rec["level"]) + rec["base_offset"]
    group.mesh = frame_mesh(rec, base_z)     # FRAGILE: mesmo motivo de regenerate_wall
    group.xform = None
    group.children = []
    group.ext = dict(group.ext or {}, **{KEY: rec})


def frames(scene) -> list:
    return [g for g in scene.groups if frame_record(g) is not None]


# ---------------------------------------------------------------------------
# Peitoril e folha de porta (peças que nascem das aberturas de uma parede)
# ---------------------------------------------------------------------------

def _axis_frame(rec):
    """``(origem, u, n, comprimento)`` do eixo ``a``→``b`` de um registro."""
    ax, ay = rec["a"]
    bx, by = rec["b"]
    length = math.hypot(bx - ax, by - ay)
    if length <= _EPS:
        raise BimProtoError("the axis has no length")
    u = ((bx - ax) / length, (by - ay) / length)
    return (ax, ay), u, (-u[1], u[0]), length


def _common(rec, out):
    out["a"] = [float(v) for v in rec["a"]][:2]
    out["b"] = [float(v) for v in rec["b"]][:2]
    out["level"] = str(rec["level"])
    out["base_offset"] = float(rec.get("base_offset", 0.0))
    return out


def normalize_sill(rec: dict) -> dict:
    """Peitoril (pingadeira) de uma janela: uma laje que sai da FACE da parede.

    ``a``/``b`` são o eixo da parede; ``s0``/``s1`` o vão (ao longo do eixo);
    ``z`` a cota do topo do peitoril acima da base; ``wall_thickness`` a
    espessura da parede; ``projection`` quanto sai da face; ``overhang`` quanto
    passa de cada lado do vão; ``side`` +1 (esquerda do eixo) ou −1. A peça
    fica FORA da parede, sem invadir o sólido dela."""
    try:
        out = _common(rec, {
            "kind": "sill",
            "s0": float(min(rec["s0"], rec["s1"])),
            "s1": float(max(rec["s0"], rec["s1"])),
            "z": float(rec["z"]),
            "wall_thickness": float(rec["wall_thickness"]),
            "thickness": float(rec.get("thickness", 0.03)),
            "projection": float(rec.get("projection", 0.05)),
            "overhang": float(rec.get("overhang", 0.03)),
            "side": int(rec.get("side", 1)),
        })
    except (KeyError, TypeError, ValueError) as exc:
        raise BimProtoError(f"bad sill record: {exc}") from exc
    _, _, _, length = _axis_frame(out)
    nums = out["a"] + out["b"] + [out[k] for k in (
        "s0", "s1", "z", "wall_thickness", "thickness", "projection",
        "overhang", "base_offset")]
    if not all(math.isfinite(v) for v in nums):
        raise BimProtoError("non-finite value in the sill record")
    if out["side"] not in (-1, 1):
        raise BimProtoError("side must be 1 or -1")
    if min(out["thickness"], out["projection"], out["wall_thickness"]) <= _EPS \
            or out["overhang"] < 0 or out["s1"] - out["s0"] <= _EPS:
        raise BimProtoError("a sill needs positive thickness, projection and "
                            "width")
    if out["z"] - out["thickness"] < -_EPS:
        raise BimProtoError("the sill would go below the wall's base")
    if out["s0"] - out["overhang"] < -_EPS \
            or out["s1"] + out["overhang"] > length + _EPS:
        raise BimProtoError("the sill must lie inside the wall's length")
    return out


def sill_mesh(rec: dict, base_z: float) -> Mesh:
    origin, u, n, _ = _axis_frame(rec)
    half = rec["wall_thickness"] / 2.0
    t_a, t_b = rec["side"] * half, rec["side"] * (half + rec["projection"])
    mesh = Mesh()
    _add_box(mesh, origin, u, n, rec["s0"] - rec["overhang"],
             rec["s1"] + rec["overhang"], min(t_a, t_b), max(t_a, t_b),
             base_z + rec["z"] - rec["thickness"], base_z + rec["z"])
    return mesh


#: Porta detalhada (medidas lidas dos blocos de porta de um DWG real; metros).
LEAF_DETAIL = {"jamb_width": 0.035, "stop_depth": 0.010, "trim_width": 0.06,
               "trim_thickness": 0.012, "stile": 0.05, "glass_thickness": 0.01}


def normalize_leaf(rec: dict) -> dict:
    """Porta dentro de um vão de parede (``s0``/``s1`` ao longo do eixo
    ``a``→``b``, ``head`` = topo do vão acima da base).

    ``style``:

    * ``"swing"`` (padrão): folha maciça de ``thickness`` que gira em torno da
      dobradiça; ``hinge`` ``"a"``/``"b"`` é o lado da dobradiça, ``side`` (+1
      esquerda do eixo, −1 direita) para onde abre, ``angle`` em graus (0 =
      fechada), ``clearance`` a folga em volta. Com ``frame`` verdadeiro leva
      MARCO (dois batentes com rebaixo ``stop_depth`` para a folha encostar e
      uma travessa em cima, de largura ``jamb_width``); com ``trim`` leva
      GUARNIÇÃO (alizar de ``trim_width`` x ``trim_thickness`` nas duas faces
      da parede); com ``arc`` desenha o arco de giro como arestas.
    * ``"sliding"``: porta de correr de vidro: um bastidor (montantes de
      ``stile`` e travessas) de ``thickness`` com vidro de ``glass_thickness``;
      ``open`` (0 a 1) é quanto correu para o lado ``hinge`` (onde estaciona).

    ``wall_thickness`` é a espessura da parede (profundidade do marco e posição
    da guarnição); ``t_offset`` desloca o plano da porta do eixo da parede."""
    d = LEAF_DETAIL
    try:
        out = _common(rec, {
            "kind": "leaf",
            "style": str(rec.get("style", "swing")),
            "s0": float(min(rec["s0"], rec["s1"])),
            "s1": float(max(rec["s0"], rec["s1"])),
            "head": float(rec["head"]),
            "thickness": float(rec.get("thickness", 0.04)),
            "hinge": str(rec.get("hinge", "a")),
            "side": int(rec.get("side", 1)),
            "angle": float(rec.get("angle", 0.0)),
            "clearance": float(rec.get("clearance", 0.01)),
            "frame": bool(rec.get("frame", False)),
            "trim": bool(rec.get("trim", False)),
            "arc": bool(rec.get("arc", False)),
            "wall_thickness": float(rec.get("wall_thickness", 0.15)),
            "t_offset": float(rec.get("t_offset", 0.0)),
            "open": float(rec.get("open", 0.0)),
            **{k: float(rec.get(k, v)) for k, v in d.items()},
        })
    except (KeyError, TypeError, ValueError) as exc:
        raise BimProtoError(f"bad door record: {exc}") from exc
    _, _, _, length = _axis_frame(out)
    nums = out["a"] + out["b"] + [out[k] for k in (
        "s0", "s1", "head", "thickness", "angle", "clearance", "base_offset",
        "wall_thickness", "t_offset", "open", *d)]
    if not all(math.isfinite(v) for v in nums):
        raise BimProtoError("non-finite value in the door record")
    if out["style"] not in ("swing", "sliding"):
        raise BimProtoError("style must be 'swing' or 'sliding'")
    if out["hinge"] not in ("a", "b") or out["side"] not in (-1, 1):
        raise BimProtoError("hinge must be 'a' or 'b' and side 1 or -1")
    if not 0.0 <= out["angle"] <= 150.0:
        raise BimProtoError("angle must be between 0 and 150 degrees")
    if not 0.0 <= out["open"] <= 1.0:
        raise BimProtoError("open must be between 0 and 1")
    c = out["clearance"]
    jw = out["jamb_width"] if out["frame"] else 0.0
    if c < 0 or out["thickness"] <= _EPS or out["wall_thickness"] <= _EPS \
            or min(d.values()) < 0 \
            or out["s1"] - out["s0"] <= 2 * (c + jw) + _EPS \
            or out["head"] <= 2 * (c + jw) + _EPS:
        raise BimProtoError("the door does not fit its opening")
    if out["frame"] and out["thickness"] > out["wall_thickness"] - _EPS:
        raise BimProtoError("the leaf cannot be thicker than the wall")
    if out["s0"] < -_EPS or out["s1"] > length + _EPS:
        raise BimProtoError("the opening must lie inside the wall's length")
    if out["style"] == "sliding" and (out["stile"] * 2 >= out["s1"] - out["s0"]
                                      or out["glass_thickness"] >= out["thickness"]):
        raise BimProtoError("the sash is too small for its stiles or glass")
    return out


def _ccw(loop):
    area2 = sum(loop[i][0] * loop[(i + 1) % len(loop)][1]
                - loop[(i + 1) % len(loop)][0] * loop[i][1]
                for i in range(len(loop)))
    return loop if area2 > 0 else loop[::-1]


def leaf_mesh(rec: dict, base_z: float) -> Mesh:
    """Veja :func:`normalize_leaf`. Cada peça (folha, batentes, travessa,
    guarnições, montantes, vidro) é um sólido fechado e separado."""
    origin, u, n, _ = _axis_frame(rec)
    mesh = Mesh()
    z0 = base_z
    c = rec["clearance"]
    side, hinge = rec["side"], rec["hinge"]
    head = rec["head"]
    D, lt, off = rec["wall_thickness"], rec["thickness"], rec["t_offset"]
    jw = 0.0
    def world(s_, t_):
        return (origin[0] + u[0] * s_ + n[0] * t_, origin[1] + u[1] * s_ + n[1] * t_)

    def plan_prism(loop_st, za, zb):
        _add_prism(mesh, _ccw([world(s_, t_) for s_, t_ in loop_st]), za, zb)

    if rec["style"] == "sliding":
        st, glt = rec["stile"], rec["glass_thickness"]
        width = rec["s1"] - rec["s0"]
        shift = (-1.0 if hinge == "a" else 1.0) * rec["open"] * width
        sa, sb = rec["s0"] + shift + c, rec["s1"] + shift - c
        ta, tb = off - lt / 2, off + lt / 2
        za, zb = z0 + c, z0 + head - c
        plan_prism([(sa, ta), (sa + st, ta), (sa + st, tb), (sa, tb)], za, zb)
        plan_prism([(sb - st, ta), (sb, ta), (sb, tb), (sb - st, tb)], za, zb)
        # travessas de montante a montante, 1 mm mais rasas (nada divide vértice)
        r = _RAIL_RECESS
        for zr0, zr1 in ((za, za + st), (zb - st, zb)):
            plan_prism([(sa + st, ta + r), (sb - st, ta + r),
                        (sb - st, tb - r), (sa + st, tb - r)], zr0, zr1)
        plan_prism([(sa + st, off - glt / 2), (sb - st, off - glt / 2),
                    (sb - st, off + glt / 2), (sa + st, off + glt / 2)],
                   za + st, zb - st)
        return mesh

    if rec["frame"]:
        jw, sd = rec["jamb_width"], rec["stop_depth"]
        # perfil do batente em (k para dentro do vão, x para o lado de abrir):
        # a parte do lado contrário ao giro leva o rebaixo ``sd``
        prof = [(0, -D / 2), (0, D / 2), (jw, D / 2), (jw, D / 2 - lt),
                (jw + sd, D / 2 - lt), (jw + sd, -D / 2)]
        for edge, d_in in ((rec["s0"], 1.0), (rec["s1"], -1.0)):
            plan_prism([(edge + d_in * k, off + side * x) for k, x in prof],
                       z0, z0 + head)
        # travessa de cima: o mesmo perfil, em duas caixas (parte do rebaixo,
        # mais baixa e mais curta; parte da folha), 1 mm abaixo do topo do vão
        # para não dividir vértice com os batentes
        r = _RAIL_RECESS
        x_step = D / 2 - lt
        for (xa, xb), drop, s_in in (((-D / 2, x_step), jw + sd, jw + sd),
                                     ((x_step, D / 2), jw, jw)):
            ta, tb = sorted((off + side * xa, off + side * xb))
            _add_box(mesh, origin, u, n, rec["s0"] + s_in, rec["s1"] - s_in,
                     ta, tb, z0 + head - drop, z0 + head - r)
    if rec["trim"]:
        tw, tt = rec["trim_width"], rec["trim_thickness"]
        ext = jw - tw                              # a guarnição passa do vão
        faces = ((D / 2, D / 2 + tt), (-D / 2 - tt, -D / 2))
        for edge, d_in in ((rec["s0"], 1.0), (rec["s1"], -1.0)):
            for xa, xb in faces:
                ta, tb = sorted((off + side * xa, off + side * xb))
                sa, sb = sorted((edge + d_in * (jw - _RAIL_RECESS), edge + d_in * ext))
                plan_prism([(sa, ta), (sb, ta), (sb, tb), (sa, tb)],
                           z0, z0 + head - jw - _RAIL_RECESS)
        for xa, xb in faces:                       # guarnição de cima
            ta, tb = sorted((off + side * xa, off + side * xb))
            sa, sb = rec["s0"] + ext, rec["s1"] - ext
            plan_prism([(sa, ta), (sb, ta), (sb, tb), (sa, tb)],
                       z0 + head - jw, z0 + head - jw + tw)

    # folha: gira em torno da dobradiça, que fica entre os batentes
    clear = rec["s1"] - rec["s0"] - 2 * jw
    width = clear - 2 * c
    d = 1.0 if hinge == "a" else -1.0
    hinge_s = rec["s0"] + jw + c if hinge == "a" else rec["s1"] - jw - c
    th = math.radians(rec["angle"])
    e_s = (d * math.cos(th), side * math.sin(th))             # ao longo da folha
    e_t = (-e_s[1], e_s[0])                                   # espessura
    # fechada, a folha fica no vão do lado do giro, junto ao rebaixo
    t_mid = off + side * (D / 2 - lt / 2) if rec["frame"] else off
    foot = []
    for x, tt in ((0, -lt / 2), (width, -lt / 2), (width, lt / 2), (0, lt / 2)):
        foot.append((hinge_s + e_s[0] * x + e_t[0] * tt,
                     t_mid + e_s[1] * x + e_t[1] * tt))
    plan_prism(foot, z0 + c, z0 + head - jw - c)
    if rec["arc"]:                                           # símbolo do arco de giro
        steps, zr = 24, z0 + 0.002
        pts = []
        for i in range(steps + 1):
            a_ = math.pi / 2 * i / steps
            pts.append(world(hinge_s + d * width * math.cos(a_),
                             t_mid + side * width * math.sin(a_)))
        for p, q in zip(pts, pts[1:]):
            mesh.add_edge(QVector3D(p[0], p[1], zr), QVector3D(q[0], q[1], zr))
    return mesh


# ---------------------------------------------------------------------------
# Ambientes (zonas de piso): detectados pelas paredes, com nome e área
# ---------------------------------------------------------------------------

def _footprints(records):
    """Retângulos (centro do eixo, comprimento, espessura) das paredes e
    esquadrias — o que fecha um ambiente (porta fechada: o retângulo da parede
    cobre o vão)."""
    out = []
    for r in records:
        if r.get("kind", "wall") == "frame":
            thick = r["depth"]
        elif r.get("kind", "wall") == "wall":
            thick = r["thickness"]
        else:
            continue
        (ax, ay), (bx, by) = r["a"], r["b"]
        ln = math.hypot(bx - ax, by - ay)
        if ln > _EPS:
            out.append((ax, ay, (bx - ax) / ln, (by - ay) / ln, ln, thick))
    return out


def _chain_loops(free):
    """Contornos (listas de vértices (i, j) na grade) da máscara ``free``
    (2D de bool, índice [j, i]). A região livre fica à ESQUERDA: contorno externo
    anti-horário, ilhas (buracos) horárias."""
    import numpy as np
    ny, nx = free.shape
    pad = np.zeros((ny + 2, nx + 2), dtype=bool)
    pad[1:-1, 1:-1] = free
    edges: dict = {}

    def add(a, b):
        edges.setdefault(a, []).append(b)

    below = pad[:-2, 1:-1]          # célula (j-1, i)
    above = pad[2:, 1:-1]
    left = pad[1:-1, :-2]
    right = pad[1:-1, 2:]
    for j, i in zip(*np.nonzero(free & ~below)):      # borda de baixo: da esquerda p/ direita
        add((int(i), int(j)), (int(i) + 1, int(j)))
    for j, i in zip(*np.nonzero(free & ~right)):      # borda da direita: de baixo p/ cima
        add((int(i) + 1, int(j)), (int(i) + 1, int(j) + 1))
    for j, i in zip(*np.nonzero(free & ~above)):      # borda de cima: da direita p/ esquerda
        add((int(i) + 1, int(j) + 1), (int(i), int(j) + 1))
    for j, i in zip(*np.nonzero(free & ~left)):       # borda da esquerda: de cima p/ baixo
        add((int(i), int(j) + 1), (int(i), int(j)))
    loops = []
    while edges:
        start = next(iter(edges))
        loop, cur, prev_dir = [start], start, None
        while True:
            outs = edges[cur]
            if len(outs) == 1 or prev_dir is None:
                nxt = outs.pop(0)
            else:                                   # vértice de sela: vira à esquerda
                def turn(o):
                    d = (o[0] - cur[0], o[1] - cur[1])
                    return prev_dir[0] * d[1] - prev_dir[1] * d[0]
                nxt = max(outs, key=turn)
                outs.remove(nxt)
            if not outs:
                del edges[cur]
            prev_dir = (nxt[0] - cur[0], nxt[1] - cur[1])
            cur = nxt
            if cur == start:
                break
            loop.append(cur)
        loops.append(loop)
    return loops


def _simplify_ring(pts, tol):
    """Douglas-Peucker num anel fechado (lista de (x, y))."""
    if len(pts) <= 4:
        return pts
    def dist(p, a, b):
        dx, dy = b[0] - a[0], b[1] - a[1]
        ln = math.hypot(dx, dy)
        if ln < 1e-12:
            return math.hypot(p[0] - a[0], p[1] - a[1])
        return abs((p[0] - a[0]) * dy - (p[1] - a[1]) * dx) / ln

    def rdp(seq):
        a, b = seq[0], seq[-1]
        idx, dmax = 0, 0.0
        for k in range(1, len(seq) - 1):
            d = dist(seq[k], a, b)
            if d > dmax:
                idx, dmax = k, d
        if dmax > tol:
            return rdp(seq[:idx + 1])[:-1] + rdp(seq[idx:])
        return [a, b]

    far = max(range(len(pts)), key=lambda k: math.hypot(pts[k][0] - pts[0][0],
                                                          pts[k][1] - pts[0][1]))
    first = rdp(pts[:far + 1])
    second = rdp(pts[far:] + [pts[0]])
    return first[:-1] + second[:-1]


def _ring_area(pts) -> float:
    """Área COM SINAL (anti-horário positiva) de um anel (lista de (x, y))."""
    return sum(pts[i][0] * pts[(i + 1) % len(pts)][1]
               - pts[(i + 1) % len(pts)][0] * pts[i][1]
               for i in range(len(pts))) / 2.0


def detect_rooms(records, *, cell: float = 0.01, min_area: float = 1.0,
                 extra=()) -> list:
    """Acha os AMBIENTES fechados pelas paredes (e esquadrias) de ``records``: o
    espaço livre, separado em regiões, menos a região de fora. Cada ambiente é
    ``{"outline": [[x, y]…] (anti-horário), "holes": [[[x, y]…]…] (pilares e
    ilhas), "area": m²}``, do maior para o menor.

    Porta fechada para este fim (o retângulo da parede cobre o vão), então a
    porta SEPARA os cômodos; uma abertura sem parede (um vão sem peça) JUNTA os
    dois lados — nesse caso ponha uma peça ou uma entrada em ``extra`` (outros
    registros com ``a``, ``b`` e ``thickness``). ``cell`` é a grade (m); a área sai
    da grade e o contorno é simplificado a ~1,2 célula. Regiões menores que
    ``min_area`` não viram ambiente."""
    import numpy as np
    feet = _footprints(list(records) + list(extra))
    if not feet:
        return []
    xs = [f[0] for f in feet] + [f[0] + f[2] * f[4] for f in feet]
    ys = [f[1] for f in feet] + [f[1] + f[3] * f[4] for f in feet]
    pad_m = 1.0
    x0, y0 = min(xs) - pad_m, min(ys) - pad_m
    nx = int((max(xs) + pad_m - x0) / cell) + 2
    ny = int((max(ys) + pad_m - y0) / cell) + 2
    if nx * ny > 40_000_000:
        raise BimProtoError("the plan is too large for this cell size")
    solid = np.zeros((ny, nx), dtype=bool)
    for ax, ay, ux, uy, ln, th in feet:
        reach = ln + th
        i0 = max(int((min(ax, ax + ux * ln) - th - x0) / cell) - 1, 0)
        i1 = min(int((max(ax, ax + ux * ln) + th - x0) / cell) + 2, nx)
        j0 = max(int((min(ay, ay + uy * ln) - th - y0) / cell) - 1, 0)
        j1 = min(int((max(ay, ay + uy * ln) + th - y0) / cell) + 2, ny)
        gx = x0 + (np.arange(i0, i1) + 0.5) * cell
        gy = y0 + (np.arange(j0, j1) + 0.5) * cell
        X, Y = np.meshgrid(gx, gy)
        s = (X - ax) * ux + (Y - ay) * uy
        t = -(X - ax) * uy + (Y - ay) * ux
        solid[j0:j1, i0:i1] |= (s >= 0) & (s <= ln) & (np.abs(t) <= th / 2.0)
    free = ~solid
    # componentes conexos (4 vizinhos) por varredura
    labels = np.zeros((ny, nx), dtype=np.int32)
    comps, count = [], 0
    flat_free = free.ravel()
    flat_lab = labels.ravel()
    for start in np.flatnonzero(flat_free):
        if flat_lab[start]:
            continue
        count += 1
        stack, size, touches = [int(start)], 0, False
        flat_lab[start] = count
        while stack:
            c = stack.pop()
            size += 1
            j, i = divmod(c, nx)
            if i == 0 or j == 0 or i == nx - 1 or j == ny - 1:
                touches = True
            for n_ in (c - 1, c + 1, c - nx, c + nx):
                if 0 <= n_ < flat_free.size and flat_free[n_] and not flat_lab[n_]:
                    if (n_ == c - 1 and i == 0) or (n_ == c + 1 and i == nx - 1):
                        continue
                    flat_lab[n_] = count
                    stack.append(n_)
        comps.append((count, size, touches))
    out = []
    for lab, size, touches in comps:
        if touches or size * cell * cell < min_area:
            continue
        mask = labels == lab
        rings = []
        for loop in _chain_loops(mask):
            pts = [(x0 + i * cell, y0 + j * cell) for i, j in loop]
            simp = _simplify_ring(pts, 1.2 * cell)
            if len(simp) >= 3 and abs(_ring_area(simp)) > cell * cell:
                rings.append(simp)
        if not rings:
            continue
        outer = max(rings, key=_ring_area)
        holes = [r for r in rings if r is not outer and _ring_area(r) < 0]
        out.append({"outline": [[round(x, 4), round(y, 4)] for x, y in outer],
                    "holes": [[[round(x, 4), round(y, 4)] for x, y in h] for h in holes],
                    "area": round(size * cell * cell, 3)})
    out.sort(key=lambda r: -r["area"])
    return out


def normalize_room(rec: dict) -> dict:
    """Registro de um ambiente (zona de piso): ``outline`` (anel anti-horário de
    pontos (x, y) em metros), ``holes`` (anéis dos pilares/ilhas), ``name``,
    ``thickness`` da laje fina de piso e ``level``/``base_offset``. A ``area`` é
    DERIVADA do contorno (área do anel menos os buracos)."""
    try:
        outline = [[float(x), float(y)] for x, y in rec["outline"]]
        holes = [[[float(x), float(y)] for x, y in h] for h in rec.get("holes") or []]
        out = {"kind": "room", "name": str(rec.get("name", "")), "outline": outline,
               "holes": holes, "level": str(rec["level"]),
               "base_offset": float(rec.get("base_offset", 0.0)),
               "thickness": float(rec.get("thickness", 0.01)),
               "use": str(rec.get("use", ""))}
    except (KeyError, TypeError, ValueError) as exc:
        raise BimProtoError(f"bad room record: {exc}") from exc
    if len(outline) < 3 or any(len(h) < 3 for h in holes):
        raise BimProtoError("a room needs a ring of at least 3 points")
    nums = [v for p in outline for v in p] + [v for h in holes for p in h for v in p] \
        + [out["base_offset"], out["thickness"]]
    if not all(math.isfinite(v) for v in nums):
        raise BimProtoError("non-finite value in the room record")
    if out["thickness"] <= _EPS:
        raise BimProtoError("the floor slab needs a positive thickness")
    if _ring_area(outline) <= _EPS:
        out["outline"] = outline[::-1]                  # sempre anti-horário
    if _ring_area(out["outline"]) <= _EPS:
        raise BimProtoError("the room has no area")
    out["holes"] = [h if _ring_area(h) < 0 else h[::-1] for h in holes]
    out["area"] = round(room_area(out), 3)
    return out


def room_area(rec) -> float:
    """Área (m²) do ambiente: o anel externo menos os buracos."""
    return _ring_area([tuple(p) for p in rec["outline"]]) \
        + sum(_ring_area([tuple(p) for p in h]) for h in rec.get("holes") or [])


def room_mesh(rec: dict, base_z: float) -> Mesh:
    """Laje fina de piso do ambiente, fechada, com os pilares como furos."""
    outer = [tuple(p) for p in rec["outline"]]
    holes = [[tuple(p) for p in h] for h in rec["holes"]]
    z0, z1 = base_z, base_z + rec["thickness"]

    def loop(pts, z):
        return [QVector3D(x, y, z) for x, y in pts]

    mesh = Mesh()
    mesh.add_face(list(reversed(loop(outer, z0))),
                  [list(reversed(loop(h, z0))) for h in holes])
    mesh.add_face(loop(outer, z1), [loop(h, z1) for h in holes])
    for ring in [outer] + holes:
        for i in range(len(ring)):
            j = (i + 1) % len(ring)
            a, b = ring[i], ring[j]
            mesh.add_face([QVector3D(a[0], a[1], z0), QVector3D(b[0], b[1], z0),
                           QVector3D(b[0], b[1], z1), QVector3D(a[0], a[1], z1)])
    return mesh


def room_record(group):
    ext = getattr(group, "ext", None)
    rec = ext.get(KEY) if isinstance(ext, dict) else None
    return rec if isinstance(rec, dict) and rec.get("kind") == "room" else None


def rooms(scene) -> list:
    return [g for g in scene.groups if room_record(g) is not None]


def create_rooms(viewport, found, *, level="PB", names=None, thickness=0.01) -> list:
    """Cria os ambientes de ``found`` (a saída de :func:`detect_rooms`) numa
    zona de piso cada — UM passo de desfazer, tudo ou nada. ``names`` é uma lista
    na mesma ordem (ou um ``dict`` índice→nome); sem nome, ``Ambiente N``."""
    found = list(found)
    made: list = []

    def mutate(scene):
        scene.plugin_data[KEY] = project_data(scene)
        for idx, f in enumerate(found):
            nm = (names or {}).get(idx) if isinstance(names, dict) else \
                (names[idx] if names is not None and idx < len(names) else None)
            g = _build_piece(scene, dict(f, level=level, thickness=thickness,
                                         name=nm or ""), "room")
            if nm:
                g.name = nm
                g.ifc = {"class": "IfcSpace", "name": nm}
            scene.groups.append(g)
            made.append(g)

    if not found:
        return []
    return made if _run(viewport, mutate) else []


def _inside(pt, ring) -> bool:
    return _pip(pt, [tuple(p) for p in ring])


def name_rooms(found, hints) -> list:
    """Um nome por ambiente de ``found``, pelas dicas ``hints`` — uma lista de
    ``(x, y, nome)`` (p.ex. onde há louça sanitária ou bancada de cozinha, vindas
    dos blocos de uma planta). Vale o nome que mais se repete dentro do ambiente;
    sem dica dentro, ``None`` (o chamador dá o nome). Ordem de ``found``."""
    names = []
    for f in found:
        votes: dict = {}
        for x, y, nm in hints:
            if _inside((x, y), f["outline"]) and not any(
                    _inside((x, y), h) for h in f.get("holes") or []):
                votes[nm] = votes.get(nm, 0) + 1
        names.append(max(votes, key=votes.get) if votes else None)
    return names


def area_table(scene, *, decimal: str = ",") -> list:
    """``[(nome, "41,18")…]`` — a tabela de área útil estimada: um ambiente por
    linha, na ordem do documento, com a área em m² e duas casas."""
    out = []
    for g in rooms(scene):
        rec = room_record(g)
        out.append((g.name, f"{room_area(rec):.2f}".replace(".", decimal)))
    return out


# ---------------------------------------------------------------------------
# Regra de giro: a porta SEMPRE abre para a parede (encosta na parede ao lado)
# ---------------------------------------------------------------------------

def door_swing(records, wall_index: int, opening_index: int, *, width=None,
               max_gap: float = 1.0, perpendicular: float = 20.0,
               ambiguity: float = 0.10):
    """Para onde a porta de ``records[wall_index].openings[opening_index]``
    deve abrir, pela regra «a folha abre para a parede»: a dobradiça fica do
    lado do CANTO e a folha, aberta a 90°, fica rente à parede que faz o canto.

    Devolve ``{"hinge": "a"|"b", "side": +1|-1, "gap": m, "wall": índice da
    parede que recebe a folha}`` ou ``None`` se nenhuma parede perpendicular
    fica a até ``max_gap`` (m, da face da parede ao batente; padrão 1 m) com comprimento
    suficiente para receber a folha (a largura da porta, ou ``width``).
    O ``gap`` é a folga entre o batente e essa parede; ganha o canto mais colado.
    ``alternatives`` lista as outras escolhas que também encostam numa parede e
    ``ambiguous`` é verdadeiro quando alguma está a menos de ``ambiguity`` (m) da
    melhor: nesse caso a regra sozinha não decide. ``side`` +1 é a esquerda do eixo ``a``→``b`` da parede."""
    w = records[wall_index]
    o = w["openings"][opening_index]
    (ax, ay), (bx, by) = w["a"], w["b"]
    length = math.hypot(bx - ax, by - ay)
    u = ((bx - ax) / length, (by - ay) / length)
    n = (-u[1], u[0])
    leaf = (o["s1"] - o["s0"]) if width is None else float(width)
    sin_min = math.cos(math.radians(perpendicular))   # |sin| mínimo de 90° ± folga
    found = []
    for j, v in enumerate(records):
        if j == wall_index or v.get("level") != w.get("level"):
            continue
        va, vb = v["a"], v["b"]
        lv = math.hypot(vb[0] - va[0], vb[1] - va[1])
        if lv <= _EPS:
            continue
        vu = ((vb[0] - va[0]) / lv, (vb[1] - va[1]) / lv)
        if abs(u[0] * vu[1] - u[1] * vu[0]) < sin_min:    # não é perpendicular
            continue
        offs = [(p[0] - ax) * n[0] + (p[1] - ay) * n[1] for p in (va, vb)]
        ss = [(p[0] - ax) * u[0] + (p[1] - ay) * u[1] for p in (va, vb)]
        if min(abs(x) for x in offs) > 0.3 + v["thickness"]:  # não encosta nesta parede
            continue
        s_v = (ss[0] + ss[1]) / 2.0
        for side in (1, -1):
            if max(side * x for x in offs) < 0.7 * leaf:      # curta demais p/ a folha
                continue
            for hinge, s_h, ok in (("a", o["s0"], s_v <= o["s0"] + 0.05),
                                   ("b", o["s1"], s_v >= o["s1"] - 0.05)):
                if not ok:
                    continue
                gap = abs(s_h - s_v) - v["thickness"] / 2.0
                if -0.05 <= gap <= max_gap:
                    found.append({"hinge": hinge, "side": side, "gap": gap,
                                  "wall": j})
    if not found:
        return None
    found.sort(key=lambda c: c["gap"])
    best = dict(found[0])
    # outra escolha que também «abre para a parede» e quase tão colada: a regra
    # sozinha não decide (precisa saber QUAL ambiente a porta serve)
    best["alternatives"] = [c for c in found[1:]
                            if (c["hinge"], c["side"]) != (best["hinge"], best["side"])]
    best["ambiguous"] = any(c["gap"] - best["gap"] <= ambiguity
                            for c in best["alternatives"])
    return best


def swing_overrides(records):
    """Um ``overrides`` para :func:`create_leaves` (e base para a ``choose`` de
    :func:`create_blocks`) que dá a cada porta o lado de :func:`door_swing`;
    a porta sem parede ao lado fica com o padrão."""
    recs = list(records)

    def pick(w, idx, o):
        for i, r in enumerate(recs):
            if r["a"] == w["a"] and r["b"] == w["b"] and r.get("openings") == w.get("openings"):
                found = door_swing(recs, i, idx)
                return {"hinge": found["hinge"], "side": found["side"]} if found else None
        return None

    return pick


# ---------------------------------------------------------------------------
# Blocos de biblioteca: um componente (p.ex. do SketchUp) posto numa abertura
# ---------------------------------------------------------------------------
# Convenção do bloco (a mesma que um desenhista segue ao modelar o componente):
# a ORIGEM do componente é o centro do vão, no chão e no eixo da parede; +X corre
# ao longo da parede, +Y é o lado para onde a porta abre (ou a face de fora da
# janela), +Z para cima. Cada instância SÓ ganha uma matriz; a malha é a do
# componente (compartilhada), então editar o bloco atualiza todas as portas.

def load_block_library(path) -> dict:
    """Abre ``path`` (.skp, .igz…) numa cena à parte e devolve ``{nome:
    grupo}`` com um componente por nome. O nome do componente é a chave que
    ``create_blocks`` usa para escolher o bloco certo para cada abertura."""
    from pathlib import Path
    path = Path(path)
    scene = Scene()
    if path.suffix.lower() == ".skp":
        from formats import skp
        skp.load_skp(scene, path)
    else:
        from formats import igz
        igz.load_into(scene, path)
    return {g.name: g for g in scene.groups if g.is_component()}


def normalize_block(rec: dict) -> dict:
    """Registro de um bloco posto numa abertura: eixo da parede ``a``→``b``,
    ``s`` (centro do vão ao longo do eixo), ``block`` (nome do componente),
    ``flip`` (espelha ao longo da parede: troca o lado da dobradiça), ``side``
    (+1 ou −1: espelha a profundidade, troca o lado de abrir), ``t_offset`` e
    ``ifc`` (classe IFC do grupo)."""
    try:
        out = _common(rec, {
            "kind": "block", "block": str(rec["block"]),
            "s": float(rec["s"]), "flip": bool(rec.get("flip", False)),
            "side": int(rec.get("side", 1)),
            "t_offset": float(rec.get("t_offset", 0.0)),
            "ifc": str(rec.get("ifc", "IfcDoor")),
        })
    except (KeyError, TypeError, ValueError) as exc:
        raise BimProtoError(f"bad block record: {exc}") from exc
    _, _, _, length = _axis_frame(out)
    if not all(math.isfinite(v) for v in out["a"] + out["b"] + [
            out["s"], out["t_offset"], out["base_offset"]]):
        raise BimProtoError("non-finite value in the block record")
    if out["side"] not in (-1, 1) or out["ifc"] not in bim.IFC_CLASSES:
        raise BimProtoError("side must be 1 or -1 and ifc a known IFC class")
    if not -_EPS <= out["s"] <= length + _EPS:
        raise BimProtoError("the block must lie inside the wall's length")
    return out


def block_placement(rec: dict, base_z: float):
    """Matriz (componente → mundo) de um bloco, pela convenção acima."""
    from PySide6.QtGui import QMatrix4x4
    origin, u, n, _ = _axis_frame(rec)
    fx = -1.0 if rec["flip"] else 1.0
    fy = float(rec["side"])
    cx = origin[0] + u[0] * rec["s"] + n[0] * rec["t_offset"]
    cy = origin[1] + u[1] * rec["s"] + n[1] * rec["t_offset"]
    return QMatrix4x4(u[0] * fx, n[0] * fy, 0, cx,
                      u[1] * fx, n[1] * fy, 0, cy,
                      0, 0, 1, base_z,
                      0, 0, 0, 1)


def _place_block(scene, rec: dict, library: dict) -> Group:
    rec = normalize_block(rec)
    proto = library.get(rec["block"])
    if proto is None:
        raise BimProtoError(f"the library has no block {rec['block']!r}")
    # FRAGILE: copy_group é público mas fora do contrato documentado; é o que o
    # Copiar/Colar do núcleo usa e faz a cópia COMPARTILHAR a malha do componente.
    from core.group import copy_group
    g = copy_group(proto)
    g.name = _next_name(scene, "Block")
    g.xform = block_placement(rec, level_z(scene, rec["level"])
                              + rec["base_offset"])
    g.layer = None
    bim.tag_group(g, rec["ifc"], g.name)
    g.ext = {KEY: rec}
    return g


def create_blocks(viewport, library: dict, choose, targets=None) -> list:
    """Põe um bloco da ``library`` em cada abertura das paredes ``targets`` (ou
    de todas) — UM passo de desfazer, tudo ou nada. ``choose(parede, índice,
    abertura)`` devolve ``None`` (pular) ou um dict com ``block`` (nome do
    componente) e, se quiser, ``flip``, ``side``, ``t_offset``, ``ifc``."""
    scene = viewport.scene
    todo = [g for g in (targets if targets is not None else walls(scene))
            if wall_record(g) is not None]
    jobs = []
    for g in todo:
        w = wall_record(g)
        for idx, o in enumerate(w.get("openings") or []):
            pick = choose(w, idx, o)
            if pick:
                jobs.append(dict(pick, a=w["a"], b=w["b"], s=(o["s0"] + o["s1"]) / 2,
                                 level=w["level"],
                                 base_offset=w.get("base_offset", 0.0),
                                 ifc=pick.get("ifc", "IfcWindow" if o["sill"] > _EPS
                                              else "IfcDoor")))
    if not jobs:
        return []
    made: list = []

    def mutate(sc):
        for rec in jobs:
            g = _place_block(sc, rec, library)
            sc.groups.append(g)
            made.append(g)

    return made if _run(viewport, mutate) else []


#: kind -> (normalizar, malha, classe IFC, nome no menu)
_KINDS = {
    "wall": (normalize_record, wall_mesh, "IfcWall", "Wall"),
    "frame": (normalize_frame, frame_mesh, None, "Frame"),
    "sill": (normalize_sill, sill_mesh, "IfcMember", "Sill"),
    "leaf": (normalize_leaf, leaf_mesh, "IfcDoor", "Door leaf"),
    "block": (normalize_block, None, None, "Block"),
    "room": (normalize_room, room_mesh, "IfcSpace", "Room"),
}


def element_record(group):
    """O registro (``group.ext[KEY]``) de qualquer peça do BIM proto (parede,
    esquadria, peitoril, folha de porta) ou ``None``."""
    ext = getattr(group, "ext", None)
    rec = ext.get(KEY) if isinstance(ext, dict) else None
    if isinstance(rec, dict) and rec.get("kind") in _KINDS:
        return rec
    return None


def elements(scene) -> list:
    return [g for g in scene.groups if element_record(g) is not None]


def regenerate_element(scene, group) -> None:
    """Refaz a malha de QUALQUER peça a partir do seu registro (um passo, pelo
    comando que a chamar)."""
    rec = element_record(group)
    if rec is None:
        raise BimProtoError(f"«{group.name}» is not a BIM proto element")
    norm, mesh_of, _ifc, _label = _KINDS[rec["kind"]]
    rec = norm(rec)
    if mesh_of is None:                    # bloco: a malha é a do componente
        group.xform = block_placement(rec, level_z(scene, rec["level"])
                                      + rec["base_offset"])
        group.ext = dict(group.ext or {}, **{KEY: rec})
        return
    group.mesh = mesh_of(rec, level_z(scene, rec["level"]) + rec["base_offset"])
    group.xform = None        # FRAGILE: troca de malha, como em regenerate_wall
    group.children = []
    group.ext = dict(group.ext or {}, **{KEY: rec})


def _build_piece(scene, rec: dict, kind: str) -> Group:
    norm, mesh_of, ifc, label = _KINDS[kind]
    rec = norm(rec)
    g = Group(mesh_of(rec, level_z(scene, rec["level"]) + rec["base_offset"]),
              name=_next_name(scene, label))
    bim.tag_group(g, ifc, g.name)
    g.ext = {KEY: rec}
    return g


def create_sills(viewport, targets=None, *, thickness=0.03, projection=0.05,
                 overhang=0.03, side=1) -> list:
    """Um peitoril para cada JANELA (abertura com ``sill`` > 0) das paredes
    ``targets`` (ou de todas) — um único passo de desfazer, tudo ou nada.
    Devolve os grupos criados, ou ``[]``."""
    return _create_from_openings(viewport, targets, "sill", lambda w, o, idx: {
        "z": o["sill"], "s0": o["s0"], "s1": o["s1"],
        "wall_thickness": w["thickness"], "thickness": thickness,
        "projection": projection, "overhang": overhang, "side": side},
        lambda o: o["sill"] > _EPS)


def create_leaves(viewport, targets=None, *, overrides=None, auto_swing=False,
                  **params) -> list:
    """Uma porta para cada ABERTURA DE PORTA (``sill`` 0) das paredes ``targets``
    (ou de todas) — um único passo de desfazer, tudo ou nada.

    ``params`` são campos do registro (veja :func:`normalize_leaf`: ``style``,
    ``thickness``, ``hinge``, ``side``, ``angle``, ``clearance``, ``frame``,
    ``trim``, ``arc``, ``open``…) e valem para todas. ``overrides(parede,
    índice, abertura)`` pode devolver um dict com campos só daquela porta (ou
    ``None``) — é como cada porta de uma planta ganha o seu lado e o seu tipo.
    ``auto_swing=True`` aplica a regra «a porta abre para a parede»
    (:func:`door_swing`) às portas que têm parede ao lado; ``overrides`` ainda
    manda por cima dela."""
    base = {"thickness": 0.04, "hinge": "a", "side": 1, "angle": 0.0,
            "clearance": 0.01}
    base.update(params)
    auto = swing_overrides([wall_record(g) for g in walls(viewport.scene)]) \
        if auto_swing else None

    def extra(w, o, idx=0):
        rec = dict(base, head=o["head"], s0=o["s0"], s1=o["s1"],
                   wall_thickness=w["thickness"])
        if auto is not None:
            rec.update(auto(w, idx, o) or {})
        if overrides is not None:
            rec.update(overrides(w, idx, o) or {})
        return rec

    return _create_from_openings(viewport, targets, "leaf", extra,
                                 lambda o: o["sill"] <= _EPS)


def _create_from_openings(viewport, targets, kind, extra, wanted) -> list:
    scene = viewport.scene
    todo = [g for g in (targets if targets is not None else walls(scene))
            if wall_record(g) is not None]
    jobs = []
    for g in todo:
        w = wall_record(g)
        for idx, o in enumerate(w.get("openings") or []):
            if wanted(o):
                fields = extra(w, o, idx)
                jobs.append(dict(fields, a=w["a"], b=w["b"], level=w["level"],
                                 base_offset=w.get("base_offset", 0.0)))
    if not jobs:
        return []
    made: list = []

    def mutate(sc):
        for rec in jobs:
            g = _build_piece(sc, rec, kind)
            sc.groups.append(g)
            made.append(g)

    return made if _run(viewport, mutate) else []


# ---------------------------------------------------------------------------
# Encontros de parede (cantos em L e em T)
# ---------------------------------------------------------------------------

def join_walls(records, tol: float = 0.3, skew: float = 10.0):
    """Resolve os ENCONTROS entre paredes desenhadas pelo eixo, sem sobrepor
    nem deixar fresta (exato para encontros a 90°; ``skew`` é o desvio, em
    graus, que ainda se aceita como 90°). Devolve ``(registros, encontros)``:
    cópias dos registros com ``a``/``b`` ajustados (as aberturas acompanham) e
    a lista ``[{"kind": "L"|"T", "walls": (i, j)}]``.

    * Canto em L (as duas pontas perto do cruzamento dos eixos, até ``tol``):
      a parede MAIS LONGA avança até a face de fora da outra; a outra recua até
      a face de dentro da primeira.
    * T (a ponta de uma encosta no meio da outra): a ponta recua até a face.

    Só se unem paredes do mesmo nível e da mesma ``base_offset``. Nada além de
    ``a`` e ``b`` muda. Uma peça que ficaria com menos de 2 cm (ou invertida)
    não é tocada."""
    recs = [copy.deepcopy(dict(r)) for r in records]
    geo = []
    for r in recs:
        ax, ay = r["a"]
        bx, by = r["b"]
        length = math.hypot(bx - ax, by - ay)
        geo.append((length, ((bx - ax) / length, (by - ay) / length)
                    if length > _EPS else (1.0, 0.0)))

    def cross(p, q):
        return p[0] * q[1] - p[1] * q[0]

    cands = []
    sin_min = math.cos(math.radians(skew))   # |sin| mínimo p/ 90° ± skew
    for i, A in enumerate(recs):
        for end_a in (0, 1):
            p = A["a"] if end_a == 0 else A["b"]
            ua = geo[i][1] if end_a == 1 else (-geo[i][1][0], -geo[i][1][1])
            for j, B in enumerate(recs):
                if j == i or B.get("level") != A.get("level") \
                        or abs(B.get("base_offset", 0.0)
                               - A.get("base_offset", 0.0)) > 1e-6:
                    continue
                lb, vb = geo[j]
                den = cross(ua, vb)
                if abs(den) < sin_min:
                    continue
                q = B["a"]
                qp = (q[0] - p[0], q[1] - p[1])
                lam = cross(qp, vb) / den          # de p até X, ao longo de ua
                mu = cross(qp, ua) / den           # de B.a até X, ao longo de vb
                # a ponta pode ficar curta até ``tol`` ou passar do cruzamento
                # até ``tol`` — mas nunca mais que metade da própria parede
                # (senão a OUTRA ponta de uma peça curta se uniria a algo
                # que está atrás dela)
                if lam > tol or -lam > min(tol, geo[i][0] / 2.0) \
                        or mu < -tol or mu > lb + tol:
                    continue
                kind = "L" if mu <= tol or mu >= lb - tol else "T"
                end_b = 0 if mu <= tol else 1
                cands.append((abs(lam), i, end_a, j, end_b, kind, ua, den, lam))
    cands.sort(key=lambda c: (c[0], c[1], c[3]))

    used, joins = set(), []
    new_end: dict = {}

    def point(i, end):
        return recs[i]["a"] if end == 0 else recs[i]["b"]

    for _dist, i, end_a, j, end_b, kind, ua, den, lam in cands:
        if (i, end_a) in used or (kind == "L" and (j, end_b) in used):
            continue
        sin = abs(den)
        p = point(i, end_a)
        x = (p[0] + ua[0] * lam, p[1] + ua[1] * lam)   # X = p + ua * lam
        tb = recs[j]["thickness"]
        ta = recs[i]["thickness"]
        if kind == "T":
            back = tb / 2.0 / sin
            new_end[(i, end_a)] = (x[0] - ua[0] * back, x[1] - ua[1] * back)
            used.add((i, end_a))
        else:
            ub = geo[j][1] if end_b == 1 else (-geo[j][1][0], -geo[j][1][1])
            win_i = (geo[i][0], -i) >= (geo[j][0], -j)
            if win_i:
                new_end[(i, end_a)] = (x[0] + ua[0] * (tb / 2.0 / sin),
                                       x[1] + ua[1] * (tb / 2.0 / sin))
                new_end[(j, end_b)] = (x[0] - ub[0] * (ta / 2.0 / sin),
                                       x[1] - ub[1] * (ta / 2.0 / sin))
            else:
                new_end[(j, end_b)] = (x[0] + ub[0] * (ta / 2.0 / sin),
                                       x[1] + ub[1] * (ta / 2.0 / sin))
                new_end[(i, end_a)] = (x[0] - ua[0] * (tb / 2.0 / sin),
                                       x[1] - ua[1] * (tb / 2.0 / sin))
            used.update({(i, end_a), (j, end_b)})
        joins.append({"kind": kind, "walls": (i, j)})

    # Uma peça curta que o encontro faria sumir (ou virar do avesso) fica como
    # estava: encurtar além do comprimento não é unir, é apagar.
    def new_length(i):
        a_ = new_end.get((i, 0), recs[i]["a"])
        b_ = new_end.get((i, 1), recs[i]["b"])
        u_ = geo[i][1]
        return (b_[0] - a_[0]) * u_[0] + (b_[1] - a_[1]) * u_[1]

    for i in {k[0] for k in new_end}:
        if new_length(i) < _MIN_JOIN_LENGTH:
            new_end.pop((i, 0), None)
            new_end.pop((i, 1), None)
            joins[:] = [j for j in joins if i not in j["walls"]]

    for (i, end), (nx_, ny_) in new_end.items():
        r = recs[i]
        u = geo[i][1]
        if end == 0:
            delta = (nx_ - r["a"][0]) * u[0] + (ny_ - r["a"][1]) * u[1]
            r["a"] = [nx_, ny_]
            for o in r.get("openings") or []:
                o["s0"] -= delta
                o["s1"] -= delta
        else:
            r["b"] = [nx_, ny_]
    return recs, joins


def join_corners(viewport, targets=None, tol: float = 0.3) -> int:
    """«Unir cantos»: aplica :func:`join_walls` às paredes (``targets``, ou
    todas) e regenera o 3D — um único passo de desfazer; tudo ou nada.
    Devolve quantas paredes mudaram (0 se nada precisava)."""
    scene = viewport.scene
    todo = [g for g in (targets if targets is not None else walls(scene))
            if wall_record(g) is not None]
    new, _joins = join_walls([wall_record(g) for g in todo], tol=tol)
    def moved(p, q):
        return math.hypot(p[0] - q[0], p[1] - q[1]) > 1e-6

    changed = [(g, r) for g, r in zip(todo, new)
               if moved(r["a"], wall_record(g)["a"])
               or moved(r["b"], wall_record(g)["b"])]
    if not changed:
        _flash(viewport, _t("No wall corners to join."))
        return 0

    def mutate(sc):
        for g, r in changed:
            g.ext = dict(g.ext, **{KEY: r})
            regenerate_wall(sc, g)

    if not _run(viewport, mutate):
        return 0
    _flash(viewport, _t("{n} wall(s) joined", n=len(changed)))
    return len(changed)


# ---------------------------------------------------------------------------
# Planta 2D com projeções (o que está ACIMA do plano de corte, tracejado)
# ---------------------------------------------------------------------------

def _scene_bounds(scene):
    """``(mín, máx)`` (x, y, z) de todos os grupos, ou ``None``."""
    # FRAGILE: world_mesh é público mas fora do contrato documentado.
    from core.group import world_mesh
    lo = [math.inf] * 3
    hi = [-math.inf] * 3
    for g in scene.groups:
        for v in world_mesh(g).vertices:
            p = v.position
            for i, c in enumerate((p.x(), p.y(), p.z())):
                lo[i], hi[i] = min(lo[i], c), max(hi[i], c)
    return (lo, hi) if lo[0] != math.inf else None


def _uncovered(a, b, refs, tol, step=0.02, min_len=0.02):
    """Pedaços do segmento ``a``→``b`` (2D) a mais de ``tol`` de TODOS os
    segmentos de ``refs`` (array (N, 4)); lista de ((x0, y0), (x1, y1))."""
    import numpy as np
    a, b = np.asarray(a, float), np.asarray(b, float)
    length = float(np.hypot(*(b - a)))
    if length < _EPS:
        return []
    n = max(2, int(length / step) + 1)
    ts = np.linspace(0.0, 1.0, n)
    pts = a[None, :] + ts[:, None] * (b - a)[None, :]
    if len(refs):
        p0, d = refs[:, :2], refs[:, 2:] - refs[:, :2]
        dd = np.maximum((d * d).sum(1), 1e-18)
        near = np.zeros(n, dtype=bool)
        for i0 in range(0, n, 256):
            q = pts[i0:i0 + 256]
            rel = q[:, None, :] - p0[None, :, :]
            t = np.clip((rel * d[None]).sum(2) / dd[None], 0.0, 1.0)
            proj = p0[None] + t[..., None] * d[None]
            near[i0:i0 + 256] = (((q[:, None, :] - proj) ** 2).sum(2)
                                 <= tol * tol).any(1)
    else:
        near = np.zeros(n, dtype=bool)
    out, start = [], None
    for i in range(n):
        if not near[i] and start is None:
            start = i
        if (near[i] or i == n - 1) and start is not None:
            end = i - 1 if near[i] else i
            if end >= start:
                # a fronteira cai entre duas amostras: abre meio passo de cada
                # lado (sem passar das pontas do segmento)
                t0 = max(0.0, ts[start] - (step / 2) / length) if start > 0 else 0.0
                t1 = min(1.0, ts[end] + (step / 2) / length) if end < n - 1 else 1.0
                p, q = a + t0 * (b - a), a + t1 * (b - a)
                if np.hypot(*(q - p)) >= min_len:
                    out.append(((float(p[0]), float(p[1])),
                                (float(q[0]), float(q[1]))))
            start = None
    return out


def plan_drawing(scene, cut_z: float = 1.2, *, tol: float = 0.005) -> dict:
    """A planta 2D de ``scene`` cortada a ``cut_z`` (m), como se desenha à mão
    ou no AutoCAD, em coordenadas de mundo (x, y):

    * ``"cut"``   — anéis (arrays (N, 2)) do que o plano corta: o poché;
    * ``"below"`` — segmentos ``(x0, y0, x1, y1)`` do que está ABAIXO do corte
      e se vê de cima (peitoris, folhas, mobília), linha fina;
    * ``"rooms"`` — os ambientes do documento: ``name``, ``outline``, ``holes``, ``area``;
    * ``"above"`` — segmentos da PROJEÇÃO do que está ACIMA do corte (verga
      sobre a porta, travessa do marco, beiral…), para sair TRACEJADO; só o que
      não coincide com o corte nem com o que está abaixo (``tol`` em metros).

    Usa o passe de linhas do núcleo (``core.hlr``); a cena não é alterada.
    Sem placa de vídeo."""
    import numpy as np
    from core.camera import OrbitCamera
    from core.hlr import KIND_CUT, hlr_drawing
    from core.section import SectionPlane

    bounds = _scene_bounds(scene)
    empty = {"cut": [], "below": np.zeros((0, 4)), "above": np.zeros((0, 4)),
             "rooms": []}
    if bounds is None:
        return empty
    lo, hi = bounds
    centre = [(lo[i] + hi[i]) / 2 for i in range(3)]
    diag = math.dist(lo, hi) or 1.0

    def camera():
        cam = OrbitCamera()
        cam.perspective = False
        cam.target = QVector3D(*centre)
        cam.distance = diag * 2
        cam.yaw, cam.pitch = -math.pi / 2, math.radians(89.9)
        return cam

    saved = list(scene.section_planes)
    flags = [sp.active for sp in saved]
    # as zonas de piso dos ambientes não são matéria: tapariam as linhas do piso
    # (arco de giro, soleiras) na vista de cima; saem do desenho e voltam depois
    all_groups = list(scene.groups)
    room_info = [{"name": g.name, "outline": r["outline"], "holes": r["holes"],
                  "area": room_area(r)} for g in all_groups for r in [room_record(g)]
                 if r is not None]
    scene.groups[:] = [g for g in all_groups if room_record(g) is None]

    def run(normal_z):
        plane = SectionPlane(QVector3D(0, 0, cut_z), QVector3D(0, 0, normal_z))
        scene.section_planes[:] = [plane]
        scene.set_active_section(plane)
        return hlr_drawing(scene, camera(), hidden=False)

    try:
        below = run(1.0)          # tira o que está acima: o que sobra é a planta
        above = run(-1.0)         # tira o que está abaixo: sobra a projeção
    finally:
        scene.groups[:] = all_groups
        scene.section_planes[:] = saved
        for sp, f in zip(saved, flags):
            sp.active = f

    def to_xy(d):
        """Mapa afim (u, v) -> (x, y) deste desenho, pelos segmentos."""
        if not len(d.segs):
            return None
        uv = np.concatenate([d.segs[:, :2], d.segs[:, 2:]])
        xy = np.concatenate([d.world[:, 0, :2], d.world[:, 1, :2]])
        m, *_ = np.linalg.lstsq(np.c_[uv, np.ones(len(uv))], xy, rcond=None)
        return lambda q: np.c_[q, np.ones(len(q))] @ m

    f_below, f_above = to_xy(below), to_xy(above)
    cut = [f_below(lp) for lp in below.loops] if f_below else []
    below_xy = below.world[:, :, :2].reshape(-1, 4) if len(below.segs) \
        else np.zeros((0, 4))
    ref = [below_xy]
    for lp in cut:                       # as bordas do poché também cobrem
        ref.append(np.c_[lp, np.roll(lp, -1, axis=0)])
    ref = np.concatenate(ref)
    proj = []
    if f_above:
        for (x0, y0, x1, y1), k in zip(
                above.world[:, :, :2].reshape(-1, 4), above.kinds):
            if k == KIND_CUT:            # o próprio corte (já está no poché)
                continue
            ref_now = np.concatenate([ref, np.asarray(proj, float).reshape(-1, 4)])
            for p, q in _uncovered((x0, y0), (x1, y1), ref_now, tol):
                proj.append((p[0], p[1], q[0], q[1]))
    return {"cut": cut, "below": below_xy,
            "above": np.asarray(proj, float).reshape(-1, 4),
            "rooms": room_info}


#: Estilos da planta 2D. Larguras e tracejado em PONTOS de papel. ``"ao"`` foi MEDIDO
#: no vetor de um PDF de layout de escritório (poché preto cheio e sem contorno; mobília
#: e portas em linha fina cinza de 0,48 pt; projeções em tracejado preto de 0,48 pt, traço
#: de 9,8 pt e vão de 4,3 pt); ``"cad"`` é o cinza-azulado com projeção laranja.
PLAN_STYLES = {
    "ao": {"cut_fill": "#000000", "cut_edge": None,
           "below": ("#989898", 0.48), "above": ("#000000", 0.48),
           "dash": (9.8, 4.3), "lines_under_cut": True},
    "cad": {"cut_fill": "#3a3f48", "cut_edge": None,
            "below": ("#222222", 0.5), "above": ("#b04a00", 0.6),
            "dash": (6.0, 4.0), "lines_under_cut": False},
}


def render_plan(plan: dict, width: int = 1600, margin: int = 40, title: str = "",
                legend: bool = True, style: str = "ao", px_per_pt: float = 2.0,
                labels: bool = False):
    """Desenha o resultado de :func:`plan_drawing` numa ``QImage`` — poché, linha fina
    para o que está abaixo do corte e TRACEJADO para a projeção do que está acima,
    como na planta do AutoCAD; ``labels=True`` escreve nome e área de cada ambiente
    (``plan["rooms"]``). ``style``: chave de :data:`PLAN_STYLES` (``"ao"`` por
    padrão); ``px_per_pt`` converte os pontos do estilo em pixels. Sem janela e sem GL."""
    import numpy as np
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPolygonF

    st = PLAN_STYLES[style]
    pts = [lp for lp in plan["cut"]]
    for key in ("below", "above"):
        if len(plan[key]):
            pts.append(plan[key][:, :2])
            pts.append(plan[key][:, 2:])
    if not pts:
        img = QImage(width, 200, QImage.Format_RGB32)
        img.fill(QColor("white"))
        return img
    allp = np.concatenate(pts)
    x0, y0 = allp.min(0)
    x1, y1 = allp.max(0)
    k = (width - 2 * margin) / max(x1 - x0, 1e-6)
    foot = 60 if legend else 20
    height = int((y1 - y0) * k) + 2 * margin + foot
    img = QImage(width, height, QImage.Format_RGB32)
    img.fill(QColor("white"))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)

    def X(x):
        return margin + (x - x0) * k

    def Y(y):
        return height - margin - foot + 20 - (y - y0) * k

    def pen(spec, dashed=False):
        colour, pt = spec
        w = max(pt * px_per_pt, 0.6)
        q = QPen(QColor(colour), w, Qt.CustomDashLine if dashed else Qt.SolidLine)
        if dashed:                      # o Qt mede o tracejado em larguras da caneta
            q.setDashPattern([st["dash"][0] * px_per_pt / w, st["dash"][1] * px_per_pt / w])
        q.setCapStyle(Qt.FlatCap)
        return q

    def poche():
        if st["cut_edge"]:
            p.setPen(QPen(QColor(st["cut_edge"]), 1.0))
        else:
            p.setPen(Qt.NoPen)
        p.setBrush(QColor(st["cut_fill"]))
        for lp in plan["cut"]:
            p.drawPolygon(QPolygonF([QPointF(X(a), Y(b)) for a, b in lp]))

    def below():
        p.setPen(pen(st["below"]))
        for a, b, c, d in plan["below"]:
            p.drawLine(QPointF(X(a), Y(b)), QPointF(X(c), Y(d)))

    if labels:                          # um véu claro sob as linhas: onde é cada ambiente
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#f1f1ee"))
        for rm in plan.get("rooms", []):
            p.drawPolygon(QPolygonF([QPointF(X(a), Y(b)) for a, b in rm["outline"]]))
            p.setBrush(QColor("white"))
            for h in rm["holes"]:
                p.drawPolygon(QPolygonF([QPointF(X(a), Y(b)) for a, b in h]))
            p.setBrush(QColor("#f1f1ee"))
    if st["lines_under_cut"]:
        below()
        poche()
    else:
        poche()
        below()
    p.setPen(pen(st["above"], dashed=True))
    for a, b, c, d in plan["above"]:
        p.drawLine(QPointF(X(a), Y(b)), QPointF(X(c), Y(d)))
    if labels:
        p.setFont(QFont("DejaVu Sans", 11))
        p.setPen(QColor("#111"))
        for rm in plan.get("rooms", []):
            ring = rm["outline"]
            a2 = _ring_area([tuple(q) for q in ring]) * 2
            cx = sum((ring[i][0] + ring[(i + 1) % len(ring)][0])
                     * (ring[i][0] * ring[(i + 1) % len(ring)][1]
                        - ring[(i + 1) % len(ring)][0] * ring[i][1])
                     for i in range(len(ring))) / (3 * a2)
            cy = sum((ring[i][1] + ring[(i + 1) % len(ring)][1])
                     * (ring[i][0] * ring[(i + 1) % len(ring)][1]
                        - ring[(i + 1) % len(ring)][0] * ring[i][1])
                     for i in range(len(ring))) / (3 * a2)
            txt = f"{rm['name']}\n{rm['area']:.2f} m²".replace(".", ",")
            r = p.boundingRect(0, 0, 400, 100, Qt.AlignCenter, txt)
            p.drawText(int(X(cx) - r.width() / 2), int(Y(cy) - r.height() / 2),
                       r.width(), r.height(), Qt.AlignCenter, txt)
    p.setFont(QFont("DejaVu Sans", 12))
    p.setPen(QColor("#222"))
    if title:
        p.drawText(14, 24, title)
    if legend:
        ly = height - 22
        p.setBrush(QColor(st["cut_fill"]))
        p.setPen(Qt.NoPen)
        p.drawRect(14, ly - 10, 26, 12)
        p.setPen(QColor("#222"))
        p.drawText(48, ly, _t("cut"))
        lw = pen((st["below"][0], max(st["below"][1], 0.8)))
        p.setPen(lw)
        p.drawLine(190, ly - 4, 226, ly - 4)
        p.setPen(QColor("#222"))
        p.drawText(234, ly, _t("below the cut"))
        p.setPen(pen((st["above"][0], max(st["above"][1], 0.8)), dashed=True))
        p.drawLine(430, ly - 4, 480, ly - 4)
        p.setPen(QColor("#222"))
        p.drawText(488, ly, _t("projection (above the cut)"))
    p.end()
    return img


# ---------------------------------------------------------------------------
# Comando: um passo de desfazer para criar / atualizar
# ---------------------------------------------------------------------------

class BimProtoCommand(Command):
    """Executa ``mutate(scene)`` como UM passo de desfazer.

    Guarda o que a mutação pode tocar — a lista de grupos, e de cada grupo a
    malha, o ``ext``, a etiqueta IFC e a matriz — mais o dado do projeto.
    O mesmo padrão do WindowizerCommand (examples/extensions/windowizer.py):
    ``History.execute`` só reverte a malha solta, não grupos nem ``ext``."""

    def __init__(self, mutate) -> None:
        self.mutate = mutate
        self.before = self.after = None

    @staticmethod
    def _capture(scene) -> dict:
        return {
            "groups": list(scene.groups),
            "state": [(g, g.mesh, list(g.children or ()), g.xform,
                       g.ifc and dict(g.ifc), copy.deepcopy(g.ext))
                      for g in scene.groups],
            "data": copy.deepcopy(scene.plugin_data.get(KEY)),
            "selection": set(scene.selection),
        }

    @staticmethod
    def _restore(scene, s) -> None:
        scene.groups[:] = s["groups"]
        for g, mesh, kids, xform, ifc, ext in s["state"]:
            g.mesh, g.children, g.xform = mesh, list(kids), xform
            g.ifc, g.ext = ifc and dict(ifc), copy.deepcopy(ext)
        if s["data"] is None:
            scene.plugin_data.pop(KEY, None)
        else:
            scene.plugin_data[KEY] = copy.deepcopy(s["data"])
        scene.selection.clear()
        scene.selection.update(x for x in s["selection"]
                               if x in scene.groups)

    def do(self, scene) -> None:
        if self.after is None:
            self.before = self._capture(scene)
            try:
                self.mutate(scene)
            except BaseException:
                self._restore(scene, self.before)
                raise
            self.after = self._capture(scene)
        else:
            self._restore(scene, self.after)
        scene.version += 1

    def undo(self, scene) -> None:
        self._restore(scene, self.before)
        scene.version += 1


def _run(viewport, mutate) -> bool:
    viewport.history.execute(BimProtoCommand(mutate))
    if viewport.history.last_error:
        _flash(viewport, viewport.history.last_error.split(": ", 1)[-1])
        return False
    notify = getattr(viewport, "notify_scene_changed", None)
    if callable(notify):
        notify()
    viewport.update()
    return True


def _flash(viewport, text: str, msec: int = 4000) -> None:
    flash = getattr(viewport, "flash_status", None)
    if callable(flash):
        flash(text, msec)


def create_wall(viewport, a, b, *, thickness=None, height=None,
                level="PB", base_offset=0.0):
    """Cria uma parede de ``a`` a ``b`` (x, y em metros) — um passo de
    desfazer. Devolve o grupo, ou ``None`` se falhou (a razão vai à barra
    de estado e ao ``history.last_error``)."""
    made = []

    def mutate(scene):
        rec = {"kind": "wall", "a": list(a)[:2], "b": list(b)[:2],
               "thickness": DEFAULTS["thickness"] if thickness is None
               else thickness,
               "height": DEFAULTS["height"] if height is None else height,
               "level": level, "base_offset": base_offset}
        data = project_data(scene)          # garante PB também no arquivo
        scene.plugin_data[KEY] = data
        g = build_wall_group(scene, rec)
        scene.groups.append(g)
        made.append(g)

    return made[0] if _run(viewport, mutate) and made else None


def wall_from_rectangle(points, min_fill: float = 0.97) -> dict:
    """Registro de parede (``a``, ``b``, ``thickness``) a partir de um polígono
    RETANGULAR em planta, em qualquer orientação ou ordem de vértices: o eixo
    corre pelo meio do lado maior, a espessura é o lado menor. É o que uma
    planta importada (alvenaria desenhada como retângulos) precisa para virar
    parede. Levanta :class:`BimProtoError` se o polígono não preenche pelo
    menos ``min_fill`` do retângulo mínimo que o contém (L, T, curvo…)."""
    pts = [(float(x), float(y)) for x, y in points]
    if len(pts) < 3:
        raise BimProtoError("a polygon needs at least 3 points")
    best = None
    for i in range(len(pts)):
        dx = pts[(i + 1) % len(pts)][0] - pts[i][0]
        dy = pts[(i + 1) % len(pts)][1] - pts[i][1]
        length = math.hypot(dx, dy)
        if length < _EPS:
            continue
        ux, uy = dx / length, dy / length
        s_ = [x * ux + y * uy for x, y in pts]
        t_ = [-x * uy + y * ux for x, y in pts]
        area = (max(s_) - min(s_)) * (max(t_) - min(t_))
        if best is None or area < best[0]:
            best = (area, ux, uy, min(s_), max(s_), min(t_), max(t_))
    if best is None:
        raise BimProtoError("degenerate polygon")
    area, ux, uy, s0, s1, t0, t1 = best
    shoelace = abs(sum(pts[i][0] * pts[(i + 1) % len(pts)][1]
                       - pts[(i + 1) % len(pts)][0] * pts[i][1]
                       for i in range(len(pts)))) / 2.0
    if area <= _EPS or shoelace / area < min_fill:
        raise BimProtoError("the polygon is not a rectangle")
    if s1 - s0 < t1 - t0:                       # o lado maior vira o eixo
        ux, uy, s0, s1, t0, t1 = -uy, ux, t0, t1, -s1, -s0
    nx, ny = -uy, ux
    tm = (t0 + t1) / 2.0
    return {"a": [ux * s0 + nx * tm, uy * s0 + ny * tm],
            "b": [ux * s1 + nx * tm, uy * s1 + ny * tm],
            "thickness": t1 - t0}


def wall_gaps(records, min_gap: float = 0.45, max_gap: float = 2.7,
              parallel_tol: float = 0.02, min_piece: float = 0.25) -> list:
    """Vãos entre trechos de parede ALINHADOS: dois registros paralelos, com os
    eixos sobre a mesma reta (a folga perpendicular cabe na espessura), e uma
    folga de ``min_gap`` a ``max_gap`` metros entre as pontas. É assim que uma
    planta desenha uma porta: a parede simplesmente para e recomeça. Não conta
    como vão uma folga que tenha OUTRA parede dentro (um encontro em T), nem
    um trecho menor que ``min_piece`` (uma ponta, não uma parede).

    Devolve ``[{"a", "b", "width", "thickness", "between": (i, j)}]``, com
    ``a``/``b`` as duas bordas do vão sobre o eixo. Para fechar o vão por cima
    (verga) ou por baixo (peitoril): ``create_walls`` com ``base_offset`` e
    ``height`` — um vão ainda não é um furo paramétrico na parede."""
    recs = [(r["a"], r["b"], r["thickness"]) for r in records]

    def frame(a, b):
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        return dx / length, dy / length, length

    def inside_other(p, skip):
        for k, (a, b, th) in enumerate(recs):
            if k in skip:
                continue
            ux, uy, length = frame(a, b)
            s_ = (p[0] - a[0]) * ux + (p[1] - a[1]) * uy
            t_ = -(p[0] - a[0]) * uy + (p[1] - a[1]) * ux
            if -0.02 <= s_ <= length + 0.02 and abs(t_) <= th / 2 + 0.02:
                return True
        return False

    gaps = []
    for i, (ai, bi, thi) in enumerate(recs):
        ux, uy, li = frame(ai, bi)
        if li < min_piece:
            continue
        for j in range(i + 1, len(recs)):
            aj, bj, thj = recs[j]
            vx, vy, lj = frame(aj, bj)
            if lj < min_piece or abs(ux * vy - uy * vx) > parallel_tol:
                continue

            def st(p):
                return ((p[0] - ai[0]) * ux + (p[1] - ai[1]) * uy,
                        -(p[0] - ai[0]) * uy + (p[1] - ai[1]) * ux)
            sa, ta = st(aj)
            sb, tb = st(bj)
            if abs((ta + tb) / 2) > max(thi, thj) / 2:
                continue                                    # eixos não alinhados
            lo_j, hi_j = sorted((sa, sb))
            if lo_j >= li:
                lo, hi = li, lo_j
            elif hi_j <= 0.0:
                lo, hi = hi_j, 0.0
            else:
                continue                                    # trechos que se sobrepõem
            width = hi - lo
            if not min_gap <= width <= max_gap:
                continue
            mid = ((lo + hi) / 2)
            mid_pt = (ai[0] + ux * mid, ai[1] + uy * mid)
            if inside_other(mid_pt, {i, j}):
                continue
            gaps.append({"a": [ai[0] + ux * lo, ai[1] + uy * lo],
                         "b": [ai[0] + ux * hi, ai[1] + uy * hi],
                         "width": width, "thickness": max(thi, thj),
                         "between": (i, j)})
    return gaps


def _union_openings(openings) -> list:
    """Une aberturas que se sobrepõem ou se tocam: uma peça curta entre dois
    vãos faz o mesmo vão aparecer por dois pares de peças. A união vale o
    maior vão (menor peitoril, maior topo)."""
    out: list = []
    for o in sorted(openings, key=lambda o: o["s0"]):
        if out and o["s0"] <= out[-1]["s1"] + _EPS:
            last = out[-1]
            last["s1"] = max(last["s1"], o["s1"])
            last["sill"] = min(last["sill"], o["sill"])
            last["head"] = max(last["head"], o["head"])
        else:
            out.append(dict(o))
    return out


def merge_gap_walls(records, gaps, max_thickness_diff: float = 0.03):
    """Funde os trechos que cercam cada vão numa parede CONTÍNUA com a abertura
    no lugar da lacuna — a porta vira parâmetro da parede, não duas paredes e
    uma verga. ``gaps``: itens de :func:`wall_gaps` (o chamador escolhe quais
    viram abertura; cada um pode trazer ``head`` — topo do vão, padrão 2,10 m
    — e ``sill``, padrão 0). Trechos com espessuras que diferem mais que
    ``max_thickness_diff`` não são fundidos. Aberturas que se sobrepõem na
    parede fundida viram uma só (ver :func:`_union_openings`). Devolve ``(registros, ignorados)``:
    os registros (fundidos ou intactos) e os vãos que ficaram de fora."""
    recs = [dict(r) for r in records]
    parent = list(range(len(recs)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    used, ignored = [], []
    for g in gaps:
        i, j = g["between"]
        if abs(recs[i]["thickness"] - recs[j]["thickness"]) > max_thickness_diff:
            ignored.append(g)
            continue
        parent[find(i)] = find(j)
        used.append(g)
    groups: dict = {}
    for k in range(len(recs)):
        groups.setdefault(find(k), []).append(k)
    out = []
    for members in groups.values():
        if len(members) == 1:
            out.append(recs[members[0]])
            continue
        first = recs[min(members)]
        a0 = first["a"]
        dx, dy = first["b"][0] - a0[0], first["b"][1] - a0[1]
        length = math.hypot(dx, dy)
        ux, uy = dx / length, dy / length

        def proj(p):
            return (p[0] - a0[0]) * ux + (p[1] - a0[1]) * uy
        ends = [proj(recs[m][e]) for m in members for e in ("a", "b")]
        smin, smax = min(ends), max(ends)
        mset = set(members)
        openings = []
        for g in used:
            if g["between"][0] in mset:
                s0, s1 = sorted((proj(g["a"]) - smin, proj(g["b"]) - smin))
                openings.append({"s0": s0, "s1": s1,
                                 "sill": g.get("sill", 0.0),
                                 "head": g.get("head", 2.10)})
        merged = {k: v for k, v in first.items() if k not in ("a", "b")}
        merged.update({"a": [a0[0] + ux * smin, a0[1] + uy * smin],
                       "b": [a0[0] + ux * smax, a0[1] + uy * smax],
                       "thickness": max(recs[m]["thickness"] for m in members),
                       "openings": _union_openings(openings)})
        out.append(merged)
    return out, ignored


def create_walls(viewport, records, *, height=None, level="PB",
                 base_offset=0.0) -> list:
    """Cria várias paredes num ÚNICO passo de desfazer (importar uma planta
    não pode custar um Ctrl+Z por parede). ``records``: dicts com ``a``, ``b``
    e ``thickness`` (p.ex. de :func:`wall_from_rectangle`). Tudo ou nada: um
    registro inválido desfaz o lote inteiro. Devolve os grupos criados, ou
    ``[]`` se falhou."""
    records = list(records)
    made: list = []

    def mutate(scene):
        scene.plugin_data[KEY] = project_data(scene)
        for rec in records:
            g = build_wall_group(scene, {
                "kind": "wall", "a": rec["a"], "b": rec["b"],
                "thickness": rec["thickness"],
                "height": DEFAULTS["height"] if height is None else height,
                "level": level, "base_offset": base_offset,
                "openings": rec.get("openings", [])})
            scene.groups.append(g)
            made.append(g)

    if not records:
        return []
    return made if _run(viewport, mutate) else []


def create_frames(viewport, records, *, height=None, level="PB") -> list:
    """Cria várias esquadrias num ÚNICO passo de desfazer (tudo ou nada).
    ``records``: dicts com ``a``, ``b`` e, se quiser, ``depth``, ``divisions``,
    ``mullion``, ``panel``, ``panel_thickness``, ``ifc``, ``height`` e
    ``base_offset``. Devolve os grupos criados, ou ``[]`` se falhou."""
    records = list(records)
    made: list = []

    def mutate(scene):
        scene.plugin_data[KEY] = project_data(scene)
        for rec in records:
            g = build_frame_group(scene, dict(
                rec, kind="frame", level=level,
                height=rec.get("height", DEFAULTS["height"]
                               if height is None else height)))
            scene.groups.append(g)
            made.append(g)

    if not records:
        return []
    return made if _run(viewport, mutate) else []


def edit_walls(viewport, targets, **changes) -> int:
    """Muda parâmetros (``thickness``, ``height``, ``level``, ``base_offset``,
    ``a``, ``b``, ``openings``) de ``targets`` E regenera o 3D, num único passo de
    desfazer — desfazer devolve parâmetros e geometria juntos. Devolve
    quantas paredes mudaram."""
    allowed = {"thickness", "height", "level", "base_offset", "a", "b",
               "openings"}
    unknown = set(changes) - allowed
    if unknown:
        raise TypeError(f"unknown wall parameter(s): {sorted(unknown)}")
    todo = [g for g in targets if wall_record(g) is not None]
    if not todo:
        return 0

    def mutate(sc):
        for g in todo:
            g.ext = dict(g.ext, **{KEY: dict(wall_record(g), **changes)})
            regenerate_wall(sc, g)

    return len(todo) if _run(viewport, mutate) else 0


def update_3d(viewport, targets=None) -> int:
    """«Atualizar 3D»: regenera as paredes (``targets``, ou todas) a partir
    dos parâmetros guardados — TODAS num único passo de desfazer. Devolve
    quantas paredes foram refeitas (0 se nada foi feito)."""
    scene = viewport.scene
    if getattr(scene, "edit_group", None) is not None:
        _flash(viewport, _t("Close the group you are editing, then run "
                            "this again."))
        return 0
    todo = [g for g in (targets if targets is not None else elements(scene))
            if element_record(g) is not None]
    if not todo:
        _flash(viewport, _t("No BIM proto walls in the document."))
        return 0

    def mutate(sc):
        for g in todo:
            regenerate_element(sc, g)

    if not _run(viewport, mutate):
        return 0
    _flash(viewport, _t("{n} wall(s) updated", n=len(todo)))
    return len(todo)


# ---------------------------------------------------------------------------
# Ferramenta: parede por dois pontos
# ---------------------------------------------------------------------------

class WallTool(AxisMagnet, Tool):
    """Dois cliques (ou clique + comprimento digitado) e a parede existe.

    O ímã de eixo (``AxisMagnet``) e as inferências do motor de snap vêm do
    núcleo: ``ctx.world`` já chega encaixado. A espessura, a altura e o nível
    de base são atributos da ferramenta (um painel os ligaria a campos).

    Não é achada pelo carregador (exige ``app``), então a extensão a monta no
    ``setup`` e a ativa com ``viewport.set_active_tool`` — uma ferramenta
    achada pelo carregador seria de um disparo só (docs/plugins.md)."""

    name = "Wall (BIM proto)"
    description = ("Draw a wall from two points, with thickness, height and "
                   "base level.")
    vcb_label = "Length"

    def __init__(self, app) -> None:
        self.app = app
        self.thickness = DEFAULTS["thickness"]
        self.height = DEFAULTS["height"]
        self.level = "PB"
        self.start_point: QVector3D | None = None
        self.hover_point: QVector3D | None = None

    # ---- ciclo de vida
    def on_activate(self, viewport) -> None:
        self.start_point = self.hover_point = None
        viewport.flash_status(_t("Click the wall's first point"), 4000)

    def on_deactivate(self, viewport) -> None:
        self.start_point = self.hover_point = None

    def on_cancel(self, viewport) -> None:
        self.start_point = self.hover_point = None
        viewport.update()

    # ---- entrada espacial
    def on_click(self, ctx: ToolContext) -> None:
        vp = ctx.viewport
        if self.start_point is None:
            self.start_point = QVector3D(ctx.world)
            vp.flash_status(_t("Click the wall's second point, or type its "
                               "length"), 4000)
            return
        self._commit(vp, self.start_point, QVector3D(ctx.world))

    def on_hover(self, ctx: ToolContext) -> None:
        self.hover_point = QVector3D(ctx.world)
        ctx.viewport.update()

    def on_value(self, viewport, value) -> bool:
        """Comprimento digitado (VCB), ao longo da direção do elástico."""
        if self.start_point is None or isinstance(value, tuple):
            return False
        if self.hover_point is None or abs(value) < _EPS:
            return False
        delta = self.hover_point - self.start_point
        delta.setZ(0.0)
        if delta.length() < _EPS:
            return False
        end = self.start_point + delta.normalized() * value
        return self._commit(viewport, self.start_point, end)

    def rubber_band_lines(self):
        if self.start_point is None or self.hover_point is None:
            return []
        return [(self.start_point, self.hover_point)]

    def _commit(self, viewport, a: QVector3D, b: QVector3D) -> bool:
        if math.hypot(b.x() - a.x(), b.y() - a.y()) <= _EPS:
            return False
        g = create_wall(viewport, (a.x(), a.y()), (b.x(), b.y()),
                        thickness=self.thickness, height=self.height,
                        level=self.level)
        self.start_point = self.hover_point = None
        return g is not None



# ---------------------------------------------------------------------------
# Painel: navegador de projeto (árvore)
# ---------------------------------------------------------------------------

def project_tree_model(scene) -> list:
    """O que a árvore mostra, como dados puros (testável sem Qt):
    ``[(rótulo, [(rótulo, [...]) ...]) ...]``."""
    data = project_data(scene)
    by_level: dict = {}
    for g in elements(scene):
        by_level.setdefault(element_record(g).get("level"), []).append(g)
    levels = []
    for lv in sorted(data["levels"], key=lambda v: float(v.get("z", 0))):
        kids = [(g.name if element_record(g)["kind"] in ("sill", "leaf", "block") else
                 f"{g.name} — {room_area(element_record(g)):.2f} m²"
                 if element_record(g)["kind"] == "room" else
                 f"{g.name} — {_len(element_record(g)):.2f} m", [])
                for g in by_level.get(lv.get("id"), [])]
        levels.append((f"{lv.get('name', lv.get('id'))}  (+{float(lv['z']):.2f} m)",
                       kids))
    views = [(v.name, []) for v in scene.saved_views]
    sheets = [(c.name, []) for c in scene.compositions]
    return [(_t("Levels"), levels), (_t("Views"), views),
            (_t("Sheets"), sheets)]


def _len(rec) -> float:
    return math.hypot(rec["b"][0] - rec["a"][0], rec["b"][1] - rec["a"][1])


def fill_tree(tree, model) -> None:
    def add(parent, nodes):
        for label, kids in nodes:
            item = QTreeWidgetItem(parent, [label])
            add(item, kids)
    tree.clear()
    add(tree.invisibleRootItem(), model)
    tree.expandAll()

# ---------------------------------------------------------------------------
# Importar planta (DWG/DXF): paredes, pilares e vãos de porta
# ---------------------------------------------------------------------------

#: Palavras que, no NOME da camada, sugerem o que ela guarda (minúsculas, sem acento).
WALL_WORDS = ("alvenaria", "parede", "wall", "muro")
DOOR_WORDS = ("porta", "door")
COLUMN_WORDS = ("pilar", "column", "coluna")


def _plain(text: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", text.lower())
                   if unicodedata.category(c) != "Mn")


def open_plan(path):
    """Abre um ``.dwg`` ou ``.dxf`` com o ezdxf e devolve o documento. O DWG passa
    pelo conversor do núcleo (LibreDWG) e o DXF temporário é descartado em seguida."""
    import ezdxf
    from pathlib import Path
    path = Path(path)
    if path.suffix.lower() == ".dwg":
        from formats.dwg_bridge import discard_temp_dxf, dwg_to_dxf
        dxf = dwg_to_dxf(path)
        try:
            return ezdxf.readfile(str(dxf))
        finally:
            discard_temp_dxf(dxf)
    return ezdxf.readfile(str(path))


def _explode(ent, depth=0):
    """As entidades de ``ent`` com os blocos (aninhados) abertos, já no mundo."""
    if ent.dxftype() == "INSERT" and depth < 8:
        for v in ent.virtual_entities():
            yield from _explode(v, depth + 1)
    else:
        yield ent


def _rings(ent) -> list:
    """Anéis fechados (listas de (x, y) no MUNDO) de uma entidade: polilinha fechada
    ou o contorno de uma hachura. Bloco espelhado traz a extrusão (0,0,-1): por isso
    ``vertices_in_wcs`` (ler os pontos crus espelha a parede)."""
    kind = ent.dxftype()
    if kind == "LWPOLYLINE" and ent.closed:
        pts = [(p[0], p[1]) for p in ent.vertices_in_wcs()]
        return [pts] if len(pts) >= 3 else []
    if kind == "HATCH":
        from ezdxf.math import OCS
        ocs = OCS(ent.dxf.extrusion)
        out = []
        for path in ent.paths:
            if getattr(path, "vertices", None) and len(path.vertices) >= 3:
                w = ocs.points_to_wcs([(v[0], v[1], ent.dxf.elevation.z)
                                       for v in path.vertices])
                out.append([(q.x, q.y) for q in w])
        return out
    return []


def scan_plan(doc) -> list:
    """As camadas do desenho que têm polígonos fechados (paredes, hachuras…):
    ``[{"layer", "count", "suggested"}]`` — ``suggested`` quando o nome fala de
    parede/alvenaria. Do que tem mais polígonos para o que tem menos."""
    count: dict = {}
    for ent in doc.modelspace():
        top = ent.dxf.layer
        for e in _explode(ent):
            n = len(_rings(e))
            if n:
                count[top] = count.get(top, 0) + n
    return [{"layer": k, "count": v,
             "suggested": any(w in _plain(k) for w in WALL_WORDS)}
            for k, v in sorted(count.items(), key=lambda kv: -kv[1])]


def plan_polygons(doc, layers) -> list:
    """Os polígonos de parede e de pilar do desenho: tudo o que as camadas
    ``layers`` têm (dentro de blocos também) mais qualquer bloco cujo NOME fale de
    pilar/coluna. Iguais (a 1 mm) contam uma vez."""
    wanted = set(layers)
    seen, out = set(), []
    for ent in doc.modelspace():
        is_column = ent.dxftype() == "INSERT" and any(
            w in _plain(ent.dxf.name) for w in COLUMN_WORDS)
        if ent.dxf.layer not in wanted and not is_column:
            continue
        for e in _explode(ent):
            for ring in _rings(e):
                key = tuple(sorted((round(x, 3), round(y, 3)) for x, y in ring))
                if key not in seen:
                    seen.add(key)
                    out.append(ring)
    return out


def split_plans(records, gap: float = 6.0) -> list:
    """Separa em PLANTAS as paredes de ``records``: um desenho pode ter duas plantas
    empilhadas (base e proposta). Peças cujas caixas ficam a menos de ``gap`` m uma
    da outra são da mesma planta. ``[{"indices", "bbox": (x0, y0, x1, y1)}]``, da
    com mais peças para a com menos."""
    boxes = [(min(r["a"][0], r["b"][0]), min(r["a"][1], r["b"][1]),
              max(r["a"][0], r["b"][0]), max(r["a"][1], r["b"][1])) for r in records]
    parent = list(range(len(boxes)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    order = sorted(range(len(boxes)), key=lambda i: boxes[i][0])     # varre por x
    for a_pos, i in enumerate(order):
        for j in order[a_pos + 1:]:
            if boxes[j][0] - gap > boxes[i][2]:
                break                       # j e os seguintes começam longe demais em x
            if boxes[j][1] - gap <= boxes[i][3] and boxes[i][1] - gap <= boxes[j][3]:
                parent[find(i)] = find(j)
    groups: dict = {}
    for i in range(len(boxes)):
        groups.setdefault(find(i), []).append(i)
    out = []
    for idx in sorted(groups.values(), key=len, reverse=True):
        bx = [boxes[i] for i in idx]
        out.append({"indices": idx, "bbox": (min(b[0] for b in bx), min(b[1] for b in bx),
                                              max(b[2] for b in bx), max(b[3] for b in bx))})
    return out


def plan_doors(doc, layers=None) -> list:
    """As PORTAS do desenho: blocos nas camadas com ``porta``/``door`` no nome (ou
    ``layers``). ``[{"name", "c": (x, y), "w", "h"}]`` — o centro é o da caixa do
    desenho do bloco; largura e altura vêm do nome (``80 x 210 cm``, ``700x2100``,
    ``_090_H210``) quando ele as traz."""
    import re
    from ezdxf import bbox
    out = []
    for ins in doc.modelspace().query("INSERT"):
        lay = ins.dxf.layer
        if not (lay in (layers or ()) or (layers is None and any(
                w in _plain(lay) for w in DOOR_WORDS))):
            continue
        ents = [x for x in _explode(ins) if x.dxftype() in ("LINE", "LWPOLYLINE", "ARC", "CIRCLE")]
        if not ents:
            continue
        b = bbox.extents(ents, fast=True)
        if not b.has_data:
            continue
        name, w, h = ins.dxf.name, None, None
        for pat, k in ((r"(\d{2,3})\s*x\s*(\d{3})\s*cm", 100), (r"(\d{3,4})x(\d{4})", 1000),
                       (r"_(\d{3})_H(\d{3})", 100)):
            m = re.search(pat, name)
            if m:
                w, h = int(m.group(1)) / k, int(m.group(2)) / k
                break
        out.append({"name": name, "c": (b.center.x, b.center.y), "w": w, "h": h})
    return out


def import_plan(viewport, path=None, *, doc=None, layers=None, plan: int = 0,
                level="PB", height=None, door_head: float = 2.10,
                recenter: bool = True, min_fill: float = 0.97,
                plan_gap: float = 6.0) -> dict:
    """Importa a planta de ``path`` (``.dwg``/``.dxf``) — ou de ``doc`` já aberto — como
    paredes paramétricas, num ÚNICO passo de desfazer:

    * os polígonos das camadas ``layers`` (padrão: as sugeridas por nome) viram
      paredes (``wall_from_rectangle``); o que não é retângulo (L, T, curva) fica
      de fora e é contado;
    * se o desenho traz várias plantas empilhadas, ``plan`` escolhe uma (0 = a com
      mais peças; veja :func:`split_plans` — plantas a mais de ``plan_gap`` m uma da outra);
    * as portas do desenho (:func:`plan_doors`) são ligadas ao vão mais próximo
      (até 1,3 m); cada vão ligado vira uma ABERTURA de ``door_head`` m numa
      parede contínua, e o vão que não pôde ser fundido (espessuras diferentes)
      leva uma verga;
    * ``recenter`` põe o canto da planta em (0, 0); o deslocamento fica em
      ``plugin_data`` (``imports``) para quem for ler outras coisas do desenho.

    Devolve um resumo: peças, paredes, aberturas, vergas, portas, deslocamento…
    Levanta :class:`BimProtoError` se nada serve."""
    doc = doc if doc is not None else open_plan(path)
    cands = scan_plan(doc)
    chosen = list(layers) if layers is not None else [c["layer"] for c in cands if c["suggested"]]
    if not chosen:
        raise BimProtoError("no wall layer found: pass layers=[...]")
    pieces, skipped = [], 0
    for ring in plan_polygons(doc, chosen):
        try:
            rec = wall_from_rectangle(ring, min_fill)
        except BimProtoError:
            skipped += 1
            continue
        if rec["thickness"] >= 0.03 and math.hypot(rec["b"][0] - rec["a"][0],
                                                   rec["b"][1] - rec["a"][1]) >= 0.05:
            pieces.append(rec)
    if not pieces:
        raise BimProtoError("no straight wall pieces in the chosen layers")
    plans = split_plans(pieces, plan_gap)
    if not 0 <= plan < len(plans):
        raise BimProtoError(f"plan {plan} does not exist ({len(plans)} found)")
    sel = [pieces[i] for i in plans[plan]["indices"]]
    x0, y0, x1, y1 = plans[plan]["bbox"]
    dx, dy = (x0, y0) if recenter else (0.0, 0.0)

    def shift(pt):
        return [pt[0] - dx, pt[1] - dy]

    sel = [{"a": shift(r["a"]), "b": shift(r["b"]), "thickness": r["thickness"],
            "level": level} for r in sel]
    doors = [d for d in plan_doors(doc)
             if x0 - 3 <= d["c"][0] <= x1 + 3 and y0 - 3 <= d["c"][1] <= y1 + 3]
    gaps = wall_gaps(sel)
    used, matched = set(), []
    for d in doors:
        cx, cy = d["c"][0] - dx, d["c"][1] - dy
        best = None
        for k, g in enumerate(gaps):
            if k in used:
                continue
            mx, my = (g["a"][0] + g["b"][0]) / 2, (g["a"][1] + g["b"][1]) / 2
            dist = math.hypot(mx - cx, my - cy)
            if dist < 1.3 and (best is None or dist < best[0]):
                best = (dist, k)
        if best:
            used.add(best[1])
            matched.append(dict(gaps[best[1]], head=door_head))
    merged, ignored = merge_gap_walls(sel, matched)
    h = DEFAULTS["height"] if height is None else float(height)
    headers = [{"a": g["a"], "b": g["b"], "thickness": g["thickness"],
                "height": max(h - g["head"], 0.05), "base_offset": g["head"]}
               for g in ignored]

    def mutate(scene):
        data = project_data(scene)
        data.setdefault("imports", []).append(
            {"layers": chosen, "shift": [dx, dy], "plan": plan,
             "file": str(getattr(path, "name", path) or "")})
        scene.plugin_data[KEY] = data
        for rec in merged:
            scene.groups.append(build_wall_group(scene, {
                "kind": "wall", "a": rec["a"], "b": rec["b"], "thickness": rec["thickness"],
                "height": h, "level": level, "base_offset": 0.0,
                "openings": rec.get("openings", [])}))
        for rec in headers:
            scene.groups.append(build_wall_group(scene, dict(rec, kind="wall", level=level)))

    summary = {"layers": chosen, "pieces": len(sel), "skipped": skipped,
               "plans": [len(p_["indices"]) for p_ in plans], "plan": plan,
               "doors": len(doors), "doors_matched": len(matched),
               "walls": len(merged), "openings": sum(len(r.get("openings", [])) for r in merged),
               "headers": len(headers), "shift": [dx, dy]}
    if not _run(viewport, mutate):
        raise BimProtoError(viewport.history.last_error or "import failed")
    return summary


# ---------------------------------------------------------------------------
# Painel «BIM»: ícones e botões (a cara da extensão)
# ---------------------------------------------------------------------------

def update_rooms(viewport, *, level="PB") -> int:
    """Refaz os ambientes do documento pelas paredes e esquadrias de hoje — UM passo
    de desfazer. Os nomes dados antes continuam nos ambientes que ocupam o mesmo
    lugar (o centro de um cai dentro do outro). Devolve quantos ambientes há."""
    scene = viewport.scene
    recs = [element_record(g) for g in scene.groups
            if (element_record(g) or {}).get("kind") in ("wall", "frame")]
    found = detect_rooms(recs)
    old = [(g.name, room_record(g)) for g in rooms(scene)]

    def centre(ring):
        return (sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring))

    names = []
    for f in found:
        nm = None
        for name, rec in old:
            if _inside(centre(rec["outline"]), f["outline"]):
                nm = name
                break
        names.append(nm)
    if not found and not old:
        return 0

    def mutate(sc):
        sc.groups[:] = [g for g in sc.groups if room_record(g) is None]
        sc.plugin_data[KEY] = project_data(sc)
        for f, nm in zip(found, names):
            g = _build_piece(sc, dict(f, level=level, name=nm or ""), "room")
            if nm:
                g.name = nm
                g.ifc = {"class": "IfcSpace", "name": nm}
            sc.groups.append(g)

    return len(found) if _run(viewport, mutate) else -1


def bim_icon(key: str):
    """Ícone de 48 px desenhado com QPainter, no estilo do programa (traço na cor do
    texto da paleta e destaque laranja) — sem arquivos de imagem."""
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    ink = app.palette().windowText().color() if app else QColor(40, 44, 52)
    ink = QColor(ink.red(), ink.green(), ink.blue())
    acc = QColor("#f26b21")
    pm = QPixmap(48, 48)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    line = QPen(ink, 3.0)
    line.setJoinStyle(Qt.RoundJoin)
    line.setCapStyle(Qt.RoundCap)
    p.setPen(line)

    def accent(w=3.0):
        q = QPen(acc, w)
        q.setCapStyle(Qt.RoundCap)
        p.setPen(q)

    def wall(x0, x1, y):
        p.setPen(Qt.NoPen)
        p.setBrush(ink)
        p.drawRect(QRectF(x0, y - 4, x1 - x0, 8))
        p.setPen(line)
        p.setBrush(Qt.NoBrush)

    if key == "wall":
        wall(6, 42, 24)
        accent(); p.drawPoint(QPointF(6, 24)); p.drawPoint(QPointF(42, 24))
    elif key == "door":
        wall(6, 16, 36); wall(32, 42, 36)
        p.drawLine(QPointF(16, 36), QPointF(16, 10))
        accent(2.4)
        p.drawArc(QRectF(-10, 10, 52, 52), 0, 90 * 16)
    elif key == "window":
        wall(6, 14, 24); wall(34, 42, 24)
        accent(2.6)
        p.drawLine(QPointF(14, 22), QPointF(34, 22)); p.drawLine(QPointF(14, 26), QPointF(34, 26))
    elif key == "sill":
        wall(6, 42, 20)
        accent(3.4); p.drawLine(QPointF(10, 30), QPointF(38, 30))
    elif key == "join":
        p.setPen(Qt.NoPen); p.setBrush(ink)
        p.drawRect(QRectF(8, 8, 8, 32)); p.drawRect(QRectF(8, 32, 32, 8))
        p.setBrush(acc); p.drawRect(QRectF(8, 32, 8, 8))
    elif key == "room":
        dash = QPen(ink, 2.4, Qt.DashLine)
        p.setPen(dash); p.setBrush(QColor(242, 107, 33, 40))
        p.drawPolygon(QPolygonF([QPointF(9, 10), QPointF(39, 10), QPointF(39, 28),
                                 QPointF(28, 28), QPointF(28, 39), QPointF(9, 39)]))
        accent(4.5); p.drawPoint(QPointF(18, 22))
    elif key == "update":
        accent(3.2)
        p.drawArc(QRectF(9, 9, 30, 30), 40 * 16, 140 * 16)
        p.drawArc(QRectF(9, 9, 30, 30), 220 * 16, 140 * 16)
        p.setPen(Qt.NoPen); p.setBrush(acc)
        p.drawPolygon(QPolygonF([QPointF(40, 12), QPointF(40, 24), QPointF(29, 18)]))
        p.drawPolygon(QPolygonF([QPointF(8, 36), QPointF(8, 24), QPointF(19, 30)]))
    elif key == "plan":
        wall(8, 40, 14); wall(8, 40, 38)
        p.setPen(Qt.NoPen); p.setBrush(ink); p.drawRect(QRectF(8, 14, 8, 24))
        q = QPen(acc, 2.4, Qt.DashLine); p.setPen(q); p.drawLine(QPointF(20, 26), QPointF(40, 26))
    elif key == "table":
        p.drawRect(QRectF(8, 10, 32, 28))
        p.drawLine(QPointF(8, 20), QPointF(40, 20)); p.drawLine(QPointF(8, 29), QPointF(40, 29))
        accent(); p.drawLine(QPointF(22, 10), QPointF(22, 38))
    elif key == "import":
        accent(3.2); p.drawLine(QPointF(24, 6), QPointF(24, 26))
        p.drawLine(QPointF(16, 19), QPointF(24, 27)); p.drawLine(QPointF(32, 19), QPointF(24, 27))
        p.setPen(line); p.drawLine(QPointF(8, 34), QPointF(8, 40)); p.drawLine(QPointF(8, 40), QPointF(40, 40))
        p.drawLine(QPointF(40, 40), QPointF(40, 34))
    elif key == "blocks":
        p.drawRect(QRectF(8, 24, 14, 14)); p.drawRect(QRectF(26, 24, 14, 14))
        accent(); p.drawRect(QRectF(17, 8, 14, 14))
    p.end()
    return QIcon(pm)


def ask_import_options(parent=None):
    """O diálogo de «Importar DWG»: escolhe o arquivo, as camadas de parede, a
    planta (se o desenho tem mais de uma) e as alturas. Devolve os argumentos de
    :func:`import_plan` (com o documento já aberto) ou ``None`` se cancelado."""
    from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QFormLayout,
                                   QListWidget, QListWidgetItem)
    path, _ = QFileDialog.getOpenFileName(parent, _t("Open DWG or DXF"), "",
                                          "DWG / DXF (*.dwg *.dxf)")
    if not path:
        return None
    QApplication.setOverrideCursor(Qt.WaitCursor)
    try:
        doc = open_plan(path)
        cands = scan_plan(doc)
    finally:
        QApplication.restoreOverrideCursor()
    dlg = QDialog(parent)
    dlg.setWindowTitle(_t("Import DWG"))
    form = QFormLayout(dlg)
    lst = QListWidget()
    for c in cands:
        it = QListWidgetItem(f"{c['layer']}  ({c['count']})")
        it.setData(Qt.UserRole, c["layer"])
        it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
        it.setCheckState(Qt.Checked if c["suggested"] else Qt.Unchecked)
        lst.addItem(it)
    form.addRow(QLabel(_t("Layers with walls")), lst)
    height, head = QDoubleSpinBox(), QDoubleSpinBox()
    for w, v in ((height, DEFAULTS["height"]), (head, 2.10)):
        w.setRange(0.5, 10.0)
        w.setSingleStep(0.05)
        w.setValue(v)
    form.addRow(_t("Wall height (m)"), height)
    form.addRow(_t("Door height (m)"), head)
    recenter = QCheckBox(_t("Put the plan corner at 0,0"))
    recenter.setChecked(True)
    form.addRow(recenter)
    plan_box = QComboBox()
    form.addRow(_t("Plan to import"), plan_box)
    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)
    form.addRow(buttons)

    def checked():
        return [lst.item(i).data(Qt.UserRole) for i in range(lst.count())
                if lst.item(i).checkState() == Qt.Checked]

    def fill_plans():
        plan_box.clear()
        layers = checked()
        if not layers:
            return
        recs = []
        for ring in plan_polygons(doc, layers):
            try:
                recs.append(wall_from_rectangle(ring))
            except BimProtoError:
                pass
        for n, pl in enumerate(split_plans(recs) if recs else []):
            x0, y0, x1, y1 = pl["bbox"]
            plan_box.addItem(_t("Plan {n}: {count} pieces (y {y0:.0f} to {y1:.0f} m)",
                                n=n + 1, count=len(pl["indices"]), y0=round(y0) + 0.0,
                                y1=round(y1) + 0.0), n)

    lst.itemChanged.connect(lambda _i: fill_plans())
    fill_plans()
    if dlg.exec() != QDialog.Accepted or not checked() or plan_box.currentData() is None:
        return None
    return {"path": path, "doc": doc, "layers": checked(), "plan": plan_box.currentData(),
            "height": height.value(), "door_head": head.value(),
            "recenter": recenter.isChecked()}


class BimPanel(QWidget):
    """O painel «BIM»: botões com ícone (desenhar, aberturas, ambientes, planta 2D,
    atualizar) e, embaixo, a árvore do projeto. Cada botão chama uma função pública
    deste arquivo. O que ainda não existe aparece apagado, com «Em breve»."""

    #: ganchos que os testes trocam (nada de janela modal na prova)
    choose_save_path = staticmethod(lambda: QFileDialog.getSaveFileName(
        None, _t("Generate plan…"), "planta.png", "PNG (*.png)")[0])
    show_text = staticmethod(lambda title, text: QMessageBox.information(None, title, text))
    ask_import = staticmethod(ask_import_options)

    def __init__(self, app, start_wall) -> None:
        super().__init__()
        self.app = app
        self.setObjectName("bim_proto_panel")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)
        self.buttons: dict = {}

        def button(key, text, tip, fn, enabled=True):
            b = QToolButton()
            b.setObjectName("bim_btn_" + key)
            b.setText(text)
            b.setIcon(bim_icon(key))
            b.setIconSize(QSize(30, 30))
            b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            b.setAutoRaise(True)
            b.setMinimumWidth(66)
            b.setToolTip(tip if enabled else f"{tip} — {_t('Soon')}")
            b.setEnabled(enabled)
            if fn is not None:
                b.clicked.connect(lambda _=False, f=fn: f())
            self.buttons[key] = b
            return b

        def section(title, widgets):
            lab = QLabel(title.upper())
            f = lab.font()
            f.setBold(True)
            f.setPointSizeF(max(f.pointSizeF() - 1.5, 7))
            lab.setFont(f)
            lab.setContentsMargins(2, 6, 0, 0)
            lay.addWidget(lab)
            grid = QGridLayout()
            grid.setSpacing(2)
            for k, w in enumerate(widgets):
                grid.addWidget(w, k // 4, k % 4)
            lay.addLayout(grid)

        vp = lambda: app.viewport
        section(_t("Draw"), [
            button("wall", _t("Wall"), _t("Draw a wall from two points, with thickness, height and base level."), start_wall),
            button("import", _t("Import DWG"), _t("Import DWG"), self.import_dwg),
            button("blocks", _t("Block library"), _t("Block library"), None, enabled=False)])
        section(_t("Openings"), [
            button("join", _t("Join corners"), _t("Join corners"), lambda: join_corners(vp())),
            button("door", _t("Doors"), _t("Door leaves"), lambda: create_leaves(
                vp(), auto_swing=True, frame=True, trim=True, arc=True,
                thickness=0.035, clearance=0.002)),
            button("window", _t("Sills"), _t("Sills"), lambda: create_sills(vp()))])
        section(_t("Rooms"), [
            button("room", _t("Detect rooms"), _t("Detect rooms"), self.detect),
            button("table", _t("Area table"), _t("Estimated net area (m²)"), self.table)])
        section(_t("Plan 2D"), [button("plan", _t("Plan 2D"), _t("Generate plan…"), self.plan)])
        opts = QGridLayout()
        self.cut = QDoubleSpinBox()
        self.cut.setRange(0.1, 10.0)
        self.cut.setSingleStep(0.1)
        self.cut.setValue(1.20)
        self.cut.setSuffix(" m")
        self.style = QComboBox()
        self.style.addItem(_t("Office"), "ao")
        self.style.addItem(_t("CAD"), "cad")
        self.labels = QCheckBox(_t("Room names"))
        opts.addWidget(QLabel(_t("Cut at (m)")), 0, 0)
        opts.addWidget(self.cut, 0, 1)
        opts.addWidget(QLabel(_t("Style")), 1, 0)
        opts.addWidget(self.style, 1, 1)
        opts.addWidget(self.labels, 2, 0, 1, 2)
        lay.addLayout(opts)
        section(_t("Elements"), [button("update", _t("Update 3D"), _t("Rebuild the walls' 3D from their stored parameters."), self.update)])
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        lay.addWidget(self.tree, 1)

    def import_dwg(self):
        opts = self.ask_import()
        if not opts:
            return
        try:
            r = import_plan(self.app.viewport, **opts)
        except BimProtoError as exc:
            _flash(self.app.viewport, _t("Import failed: {why}", why=str(exc)), 8000)
            return
        _flash(self.app.viewport, _t("Imported: {walls} walls, {openings} door openings, "
                                     "{headers} headers", **r), 8000)

    def update(self):
        sel = []
        scene = self.app.scene
        for e in scene.selection:
            g = getattr(e, "owner", None) or e
            if g in scene.groups and element_record(g) is not None and g not in sel:
                sel.append(g)
        update_3d(self.app.viewport, sel or None)

    def detect(self):
        n = update_rooms(self.app.viewport)
        _flash(self.app.viewport, _t("{n} room(s) detected", n=n) if n > 0
               else _t("No closed rooms found."))

    def table(self):
        rows = area_table(self.app.scene)
        text = "\n".join(f"{nm}\t{ar}" for nm, ar in rows) or _t("No rooms yet.")
        self.show_text(_t("Estimated net area (m²)"), text)

    def plan(self):
        path = self.choose_save_path()
        if not path:
            return
        plan = plan_drawing(self.app.scene, self.cut.value())
        render_plan(plan, width=2400, style=self.style.currentData(),
                    labels=self.labels.isChecked()).save(path)
        _flash(self.app.viewport, _t("Plan saved: {path}", path=path))


# ---------------------------------------------------------------------------
# Integração com o app
# ---------------------------------------------------------------------------

def setup(app) -> None:
    tool = WallTool(app)

    def start_wall():
        # FRAGILE: set_active_tool não está em views/extension_api.py nem em
        # docs/plugins.md; o precedente é plugins/render_blender.py:1015.
        app.viewport.set_active_tool(tool)

    def refresh():
        # Atualizar 3D age sobre a seleção quando ela tem paredes nossas.
        sel = []
        for e in app.scene.selection:
            g = getattr(e, "owner", None) or e
            if g in app.scene.groups and element_record(g) is not None \
                    and g not in sel:
                sel.append(g)
        update_3d(app.viewport, sel or None)

    sub = app.add_menu(_t("BIM proto"))
    if sub is not None:
        sub.addAction(_t("Wall"), start_wall)
        sub.addAction(_t("Update 3D"), refresh)
        sub.addAction(_t("Join corners"), lambda: join_corners(app.viewport))
        sub.addAction(_t("Sills"), lambda: create_sills(app.viewport))
        sub.addAction(_t("Door leaves"), lambda: create_leaves(app.viewport))
    app.add_outliner_kind(_outliner_kind)

    panel = BimPanel(app, start_wall)

    def refresh_tree():
        fill_tree(panel.tree, project_tree_model(app.scene))

    refresh_tree()
    app.on_document_changed(refresh_tree)
    app.add_panel(_t("Project"), panel)


def _outliner_kind(group):
    rec = frame_record(group)
    if rec is not None:
        return {"type": _t("Railing" if rec["ifc"] == "IfcRailing"
                           else "Frame"), "props": [
            ("Level", rec["level"]),
            ("Mullions", str(len(rec["divisions"]))),
            ("Height", f"{rec['height']:.2f} m")]}
    rec = element_record(group)
    if rec is not None and rec["kind"] == "room":
        return {"type": _t("Room"), "props": [
            ("Level", rec["level"]), ("Area", f"{room_area(rec):.2f} m²")]}
    if rec is not None and rec["kind"] == "block":
        return {"type": _t("Block"), "props": [
            ("Level", rec["level"]), ("Block", rec["block"])]}
    if rec is not None and rec["kind"] in ("sill", "leaf"):
        return {"type": _t(_KINDS[rec["kind"]][3]), "props": [
            ("Level", rec["level"]),
            ("Thickness", f"{rec['thickness']:.2f} m")]}
    rec = wall_record(group)
    if rec is None:
        return None
    return {"type": _t("Wall"), "props": [
        ("Level", rec["level"]),
        ("Thickness", f"{rec['thickness']:.2f} m"),
        ("Height", f"{rec['height']:.2f} m")]}
