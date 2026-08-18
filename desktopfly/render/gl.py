"""OpenGL renderer for the fly overlay.

Replaces SceneKit. Upstream's scene is simple enough to state exactly: an
orthographic camera at z = 300 whose vertical half-extent is the output height,
so one world unit is one pixel; one directional key light; one ambient light;
and Blinn shading on every surface. That is what this shader does.

Everything is drawn into a transparent framebuffer, because the overlay surface
is click-through and composited over the desktop.
"""

from __future__ import annotations

import ctypes
import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
from OpenGL import GL

from desktopfly.scenegraph import Mesh, Node

# Upstream's lights: a SCNLight directional at intensity 1000 with the node
# rotated (-0.35, 0.30, 0), and an ambient at 550. SceneKit does not apply those
# numbers as raw multipliers, so the pair is kept at upstream's 1000:550 ratio
# and scaled so a surface facing the key light reads at its own colour instead
# of clipping to white - which is what happened with a literal 1.0 and 0.55.
KEY_INTENSITY = 0.77
AMBIENT_INTENSITY = 0.42
KEY_EULER = (-0.35, 0.30, 0.0)

# The diagnostic close-up uses upstream's runSnapshot rig instead: a 42 degree
# perspective camera over the fly's shoulder, with a harder key light.
SNAPSHOT_CAMERA = (30.0, -58.0, 42.0)
SNAPSHOT_FOV = 42.0
SNAPSHOT_KEY_EULER = (-0.9, 0.5, 0.0)
SNAPSHOT_KEY_INTENSITY = 0.85
SNAPSHOT_AMBIENT = 0.38

CAMERA_Z = 300.0
CAMERA_NEAR = 1.0
CAMERA_FAR = 600.0

# The blob shadow that stands in for SceneKit's deferred shadow map.
# ponytail: a soft ellipse under the fly, not a real shadow map. It is drawn on
# the same transparent surface as the fly, so the visual result is a dark patch
# either way; swap in a depth-map pass only if the overlay ever gains geometry
# that must shadow itself.
SHADOW_ALPHA = 0.30
SHADOW_RADIUS = 13.0
SHADOW_ALTITUDE_SPREAD = 1.8
SHADOW_ALTITUDE_FADE = 0.75

VERTEX_SHADER = """
#version 330 core
layout(location = 0) in vec3 a_position;
layout(location = 1) in vec3 a_normal;
layout(location = 2) in vec2 a_uv;

uniform mat4 u_model;
uniform mat4 u_view_projection;
uniform mat3 u_normal_matrix;

out vec3 v_normal;
out vec2 v_uv;

void main() {
    v_normal = normalize(u_normal_matrix * a_normal);
    v_uv = a_uv;
    gl_Position = u_view_projection * u_model * vec4(a_position, 1.0);
}
"""

FRAGMENT_SHADER = """
#version 330 core
in vec3 v_normal;
in vec2 v_uv;
out vec4 frag_color;

uniform vec4 u_diffuse;
uniform vec3 u_emission;
uniform float u_specular;
uniform float u_shininess;
uniform float u_opacity;
uniform bool u_lit;
uniform bool u_textured;
uniform vec3 u_light_direction;   // points from the surface towards the light
uniform float u_ambient;
uniform float u_key;
uniform sampler2D u_texture;

void main() {
    vec4 base = u_diffuse;
    if (u_textured) {
        base *= texture(u_texture, v_uv);
    }
    vec3 color = base.rgb;
    if (u_lit) {
        // A double-sided surface seen from behind must still light up, so the
        // normal is flipped to face the camera on back faces.
        vec3 normal = normalize(v_normal);
        if (!gl_FrontFacing) {
            normal = -normal;
        }
        // The camera is orthographic and looks down -Z, so the view direction
        // is constant and the Blinn halfway vector is cheap to form.
        vec3 view = vec3(0.0, 0.0, 1.0);
        vec3 halfway = normalize(u_light_direction + view);
        float lambert = max(dot(normal, u_light_direction), 0.0);
        float highlight = pow(max(dot(normal, halfway), 0.0), 4.0 + u_shininess * 60.0);
        color = base.rgb * (u_ambient + u_key * lambert) + vec3(u_specular * highlight);
    }
    frag_color = vec4(color + u_emission, base.a * u_opacity);
}
"""

SHADOW_VERTEX_SHADER = """
#version 330 core
layout(location = 0) in vec2 a_corner;
uniform mat4 u_view_projection;
uniform vec2 u_center;
uniform float u_radius;
out vec2 v_corner;
void main() {
    v_corner = a_corner;
    gl_Position = u_view_projection * vec4(u_center + a_corner * u_radius, 0.0, 1.0);
}
"""

