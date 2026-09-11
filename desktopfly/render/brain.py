"""The live brain map: 23k real somas, the circuit on top, spikes flashing.

Port of BrainView.swift. Everything drawn here is at a real neuron's real
position; a flash means that neuron actually crossed threshold in the
simulation this frame.

Upstream renders the spike flashes as a pool of 48 SceneKit spheres, each with
its own fade-out action. Here they are points in one additive cloud with a
per-frame brightness decay, which looks the same and is a fraction of the code.
"""

from __future__ import annotations

import ctypes
import math

import numpy as np
import numpy.typing as npt
from OpenGL import GL

from desktopfly.dataset import BrainPoints
from desktopfly.render.gl import compile_program, look_at, perspective
from desktopfly.sim import LIFSim


def clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))

CAMERA_HEIGHT = 0.6
CAMERA_DISTANCE = 29.0
CAMERA_FOV = 46.0
CAMERA_NEAR = 1.0
CAMERA_FAR = 120.0

# Upstream rotates the brain by 0.35 rad every 6 s and tilts it slightly forward.
ROTATION_RATE = 0.35 / 6.0
TILT = -0.15

# Direct manipulation. Pitch stops short of the poles so anatomical up stays up,
# which is how every connectome viewer shows a brain; the dolly range keeps the
# whole cloud between the near and far planes above.
ORBIT_RATE = 0.01  # rad per pixel dragged
PITCH_LIMIT = 1.3  # rad
ZOOM_RATE = 0.35  # units per scroll step
ZOOM_RANGE = (9.0, 70.0)

BACKGROUND = (0.03, 0.035, 0.06, 1.0)

# FlyWire super-class palette, in the index order etl.py writes.
CLASS_COLOURS = np.array(
    [
        (0.16, 0.22, 0.34, 1.0),  # optic — the majority, kept deliberately dim
        (0.45, 0.33, 0.16, 1.0),  # central
        (0.14, 0.36, 0.34, 1.0),  # sensory
        (0.10, 0.48, 0.62, 1.0),  # visual_projection
        (0.38, 0.22, 0.55, 1.0),  # visual_centrifugal
        (0.62, 0.28, 0.10, 1.0),  # descending
        (0.20, 0.45, 0.18, 1.0),  # ascending
        (0.55, 0.14, 0.14, 1.0),  # motor
        (0.50, 0.25, 0.40, 1.0),  # endocrine
    ],
    dtype=np.float32,
)

ROLE_COLOURS: dict[str, tuple[float, float, float, float]] = {
    "lc4": (0.15, 0.85, 1.0, 1.0),
    "lplc2": (0.15, 0.85, 1.0, 1.0),
    "dna01": (1.0, 0.55, 0.10, 1.0),
    "dna02": (1.0, 0.55, 0.10, 1.0),
    "mdn": (1.0, 0.20, 0.80, 1.0),
    "dnp09": (0.25, 1.0, 0.35, 1.0),
    "dng11": (0.75, 0.55, 1.0, 1.0),
    "escw": (1.0, 0.35, 0.25, 1.0),
    "gf": (1.0, 0.95, 0.4, 1.0),
}
ROLE_COLOUR_DEFAULT = (0.45, 0.45, 0.50, 1.0)

# Click stimulation, upstream's numbers.
STIM_RADIUS = 2.2
STIM_MIN_NEURONS = 6
STIM_MAX_NEURONS = 60
STIM_STRENGTH = 0.25
STIM_DURATION_MS = 400

FLASH_CAPACITY = 48
FLASH_DECAY_PER_S = 3.6  # a normal spike fades in ~0.28 s, as upstream
FLASH_DECAY_GF_PER_S = 1.7  # the giant fiber lingers, because it matters

POINT_VERTEX_SHADER = """
#version 330 core
layout(location = 0) in vec3 a_position;
layout(location = 1) in vec4 a_colour;
uniform mat4 u_mvp;
uniform float u_point_scale;
uniform vec2 u_radius_limits;
out vec4 v_colour;
void main() {
    v_colour = a_colour;
    gl_Position = u_mvp * vec4(a_position, 1.0);
    // SceneKit sizes points in world units and then clamps the screen-space
    // radius; this is the same rule expressed directly.
    float radius = u_point_scale / max(gl_Position.w, 0.001);
    gl_PointSize = 2.0 * clamp(radius, u_radius_limits.x, u_radius_limits.y);
}
"""

