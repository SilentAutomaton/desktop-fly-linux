"""Headless rendering for the --snapshot and --brainshot diagnostics.

Port of the SCNRenderer.snapshot path in main.swift. EGL is used with no
surface at all, so this works over SSH and in a session with no compositor:
the frame is drawn into a framebuffer object and read back.
"""

from __future__ import annotations

import ctypes
from collections.abc import Callable
from pathlib import Path

import numpy as np
import numpy.typing as npt
from OpenGL import EGL, GL

# Mesa's surfaceless platform: an EGL display backed by no window system at all.
EGL_PLATFORM_SURFACELESS_MESA = 0x31DD


class OffscreenContext:
    """A current OpenGL 3.3 core context with no window behind it."""

    def __init__(self, width: int, height: int):
        self.width = width
        self.height = height
        self._display = EGL.eglGetPlatformDisplay(
            EGL_PLATFORM_SURFACELESS_MESA, EGL.EGL_DEFAULT_DISPLAY, None
        )
        if self._display == EGL.EGL_NO_DISPLAY:
            raise RuntimeError("EGL: no surfaceless display available")
        major, minor = ctypes.c_long(), ctypes.c_long()
        if not EGL.eglInitialize(self._display, major, minor):
            raise RuntimeError("EGL: eglInitialize failed")

        config_attributes = np.array(
            [
                EGL.EGL_SURFACE_TYPE,
                EGL.EGL_PBUFFER_BIT,
                EGL.EGL_RENDERABLE_TYPE,
                EGL.EGL_OPENGL_BIT,
                EGL.EGL_RED_SIZE,
                8,
                EGL.EGL_GREEN_SIZE,
                8,
                EGL.EGL_BLUE_SIZE,
                8,
                EGL.EGL_ALPHA_SIZE,
                8,
                EGL.EGL_DEPTH_SIZE,
                24,
                EGL.EGL_NONE,
            ],
            dtype=np.int32,
        )
        config = (EGL.EGLConfig * 1)()
        found = ctypes.c_long()
        EGL.eglChooseConfig(self._display, config_attributes, config, 1, found)
        if found.value == 0:
            raise RuntimeError("EGL: no suitable config")

        EGL.eglBindAPI(EGL.EGL_OPENGL_API)
        context_attributes = np.array(
            [
                EGL.EGL_CONTEXT_MAJOR_VERSION,
                3,
                EGL.EGL_CONTEXT_MINOR_VERSION,
                3,
                EGL.EGL_CONTEXT_OPENGL_PROFILE_MASK,
                EGL.EGL_CONTEXT_OPENGL_CORE_PROFILE_BIT,
                EGL.EGL_NONE,
            ],
            dtype=np.int32,
        )
        self._context = EGL.eglCreateContext(
            self._display, config[0], EGL.EGL_NO_CONTEXT, context_attributes
        )
        if self._context == EGL.EGL_NO_CONTEXT:
            raise RuntimeError("EGL: could not create an OpenGL 3.3 core context")
        EGL.eglMakeCurrent(self._display, EGL.EGL_NO_SURFACE, EGL.EGL_NO_SURFACE, self._context)

        self._colour = GL.glGenRenderbuffers(1)
        GL.glBindRenderbuffer(GL.GL_RENDERBUFFER, self._colour)
        GL.glRenderbufferStorage(GL.GL_RENDERBUFFER, GL.GL_RGBA8, width, height)
        self._depth = GL.glGenRenderbuffers(1)
        GL.glBindRenderbuffer(GL.GL_RENDERBUFFER, self._depth)
        GL.glRenderbufferStorage(GL.GL_RENDERBUFFER, GL.GL_DEPTH_COMPONENT24, width, height)
        self._framebuffer = GL.glGenFramebuffers(1)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self._framebuffer)
        GL.glFramebufferRenderbuffer(
            GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0, GL.GL_RENDERBUFFER, self._colour
        )
        GL.glFramebufferRenderbuffer(
            GL.GL_FRAMEBUFFER, GL.GL_DEPTH_ATTACHMENT, GL.GL_RENDERBUFFER, self._depth
        )
        if GL.glCheckFramebufferStatus(GL.GL_FRAMEBUFFER) != GL.GL_FRAMEBUFFER_COMPLETE:
            raise RuntimeError("EGL: incomplete framebuffer")

    def read_pixels(self) -> npt.NDArray[np.uint8]:
        GL.glPixelStorei(GL.GL_PACK_ALIGNMENT, 1)
        raw = GL.glReadPixels(0, 0, self.width, self.height, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE)
        image = np.frombuffer(raw, dtype=np.uint8).reshape(self.height, self.width, 4)
        return np.flipud(image)  # OpenGL reads bottom-up

    def close(self) -> None:
        EGL.eglMakeCurrent(
            self._display, EGL.EGL_NO_SURFACE, EGL.EGL_NO_SURFACE, EGL.EGL_NO_CONTEXT
        )
        EGL.eglDestroyContext(self._display, self._context)
        EGL.eglTerminate(self._display)

    def __enter__(self) -> OffscreenContext:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def save_png(image: npt.NDArray[np.uint8], path: Path) -> None:
    from PIL import Image

    Image.fromarray(image, mode="RGBA").save(path)