SHADOW_FRAGMENT_SHADER = """
#version 330 core
in vec2 v_corner;
out vec4 frag_color;
uniform float u_alpha;
void main() {
    // Soft radial falloff, so the blob has no visible rim.
    float d = clamp(1.0 - length(v_corner), 0.0, 1.0);
    frag_color = vec4(0.0, 0.0, 0.0, u_alpha * d * d);
}
"""


def compile_program(vertex_source: str, fragment_source: str) -> int:
    program = GL.glCreateProgram()
    for source, stage in (
        (vertex_source, GL.GL_VERTEX_SHADER),
        (fragment_source, GL.GL_FRAGMENT_SHADER),
    ):
        shader = GL.glCreateShader(stage)
        GL.glShaderSource(shader, source)
        GL.glCompileShader(shader)
        if not GL.glGetShaderiv(shader, GL.GL_COMPILE_STATUS):
            raise RuntimeError(GL.glGetShaderInfoLog(shader).decode())
        GL.glAttachShader(program, shader)
        GL.glDeleteShader(shader)
    GL.glLinkProgram(program)
    if not GL.glGetProgramiv(program, GL.GL_LINK_STATUS):
        raise RuntimeError(GL.glGetProgramInfoLog(program).decode())
    return int(program)


def orthographic(half_width: float, half_height: float) -> npt.NDArray[np.float32]:
    """Upstream's camera: orthographic, one world unit per pixel, looking down -Z."""
    matrix = np.eye(4, dtype=np.float32)
    matrix[0, 0] = 1.0 / half_width
    matrix[1, 1] = 1.0 / half_height
    matrix[2, 2] = -2.0 / (CAMERA_FAR - CAMERA_NEAR)
    matrix[2, 3] = -(CAMERA_FAR + CAMERA_NEAR) / (CAMERA_FAR - CAMERA_NEAR)
    # The camera sits at +Z looking back at the scene.
    view = np.eye(4, dtype=np.float32)
    view[2, 3] = -CAMERA_Z
    return matrix @ view


def perspective(
    fov_degrees: float, aspect: float, near: float, far: float
) -> npt.NDArray[np.float32]:
    f = 1.0 / math.tan(math.radians(fov_degrees) / 2.0)
    matrix = np.zeros((4, 4), dtype=np.float32)
    # SceneKit's fieldOfView is vertical, which is what f is scaled against here.
    matrix[0, 0] = f / aspect
    matrix[1, 1] = f
    matrix[2, 2] = (far + near) / (near - far)
    matrix[2, 3] = 2 * far * near / (near - far)
    matrix[3, 2] = -1.0
    return matrix


def look_at(
    eye: tuple[float, float, float],
    target: tuple[float, float, float],
    up: tuple[float, float, float] = (0.0, 0.0, 1.0),
) -> npt.NDArray[np.float32]:
    """The view matrix for SCNLookAtConstraint, with +Z up as in the fly's frame."""
    eye_v = np.asarray(eye, dtype=np.float64)
    forward = np.asarray(target, dtype=np.float64) - eye_v
    forward /= np.linalg.norm(forward)
    right = np.cross(forward, np.asarray(up, dtype=np.float64))
    right /= np.linalg.norm(right)
    true_up = np.cross(right, forward)
    matrix = np.eye(4, dtype=np.float32)
    matrix[0, :3] = right
    matrix[1, :3] = true_up
    matrix[2, :3] = -forward
    matrix[:3, 3] = -matrix[:3, :3] @ eye_v
    return matrix


def light_direction(euler: tuple[float, float, float] = KEY_EULER) -> tuple[float, float, float]:
    """A SceneKit directional light shines along its node's -Z axis."""
    pitch, yaw, roll = euler
    cx, sx = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    cz, sz = math.cos(roll), math.sin(roll)
    rotation = np.array(
        [
            [cy * cz, -cy * sz, sy],
            [sx * sy * cz + cx * sz, -sx * sy * sz + cx * cz, -sx * cy],
            [-cx * sy * cz + sx * sz, cx * sy * sz + sx * cz, cx * cy],
        ]
    )
    forward = rotation @ np.array([0.0, 0.0, -1.0])
    # The shader wants the direction from the surface towards the light.
    towards = -forward / np.linalg.norm(forward)
    return (float(towards[0]), float(towards[1]), float(towards[2]))


@dataclass
class _Buffers:
    vao: int
    vertex_buffer: int
    index_buffer: int
    count: int
    texture: int | None


