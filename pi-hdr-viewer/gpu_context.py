"""GPU context: EGL + GBM + GLES 3.1 ctypes bindings for Pi 5 VideoCore VII.

Provides a surfaceless GLES 3.1 compute context via GBM (platform) + EGL
(context management). Used by gpu_pipeline.py for GPU-accelerated HDR
processing.

If GPU libraries are unavailable, GPU_AVAILABLE is False and GPUContext
raises RuntimeError on construction.
"""

import ctypes
import ctypes.util
import os

GPU_AVAILABLE = False

# -- Library loading ----------------------------------------------------------

try:
    _gbm = ctypes.CDLL(ctypes.util.find_library("gbm") or "libgbm.so.1",
                        use_errno=True)
    _egl = ctypes.CDLL(ctypes.util.find_library("EGL") or "libEGL.so.1",
                        use_errno=True)
    _gles = ctypes.CDLL(ctypes.util.find_library("GLESv2") or "libGLESv2.so.2",
                         use_errno=True)
    GPU_AVAILABLE = True
except OSError as e:
    print(f"GPU: libraries not available ({e})")

if not GPU_AVAILABLE:
    class GPUContext:
        """Stub when GPU libraries are missing."""
        def __init__(self, drm_fd):
            raise RuntimeError("GPU libraries not available")
