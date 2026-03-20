"""GPU-accelerated HDR pipeline using GLES 3.1 compute shaders.

Replaces the numpy pipeline in hdr_pipeline.py with a single compute shader
that runs on the Pi 5's VideoCore VII GPU. The shader performs:
  sRGB EOTF → gain map apply → sRGB-to-BT.2020 → PQ OETF → 10-bit pack

Output is written to an SSBO and copied to the DRM framebuffer mmap.
"""

import ctypes
import numpy as np

from gpu_context import (
    GPU_AVAILABLE, GPUContext,
    # GLES functions
    compile_shader, link_program,
    glUseProgram, glDeleteProgram,
    glGetUniformLocation, glUniform1i, glUniform1f, glUniformMatrix3fv,
    glGenTextures, glDeleteTextures, glBindTexture, glActiveTexture,
    glTexImage2D, glTexParameteri,
    glGenBuffers, glDeleteBuffers, glBindBuffer, glBufferData,
    glBindBufferBase, glMapBufferRange, glUnmapBuffer,
    glDispatchCompute, glMemoryBarrier,
    # Constants
    GL_COMPUTE_SHADER, GL_TEXTURE_2D,
    GL_TEXTURE_MIN_FILTER, GL_TEXTURE_MAG_FILTER,
    GL_TEXTURE_WRAP_S, GL_TEXTURE_WRAP_T,
    GL_LINEAR, GL_CLAMP_TO_EDGE,
    GL_TEXTURE0, GL_TEXTURE1,
    GL_RGB, GL_RED, GL_UNSIGNED_BYTE, GL_RGB8, GL_R8,
    GL_SHADER_STORAGE_BUFFER, GL_DYNAMIC_DRAW,
    GL_MAP_READ_BIT, GL_MAP_WRITE_BIT,
    GL_BUFFER_UPDATE_BARRIER_BIT, GL_TRUE,
)

# sRGB → BT.2020 color matrix (same as hdr_pipeline.SRGB_TO_BT2020)
SRGB_TO_BT2020 = np.array([
    [0.6274, 0.3293, 0.0433],
    [0.0691, 0.9195, 0.0114],
    [0.0164, 0.0880, 0.8956],
], dtype=np.float32)

# ── Compute shader source ───────────────────────────────────────────────