POINT_FRAGMENT_SHADER = """
#version 330 core
in vec4 v_colour;
out vec4 frag_colour;
void main() {
    // Round, soft-edged points: a square spike looks like a rendering bug.
    vec2 offset = gl_PointCoord - vec2(0.5);
    float falloff = 1.0 - smoothstep(0.35, 0.5, length(offset));
    if (falloff <= 0.0) {
        discard;
    }
    frag_colour = vec4(v_colour.rgb * v_colour.a * falloff, v_colour.a * falloff);
}
"""


def _point_cloud(
    positions: npt.NDArray[np.float32], colours: npt.NDArray[np.float32]
) -> tuple[int, int]:
    vao = GL.glGenVertexArrays(1)
    GL.glBindVertexArray(vao)
    buffer = GL.glGenBuffers(1)
    interleaved = np.hstack((positions, colours)).astype(np.float32)
    GL.glBindBuffer(GL.GL_ARRAY_BUFFER, buffer)
    GL.glBufferData(GL.GL_ARRAY_BUFFER, interleaved.nbytes, interleaved, GL.GL_DYNAMIC_DRAW)
    GL.glEnableVertexAttribArray(0)
    GL.glVertexAttribPointer(0, 3, GL.GL_FLOAT, GL.GL_FALSE, 28, ctypes.c_void_p(0))
    GL.glEnableVertexAttribArray(1)
    GL.glVertexAttribPointer(1, 4, GL.GL_FLOAT, GL.GL_FALSE, 28, ctypes.c_void_p(12))
    GL.glBindVertexArray(0)
    return int(vao), int(buffer)