def render_to_png(width: int, height: int, draw: Callable[[], None], path: Path) -> None:
    with OffscreenContext(width, height) as context:
        draw()
        save_png(context.read_pixels(), path)


def snapshot_fly(path: Path, size: int = 720) -> None:
    """Port of runSnapshot in main.swift: a close-up of the body, over its shoulder."""
    import numpy as np

    from desktopfly.behavior import Fly
    from desktopfly.render import gl

    with OffscreenContext(size, size) as context:
        renderer = gl.Renderer()
        renderer.initialise()
        renderer.resize(size, size)
        renderer.ambient = gl.SNAPSHOT_AMBIENT
        renderer.key = gl.SNAPSHOT_KEY_INTENSITY
        renderer.key_euler = gl.SNAPSHOT_KEY_EULER

        fly = Fly((0.0, 0.0))
        fly.heading = np.pi / 2
        # Upstream's pose: a mid-stride tripod, so the legs are not all identical.
        for index, leg in enumerate(fly.model.legs):
            leg.angle = [0.25, -0.2, -0.22, 0.28, 0.2, -0.25][index]
            leg.lift = [0.35, 0.0, 0.0, 0.3, 0.0, 0.35][index]
            leg.apply()
        fly.sync_node()

        view_projection = gl.perspective(gl.SNAPSHOT_FOV, 1.0, 1.0, 600.0) @ gl.look_at(
            gl.SNAPSHOT_CAMERA, (0.0, 0.0, 5.0)
        )
        renderer.draw([fly.node], [], view_projection=view_projection)
        save_png(context.read_pixels(), path)


def snapshot_brain(path: Path, width: int = 720, height: int = 560) -> None:
    """Port of runBrainshot in main.swift: the brain map with a burst of spikes."""
    import numpy as np

    from desktopfly.dataset import load_brain_data
    from desktopfly.render.brain import BrainRenderer
    from desktopfly.sim import LIFSim, SpikeBus

    data = load_brain_data()
    if data is None:
        raise SystemExit("no data/ — run etl.py first")
    sim = LIFSim(data.circuit, spike_bus=SpikeBus())
    with OffscreenContext(width, height) as context:
        renderer = BrainRenderer(data.points, sim)
        renderer.initialise()
        renderer.resize(width, height)
        renderer.angle = 0.5
        # Decorate with a burst so the still shows the live look, as upstream does.
        rng = np.random.default_rng(0)
        for neuron in rng.integers(0, sim.n, 40):
            renderer.flash(int(neuron), is_gf=False)
        renderer.flash(int(sim.groups.gf[0]), is_gf=True)
        renderer.draw()
        save_png(context.read_pixels(), path)