COMPUTE_SHADER_SOURCE = """\
#version 310 es
layout(local_size_x = 16, local_size_y = 16) in;

precision highp float;
precision highp int;

// Input textures
layout(binding = 0) uniform highp sampler2D u_sdr_tex;
layout(binding = 1) uniform highp sampler2D u_gainmap_tex;

// Gain map parameters
uniform int u_has_gainmap;
uniform float u_gm_gamma;
uniform float u_gm_min;
uniform float u_gm_max;
uniform float u_offset_sdr;
uniform float u_offset_hdr;

// Output mode: 0 = HDR PQ, 1 = SDR native, 2 = SDR tone-mapped
uniform int u_output_mode;

// Display and image dimensions
uniform int u_display_width;
uniform int u_display_height;
uniform int u_image_x;
uniform int u_image_y;
uniform int u_image_width;
uniform int u_image_height;

// sRGB → BT.2020 color matrix (column-major in GL)
uniform highp mat3 u_color_matrix;

// Output SSBO: packed XRGB2101010 pixels
layout(std430, binding = 0) buffer OutputBuffer {
    uint pixels[];
};

// sRGB electrical-to-optical transfer function (gamma decode)
highp vec3 srgb_eotf(highp vec3 x) {
    return mix(
        x / 12.92,
        pow((x + 0.055) / 1.055, vec3(2.4)),
        step(vec3(0.04045), x)
    );
}

// sRGB optical-to-electrical transfer function (gamma encode)
highp vec3 srgb_oetf(highp vec3 x) {
    return mix(
        x * 12.92,
        1.055 * pow(x, vec3(1.0 / 2.4)) - 0.055,
        step(vec3(0.0031308), x)
    );
}

// PQ (ST 2084) optical-to-electrical transfer function
// Input: linear light where 1.0 = SDR white (203 nits)
// Output: PQ-encoded [0, 1]
highp vec3 pq_oetf(highp vec3 L) {
    highp vec3 Y = clamp(L * (203.0 / 10000.0), 0.0, 1.0);
    highp vec3 Ym1 = pow(Y, vec3(0.1593017578125));
    highp vec3 num = vec3(0.8359375) + 18.8515625 * Ym1;
    highp vec3 den = vec3(1.0) + 18.6875 * Ym1;
    return pow(num / den, vec3(78.84375));
}

void main() {
    ivec2 pos = ivec2(gl_GlobalInvocationID.xy);

    if (pos.x >= u_display_width || pos.y >= u_display_height) return;

    uint idx = uint(pos.y) * uint(u_display_width) + uint(pos.x);

    // Letterbox: black outside image area
    ivec2 img_pos = pos - ivec2(u_image_x, u_image_y);
    if (img_pos.x < 0 || img_pos.y < 0 ||
        img_pos.x >= u_image_width || img_pos.y >= u_image_height) {
        pixels[idx] = 0u;
        return;
    }

    // Texture coordinate (pixel center)
    vec2 uv = (vec2(img_pos) + 0.5) / vec2(float(u_image_width), float(u_image_height));

    // Sample SDR image
    highp vec3 sdr = texture(u_sdr_tex, uv).rgb;

    // SDR native: raw sRGB values packed to 10-bit, no transforms
    if (u_output_mode == 1) {
        uvec3 sdr8 = uvec3(clamp(sdr * 255.0 + 0.5, 0.0, 255.0));
        uvec3 sdr10 = (sdr8 << 2u) | (sdr8 >> 6u);
        pixels[idx] = (sdr10.r << 20u) | (sdr10.g << 10u) | sdr10.b;
        return;
    }

    // Decode sRGB to linear
    highp vec3 linear_rgb = srgb_eotf(sdr);

    highp vec3 hdr_linear;
    if (u_has_gainmap != 0) {
        float gm = texture(u_gainmap_tex, uv).r;

        // Gain map gamma decode
        if (u_gm_gamma != 1.0) {
            gm = pow(gm, 1.0 / u_gm_gamma);
        }

        // log2 gain: interpolate between min and max
        float log2_gain = u_gm_min * (1.0 - gm) + u_gm_max * gm;

        // HDR formula: (sdr + offset_sdr) * 2^gain - offset_hdr
        hdr_linear = (linear_rgb + u_offset_sdr) * exp2(log2_gain) - u_offset_hdr;
        hdr_linear = max(hdr_linear, vec3(0.0));
    } else {
        hdr_linear = linear_rgb;
    }

    // SDR tone-mapped: Reinhard → sRGB gamma → 8→10 bit native
    if (u_output_mode == 2) {
        highp vec3 mapped = hdr_linear / (vec3(1.0) + hdr_linear);
        highp vec3 gamma = srgb_oetf(mapped);
        uvec3 sdr8 = uvec3(clamp(gamma * 255.0 + 0.5, 0.0, 255.0));
        uvec3 sdr10 = (sdr8 << 2u) | (sdr8 >> 6u);
        pixels[idx] = (sdr10.r << 20u) | (sdr10.g << 10u) | sdr10.b;
        return;
    }

    // HDR PQ: sRGB → BT.2020 gamut conversion → PQ encode
    highp vec3 bt2020 = u_color_matrix * hdr_linear;
    bt2020 = max(bt2020, vec3(0.0));

    // Linear → PQ (perceptual quantizer)
    highp vec3 pq = pq_oetf(bt2020);

    // Quantize to 10-bit and pack into XRGB2101010
    uvec3 pq10 = uvec3(clamp(pq * 1023.0 + 0.5, 0.0, 1023.0));
    pixels[idx] = (pq10.r << 20u) | (pq10.g << 10u) | pq10.b;
}
"""

# ── GPUPipeline ─────────────────────────────────────────────────────────

