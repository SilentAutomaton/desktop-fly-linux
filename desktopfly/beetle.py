"""The procedural stag-beetle body: an alternate skin for the same contract.

Port of BeetleModel.swift. The behaviour layer only ever reaches the body
through the FlyModel struct, so a second geometry drops in without touching the
state machine, the gait, the flight or the connectome mapping. Nothing here
makes a decision; it is shape.

Local frame matches geometry.py: +Y is forward, +Z is up, the ground is z = 0.
The overlay camera looks straight down, so this is read dorsally - the
mandibles, the pronotum shield and the elytra seam carry the whole silhouette.

The dimensions and colours are upstream's numbers and describe one specific
animal, so like the fruit fly's they stay here beside the shapes they build
rather than moving into constants.py.
"""

from __future__ import annotations

import math

from .constants import FLY_SCALE
from .geometry import (
    Color,
    FlyModel,
    Leg,
    bezier_outline,
    blend,
    box_mesh,
    build_leg,
    capsule_mesh,
    cone_mesh,
    extruded_mesh,
    material,
    oval_mesh,
    sphere_mesh,
)
from .scenegraph import Material, Mesh, Node

SHELL_BLACK: Color = (0.13, 0.09, 0.07, 1.0)
SHELL_RED: Color = (0.31, 0.13, 0.07, 1.0)
JAW_BLACK: Color = (0.09, 0.06, 0.05, 1.0)
EYE_BLACK: Color = (0.05, 0.05, 0.05, 1.0)
ABDOMEN_BROWN: Color = (0.18, 0.11, 0.08, 1.0)

# `_land()` used to refold every wing to a fixed yaw of side * 0.13. Cancelling
# that in the outline keeps a folded hindwing square under its elytron instead
# of swinging its tip out past the edge of the shell.
HINDWING_FOLD = 0.13


def _elytron_mesh(side: float) -> Mesh:
    """One wing case, hinged at its front-inner corner.

    The path is mirrored rather than the node, so both elytra rotate with
    sign-symmetric angles and the seam between them stays straight.
    """
    outline = bezier_outline(
        (0.0, 0.0),
        (
            ((side * 3.4, -0.2), (side * 5.3, -2.2), (side * 5.3, -4.6)),
            ((side * 5.3, -8.4), (side * 4.4, -11.2), (side * 2.6, -12.2)),
            ((side * 1.7, -13.3), (side * 0.7, -14.0), (0.0, -14.1)),
        ),
    )
    shell = material(SHELL_RED, specular=0.85, shininess=0.95)
    shell.double_sided = True
    return extruded_mesh(outline, 2.2, shell)


def _hindwing_mesh(side: float) -> Mesh:
    """The membranous surface that actually beats, hidden until the elytra open."""
    membrane = Material(
        diffuse=(0.58, 0.47, 0.36, 0.42), specular=0.85, shininess=0.85, double_sided=True
    )
    return oval_mesh((-1.9, -11.5, 3.8, 12.0), membrane, rotation=-side * HINDWING_FOLD)


def _pronotum_mesh() -> Mesh:
    """Narrow at the head, widest at mid-length, squared off where the elytra meet.

    This outline is what says "beetle" from directly above.
    """
    outline = bezier_outline(
        (-3.3, 4.2),
        (
            ((-4.6, 3.9), (-5.0, 2.2), (-5.0, 0.4)),
            ((-5.0, -1.4), (-4.8, -2.4), (-4.3, -3.0)),
            ((-4.3, -3.0), (4.3, -3.0), (4.3, -3.0)),  # the straight rear edge
            ((4.8, -2.4), (5.0, -1.4), (5.0, 0.4)),
            ((5.0, 2.2), (4.6, 3.9), (3.3, 4.2)),
        ),
    )
    shield = material(SHELL_BLACK, specular=0.85, shininess=0.95)
    shield.double_sided = True
    return extruded_mesh(outline, 3.0, shield)


def _build_mandible(side: float) -> Node:
    """A splayed outer segment, an inward-hooking tip, and an inner tooth.

    Cones point along +Y, so every joint here is a plain rotation about Z.
    """
    jaw = material(JAW_BLACK, specular=0.9, shininess=0.95)
    root = Node(name="mandible", position=[side * 2.1, 10.8, 5.0], euler=[0.0, 0.0, -side * 0.42])
    root.add(Node(mesh=cone_mesh(0.60, 1.05, 5.0, jaw), position=[0.0, 2.5, 0.0]))

    joint = Node(name="jaw-joint", position=[0.0, 5.0, 0.0], euler=[0.0, 0.0, side * 1.16])
    joint.add(Node(mesh=cone_mesh(0.08, 0.58, 4.2, jaw), position=[0.0, 2.1, 0.0]))
    root.add(joint)

    root.add(
        Node(
            mesh=cone_mesh(0.06, 0.42, 1.9, material(JAW_BLACK, specular=0.9, shininess=0.9)),
            position=[-side * 0.45, 3.4, 0.0],
            euler=[0.0, 0.0, side * 1.45],
        )
    )
    return root


