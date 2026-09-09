"""The procedural fruit-fly body.

Port of FlyModel.swift buildFlyModel / buildLeg / wingShape / abdomenTexture.
FlyWire is a brain connectome and ships no body geometry, so the body is
modelled while the behaviour driving it is real.

Local frame, as upstream: +Y is forward, +Z is up, the ground is z = 0. The
dimensions, colours and the six-leg table are upstream's numbers; they describe
a specific animal rather than a tunable, so they stay here next to the shapes
they build instead of moving into constants.py.

The mesh builders replace SceneKit's SCNSphere / SCNCapsule / SCNCone / SCNShape
primitives; each is oriented the way SceneKit orients its own, so the transforms
around them are unchanged.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

import numpy as np
import numpy.typing as npt

from .constants import FLY_SCALE
from .legdynamics import ANKLE_ANGLE, REST_KNEE, LegFeedback, LegGeometry, ground_elevation
from .scenegraph import Material, Mesh, Node

Color = tuple[float, float, float, float]

BODY_BROWN: Color = (0.50, 0.38, 0.22, 1.0)
LEG_BROWN: Color = (0.33, 0.24, 0.14, 1.0)
EYE_RED: Color = (0.62, 0.10, 0.07, 1.0)
ANTENNA_BROWN: Color = (0.30, 0.22, 0.13, 1.0)
PROBOSCIS_BROWN: Color = (0.35, 0.26, 0.16, 1.0)


def blend(color: Color, fraction: float, other: Color) -> Color:
    """NSColor.blended(withFraction:of:) — used for the head and the tarsi."""
    return (
        color[0] + (other[0] - color[0]) * fraction,
        color[1] + (other[1] - color[1]) * fraction,
        color[2] + (other[2] - color[2]) * fraction,
        color[3],
    )


def material(color: Color, specular: float = 0.25, shininess: float = 0.25) -> Material:
    """Port of FlyModel.swift mat()."""
    return Material(diffuse=color, specular=specular, shininess=shininess)


# --- mesh builders ----------------------------------------------------------


def _finish(
    positions: list[tuple[float, float, float]],
    normals: list[tuple[float, float, float]],
    uvs: list[tuple[float, float]],
    indices: list[int],
    mat: Material,
) -> Mesh:
    return Mesh(
        positions=np.asarray(positions, dtype=np.float32),
        normals=np.asarray(normals, dtype=np.float32),
        uvs=np.asarray(uvs, dtype=np.float32),
        indices=np.asarray(indices, dtype=np.uint32),
        material=mat,
    )


def sphere_mesh(radius: float, mat: Material, rings: int = 16, segments: int = 24) -> Mesh:
    positions, normals, uvs, indices = [], [], [], []
    for ring in range(rings + 1):
        polar = math.pi * ring / rings
        for segment in range(segments + 1):
            azimuth = 2.0 * math.pi * segment / segments
            nx = math.sin(polar) * math.cos(azimuth)
            ny = math.cos(polar)
            nz = math.sin(polar) * math.sin(azimuth)
            normals.append((nx, ny, nz))
            positions.append((nx * radius, ny * radius, nz * radius))
            uvs.append((segment / segments, ring / rings))
    for ring in range(rings):
        for segment in range(segments):
            a = ring * (segments + 1) + segment
            b = a + segments + 1
            indices += [a, b, a + 1, a + 1, b, b + 1]
    return _finish(positions, normals, uvs, indices, mat)


def capsule_mesh(cap_radius: float, height: float, mat: Material, segments: int = 12) -> Mesh:
    """A capsule along Y, total length `height` including the two caps.

    Matches SCNCapsule, so the rotations upstream applies around it still hold.
    """
    body = max(0.0, height - 2.0 * cap_radius)
    rings = 8
    positions, normals, uvs, indices = [], [], [], []

    def ring_of(y: float, radius: float, ny: float, v: float) -> None:
        for segment in range(segments + 1):
            azimuth = 2.0 * math.pi * segment / segments
            cos_a, sin_a = math.cos(azimuth), math.sin(azimuth)
            horizontal = math.sqrt(max(0.0, 1.0 - ny * ny))
            normals.append((cos_a * horizontal, ny, sin_a * horizontal))
            positions.append((cos_a * radius, y, sin_a * radius))
            uvs.append((segment / segments, v))

    for ring in range(rings + 1):  # bottom cap
        angle = math.pi / 2.0 * ring / rings
        ring_of(
            -body / 2.0 - cap_radius * math.cos(angle),
            cap_radius * math.sin(angle),
            -math.cos(angle),
            0.25 * ring / rings,
        )
    ring_of(body / 2.0, cap_radius, 0.0, 0.75)
    for ring in range(rings + 1):  # top cap
        angle = math.pi / 2.0 * ring / rings
        ring_of(
            body / 2.0 + cap_radius * math.sin(angle),
            cap_radius * math.cos(angle),
            math.sin(angle),
            0.75 + 0.25 * ring / rings,
        )

    total_rings = len(positions) // (segments + 1)
    for ring in range(total_rings - 1):
        for segment in range(segments):
            a = ring * (segments + 1) + segment
            b = a + segments + 1
            indices += [a, b, a + 1, a + 1, b, b + 1]
    return _finish(positions, normals, uvs, indices, mat)


def cone_mesh(
    top_radius: float, bottom_radius: float, height: float, mat: Material, segments: int = 16
) -> Mesh:
    positions, normals, uvs, indices = [], [], [], []
    slope = math.atan2(bottom_radius - top_radius, height)
    for level, (y, radius) in enumerate(((height / 2, top_radius), (-height / 2, bottom_radius))):
        for segment in range(segments + 1):
            azimuth = 2.0 * math.pi * segment / segments
            cos_a, sin_a = math.cos(azimuth), math.sin(azimuth)
            positions.append((cos_a * radius, y, sin_a * radius))
            normals.append((cos_a * math.cos(slope), math.sin(slope), sin_a * math.cos(slope)))
            uvs.append((segment / segments, float(level)))
    for segment in range(segments):
        a = segment
        b = segments + 1 + segment
        indices += [a, b, a + 1, a + 1, b, b + 1]
    return _finish(positions, normals, uvs, indices, mat)


def box_mesh(width: float, height: float, length: float, mat: Material) -> Mesh:
    """A box centred on its origin, matching SCNBox's axes: X wide, Y high, Z long.

    # ponytail: no chamfer. SCNBox rounds its edges by a radius the beetle sets
    # to 0.8; at the overlay's scale that is under a pixel, and adding a chamfer
    # means a rounded-box generator. Add one if a body ever needs a visible bevel.
    """
    hx, hy, hz = width / 2.0, height / 2.0, length / 2.0
    faces = (
        ((0.0, 0.0, 1.0), ((-hx, -hy, hz), (hx, -hy, hz), (hx, hy, hz), (-hx, hy, hz))),
        ((0.0, 0.0, -1.0), ((hx, -hy, -hz), (-hx, -hy, -hz), (-hx, hy, -hz), (hx, hy, -hz))),
        ((1.0, 0.0, 0.0), ((hx, -hy, hz), (hx, -hy, -hz), (hx, hy, -hz), (hx, hy, hz))),
        ((-1.0, 0.0, 0.0), ((-hx, -hy, -hz), (-hx, -hy, hz), (-hx, hy, hz), (-hx, hy, -hz))),
        ((0.0, 1.0, 0.0), ((-hx, hy, hz), (hx, hy, hz), (hx, hy, -hz), (-hx, hy, -hz))),
        ((0.0, -1.0, 0.0), ((-hx, -hy, -hz), (hx, -hy, -hz), (hx, -hy, hz), (-hx, -hy, hz))),
    )
    positions: list[tuple[float, float, float]] = []
    normals: list[tuple[float, float, float]] = []
    uvs: list[tuple[float, float]] = []
    indices: list[int] = []
    for normal, corners in faces:
        base = len(positions)
        corner_uvs = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
        for corner, uv in zip(corners, corner_uvs, strict=True):
            positions.append(corner)
            normals.append(normal)
            uvs.append(uv)
        indices += [base, base + 1, base + 2, base, base + 2, base + 3]
    return _finish(positions, normals, uvs, indices, mat)


def bezier_outline(
    start: tuple[float, float],
    segments: tuple[tuple[tuple[float, float], tuple[float, float], tuple[float, float]], ...],
    steps: int = 12,
) -> list[tuple[float, float]]:
    """Flatten a chain of cubic beziers into a polygon.

    Stands in for NSBezierPath's own flattening. Upstream asks for a flatness of
    0.1 units; at these sizes a fixed subdivision is well inside that.
    """
    points = [start]
    for control_a, control_b, end in segments:
        origin = points[-1]
        for step in range(1, steps + 1):
            t = step / steps
            u = 1.0 - t
            points.append(
                (
                    u**3 * origin[0]
                    + 3 * u * u * t * control_a[0]
                    + 3 * u * t * t * control_b[0]
                    + t**3 * end[0],
                    u**3 * origin[1]
                    + 3 * u * u * t * control_a[1]
                    + 3 * u * t * t * control_b[1]
                    + t**3 * end[1],
                )
            )
    return points


def extruded_mesh(outline: list[tuple[float, float]], depth: float, mat: Material) -> Mesh:
    """A closed outline in XY, extruded along Z and centred on z = 0.

    Replaces SCNShape. The elytra swing right open, so a zero-thickness sheet
    would show its edge; the side wall is what makes it read as a shell.

    # ponytail: fanned from the centroid, which is correct for any star-shaped
    # outline and both of the ones this fork draws. A concave body part would
    # need a real triangulator.
    """
    # A mirrored outline is wound the other way round, which would light one
    # elytron off its back face and leave the pair visibly different shades.
    # Orient every outline counter-clockwise so the front face is always front.
    area = sum(
        outline[i][0] * outline[(i + 1) % len(outline)][1]
        - outline[(i + 1) % len(outline)][0] * outline[i][1]
        for i in range(len(outline))
    )
    if area < 0:
        outline = outline[::-1]

    front, back = depth / 2.0, -depth / 2.0
    cx = sum(x for x, _ in outline) / len(outline)
    cy = sum(y for _, y in outline) / len(outline)
    positions: list[tuple[float, float, float]] = []
    normals: list[tuple[float, float, float]] = []
    uvs: list[tuple[float, float]] = []
    indices: list[int] = []

    for z, normal, flip in ((front, (0.0, 0.0, 1.0), False), (back, (0.0, 0.0, -1.0), True)):
        centre = len(positions)
        positions.append((cx, cy, z))
        normals.append(normal)
        uvs.append((0.5, 0.5))
        for x, y in outline:
            positions.append((x, y, z))
            normals.append(normal)
            uvs.append((0.5, 0.5))
        for step in range(len(outline)):
            a = centre + 1 + step
            b = centre + 1 + (step + 1) % len(outline)
            indices += [centre, b, a] if flip else [centre, a, b]

    for step in range(len(outline)):
        x0, y0 = outline[step]
        x1, y1 = outline[(step + 1) % len(outline)]
        edge = math.hypot(x1 - x0, y1 - y0)
        if edge < 1e-9:
            continue
        normal = ((y1 - y0) / edge, -(x1 - x0) / edge, 0.0)
        base = len(positions)
        for corner in ((x0, y0, back), (x1, y1, back), (x1, y1, front), (x0, y0, front)):
            positions.append(corner)
            normals.append(normal)
            uvs.append((0.5, 0.5))
        indices += [base, base + 1, base + 2, base, base + 2, base + 3]
    return _finish(positions, normals, uvs, indices, mat)


def oval_mesh(
    rect: tuple[float, float, float, float],
    mat: Material,
    rotation: float = 0.0,
    segments: int = 28,
) -> Mesh:
    """A flat oval filling `rect` (x, y, width, height), optionally pre-rotated.

    Only one face is generated: the wing materials are double-sided, so culling
    is off and a second face would double their alpha and turn them milky.
    """
    x, y, width, height = rect
    cx, cy = x + width / 2.0, y + height / 2.0
    rx, ry = width / 2.0, height / 2.0
    cos_r, sin_r = math.cos(rotation), math.sin(rotation)

    def place(px: float, py: float) -> tuple[float, float, float]:
        return (px * cos_r - py * sin_r, px * sin_r + py * cos_r, 0.0)

    positions: list[tuple[float, float, float]] = [place(cx, cy)]
    normals: list[tuple[float, float, float]] = [(0.0, 0.0, 1.0)]
    uvs: list[tuple[float, float]] = [(0.5, 0.5)]
    indices: list[int] = []
    for segment in range(segments + 1):
        angle = 2.0 * math.pi * segment / segments
        positions.append(place(cx + rx * math.cos(angle), cy + ry * math.sin(angle)))
        normals.append((0.0, 0.0, 1.0))
        uvs.append((0.5 + 0.5 * math.cos(angle), 0.5 + 0.5 * math.sin(angle)))
    for segment in range(segments):
        indices += [0, 1 + segment, 2 + segment]
    return _finish(positions, normals, uvs, indices, mat)


def wing_mesh(mat: Material, segments: int = 28) -> Mesh:
    """Port of FlyModel.swift wingShape(): a flat oval disc.

    Upstream builds it from NSBezierPath(ovalIn:) over the rect
    (x -2.6, y -16.5, w 5.2, h 16.5) with a 0.12 extrusion, so the wing reaches
    backwards from its hinge and lies over the abdomen when folded. The hinge
    sits at the very end of the membrane on purpose: with the oval straddling
    it, raising a wing rotated a forward-projecting root through the thorax. Only one
    face is generated: the material is double-sided, so culling is off and a
    second face would double the 0.28 alpha and turn the wing milky.
    """
    return oval_mesh((-2.6, -16.5, 5.2, 16.5), mat, segments=segments)


def abdomen_texture() -> npt.NDArray[np.uint8]:
    """Port of FlyModel.swift abdomenTexture(): tan with dark bands."""
    width, height = 64, 128
    base = (0.72, 0.55, 0.32)
    dark = (0.22, 0.15, 0.09)
    image = np.empty((height, width, 4), dtype=np.uint8)
    image[..., 0:3] = np.asarray([int(c * 255) for c in base], dtype=np.uint8)
    image[..., 3] = 255
    for y0, band in ((0, 26), (38, 10), (60, 10), (82, 9)):
        image[y0 : y0 + band, :, 0:3] = np.asarray([int(c * 255) for c in dark], dtype=np.uint8)
    return image


# --- the fly ----------------------------------------------------------------


class Leg:
    """One of the six legs. Port of FlyModel.swift Leg."""

    def __init__(
        self,
        root: Node,
        knee: Node,
        ankle: Node,
        geometry: LegGeometry,
        base_yaw: float,
        swing_sign: float,
        phase: float,
        is_front: bool,
    ):
        self.root = root
        self.knee = knee
        self.ankle = ankle
        self.geometry = geometry
        self.base_yaw = base_yaw
        self.swing_sign = swing_sign
        self.phase = phase
        self.is_front = is_front
        self.angle = 0.0
        self.lift = 0.0
        self.knee_angle = 0.75

    def apply(self) -> None:
        """Write the three joint angles into the node chain.

        Every controller - the scripted poses and the mechanics alike - uses
        these same local axes, so changing behaviour cannot change the
        skeleton's rotation convention or quietly reset a joint. The order is
        Rz(yaw) then Ry(-lift), which is what makes the rendered toe land where
        legdynamics computes the physical one; the reverse order was the
        discontinuity every state change used to show.
        """
        yaw = self.base_yaw + self.swing_sign * self.angle
        self.root.rotation = _yaw_then_lift(yaw, self.lift)
        self.knee.euler = [0.0, self.knee_angle, 0.0]
        self.ankle.euler = [0.0, ANKLE_ANGLE, 0.0]

    def apply_feedback(self, feedback: LegFeedback) -> None:
        self.angle = feedback.hip_angle
        self.lift = feedback.elevation_angle
        self.knee_angle = feedback.knee_angle
        self.apply()

    def pose(self) -> LegFeedback:
        """The displayed pose, as the mechanics would describe it."""
        return LegFeedback(
            hip_angle=self.angle, elevation_angle=self.lift, knee_angle=self.knee_angle
        )


def _yaw_then_lift(yaw: float, lift: float) -> npt.NDArray[np.float32]:
    cos_y, sin_y = math.cos(yaw), math.sin(yaw)
    cos_l, sin_l = math.cos(-lift), math.sin(-lift)
    return np.array(
        [
            [cos_y * cos_l, -sin_y, cos_y * sin_l],
            [sin_y * cos_l, cos_y, sin_y * sin_l],
            [-sin_l, 0.0, cos_l],
        ],
        dtype=np.float32,
    )


@dataclass
class FlyModel:
    root: Node
    legs: list[Leg]
    folded_wings: Node
    blur_wing_left: Node
    blur_wing_right: Node
    abdomen: Node
    # Wing cases, beetle forms only. Display-only nodes: _update_elytra swings
    # them open, nothing ever reads them back.
    elytra_left: Node | None = None
    elytra_right: Node | None = None
    # How far the wings swing out in flight. Body-specific: a beetle keeps a
    # narrower stroke than a fly, whose wings must clear a raised hinge.
    wing_flight_spread: float = 0.625


def build_leg(
    attach: tuple[float, float, float],
    base_yaw: float,
    swing_sign: float,
    phase: float,
    is_front: bool,
    femur: float,
    tibia: float,
    tarsus: float,
    color: Color = LEG_BROWN,
    thickness: float = 1.0,
) -> Leg:
    """Port of FlyModel.swift buildLeg(): femur, tibia and tarsus down a chain."""
    leg_material = material(color)
    root = Node(name="leg", position=list(attach))

    femur_node = Node(mesh=capsule_mesh(0.48 * thickness, femur, leg_material))
    femur_node.euler = [0.0, 0.0, -math.pi / 2]
    femur_node.position = [femur / 2, 0.0, 0.0]
    root.add(femur_node)

    knee = Node(name="knee", position=[femur, 0.0, 0.0], euler=[0.0, 0.75, -0.30 * swing_sign])
    root.add(knee)

    tibia_node = Node(mesh=capsule_mesh(0.38 * thickness, tibia, leg_material))
    tibia_node.euler = [0.0, 0.0, -math.pi / 2]
    tibia_node.position = [tibia / 2, 0.0, 0.0]
    knee.add(tibia_node)

    ankle = Node(name="ankle", position=[tibia, 0.0, 0.0], euler=[0.0, 0.35, -0.15 * swing_sign])
    knee.add(ankle)

    tarsus_node = Node(
        mesh=capsule_mesh(0.24 * thickness, tarsus, material(blend(color, 0.25, (0, 0, 0, 1))))
    )
    tarsus_node.euler = [0.0, 0.0, -math.pi / 2]
    tarsus_node.position = [tarsus / 2, 0.0, 0.0]
    ankle.add(tarsus_node)

    geometry = LegGeometry(
        attach_x=attach[0],
        attach_y=attach[1],
        attach_z=attach[2],
        base_yaw=base_yaw,
        side=swing_sign,
        femur=femur,
        tibia=tibia,
        tarsus=tarsus,
    )
    leg = Leg(root, knee, ankle, geometry, base_yaw, swing_sign, phase, is_front)
    leg.knee_angle = REST_KNEE
    leg.lift = ground_elevation(geometry, REST_KNEE)
    leg.apply()
    return leg


# side, attach point, yaw offset, gait phase, is front, femur, tibia, tarsus.
# The front pair carries the grooming motion; the hind pair is the longest.
LEG_SPECS: tuple[
    tuple[float, tuple[float, float, float], float, float, bool, float, float, float], ...
] = (
    (1.0, (3.1, 5.3, 4.5), 0.95, 0.0, True, 4.2, 4.8, 3.2),
    (-1.0, (-3.1, 5.3, 4.5), 0.95, 0.5, True, 4.2, 4.8, 3.2),
    (1.0, (3.7, 2.0, 4.5), -0.10, 0.5, False, 4.8, 5.6, 3.8),
    (-1.0, (-3.7, 2.0, 4.5), -0.10, 0.0, False, 4.8, 5.6, 3.8),
    (1.0, (3.3, -1.2, 4.5), -0.95, 0.0, False, 5.8, 7.0, 4.6),
    (-1.0, (-3.3, -1.2, 4.5), -0.95, 0.5, False, 5.8, 7.0, 4.6),
)


class BodyForm(Enum):
    """Which body to build. Purely cosmetic.

    Every form satisfies the same FlyModel contract - six legs, folded wings
    with exactly two children, the two blur discs and an abdomen - so nothing in
    the behaviour layer ever branches on which one is on screen.
    """

    FLY = "fruit fly"
    BEETLE = "stag beetle"


def build_body(form: BodyForm) -> FlyModel:
    if form is BodyForm.BEETLE:
        # Imported here rather than at the top: beetle.py builds its body from
        # this module's meshes, so importing it eagerly would be a cycle.
        from .beetle import build_beetle_model

        return build_beetle_model()
    return build_fly_model()


def build_fly_model() -> FlyModel:
    """Port of FlyModel.swift buildFlyModel()."""
    root = Node(name="fly", scale=[FLY_SCALE, FLY_SCALE, FLY_SCALE])

    thorax = Node(
        name="thorax",
        mesh=sphere_mesh(4.6, material(BODY_BROWN, specular=0.35, shininess=0.4)),
        position=[0.0, 2.5, 6.2],
        scale=[0.95, 1.15, 0.85],
    )
    root.add(thorax)

    abdomen_material = Material(
        diffuse=(1.0, 1.0, 1.0, 1.0), specular=0.3, shininess=0.35, texture=abdomen_texture()
    )
    abdomen = Node(
        name="abdomen",
        mesh=sphere_mesh(5.0, abdomen_material),
        position=[0.0, -6.5, 5.6],
        scale=[0.9, 1.5, 0.75],
    )
    root.add(abdomen)

    head = Node(
        name="head",
        mesh=sphere_mesh(3.0, material(blend(BODY_BROWN, 0.15, (1, 1, 1, 1)))),
        position=[0.0, 9.0, 6.0],
        scale=[1.0, 0.85, 0.9],
    )
    root.add(head)

    eye_mesh = sphere_mesh(2.0, material(EYE_RED, specular=0.9, shininess=0.9))
    for side in (-1.0, 1.0):
        root.add(
            Node(
                name="eye",
                mesh=eye_mesh,
                position=[side * 2.1, 9.7, 6.4],
                scale=[0.8, 1.0, 1.15],
            )
        )

    antenna_mesh = capsule_mesh(0.16, 2.2, material(ANTENNA_BROWN))
    for side in (-1.0, 1.0):
        root.add(
            Node(
                name="antenna",
                mesh=antenna_mesh,
                position=[side * 0.9, 11.6, 6.3],
                euler=[-1.15, 0.0, side * 0.35],
            )
        )

    root.add(
        Node(
            name="proboscis",
            mesh=cone_mesh(0.6, 0.22, 2.4, material(PROBOSCIS_BROWN)),
            position=[0.0, 10.4, 4.6],
            euler=[-0.5, 0.0, 0.0],
        )
    )

    legs: list[Leg] = []
    for side, attach, yaw_offset, phase, is_front, femur, tibia, tarsus in LEG_SPECS:
        base_yaw = yaw_offset if side > 0 else math.pi - yaw_offset
        leg = build_leg(attach, base_yaw, side, phase, is_front, femur, tibia, tarsus)
        root.add(leg.root)
        legs.append(leg)

    folded_wings = Node(name="wings")
    wing_material = Material(
        diffuse=(0.92, 0.92, 0.92, 0.28), specular=0.9, shininess=0.9, double_sided=True
    )
    shared_wing = wing_mesh(wing_material)
    for side in (-1.0, 1.0):
        folded_wings.add(
            Node(
                name="wing",
                mesh=shared_wing,
                # Above the thorax and the breathing abdomen, so a wing at any
                # point in its stroke has something to sweep over.
                position=[side * 1.6, 0.5, 10.4 if side > 0 else 10.25],
                euler=[0.0, 0.0, side * 0.13],
            )
        )
    root.add(folded_wings)

    def blur_wing(side: float) -> Node:
        # A flattened, unlit disc that only shows while the wings are beating:
        # the motion smear a real wing leaves at 200 Hz.
        node = Node(
            name="blur",
            mesh=sphere_mesh(
                1.0,
                Material(diffuse=(0.85, 0.85, 0.85, 0.30), lighting="constant", double_sided=True),
            ),
            position=[side * 8.4, -2.8, 10.65],
            scale=[5.5, 2.4, 0.3],
            euler=[0.0, 0.0, side * -0.45],
        )
        node.hidden = True
        return node

    blur_left, blur_right = blur_wing(-1.0), blur_wing(1.0)
    root.add(blur_left)
    root.add(blur_right)

    return FlyModel(
        root=root,
        legs=legs,
        folded_wings=folded_wings,
        blur_wing_left=blur_left,
        blur_wing_right=blur_right,
        abdomen=abdomen,
        wing_flight_spread=1.1,
    )