else:
    # -- Helper ---------------------------------------------------------------

    _c_int = ctypes.c_int32
    _c_uint = ctypes.c_uint32
    _c_void_p = ctypes.c_void_p
    _c_char_p = ctypes.c_char_p
    _c_float = ctypes.c_float
    _c_ubyte = ctypes.c_uint8
    _c_sizei = ctypes.c_ssize_t  # GLsizeiptr / GLintptr on 64-bit

    def _bind(lib, name, argtypes, restype):
        fn = getattr(lib, name)
        fn.argtypes = argtypes
        fn.restype = restype
        return fn

    # -- GBM bindings ---------------------------------------------------------

    gbm_create_device = _bind(_gbm, "gbm_create_device",
                              [ctypes.c_int], _c_void_p)
    gbm_device_destroy = _bind(_gbm, "gbm_device_destroy",
                               [_c_void_p], None)

    # -- EGL constants --------------------------------------------------------

    EGL_NO_DISPLAY = ctypes.c_void_p(0)
    EGL_NO_CONTEXT = ctypes.c_void_p(0)
    EGL_NO_SURFACE = ctypes.c_void_p(0)
    EGL_NONE = 0x3038

    EGL_OPENGL_ES_API = 0x30A0
    EGL_SURFACE_TYPE = 0x3033
    EGL_RENDERABLE_TYPE = 0x3040
    EGL_OPENGL_ES3_BIT = 0x0040
    EGL_CONTEXT_MAJOR_VERSION = 0x3098
    EGL_CONTEXT_MINOR_VERSION = 0x30FB
    EGL_RED_SIZE = 0x3024
    EGL_GREEN_SIZE = 0x3025
    EGL_BLUE_SIZE = 0x3026
    EGL_ALPHA_SIZE = 0x3027

    # -- EGL function bindings ------------------------------------------------

    eglGetDisplay = _bind(_egl, "eglGetDisplay",
                          [_c_void_p], _c_void_p)
    eglInitialize = _bind(_egl, "eglInitialize",
                          [_c_void_p, ctypes.POINTER(_c_int), ctypes.POINTER(_c_int)],
                          _c_uint)
    eglTerminate = _bind(_egl, "eglTerminate",
                         [_c_void_p], _c_uint)
    eglBindAPI = _bind(_egl, "eglBindAPI",
                       [_c_uint], _c_uint)
    eglChooseConfig = _bind(_egl, "eglChooseConfig",
                            [_c_void_p, ctypes.POINTER(_c_int), ctypes.POINTER(_c_void_p),
                             _c_int, ctypes.POINTER(_c_int)], _c_uint)
    eglCreateContext = _bind(_egl, "eglCreateContext",
                             [_c_void_p, _c_void_p, _c_void_p,
                              ctypes.POINTER(_c_int)], _c_void_p)
    eglMakeCurrent = _bind(_egl, "eglMakeCurrent",
                           [_c_void_p, _c_void_p, _c_void_p, _c_void_p], _c_uint)
    eglDestroyContext = _bind(_egl, "eglDestroyContext",
                              [_c_void_p, _c_void_p], _c_uint)
    eglGetError = _bind(_egl, "eglGetError", [], _c_int)

    # -- GLES constants -------------------------------------------------------

    GL_TRUE = 1
    GL_FALSE = 0

    # Shader types
    GL_COMPUTE_SHADER = 0x91B9
    GL_VERTEX_SHADER = 0x8B31
    GL_FRAGMENT_SHADER = 0x8B30

    # Shader queries
    GL_COMPILE_STATUS = 0x8B81
    GL_LINK_STATUS = 0x8B82
    GL_INFO_LOG_LENGTH = 0x8B84

    # Texture
    GL_TEXTURE_2D = 0x0DE1
    GL_TEXTURE_MIN_FILTER = 0x2801
    GL_TEXTURE_MAG_FILTER = 0x2800
    GL_TEXTURE_WRAP_S = 0x2802
    GL_TEXTURE_WRAP_T = 0x2803
    GL_LINEAR = 0x2601
    GL_NEAREST = 0x2600
    GL_CLAMP_TO_EDGE = 0x812F
    GL_TEXTURE0 = 0x84C0
    GL_TEXTURE1 = 0x84C1
    GL_RGB = 0x1907
    GL_RED = 0x1903
    GL_UNSIGNED_BYTE = 0x1401
    GL_RGB8 = 0x8051
    GL_R8 = 0x8229
    GL_UNPACK_ALIGNMENT = 0x0CF5

    # Buffer
    GL_SHADER_STORAGE_BUFFER = 0x90D2
    GL_DYNAMIC_DRAW = 0x88E8

    # Map
    GL_MAP_READ_BIT = 0x0001
    GL_MAP_WRITE_BIT = 0x0002

    # Barrier
    GL_BUFFER_UPDATE_BARRIER_BIT = 0x00000200

    # Info
    GL_VERSION = 0x1F02
    GL_RENDERER = 0x1F01
    GL_MAX_COMPUTE_WORK_GROUP_SIZE = 0x91BF
    GL_MAX_COMPUTE_WORK_GROUP_COUNT = 0x91BE

    # -- GLES function bindings -----------------------------------------------

    # Info
    glGetString = _bind(_gles, "glGetString", [_c_uint], _c_char_p)
    glGetIntegerv = _bind(_gles, "glGetIntegerv",
                          [_c_uint, ctypes.POINTER(_c_int)], None)
    glGetError = _bind(_gles, "glGetError", [], _c_uint)
    glPixelStorei = _bind(_gles, "glPixelStorei", [_c_uint, _c_int], None)

    # Shader
    glCreateShader = _bind(_gles, "glCreateShader", [_c_uint], _c_uint)
    glDeleteShader = _bind(_gles, "glDeleteShader", [_c_uint], None)
    glShaderSource = _bind(_gles, "glShaderSource",
                           [_c_uint, ctypes.c_int, ctypes.POINTER(_c_char_p),
                            ctypes.POINTER(_c_int)], None)
    glCompileShader = _bind(_gles, "glCompileShader", [_c_uint], None)
    glGetShaderiv = _bind(_gles, "glGetShaderiv",
                          [_c_uint, _c_uint, ctypes.POINTER(_c_int)], None)
    glGetShaderInfoLog = _bind(_gles, "glGetShaderInfoLog",
                               [_c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_int),
                                _c_char_p], None)

    # Program
    glCreateProgram = _bind(_gles, "glCreateProgram", [], _c_uint)
    glDeleteProgram = _bind(_gles, "glDeleteProgram", [_c_uint], None)
    glAttachShader = _bind(_gles, "glAttachShader", [_c_uint, _c_uint], None)
    glLinkProgram = _bind(_gles, "glLinkProgram", [_c_uint], None)
    glGetProgramiv = _bind(_gles, "glGetProgramiv",
                           [_c_uint, _c_uint, ctypes.POINTER(_c_int)], None)
    glGetProgramInfoLog = _bind(_gles, "glGetProgramInfoLog",
                                [_c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_int),
                                 _c_char_p], None)
    glUseProgram = _bind(_gles, "glUseProgram", [_c_uint], None)

    # Uniforms
    glGetUniformLocation = _bind(_gles, "glGetUniformLocation",
                                 [_c_uint, _c_char_p], _c_int)
    glUniform1i = _bind(_gles, "glUniform1i", [_c_int, _c_int], None)
    glUniform1f = _bind(_gles, "glUniform1f", [_c_int, _c_float], None)
    glUniformMatrix3fv = _bind(_gles, "glUniformMatrix3fv",
                               [_c_int, ctypes.c_int, _c_ubyte,
                                ctypes.POINTER(_c_float)], None)

    # Textures
    glGenTextures = _bind(_gles, "glGenTextures",
                          [ctypes.c_int, ctypes.POINTER(_c_uint)], None)
    glDeleteTextures = _bind(_gles, "glDeleteTextures",
                             [ctypes.c_int, ctypes.POINTER(_c_uint)], None)
    glBindTexture = _bind(_gles, "glBindTexture", [_c_uint, _c_uint], None)
    glActiveTexture = _bind(_gles, "glActiveTexture", [_c_uint], None)
    glTexImage2D = _bind(_gles, "glTexImage2D",
                         [_c_uint, _c_int, _c_int, ctypes.c_int, ctypes.c_int,
                          _c_int, _c_uint, _c_uint, _c_void_p], None)
    glTexParameteri = _bind(_gles, "glTexParameteri",
                            [_c_uint, _c_uint, _c_int], None)

    # Buffers
    glGenBuffers = _bind(_gles, "glGenBuffers",
                         [ctypes.c_int, ctypes.POINTER(_c_uint)], None)
    glDeleteBuffers = _bind(_gles, "glDeleteBuffers",
                            [ctypes.c_int, ctypes.POINTER(_c_uint)], None)
    glBindBuffer = _bind(_gles, "glBindBuffer", [_c_uint, _c_uint], None)
    glBufferData = _bind(_gles, "glBufferData",
                         [_c_uint, _c_sizei, _c_void_p, _c_uint], None)
    glBindBufferBase = _bind(_gles, "glBindBufferBase",
                             [_c_uint, _c_uint, _c_uint], None)
    glMapBufferRange = _bind(_gles, "glMapBufferRange",
                             [_c_uint, _c_sizei, _c_sizei, _c_uint], _c_void_p)
    glUnmapBuffer = _bind(_gles, "glUnmapBuffer", [_c_uint], _c_ubyte)

    # Compute
    glDispatchCompute = _bind(_gles, "glDispatchCompute",
                              [_c_uint, _c_uint, _c_uint], None)
    glMemoryBarrier = _bind(_gles, "glMemoryBarrier", [_c_uint], None)

    # Indexed integer query (for compute work group limits)
    glGetIntegeri_v = _bind(_gles, "glGetIntegeri_v",
                            [_c_uint, _c_uint, ctypes.POINTER(_c_int)], None)

    # -- Shader compilation helpers -------------------------------------------

    def compile_shader(source, shader_type):
        """Compile a GLSL shader. Raises RuntimeError on failure."""
        shader = glCreateShader(shader_type)
        src = source.encode("utf-8")
        src_ptr = _c_char_p(src)
        length = _c_int(len(src))
        glShaderSource(shader, 1, ctypes.byref(src_ptr), ctypes.byref(length))
        glCompileShader(shader)

        status = _c_int()
        glGetShaderiv(shader, GL_COMPILE_STATUS, ctypes.byref(status))
        if not status.value:
            log_len = _c_int()
            glGetShaderiv(shader, GL_INFO_LOG_LENGTH, ctypes.byref(log_len))
            log_buf = ctypes.create_string_buffer(max(log_len.value, 1))
            glGetShaderInfoLog(shader, log_len.value, None, log_buf)
            glDeleteShader(shader)
            raise RuntimeError(f"Shader compilation failed:\n{log_buf.value.decode()}")
        return shader

    def link_program(*shaders):
        """Link shaders into a program. Raises RuntimeError on failure."""
        program = glCreateProgram()
        for s in shaders:
            glAttachShader(program, s)
        glLinkProgram(program)

        status = _c_int()
        glGetProgramiv(program, GL_LINK_STATUS, ctypes.byref(status))
        if not status.value:
            log_len = _c_int()
            glGetProgramiv(program, GL_INFO_LOG_LENGTH, ctypes.byref(log_len))
            log_buf = ctypes.create_string_buffer(max(log_len.value, 1))
            glGetProgramInfoLog(program, log_len.value, None, log_buf)
            glDeleteProgram(program)
            raise RuntimeError(f"Program link failed:\n{log_buf.value.decode()}")

        for s in shaders:
            glDeleteShader(s)
        return program

    # -- GPUContext ------------------------------------------------------------

    class GPUContext:
        """Surfaceless GLES 3.1 context via GBM + EGL.

        Usage:
            ctx = GPUContext(drm_fd)
            # ... use GLES calls ...
            ctx.close()
        """

        def __init__(self, drm_fd):
            self.drm_fd = drm_fd
            self._gbm_device = None
            self._egl_display = None
            self._egl_context = None

            # Create GBM device from DRM fd
            self._gbm_device = gbm_create_device(drm_fd)
            if not self._gbm_device:
                raise RuntimeError("Failed to create GBM device")

            # Get EGL display from GBM device
            self._egl_display = eglGetDisplay(self._gbm_device)
            if not self._egl_display or self._egl_display == EGL_NO_DISPLAY.value:
                raise RuntimeError(f"Failed to get EGL display (error 0x{eglGetError():04x})")

            # Initialize EGL
            major, minor = _c_int(), _c_int()
            if not eglInitialize(self._egl_display, ctypes.byref(major), ctypes.byref(minor)):
                raise RuntimeError(f"Failed to initialize EGL (error 0x{eglGetError():04x})")

            print(f"GPU: EGL {major.value}.{minor.value}")

            # Bind OpenGL ES API
            if not eglBindAPI(EGL_OPENGL_ES_API):
                raise RuntimeError("Failed to bind OpenGL ES API")

            # Choose EGL config (surfaceless, GLES 3.1 capable)
            config_attribs = (_c_int * 7)(
                EGL_SURFACE_TYPE, 0,  # surfaceless
                EGL_RENDERABLE_TYPE, EGL_OPENGL_ES3_BIT,
                EGL_NONE, 0, 0
            )
            config = _c_void_p()
            num_configs = _c_int()
            if not eglChooseConfig(self._egl_display, config_attribs,
                                   ctypes.byref(config), 1, ctypes.byref(num_configs)):
                raise RuntimeError(f"Failed to choose EGL config (error 0x{eglGetError():04x})")
            if num_configs.value == 0:
                raise RuntimeError("No suitable EGL config found")

            # Create GLES 3.1 context
            ctx_attribs = (_c_int * 5)(
                EGL_CONTEXT_MAJOR_VERSION, 3,
                EGL_CONTEXT_MINOR_VERSION, 1,
                EGL_NONE,
            )
            self._egl_context = eglCreateContext(
                self._egl_display, config, EGL_NO_CONTEXT, ctx_attribs)
            if not self._egl_context or self._egl_context == EGL_NO_CONTEXT.value:
                raise RuntimeError(f"Failed to create GLES 3.1 context (error 0x{eglGetError():04x})")

            # Make current (surfaceless)
            if not eglMakeCurrent(self._egl_display,
                                  EGL_NO_SURFACE, EGL_NO_SURFACE,
                                  self._egl_context):
                raise RuntimeError(f"Failed to make EGL context current (error 0x{eglGetError():04x})")

            # Print GPU info
            renderer = glGetString(GL_RENDERER)
            version = glGetString(GL_VERSION)
            print(f"GPU: {renderer.decode() if renderer else 'unknown'}")
            print(f"GPU: {version.decode() if version else 'unknown'}")

            # Set pixel unpack alignment to 1 (for RGB8 textures with arbitrary widths)
            glPixelStorei(GL_UNPACK_ALIGNMENT, 1)

        def close(self):
            """Release EGL and GBM resources."""
            if self._egl_display:
                eglMakeCurrent(self._egl_display,
                               EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT)
                if self._egl_context:
                    eglDestroyContext(self._egl_display, self._egl_context)
                    self._egl_context = None
                eglTerminate(self._egl_display)
                self._egl_display = None
            if self._gbm_device:
                gbm_device_destroy(self._gbm_device)
                self._gbm_device = None
            print("GPU: context closed")


    # -- Self-test ------------------------------------------------------------

    if __name__ == "__main__":
        # Quick test: open DRM, create GPU context, print info
        for i in range(4):
            path = f"/dev/dri/card{i}"
            try:
                fd = os.open(path, os.O_RDWR | os.O_CLOEXEC)
                try:
                    ctx = GPUContext(fd)
                    # Query compute limits (indexed queries)
                    val = _c_int()
                    for axis in range(3):
                        glGetIntegeri_v(GL_MAX_COMPUTE_WORK_GROUP_SIZE, axis,
                                        ctypes.byref(val))
                        print(f"  max workgroup size[{axis}] = {val.value}")
                        glGetIntegeri_v(GL_MAX_COMPUTE_WORK_GROUP_COUNT, axis,
                                        ctypes.byref(val))
                        print(f"  max workgroup count[{axis}] = {val.value}")
                    ctx.close()
                    break
                finally:
                    os.close(fd)
            except (OSError, RuntimeError) as e:
                continue
        else:
            print("No GPU context could be created")
