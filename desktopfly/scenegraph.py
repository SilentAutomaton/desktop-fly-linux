"""A minimal transform tree, standing in for SceneKit's SCNNode.

The port needs exactly what upstream used SceneKit for: a parent-child
hierarchy of translate/rotate/scale transforms with a mesh hanging off some of
the nodes. Writing those ~90 lines keeps geometry.py and behavior.py readable
next to the Swift they came from, and keeps both of them free of any renderer.

Euler angles follow SceneKit's convention: the components are pitch (X), yaw
(Y) and roll (Z), applied roll first, so the local matrix is Rx @ Ry @ Rz.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

Vec3 = tuple[float, float, float]
Mat4 = npt.NDArray[np.float32]


@dataclass
class Material:
    diffuse: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0)
    specular: float = 0.25
    shininess: float = 0.25
    emission: tuple[float, float, float] = (0.0, 0.0, 0.0)
    lighting: str = "blinn"  # "blinn" is lit, "constant" ignores the lights
    double_sided: bool = False
    additive: bool = False
    texture: npt.NDArray[np.uint8] | None = None  # (h, w, 4) RGBA, or None


@dataclass
class Mesh:
    positions: npt.NDArray[np.float32]  # (v, 3)
    normals: npt.NDArray[np.float32]  # (v, 3)
    uvs: npt.NDArray[np.float32]  # (v, 2)
    indices: npt.NDArray[np.uint32]  # (t * 3,)
    material: Material


@dataclass
class Node:
    name: str = ""
    position: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    euler: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    scale: list[float] = field(default_factory=lambda: [1.0, 1.0, 1.0])
    hidden: bool = False
    opacity: float = 1.0
    mesh: Mesh | None = None
    children: list[Node] = field(default_factory=list)

    def add(self, child: Node) -> Node:
        self.children.append(child)
        return child

    def local_matrix(self) -> Mat4:
        px, py, pz = self.euler
        cx, sx = np.cos(px), np.sin(px)
        cy, sy = np.cos(py), np.sin(py)
        cz, sz = np.cos(pz), np.sin(pz)
        # Rx @ Ry @ Rz, written out because it is called once per node per frame.
        rotation = np.array(
            [
                [cy * cz, -cy * sz, sy],
                [sx * sy * cz + cx * sz, -sx * sy * sz + cx * cz, -sx * cy],
                [-cx * sy * cz + sx * sz, cx * sy * sz + sx * cz, cx * cy],
            ],
            dtype=np.float32,
        )
        matrix = np.eye(4, dtype=np.float32)
        matrix[:3, :3] = rotation * np.asarray(self.scale, dtype=np.float32)
        matrix[:3, 3] = self.position
        return matrix

    def walk(self, parent: Mat4 | None = None) -> list[tuple[Mesh, Mat4, float]]:
        """Flatten the visible subtree into (mesh, world matrix, opacity) triples."""
        if self.hidden:
            return []
        world = self.local_matrix() if parent is None else parent @ self.local_matrix()
        drawn: list[tuple[Mesh, Mat4, float]] = []
        if self.mesh is not None:
            drawn.append((self.mesh, world, self.opacity))
        for child in self.children:
            drawn.extend(child.walk(world))
        return drawn