class BrainRenderer:
    """Draws the brain map and answers clicks. One instance per GL context."""

    def __init__(self, points: BrainPoints, sim: LIFSim):
        self.points = points
        self.sim = sim
        self.width = 1
        self.height = 1
        self.yaw = 0.0
        self.pitch = TILT
        self.distance = CAMERA_DISTANCE
        self.paused = False  # the pointer is over the window: hold still to aim

        self._program = 0
        self._uniform: dict[str, int] = {}
        self._soma_vao = self._circuit_vao = self._flash_vao = 0
        self._flash_buffer = 0

        # Flash state, parallel arrays so the whole pool updates in one step.
        self._flash_position = np.zeros((FLASH_CAPACITY, 3), dtype=np.float32)
        self._flash_colour = np.zeros((FLASH_CAPACITY, 4), dtype=np.float32)
        self._flash_decay = np.zeros(FLASH_CAPACITY, dtype=np.float32)
        self._flash_next = 0

    # -- setup --------------------------------------------------------------

    def initialise(self) -> None:
        self._program = compile_program(POINT_VERTEX_SHADER, POINT_FRAGMENT_SHADER)
        for name in ("u_mvp", "u_point_scale", "u_radius_limits"):
            self._uniform[name] = GL.glGetUniformLocation(self._program, name)

        colours = CLASS_COLOURS[np.clip(self.points.class_index, 0, len(CLASS_COLOURS) - 1)]
        self._soma_vao, _ = _point_cloud(self.points.positions, colours)
        self._soma_count = len(self.points.positions)

        circuit_colours = np.asarray(
            [ROLE_COLOURS.get(role, ROLE_COLOUR_DEFAULT) for role in self.sim.roles],
            dtype=np.float32,
        )
        self._circuit_vao, _ = _point_cloud(self.sim.positions, circuit_colours)
        self._circuit_count = self.sim.n

        self._flash_vao, self._flash_buffer = _point_cloud(self._flash_position, self._flash_colour)

    def resize(self, width: int, height: int) -> None:
        self.width = max(1, width)
        self.height = max(1, height)

    # -- per frame ----------------------------------------------------------

    def _model_matrix(self) -> npt.NDArray[np.float32]:
        cos_a, sin_a = math.cos(self.yaw), math.sin(self.yaw)
        cos_t, sin_t = math.cos(self.pitch), math.sin(self.pitch)
        yaw = np.array(
            [[cos_a, 0, sin_a, 0], [0, 1, 0, 0], [-sin_a, 0, cos_a, 0], [0, 0, 0, 1]],
            dtype=np.float32,
        )
        tilt = np.array(
            [[1, 0, 0, 0], [0, cos_t, -sin_t, 0], [0, sin_t, cos_t, 0], [0, 0, 0, 1]],
            dtype=np.float32,
        )
        return tilt @ yaw

    def _view_projection(self) -> npt.NDArray[np.float32]:
        projection = perspective(CAMERA_FOV, self.width / self.height, CAMERA_NEAR, CAMERA_FAR)
        view = look_at(
            (0.0, CAMERA_HEIGHT, self.distance), (0.0, 0.0, 0.0), up=(0.0, 1.0, 0.0)
        )
        return projection @ view

    def orbit(self, dx: float, dy: float) -> None:
        """Drag to look at the brain from somewhere else."""
        self.yaw += dx * ORBIT_RATE
        self.pitch = clamp(self.pitch + dy * ORBIT_RATE, -PITCH_LIMIT, PITCH_LIMIT)

    def zoom(self, delta: float) -> None:
        """Scroll to dolly the camera, bounded by the near and far planes."""
        self.distance = clamp(self.distance - delta * ZOOM_RATE, *ZOOM_RANGE)

    def step(self, dt: float) -> None:
        """Advance the rotation and drain the spike bus into the flash pool.

        The flash pool decays whether or not the rotation is held, so hovering
        to aim at a region never stops the activity being aimed at.
        """
        if not self.paused:
            self.yaw += ROTATION_RATE * dt
        if self.sim.spike_bus is not None:
            for event in self.sim.spike_bus.pop_all():
                self.flash(event.neuron, event.is_gf)
        self._flash_colour[:, 3] -= self._flash_decay * dt
        np.clip(self._flash_colour[:, 3], 0.0, None, out=self._flash_colour[:, 3])

    def flash(self, neuron: int, is_gf: bool) -> None:
        slot = self._flash_next
        self._flash_next = (self._flash_next + 1) % FLASH_CAPACITY
        self._flash_position[slot] = self.sim.positions[neuron]
        self._flash_colour[slot] = (1.0, 0.9, 0.3, 1.0) if is_gf else (0.75, 0.95, 1.0, 0.8)
        self._flash_decay[slot] = FLASH_DECAY_GF_PER_S if is_gf else FLASH_DECAY_PER_S

    def draw(self) -> None:
        GL.glViewport(0, 0, self.width, self.height)
        GL.glClearColor(*BACKGROUND)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
        GL.glDisable(GL.GL_DEPTH_TEST)
        GL.glEnable(GL.GL_BLEND)
        GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE)  # additive, as upstream
        GL.glEnable(GL.GL_PROGRAM_POINT_SIZE)

        mvp = (self._view_projection() @ self._model_matrix()).astype(np.float32)
        GL.glUseProgram(self._program)
        GL.glUniformMatrix4fv(self._uniform["u_mvp"], 1, GL.GL_TRUE, mvp)

        # The whole brain first, dim; then the simulated circuit brighter on top;
        # then the spikes, which is the only layer that moves.
        self._draw_cloud(self._soma_vao, self._soma_count, scale=14.0, limits=(0.7, 1.6))
        self._draw_cloud(self._circuit_vao, self._circuit_count, scale=34.0, limits=(1.6, 2.6))

        GL.glBindVertexArray(self._flash_vao)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._flash_buffer)
        interleaved = np.hstack((self._flash_position, self._flash_colour)).astype(np.float32)
        GL.glBufferSubData(GL.GL_ARRAY_BUFFER, 0, interleaved.nbytes, interleaved)
        GL.glUniform1f(self._uniform["u_point_scale"], 90.0)
        GL.glUniform2f(self._uniform["u_radius_limits"], 2.0, 9.0)
        GL.glDrawArrays(GL.GL_POINTS, 0, FLASH_CAPACITY)
        GL.glBindVertexArray(0)

    def _draw_cloud(self, vao: int, count: int, scale: float, limits: tuple[float, float]) -> None:
        GL.glUniform1f(self._uniform["u_point_scale"], scale)
        GL.glUniform2f(self._uniform["u_radius_limits"], *limits)
        GL.glBindVertexArray(vao)
        GL.glDrawArrays(GL.GL_POINTS, 0, count)

    # -- clicking -----------------------------------------------------------

    def pick(self, x: float, y: float) -> list[int]:
        """Neurons under a click, in the window's pixel coordinates (y down).

        Port of BrainWindowController.handleClick: unproject the click into a
        ray, find the nearest circuit neuron, then take its neighbourhood.
        """
        ndc_x = 2.0 * x / self.width - 1.0
        ndc_y = 1.0 - 2.0 * y / self.height
        inverse = np.linalg.inv(self._view_projection() @ self._model_matrix())

        def unproject(depth: float) -> npt.NDArray[np.float64]:
            point = inverse @ np.array([ndc_x, ndc_y, depth, 1.0])
            return np.asarray(point[:3] / point[3], np.float64)

        near = unproject(-1.0)
        far = unproject(1.0)
        direction = far - near
        direction /= np.linalg.norm(direction)

        offsets = self.sim.positions - near
        along = offsets @ direction
        perpendicular = np.linalg.norm(offsets - np.outer(along, direction), axis=1)
        anchor_index = int(np.argmin(perpendicular))
        anchor = self.sim.positions[anchor_index]

        distances = np.linalg.norm(self.sim.positions - anchor, axis=1)
        picked = np.flatnonzero(distances < STIM_RADIUS)
        if len(picked) < STIM_MIN_NEURONS or len(picked) > STIM_MAX_NEURONS:
            limit = STIM_MIN_NEURONS if len(picked) < STIM_MIN_NEURONS else STIM_MAX_NEURONS
            picked = np.argsort(distances)[:limit]
        return [int(i) for i in picked]

    def stimulate(self, picked: list[int]) -> str:
        """Fire the picked cluster and return the label to show for it."""
        if not picked:
            return ""
        self.sim.stimulate(np.asarray(picked, dtype=np.int64), STIM_STRENGTH, STIM_DURATION_MS)
        for neuron in picked[:16]:
            self.flash(neuron, is_gf=False)
        return self.region_name(picked)

    def region_name(self, picked: list[int]) -> str:
        """Port of BrainWindowController.regionName."""
        counts: dict[str, int] = {}
        for index in picked:
            counts[self.sim.roles[index]] = counts.get(self.sim.roles[index], 0) + 1
        major = max(counts, key=lambda role: counts[role])

        def side_suffix(role: str) -> str:
            left = sum(
                1 for i in picked if self.sim.roles[i] == role and self.sim.positions[i][0] < 0
            )
            right = sum(1 for i in picked if self.sim.roles[i] == role) - left
            if left == right:
                return ""
            return " · left" if left > right else " · right"

        if major in ("lc4", "lplc2"):
            return f"⚡ Looming detectors (LC4/LPLC2){side_suffix(major)}"
        if major == "gf":
            return "⚡ Giant Fiber (DNp01) — escape!"
        if major in ("dna01", "dna02"):
            return f"⚡ Steering neurons (DNa01/02){side_suffix(major)}"
        if major == "dnp09":
            return "⚡ Walking command (DNp09)"
        if major == "dng11":
            return "⚡ Grooming command (DNg11)"
        if major == "escw":
            return "⚡ Escape-wing DNs (DNp02/04/11)"
        if major == "mdn":
            return "⚡ Moonwalker neurons (MDN)"
        other = next((i for i in picked if self.sim.roles[i] == "other"), picked[0])
        label = self.sim.types[other]
        if not label or label == "?":
            label = "central"
        return f"⚡ {label} neurons"