_c_uint = ctypes.c_uint32
_c_int = ctypes.c_int32
_c_float = ctypes.c_float


class GPUPipeline:
    """GPU-accelerated HDR pixel pipeline.

    Compiles a GLES 3.1 compute shader that converts SDR+gainmap to
    PQ BT.2020 XRGB2101010 packed pixels. Output is stored in an SSBO
    and can be copied to a DRM framebuffer mmap.
    """

    def __init__(self, gpu_ctx, width, height):
        self.gpu_ctx = gpu_ctx
        self.width = width
        self.height = height
        self._ssbo_size = width * height * 4  # bytes

        # Compile compute shader
        cs = compile_shader(COMPUTE_SHADER_SOURCE, GL_COMPUTE_SHADER)
        self._program = link_program(cs)
        print(f"GPU: compute shader compiled (program {self._program})")

        # Cache uniform locations
        self._u = {}
        for name in ["u_has_gainmap", "u_gm_gamma", "u_gm_min", "u_gm_max",
                      "u_offset_sdr", "u_offset_hdr", "u_output_mode",
                      "u_display_width", "u_display_height",
                      "u_image_x", "u_image_y", "u_image_width", "u_image_height",
                      "u_color_matrix"]:
            loc = glGetUniformLocation(self._program, name.encode("utf-8"))
            self._u[name] = loc

        # Create output SSBO
        self._ssbo = _c_uint()
        glGenBuffers(1, ctypes.byref(self._ssbo))
        glBindBuffer(GL_SHADER_STORAGE_BUFFER, self._ssbo)
        glBufferData(GL_SHADER_STORAGE_BUFFER, self._ssbo_size, None, GL_DYNAMIC_DRAW)
        glBindBuffer(GL_SHADER_STORAGE_BUFFER, 0)
        print(f"GPU: SSBO allocated ({self._ssbo_size // 1024}KB)")

        # Create input textures
        tex_ids = (_c_uint * 2)()
        glGenTextures(2, tex_ids)
        self._sdr_tex = tex_ids[0]
        self._gm_tex = tex_ids[1]

        # Configure textures with bilinear filtering
        for tex in (self._sdr_tex, self._gm_tex):
            glBindTexture(GL_TEXTURE_2D, tex)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
        glBindTexture(GL_TEXTURE_2D, 0)

        # Upload color matrix once (it never changes)
        glUseProgram(self._program)
        # Column-major order for GL: transpose the row-major numpy matrix
        mat_cm = SRGB_TO_BT2020.T.astype(np.float32).flatten()
        mat_data = (_c_float * 9)(*mat_cm)
        glUniformMatrix3fv(self._u["u_color_matrix"], 1, 0, mat_data)

        # Set display dimensions (constant)
        glUniform1i(self._u["u_display_width"], width)
        glUniform1i(self._u["u_display_height"], height)
        glUseProgram(0)

        # Precompute dispatch dimensions
        self._groups_x = (width + 15) // 16
        self._groups_y = (height + 15) // 16

    def _upload_sdr_texture(self, pil_image):
        """Upload a PIL RGB image as the SDR texture."""
        if pil_image.mode != "RGB":
            pil_image = pil_image.convert("RGB")
        w, h = pil_image.size
        data = pil_image.tobytes()
        glActiveTexture(GL_TEXTURE0)
        glBindTexture(GL_TEXTURE_2D, self._sdr_tex)
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB8, w, h, 0,
                     GL_RGB, GL_UNSIGNED_BYTE, data)

    def _upload_gainmap_texture(self, pil_image):
        """Upload a PIL L (grayscale) image as the gain map texture."""
        if pil_image.mode != "L":
            pil_image = pil_image.convert("L")
        w, h = pil_image.size
        data = pil_image.tobytes()
        glActiveTexture(GL_TEXTURE1)
        glBindTexture(GL_TEXTURE_2D, self._gm_tex)
        glTexImage2D(GL_TEXTURE_2D, 0, GL_R8, w, h, 0,
                     GL_RED, GL_UNSIGNED_BYTE, data)

    def _dispatch(self, has_gainmap, params, img_w, img_h, x_off, y_off,
                  output_mode=0):
        """Set uniforms and dispatch the compute shader.

        output_mode: 0 = HDR PQ, 1 = SDR native, 2 = SDR tone-mapped
        """
        glUseProgram(self._program)

        # Image placement
        glUniform1i(self._u["u_image_x"], x_off)
        glUniform1i(self._u["u_image_y"], y_off)
        glUniform1i(self._u["u_image_width"], img_w)
        glUniform1i(self._u["u_image_height"], img_h)

        # Output mode
        glUniform1i(self._u["u_output_mode"], output_mode)

        # Gain map parameters
        glUniform1i(self._u["u_has_gainmap"], 1 if has_gainmap else 0)
        if has_gainmap and params:
            glUniform1f(self._u["u_gm_gamma"], params.gamma)
            glUniform1f(self._u["u_gm_min"], params.gain_map_min)
            glUniform1f(self._u["u_gm_max"], params.gain_map_max)
            glUniform1f(self._u["u_offset_sdr"], params.offset_sdr)
            glUniform1f(self._u["u_offset_hdr"], params.offset_hdr)

        # Bind SSBO
        glBindBufferBase(GL_SHADER_STORAGE_BUFFER, 0, self._ssbo)

        # Dispatch
        glDispatchCompute(self._groups_x, self._groups_y, 1)

        # Barrier to make SSBO writes visible for subsequent reads
        glMemoryBarrier(GL_BUFFER_UPDATE_BARRIER_BIT)

        glUseProgram(0)

    def render_hdr(self, sdr_image, gainmap_image, params, img_w, img_h, x_off, y_off):
        """Render an HDR image (SDR + gain map) to the output SSBO.

        Args:
            sdr_image: PIL Image (RGB), already scaled to fit display
            gainmap_image: PIL Image (L), scaled to match sdr_image (or None)
            params: GainMapParams from ultrahdr module
            img_w, img_h: dimensions of the image on screen
            x_off, y_off: letterbox offset in the output
        """
        self._upload_sdr_texture(sdr_image)
        if gainmap_image is not None:
            self._upload_gainmap_texture(gainmap_image)
        self._dispatch(gainmap_image is not None, params, img_w, img_h, x_off, y_off)

    def render_sdr(self, image, img_w, img_h, x_off, y_off):
        """Render an SDR image through the PQ BT.2020 pipeline."""
        self._upload_sdr_texture(image)
        self._dispatch(False, None, img_w, img_h, x_off, y_off)

    def render_fullscreen(self, image):
        """Render a fullscreen SDR canvas through PQ pipeline (grid/config in HDR)."""
        self.render_sdr(image, self.width, self.height, 0, 0)

    def render_native(self, image, img_w, img_h, x_off, y_off):
        """Render an SDR image as native sRGB (no color transform, 8→10 bit)."""
        self._upload_sdr_texture(image)
        self._dispatch(False, None, img_w, img_h, x_off, y_off, output_mode=1)

    def render_native_fullscreen(self, image):
        """Render a fullscreen SDR canvas as native sRGB (grid/config in SDR)."""
        self.render_native(image, self.width, self.height, 0, 0)

    def render_tonemap(self, sdr_image, gainmap_image, params, img_w, img_h, x_off, y_off):
        """Render gain map + Reinhard tone-map to SDR native output."""
        self._upload_sdr_texture(sdr_image)
        if gainmap_image is not None:
            self._upload_gainmap_texture(gainmap_image)
        self._dispatch(gainmap_image is not None, params, img_w, img_h, x_off, y_off,
                       output_mode=2)

    def get_pixels(self):
        """Get output as a new numpy uint32 array (copies from SSBO)."""
        glBindBuffer(GL_SHADER_STORAGE_BUFFER, self._ssbo)
        ptr = glMapBufferRange(GL_SHADER_STORAGE_BUFFER, 0,
                               self._ssbo_size, GL_MAP_READ_BIT)
        if not ptr:
            raise RuntimeError("Failed to map SSBO for reading")

        c_arr = (ctypes.c_uint32 * (self.width * self.height)).from_address(ptr)
        result = np.array(c_arr, dtype=np.uint32).reshape(self.height, self.width)

        glUnmapBuffer(GL_SHADER_STORAGE_BUFFER)
        glBindBuffer(GL_SHADER_STORAGE_BUFFER, 0)
        return result

    def copy_to_framebuffer(self, fb):
        """Copy SSBO output directly to DRM framebuffer mmap (fast path).

        Uses ctypes.memmove for a single memcpy without Python intermediaries.
        """
        glBindBuffer(GL_SHADER_STORAGE_BUFFER, self._ssbo)
        ptr = glMapBufferRange(GL_SHADER_STORAGE_BUFFER, 0,
                               self._ssbo_size, GL_MAP_READ_BIT)
        if not ptr:
            raise RuntimeError("Failed to map SSBO for reading")

        mm = fb.mmap_buffer()
        mm_buf = (ctypes.c_char * len(mm)).from_buffer(mm)
        mm_base = ctypes.addressof(mm_buf)

        if fb.pitch == self.width * 4:
            # Contiguous: single memcpy
            ctypes.memmove(mm_base, ptr, self._ssbo_size)
        else:
            # Pitch differs: row-by-row copy
            row_bytes = self.width * 4
            for y in range(self.height):
                ctypes.memmove(mm_base + y * fb.pitch,
                               ptr + y * row_bytes,
                               row_bytes)

        glUnmapBuffer(GL_SHADER_STORAGE_BUFFER)
        glBindBuffer(GL_SHADER_STORAGE_BUFFER, 0)

    def close(self):
        """Release GPU resources."""
        if self._program:
            glDeleteProgram(self._program)
            self._program = 0
        if self._ssbo.value:
            glDeleteBuffers(1, ctypes.byref(self._ssbo))
            self._ssbo = _c_uint(0)
        tex_ids = (_c_uint * 2)(self._sdr_tex, self._gm_tex)
        glDeleteTextures(2, tex_ids)
        self._sdr_tex = 0
        self._gm_tex = 0
        print("GPU: pipeline closed")