# side, attach point, yaw offset, gait phase, is front, femur, tibia, tarsus.
# Shorter and thicker than the fly's, and set wider apart under the shell.
LEG_SPECS: tuple[
    tuple[float, tuple[float, float, float], float, float, bool, float, float, float], ...
] = (
    (1.0, (4.4, 6.0, 4.0), 0.95, 0.0, True, 4.0, 4.4, 2.6),
    (-1.0, (-4.4, 6.0, 4.0), 0.95, 0.5, True, 4.0, 4.4, 2.6),
    (1.0, (4.8, 2.2, 4.0), -0.10, 0.5, False, 4.6, 5.0, 3.0),
    (-1.0, (-4.8, 2.2, 4.0), -0.10, 0.0, False, 4.6, 5.0, 3.0),
    (1.0, (4.4, -1.6, 4.0), -0.95, 0.0, False, 5.4, 6.2, 3.6),
    (-1.0, (-4.4, -1.6, 4.0), -0.95, 0.5, False, 5.4, 6.2, 3.6),
)


def build_beetle_model() -> FlyModel:
    """Port of BeetleModel.swift buildBeetleModel()."""
    root = Node(name="beetle", scale=[FLY_SCALE, FLY_SCALE, FLY_SCALE])

    for side in (-1.0, 1.0):
        root.add(_build_mandible(side))

    root.add(
        Node(
            name="head",
            mesh=box_mesh(6.4, 4.2, 2.4, material(SHELL_BLACK, specular=0.75, shininess=0.85)),
            position=[0.0, 9.6, 5.4],
        )
    )

    eye_mesh = sphere_mesh(0.95, material(EYE_BLACK, specular=0.95, shininess=0.95))
    for side in (-1.0, 1.0):
        root.add(
            Node(
                name="eye",
                mesh=eye_mesh,
                position=[side * 3.1, 9.9, 6.0],
                scale=[0.75, 1.0, 0.6],
            )
        )

    # Geniculate antennae: an elbowed shaft ending in a small lamellate club.
    shaft_mesh = capsule_mesh(0.15, 2.6, material(JAW_BLACK))
    club_mesh = box_mesh(0.65, 1.1, 0.35, material(JAW_BLACK))
    for side in (-1.0, 1.0):
        root.add(
            Node(
                name="antenna",
                mesh=shaft_mesh,
                position=[side * 3.6, 9.6, 4.6],
                euler=[0.0, 0.0, -side * 1.05],
            )
        )
        root.add(
            Node(
                name="antenna-club",
                mesh=club_mesh,
                position=[side * 5.2, 10.2, 4.6],
                euler=[0.0, 0.0, -side * 1.25],
            )
        )

    root.add(Node(name="pronotum", mesh=_pronotum_mesh(), position=[0.0, 4.4, 5.6]))
    root.add(
        Node(
            name="scutellum",
            mesh=box_mesh(2.2, 1.8, 1.0, material(SHELL_BLACK, specular=0.8, shininess=0.9)),
            position=[0.0, 1.2, 6.2],
        )
    )

    # The abdomen is an empty pivot: the behaviour layer writes its own base
    # scale onto it to breathe, so the extra flattening that keeps a beetle
    # abdomen tucked inside its shell has to live on the child instead.
    abdomen = Node(name="abdomen", position=[0.0, -4.8, 3.7], scale=[0.9, 1.5, 0.75])
    abdomen.add(
        Node(
            mesh=sphere_mesh(4.6, material(ABDOMEN_BROWN, specular=0.4, shininess=0.5)),
            scale=[1.0, 1.0, 0.60],
        )
    )
    root.add(abdomen)

    leg_colour = blend(SHELL_BLACK, 0.18, SHELL_RED)
    legs: list[Leg] = []
    for side, attach, yaw_offset, phase, is_front, femur, tibia, tarsus in LEG_SPECS:
        base_yaw = yaw_offset if side > 0 else math.pi - yaw_offset
        leg = build_leg(
            attach,
            base_yaw,
            side,
            phase,
            is_front,
            femur,
            tibia,
            tarsus,
            color=leg_colour,
            thickness=1.35,
        )
        root.add(leg.root)
        legs.append(leg)

    folded_wings = Node(name="wings")
    for side in (-1.0, 1.0):
        folded_wings.add(
            Node(
                name="hindwing",
                mesh=_hindwing_mesh(side),
                position=[side * 1.15, 0.6, 5.0],
                euler=[0.0, 0.0, side * HINDWING_FOLD],
            )
        )
    root.add(folded_wings)

    elytra = []
    for side in (-1.0, 1.0):
        elytron = Node(name="elytron", mesh=_elytron_mesh(side), position=[0.0, 1.6, 5.6])
        root.add(elytron)
        elytra.append(elytron)

    def blur_wing(side: float) -> Node:
        node = Node(
            name="blur",
            mesh=sphere_mesh(
                1.0,
                Material(diffuse=(0.62, 0.52, 0.40, 0.30), lighting="constant", double_sided=True),
            ),
            position=[side * 6.8, -1.2, 5.2],
            scale=[6.2, 2.6, 0.3],
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
        elytra_left=elytra[0],
        elytra_right=elytra[1],
    )
