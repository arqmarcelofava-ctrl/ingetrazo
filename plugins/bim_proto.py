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
    if math.hypot(b[0] - a[0], b[1] - a[1]) <= _EPS:
        raise BimProtoError("the wall has no length")
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


def build_wall_group(scene, rec: dict, name: str | None = None) -> Group:
    """Um grupo novo (ainda fora de ``scene.groups``) com a parede de ``rec``."""
    rec = normalize_record(rec)
    base_z = level_z(scene, rec["level"]) + rec["base_offset"]
    g = Group(wall_mesh(rec, base_z), name=name or _next_name(scene))
    bim.tag_group(g, "IfcWall", g.name)
    g.ext = {KEY: rec}
    return g


def _next_name(scene) -> str:
    n = sum(1 for g in scene.groups if wall_record(g) is not None) + 1
    taken = {g.name for g in scene.groups}
    while f"{_t('Wall')} {n}" in taken:
        n += 1
    return f"{_t('Wall')} {n}"


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


def edit_walls(viewport, targets, **changes) -> int:
    """Muda parâmetros (``thickness``, ``height``, ``level``, ``base_offset``,
    ``a``, ``b``) de ``targets`` E regenera o 3D, num único passo de
    desfazer — desfazer devolve parâmetros e geometria juntos. Devolve
    quantas paredes mudaram."""
    allowed = {"thickness", "height", "level", "base_offset", "a", "b"}
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
    todo = [g for g in (targets if targets is not None else walls(scene))
            if wall_record(g) is not None]
    if not todo:
        _flash(viewport, _t("No BIM proto walls in the document."))
        return 0

    def mutate(sc):
        for g in todo:
            regenerate_wall(sc, g)

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
    for g in walls(scene):
        by_level.setdefault(wall_record(g).get("level"), []).append(g)
    levels = []
    for lv in sorted(data["levels"], key=lambda v: float(v.get("z", 0))):
        kids = [(f"{g.name} — {_len(wall_record(g)):.2f} m", [])
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
            if g in app.scene.groups and wall_record(g) is not None \
                    and g not in sel:
                sel.append(g)
        update_3d(app.viewport, sel or None)

    sub = app.add_menu(_t("BIM proto"))
    if sub is not None:
        sub.addAction(_t("Wall"), start_wall)
        sub.addAction(_t("Update 3D"), refresh)
    app.add_outliner_kind(_outliner_kind)

    tree = QTreeWidget()
    tree.setHeaderHidden(True)

    def refresh_tree():
        fill_tree(tree, project_tree_model(app.scene))

    refresh_tree()
    app.on_document_changed(refresh_tree)
    app.add_panel(_t("Project"), tree)


def _outliner_kind(group):
    rec = wall_record(group)
    if rec is None:
        return None
    return {"type": _t("Wall"), "props": [
        ("Level", rec["level"]),
        ("Thickness", f"{rec['thickness']:.2f} m"),
        ("Height", f"{rec['height']:.2f} m")]}