class Renderer:
    """Draws scenegraph nodes. One instance per GL context."""

    def __init__(self) -> None:
        self._program = 0
        self._shadow_program = 0
        self._shadow_vao = 0
        self._meshes: dict[int, _Buffers] = {}
        self._uniform: dict[str, int] = {}
        self._shadow_uniform: dict[str, int] = {}
        self.width = 1
        self.height = 1
        # Swapped by the diagnostic renders; the overlay leaves them alone.
        self.ambient = AMBIENT_INTENSITY
        self.key = KEY_INTENSITY
        self.key_euler = KEY_EULER

    # -- setup --------------------------------------------------------------

    def initialise(self) -> None:
        """Compile everything. Must run with the GL context current."""
        self._program = compile_program(VERTEX_SHADER, FRAGMENT_SHADER)
        for name in (
            "u_model",
            "u_view_projection",
            "u_normal_matrix",
            "u_diffuse",
            "u_emission",
            "u_specular",
            "u_shininess",
            "u_opacity",
            "u_lit",
            "u_textured",
            "u_light_direction",
            "u_ambient",
            "u_key",
            "u_texture",
        ):
            self._uniform[name] = GL.glGetUniformLocation(self._program, name)

        self._shadow_program = compile_program(SHADOW_VERTEX_SHADER, SHADOW_FRAGMENT_SHADER)
        for name in ("u_view_projection", "u_center", "u_radius", "u_alpha"):
            self._shadow_uniform[name] = GL.glGetUniformLocation(self._shadow_program, name)
        self._shadow_vao = self._make_quad()

    def _make_quad(self) -> int:
        corners = np.array([-1, -1, 1, -1, 1, 1, -1, -1, 1, 1, -1, 1], dtype=np.float32)
        vao = GL.glGenVertexArrays(1)
        GL.glBindVertexArray(vao)
        buffer = GL.glGenBuffers(1)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, buffer)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, corners.nbytes, corners, GL.GL_STATIC_DRAW)
        GL.glEnableVertexAttribArray(0)
        GL.glVertexAttribPointer(0, 2, GL.GL_FLOAT, GL.GL_FALSE, 8, ctypes.c_void_p(0))
        GL.glBindVertexArray(0)
        return int(vao)

    def resize(self, width: int, height: int) -> None:
        self.width = max(1, width)
        self.height = max(1, height)

    def _upload(self, mesh: Mesh) -> _Buffers:
        cached = self._meshes.get(id(mesh))
        if cached is not None:
            return cached

        interleaved = np.hstack((mesh.positions, mesh.normals, mesh.uvs)).astype(np.float32)
        vao = GL.glGenVertexArrays(1)
        GL.glBindVertexArray(vao)
        vertex_buffer = GL.glGenBuffers(1)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, vertex_buffer)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, interleaved.nbytes, interleaved, GL.GL_STATIC_DRAW)
        stride = 8 * 4
        for location, size, offset in ((0, 3, 0), (1, 3, 12), (2, 2, 24)):
            GL.glEnableVertexAttribArray(location)
            GL.glVertexAttribPointer(
                location, size, GL.GL_FLOAT, GL.GL_FALSE, stride, ctypes.c_void_p(offset)
            )
        index_buffer = GL.glGenBuffers(1)
        GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, index_buffer)
        GL.glBufferData(
            GL.GL_ELEMENT_ARRAY_BUFFER, mesh.indices.nbytes, mesh.indices, GL.GL_STATIC_DRAW
        )
        GL.glBindVertexArray(0)

        texture = None
        if mesh.material.texture is not None:
            image = mesh.material.texture
            texture = int(GL.glGenTextures(1))
            GL.glBindTexture(GL.GL_TEXTURE_2D, texture)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_REPEAT)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
            GL.glTexImage2D(
                GL.GL_TEXTURE_2D,
                0,
                GL.GL_RGBA,
                image.shape[1],
                image.shape[0],
                0,
                GL.GL_RGBA,
                GL.GL_UNSIGNED_BYTE,
                image,
            )

        buffers = _Buffers(
            int(vao), int(vertex_buffer), int(index_buffer), len(mesh.indices), texture
        )
        self._meshes[id(mesh)] = buffers
        return buffers

    # -- drawing ------------------------------------------------------------

    def draw(
        self,
        roots: list[Node],
        shadow_anchors: list[tuple[float, float, float]],
        view_projection: npt.NDArray[np.float32] | None = None,
    ) -> None:
        """Render one frame. `shadow_anchors` is (x, y, altitude) per fly."""
        GL.glViewport(0, 0, self.width, self.height)
        GL.glClearColor(0.0, 0.0, 0.0, 0.0)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
        GL.glEnable(GL.GL_BLEND)
        GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
        GL.glEnable(GL.GL_DEPTH_TEST)

        if view_projection is None:
            view_projection = orthographic(self.width / 2.0, self.height / 2.0)
        self._draw_shadows(view_projection, shadow_anchors)

        GL.glUseProgram(self._program)
        GL.glUniformMatrix4fv(self._uniform["u_view_projection"], 1, GL.GL_TRUE, view_projection)
        GL.glUniform3f(self._uniform["u_light_direction"], *light_direction(self.key_euler))
        GL.glUniform1f(self._uniform["u_ambient"], self.ambient)
        GL.glUniform1f(self._uniform["u_key"], self.key)
        GL.glUniform1i(self._uniform["u_texture"], 0)

        drawn = [item for root in roots for item in root.walk()]
        # Opaque surfaces first with depth writes, then the translucent wings
        # with depth writes off, so they blend instead of erasing each other.
        opaque = [item for item in drawn if self._is_opaque(item[0], item[2])]
        translucent = [item for item in drawn if not self._is_opaque(item[0], item[2])]
        for mesh, matrix, opacity in opaque:
            self._draw_mesh(mesh, matrix, opacity)
        GL.glDepthMask(GL.GL_FALSE)
        for mesh, matrix, opacity in translucent:
            self._draw_mesh(mesh, matrix, opacity)
        GL.glDepthMask(GL.GL_TRUE)
        GL.glBindVertexArray(0)

    @staticmethod
    def _is_opaque(mesh: Mesh, opacity: float) -> bool:
        return mesh.material.diffuse[3] >= 0.999 and opacity >= 0.999

    def _draw_mesh(self, mesh: Mesh, matrix: npt.NDArray[np.float32], opacity: float) -> None:
        material = mesh.material
        buffers = self._upload(mesh)
        # Non-uniform scale is everywhere in this model (the abdomen alone is
        # 0.9 x 1.5 x 0.75), so normals need the inverse transpose.
        normal_matrix = np.linalg.inv(matrix[:3, :3]).T.astype(np.float32)

        GL.glUniformMatrix4fv(self._uniform["u_model"], 1, GL.GL_TRUE, matrix)
        GL.glUniformMatrix3fv(self._uniform["u_normal_matrix"], 1, GL.GL_TRUE, normal_matrix)
        GL.glUniform4f(self._uniform["u_diffuse"], *material.diffuse)
        GL.glUniform3f(self._uniform["u_emission"], *material.emission)
        GL.glUniform1f(self._uniform["u_specular"], material.specular)
        GL.glUniform1f(self._uniform["u_shininess"], material.shininess)
        GL.glUniform1f(self._uniform["u_opacity"], opacity)
        GL.glUniform1i(self._uniform["u_lit"], material.lighting != "constant")
        GL.glUniform1i(self._uniform["u_textured"], buffers.texture is not None)
        if buffers.texture is not None:
            GL.glActiveTexture(GL.GL_TEXTURE0)
            GL.glBindTexture(GL.GL_TEXTURE_2D, buffers.texture)
        if material.double_sided:
            GL.glDisable(GL.GL_CULL_FACE)
        else:
            GL.glEnable(GL.GL_CULL_FACE)
        if material.additive:
            GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE)

        GL.glBindVertexArray(buffers.vao)
        GL.glDrawElements(GL.GL_TRIANGLES, buffers.count, GL.GL_UNSIGNED_INT, None)

        if material.additive:
            GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)

    def _draw_shadows(
        self, view_projection: npt.NDArray[np.float32], anchors: list[tuple[float, float, float]]
    ) -> None:
        if not anchors:
            return
        GL.glUseProgram(self._shadow_program)
        GL.glUniformMatrix4fv(
            self._shadow_uniform["u_view_projection"], 1, GL.GL_TRUE, view_projection
        )
        GL.glBindVertexArray(self._shadow_vao)
        GL.glDepthMask(GL.GL_FALSE)
        for x, y, altitude in anchors:
            # A higher fly casts a wider, fainter shadow, which is the only cue
            # left that it is off the ground once the body itself is scaled up.
            GL.glUniform2f(self._shadow_uniform["u_center"], x, y)
            GL.glUniform1f(
                self._shadow_uniform["u_radius"],
                SHADOW_RADIUS * (1.0 + SHADOW_ALTITUDE_SPREAD * altitude),
            )
            GL.glUniform1f(
                self._shadow_uniform["u_alpha"],
                SHADOW_ALPHA * max(0.0, 1.0 - SHADOW_ALTITUDE_FADE * altitude),
            )
            GL.glDrawArrays(GL.GL_TRIANGLES, 0, 6)
        GL.glDepthMask(GL.GL_TRUE)
        GL.glBindVertexArray(0)