# ── Self-test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import os
    import time
    from PIL import Image

    # Open DRM device
    for i in range(4):
        path = f"/dev/dri/card{i}"
        try:
            fd = os.open(path, os.O_RDWR | os.O_CLOEXEC)
            ctx = GPUContext(fd)
            break
        except (OSError, RuntimeError):
            continue
    else:
        print("No GPU context available")
        exit(1)

    # Create pipeline at 1920x1080
    pipeline = GPUPipeline(ctx, 1920, 1080)

    # Test with a gradient image
    img = Image.new("RGB", (800, 600))
    for y in range(600):
        for x in range(800):
            img.putpixel((x, y), (int(x * 255 / 799), int(y * 255 / 599), 128))

    t0 = time.monotonic()
    pipeline.render_sdr(img, 800, 600, 560, 240)
    t1 = time.monotonic()
    pixels = pipeline.get_pixels()
    t2 = time.monotonic()

    print(f"GPU render: {(t1-t0)*1000:.1f}ms, readback: {(t2-t1)*1000:.1f}ms")
    print(f"Output shape: {pixels.shape}, dtype: {pixels.dtype}")

    # Verify some pixel values
    r = (pixels >> 20) & 0x3FF
    g = (pixels >> 10) & 0x3FF
    b = pixels & 0x3FF
    print(f"R range: [{r.min()}, {r.max()}]")
    print(f"G range: [{g.min()}, {g.max()}]")
    print(f"B range: [{b.min()}, {b.max()}]")

    # Verify black letterbox
    assert pixels[0, 0] == 0, "Top-left should be black (letterbox)"
    assert pixels[1079, 0] == 0, "Bottom-left should be black (letterbox)"

    pipeline.close()
    ctx.close()
    os.close(fd)
    print("Self-test passed")
