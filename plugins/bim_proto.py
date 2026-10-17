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

from PySide6.QtGui import QVector3D
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem

from core import bim
# FRAGILE: Group e Mesh são públicos, mas o contrato documentado só fala de
# ``group.ext``; o precedente é examples/extensions/windowizer.py.
from core.group import Group
from core.history import Command
from core.mesh import Mesh
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
            "Sill": "sill", "Door leaf": "leaf"}[what]
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


def normalize_leaf(rec: dict) -> dict:
    """Folha de porta dentro de um vão de parede (``s0``/``s1`` ao longo do eixo
    ``a``→``b``, ``head`` = topo do vão acima da base). ``hinge`` ``"a"`` ou
    ``"b"`` diz de que lado fica a dobradiça; ``side`` (+1 esquerda do eixo, −1
    direita) para onde ela abre; ``angle`` em graus (0 = fechada);
    ``clearance`` é a folga em volta (chão, topo e laterais)."""
    try:
        out = _common(rec, {
            "kind": "leaf",
            "s0": float(min(rec["s0"], rec["s1"])),
            "s1": float(max(rec["s0"], rec["s1"])),
            "head": float(rec["head"]),
            "thickness": float(rec.get("thickness", 0.04)),
            "hinge": str(rec.get("hinge", "a")),
            "side": int(rec.get("side", 1)),
            "angle": float(rec.get("angle", 0.0)),
            "clearance": float(rec.get("clearance", 0.01)),
        })
    except (KeyError, TypeError, ValueError) as exc:
        raise BimProtoError(f"bad door leaf record: {exc}") from exc
    _, _, _, length = _axis_frame(out)
    nums = out["a"] + out["b"] + [out[k] for k in (
        "s0", "s1", "head", "thickness", "angle", "clearance", "base_offset")]
    if not all(math.isfinite(v) for v in nums):
        raise BimProtoError("non-finite value in the door leaf record")
    if out["hinge"] not in ("a", "b") or out["side"] not in (-1, 1):
        raise BimProtoError("hinge must be 'a' or 'b' and side 1 or -1")
    if not 0.0 <= out["angle"] <= 150.0:
        raise BimProtoError("angle must be between 0 and 150 degrees")
    c = out["clearance"]
    if c < 0 or out["thickness"] <= _EPS \
            or out["s1"] - out["s0"] <= 2 * c + _EPS \
            or out["head"] <= 2 * c + _EPS:
        raise BimProtoError("the door leaf does not fit its opening")
    if out["s0"] < -_EPS or out["s1"] > length + _EPS:
        raise BimProtoError("the opening must lie inside the wall's length")
    return out


def leaf_mesh(rec: dict, base_z: float) -> Mesh:
    """A folha é um prisma de ``thickness`` girado ``angle`` em torno da
    dobradiça (eixo vertical) para o lado ``side``."""
    origin, u, n, _ = _axis_frame(rec)
    c = rec["clearance"]
    width = rec["s1"] - rec["s0"] - 2 * c
    d = 1.0 if rec["hinge"] == "a" else -1.0
    hinge_s = rec["s0"] + c if rec["hinge"] == "a" else rec["s1"] - c
    th = math.radians(rec["angle"])
    e_s = (d * math.cos(th), rec["side"] * math.sin(th))      # ao longo da folha
    e_t = (-e_s[1], e_s[0])                                   # espessura
    half = rec["thickness"] / 2.0
    foot_st = []
    for x, tt in ((0, -half), (width, -half), (width, half), (0, half)):
        foot_st.append((hinge_s + e_s[0] * x + e_t[0] * tt,
                        e_s[1] * x + e_t[1] * tt))
    foot = [(origin[0] + u[0] * s_ + n[0] * t_, origin[1] + u[1] * s_ + n[1] * t_)
            for s_, t_ in foot_st]
    area2 = sum(foot[i][0] * foot[(i + 1) % 4][1] - foot[(i + 1) % 4][0] * foot[i][1]
                for i in range(4))
    if area2 < 0:
        foot.reverse()
    mesh = Mesh()
    _add_prism(mesh, foot, base_z + c, base_z + rec["head"] - c)
    return mesh


#: kind -> (normalizar, malha, classe IFC, nome no menu)
_KINDS = {
    "wall": (normalize_record, wall_mesh, "IfcWall", "Wall"),
    "frame": (normalize_frame, frame_mesh, None, "Frame"),
    "sill": (normalize_sill, sill_mesh, "IfcMember", "Sill"),
    "leaf": (normalize_leaf, leaf_mesh, "IfcDoor", "Door leaf"),
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
    return _create_from_openings(viewport, targets, "sill", lambda w, o: {
        "z": o["sill"], "s0": o["s0"], "s1": o["s1"],
        "wall_thickness": w["thickness"], "thickness": thickness,
        "projection": projection, "overhang": overhang, "side": side},
        lambda o: o["sill"] > _EPS)


def create_leaves(viewport, targets=None, *, thickness=0.04, hinge="a",
                  side=1, angle=0.0, clearance=0.01) -> list:
    """Uma folha de porta para cada PORTA (abertura com ``sill`` 0) das paredes
    ``targets`` (ou de todas) — um único passo de desfazer, tudo ou nada."""
    return _create_from_openings(viewport, targets, "leaf", lambda w, o: {
        "head": o["head"], "s0": o["s0"], "s1": o["s1"], "thickness": thickness,
        "hinge": hinge, "side": side, "angle": angle, "clearance": clearance},
        lambda o: o["sill"] <= _EPS)


def _create_from_openings(viewport, targets, kind, extra, wanted) -> list:
    scene = viewport.scene
    todo = [g for g in (targets if targets is not None else walls(scene))
            if wall_record(g) is not None]
    jobs = []
    for g in todo:
        w = wall_record(g)
        for o in w.get("openings") or []:
            if wanted(o):
                jobs.append(dict(extra(w, o), a=w["a"], b=w["b"],
                                 level=w["level"],
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
        kids = [(g.name if element_record(g)["kind"] in ("sill", "leaf") else
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

    tree = QTreeWidget()
    tree.setHeaderHidden(True)

    def refresh_tree():
        fill_tree(tree, project_tree_model(app.scene))

    refresh_tree()
    app.on_document_changed(refresh_tree)
    app.add_panel(_t("Project"), tree)


def _outliner_kind(group):
    rec = frame_record(group)
    if rec is not None:
        return {"type": _t("Railing" if rec["ifc"] == "IfcRailing"
                           else "Frame"), "props": [
            ("Level", rec["level"]),
            ("Mullions", str(len(rec["divisions"]))),
            ("Height", f"{rec['height']:.2f} m")]}
    rec = element_record(group)
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
