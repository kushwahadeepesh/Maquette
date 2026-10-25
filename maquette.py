#!/usr/bin/env python3
"""
Maquette - an always-on-top reference viewer for sculptors.
3D mode: clay-shaded models (OBJ/STL/PLY/GLB/GLTF/OFF) with Blender-style navigation.
2D mode: a PureRef-style image board.  Boards save to .maq files.
Press H inside the app for every shortcut.
"""
import os
import sys
import json
import math
import time
import queue
import hashlib
import threading
import traceback

APP = "Maquette"
VERSION = "1.0.0"
FROZEN = getattr(sys, "frozen", False)
HERE = os.path.dirname(os.path.abspath(sys.executable if FROZEN else __file__))
RES_DIR = getattr(sys, "_MEIPASS", HERE)                      # bundled read-only files
if FROZEN:                                                     # installed app -> per-user data folder
    DATA_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), APP)
else:                                                          # running from source -> portable
    DATA_DIR = HERE
os.makedirs(DATA_DIR, exist_ok=True)
LOG_PATH = os.path.join(DATA_DIR, "maquette.log")


def fatal(msg):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(time.strftime("[%Y-%m-%d %H:%M:%S]\n") + msg + "\n")
    except Exception:
        pass
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, msg[-1500:], f"{APP} error", 0x10)
        except Exception:
            pass
    else:
        print(msg, file=sys.stderr)


try:
    import numpy as np
    import glfw
    import moderngl
    from PIL import Image, ImageDraw, ImageFont, ImageOps
except ImportError as _e:
    fatal(f"Missing dependency: {_e}\n\nRun setup.bat first.")
    sys.exit(1)


# =================================================================== settings
CFG_PATH = os.path.join(DATA_DIR, "config.json")
CACHE_DIR = os.path.join(DATA_DIR, "cache")
MODELS_DIR = os.path.join(HERE, "models")
PASTE_DIR = os.path.join(DATA_DIR, "pasted")
LOGO_PATH = os.path.join(RES_DIR, "assets", "maquette_256.png")
PROJECT_EXT = ".maq"
DRAG_PX = 5          # pen-friendly: small jitter is still a click
DBL_PX, DBL_T = 10, 0.45
VIEW_DRAGS = ("orbit", "pan", "zoom", "light")
MESH_EXTS = (".obj", ".stl", ".ply", ".glb", ".gltf", ".off")
IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff")
UNSUPPORTED = {".fbx": "FBX", ".ztl": "ZTL", ".zpr": "ZPR", ".blend": "BLEND", ".max": "MAX",
               ".ma": "MAYA", ".mb": "MAYA", ".psd": "PSD"}
CACHE_VER = 1
MAX_TEX = 4096
FOV = 30.0  # vertical field of view; low = less lens distortion on anatomy

AXIS_COL = [(1.0, 0.20, 0.32), (0.545, 0.863, 0.0), (0.157, 0.565, 1.0)]
AXIS_LBL = "XYZ"
ACCENT = (0.28, 0.47, 0.82)
SEL_COL = (1.0, 0.62, 0.18)
CLAYS = [
    ("Clay", (0.78, 0.78, 0.78)),
    ("Warm clay", (0.80, 0.60, 0.50)),
    ("Red wax", (0.66, 0.34, 0.29)),
    ("Bone", (0.86, 0.82, 0.72)),
    ("Slate", (0.42, 0.45, 0.50)),
]
# (axis, sign) -> (azimuth, elevation) of the view looking FROM that side
AXIS_VIEWS = {(0, 1): (90, 0), (0, -1): (-90, 0), (1, 1): (180, 0), (1, -1): (0, 0), (2, 1): (0, 90), (2, -1): (0, -90)}

DEFAULTS = {
    "x": None, "y": None, "w": 560, "h": 700,
    "on_top": True, "borderless": True, "opacity": 1.0,
    "color": 0, "flat": False, "wire": False, "ortho": False, "grid": True,
    "spin_speed": 30.0, "light": [0.0, 0.0], "view2d": False, "gray2d": False,
    "max_rect": None, "autohide": True, "recent": [],
    "session": None, "project_path": None, "project_dirty": False,
}

BUTTONS_3D = [
    ("zoom", "Zoom  (drag)"),
    ("pan", "Pan  (drag)"),
    ("focus", "Focus selected  [F]   ·   Home = all"),
    ("light", "Rotate light  (drag)   ·   L + drag"),
    ("ortho", "Perspective / Ortho  [5]"),
    ("spin", "Turntable spin  [Space]"),
    ("pin", "Always on top  [T]"),
    ("help", "Shortcuts  [H]"),
]
BUTTONS_2D = [
    ("zoom", "Zoom  (drag)"),
    ("pan", "Pan  (drag)"),
    ("focus", "Focus selected  [F]   ·   Home = all"),
    ("gray", "Grayscale value check  [K]"),
    ("flip", "Flip horizontal  [M]"),
    ("arrange", "Arrange images  [A]"),
    ("pin", "Always on top  [T]"),
    ("help", "Shortcuts  [H]"),
]

HELP_3D = [
    ("3D  ·  Navigate", [
        ("Left / Middle drag", "Orbit"),
        ("Shift+drag  ·  Right drag", "Pan"),
        ("Scroll  ·  Ctrl+drag", "Zoom"),
        ("Pen: Alt+drag", "Orbit  (+Shift pan, +Ctrl zoom)"),
        ("Click a model", "Select it"),
        ("F  ·  focus button", "Focus: orbit around selected"),
        ("Double-click  ·  Alt+Middle click", "Orbit around that point"),
        ("Home  ·  Shift+C", "Frame everything"),
        ("/", "Isolate selected (solo)"),
        ("PgUp / PgDn", "Previous / next model"),
        ("Gizmo ball", "Snap to side (again = opposite)"),
        ("1  3  7   (Ctrl = opposite)", "Front / Right / Top"),
        ("2  4  6  8   (Ctrl = pan)", "Orbit in 15° steps"),
        ("9", "View from the opposite side"),
        ("5", "Perspective / Ortho"),
        ("R", "Reset view"),
    ]),
    ("3D  ·  Look", [
        ("Space   (Shift = reverse)", "Turntable spin"),
        ("[   ]", "Spin speed"),
        ("L + drag  ·  sun button", "Rotate light   (Ctrl+L reset)"),
        ("S", "Smooth / Flat shading"),
        ("K", "Silhouette check"),
        ("M", "Mirror view (fresh eyes)"),
        ("W", "Wireframe"),
        ("C", "Clay color"),
        ("G", "Floor grid"),
        ("U", "Up axis  Y / Z  (selected)"),
        ("A", "Line models up in a row"),
        ("Delete", "Remove selected model"),
    ]),
]
HELP_2D = [
    ("2D  ·  Image board", [
        ("Drag an image", "Move"),
        ("Corner handles  ·  Shift+scroll", "Scale"),
        ("Drag empty  ·  Right / Middle  ·  Alt+drag", "Pan"),
        ("Scroll", "Zoom"),
        ("Double-click image", "Focus it"),
        ("F  /  Home", "Focus selected / Fit all"),
        ("M", "Flip horizontal"),
        ("K", "Grayscale value check"),
        ("A", "Arrange in a grid"),
        ("PgUp / PgDn", "Previous / next image"),
        ("Delete", "Remove selected image"),
    ]),
]
HELP_COMMON = [
    ("General", [
        ("Tab  ·  top bar", "Switch 2D / 3D"),
        ("Ctrl+N / Ctrl+O", "New / Open board"),
        ("Ctrl+S  (Shift = Save as)", "Save board (.maq)"),
        ("Drop files / folders  ·  O", "Add models or images"),
        ("Ctrl+V", "Paste image from clipboard"),
        ("T", "Always on top"),
        ("B", "Borderless"),
        ("Ctrl+Scroll", "Window opacity"),
        ("Drag top bar", "Move window"),
        ("Double-click top bar", "Maximize / restore"),
        ("Menu button (top-left)", "Files, recent boards, options"),
        ("H  ·  Esc", "Help / close"),
        ("Ctrl+Q", "Quit"),
    ]),
]


def load_cfg():
    cfg = dict(DEFAULTS)
    try:
        with open(CFG_PATH, "r", encoding="utf-8") as fh:
            cfg.update(json.load(fh))
    except Exception:
        pass
    # migrate configs from the Ref3D prototype
    if not cfg.get("session"):
        scene = cfg.get("scene") or []
        if not scene and cfg.get("files"):
            up = cfg.get("up") or {}
            scene = [{"path": p, "up": up.get(p)} for p in cfg["files"]]
        if scene or cfg.get("board"):
            cfg["session"] = {"scene": scene, "board": cfg.get("board") or [],
                              "mode": "2d" if cfg.get("view2d") else "3d"}
    return cfg


def save_cfg(cfg):
    for k in ("files", "index", "up", "scene", "board"):
        cfg.pop(k, None)
    try:
        with open(CFG_PATH, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)
    except Exception:
        pass


# =================================================================== math
def rot_x(deg):
    a = math.radians(deg); c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0, 0], [0, c, -s, 0], [0, s, c, 0], [0, 0, 0, 1]], "f8")


def rot_y(deg):
    a = math.radians(deg); c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s, 0], [0, 1, 0, 0], [-s, 0, c, 0], [0, 0, 0, 1]], "f8")


def rot_z(deg):
    a = math.radians(deg); c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0, 0], [s, c, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], "f8")


def translate(v):
    m = np.eye(4); m[:3, 3] = v
    return m


def perspective(fovy, aspect, n, f):
    t = 1.0 / math.tan(math.radians(fovy) / 2)
    return np.array([[t / aspect, 0, 0, 0], [0, t, 0, 0],
                     [0, 0, (f + n) / (n - f), 2 * f * n / (n - f)], [0, 0, -1, 0]], "f8")


def ortho(l, r, b, t, n, f):
    return np.array([[2 / (r - l), 0, 0, -(r + l) / (r - l)], [0, 2 / (t - b), 0, -(t + b) / (t - b)],
                     [0, 0, -2 / (f - n), -(f + n) / (f - n)], [0, 0, 0, 1]], "f8")


def angle_diff(a, b):
    return (a - b + 180.0) % 360.0 - 180.0


def bbox_corners(lo, hi):
    return np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])], "f8")


# =================================================================== loading
def vertex_normals(v, f):
    fn = np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]]).astype("f8")  # area weighted
    n = np.zeros((len(v), 3), "f8")
    for k in range(3):
        w = fn[:, k]
        n[:, k] = (np.bincount(f[:, 0], w, len(v)) + np.bincount(f[:, 1], w, len(v))
                   + np.bincount(f[:, 2], w, len(v)))
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    ln[ln == 0] = 1.0
    return (n / ln).astype("f4")


def load_mesh(path):
    """Returns (vertices f4 Nx3, normals f4 Nx3, faces u4 Mx3). Geometry is cached."""
    st = os.stat(path)
    key = hashlib.sha1(f"{CACHE_VER}|{os.path.abspath(path)}|{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest()[:24]
    cpath = os.path.join(CACHE_DIR, key + ".npz")
    if os.path.exists(cpath):
        try:
            with np.load(cpath) as d:
                v, f = d["v"], d["f"]
            return v, vertex_normals(v, f), f
        except Exception:
            pass

    import trimesh
    kw = {"process": False}
    if path.lower().endswith(".obj"):
        kw["skip_materials"] = True
    obj = trimesh.load(path, **kw)

    vs, fs, off = [], [], 0
    def add(verts, faces):
        nonlocal off
        if faces is None or len(faces) == 0:
            return
        verts = np.asarray(verts, "f8")
        vs.append(verts); fs.append(np.asarray(faces, "i8") + off); off += len(verts)

    if isinstance(obj, trimesh.Scene):
        for node in obj.graph.nodes_geometry:
            T, gname = obj.graph[node]
            g = obj.geometry.get(gname)
            if g is None or not hasattr(g, "faces"):
                continue
            T = np.asarray(T)
            add(np.asarray(g.vertices, "f8") @ T[:3, :3].T + T[:3, 3], g.faces)
    else:
        add(obj.vertices, getattr(obj, "faces", None))
    if not vs:
        raise ValueError("no triangle mesh found in the file")

    v = np.ascontiguousarray(np.concatenate(vs).astype("f4"))
    f = np.concatenate(fs)
    # weld exact duplicate vertices (UV seams) so shading is smooth everywhere
    _, idx, inv = np.unique(v.view(np.dtype((np.void, 12))).ravel(), return_index=True, return_inverse=True)
    v = np.ascontiguousarray(v[idx]); f = inv.reshape(-1)[f].astype("u4")
    f = f[(f[:, 0] != f[:, 1]) & (f[:, 1] != f[:, 2]) & (f[:, 0] != f[:, 2])]
    if len(f) == 0:
        raise ValueError("mesh has no valid faces")
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        np.savez(cpath, v=v, f=f)
    except Exception:
        pass
    return v, vertex_normals(v, f), f


def load_image(path):
    with Image.open(path) as im:
        im.seek(0)
        im = ImageOps.exif_transpose(im).convert("RGBA")
    if max(im.size) > MAX_TEX:
        im.thumbnail((MAX_TEX, MAX_TEX), Image.LANCZOS)
    return im.size[0], im.size[1], im.tobytes()


# =================================================================== shaders
MESH_VS = """
#version 330
uniform mat4 u_proj;
uniform mat4 u_mv;
uniform mat3 u_nm;
uniform float u_bias;
in vec3 in_pos;
in vec3 in_nrm;
out vec3 v_pos;
out vec3 v_nrm;
void main() {
    vec4 p = u_mv * vec4(in_pos, 1.0);
    v_pos = p.xyz;
    v_nrm = u_nm * in_nrm;
    gl_Position = u_proj * p;
    gl_Position.z -= u_bias * gl_Position.w;
}
"""
MESH_FS = """
#version 330
uniform vec3 u_color;
uniform mat3 u_lrot;
uniform int u_flat;
uniform int u_ortho;
uniform int u_wire;
uniform int u_sil;
uniform int u_mirror;
uniform float u_sel;
in vec3 v_pos;
in vec3 v_nrm;
out vec4 frag;
void main() {
    if (u_wire == 1) { frag = vec4(0.04, 0.04, 0.05, 0.45); return; }
    if (u_sil == 1) { frag = vec4(0.06, 0.06, 0.07, 1.0); return; }
    vec3 V = (u_ortho == 1) ? vec3(0.0, 0.0, 1.0) : normalize(-v_pos);
    bool front = (u_mirror == 1) ? !gl_FrontFacing : gl_FrontFacing;
    vec3 n;
    if (u_flat == 1) {
        n = normalize(cross(dFdx(v_pos), dFdy(v_pos)));
        if (u_mirror == 1) n = -n;
    } else {
        n = normalize(v_nrm);
        if (!front) n = -n;
    }
    // camera-relative studio lighting (like Blender's Solid mode), rotatable
    vec3 L1 = u_lrot * normalize(vec3(-0.45, 0.65, 0.62));   // key
    vec3 L2 = u_lrot * normalize(vec3(0.75, 0.10, 0.45));    // fill
    vec3 L3 = u_lrot * normalize(vec3(0.20, 0.85, -0.50));   // back / top
    float d1 = max(dot(n, L1), 0.0);
    float d2 = max(dot(n, L2), 0.0);
    float d3 = max(dot(n, L3), 0.0);
    float hemi = 0.5 + 0.5 * n.y;
    float ndv = max(dot(n, V), 0.0);
    vec3 c = u_color * (0.14 + 0.18 * hemi + 0.62 * d1 + 0.20 * d2 + 0.14 * d3);
    c += vec3(0.12) * pow(max(dot(n, normalize(L1 + V)), 0.0), 48.0);
    float rim = pow(1.0 - ndv, 3.0);
    c += vec3(0.85, 0.90, 1.0) * 0.16 * rim;
    c = mix(c, vec3(1.0, 0.62, 0.18), u_sel * clamp(rim * 1.6, 0.0, 0.85));
    frag = vec4(c, 1.0);
}
"""
PICK_VS = """
#version 330
uniform mat4 u_proj;
uniform mat4 u_mv;
in vec3 in_pos;
void main() { gl_Position = u_proj * u_mv * vec4(in_pos, 1.0); }
"""
PICK_FS = """
#version 330
uniform vec2 u_id;
out vec4 frag;
void main() { frag = vec4(u_id, 0.0, 1.0); }
"""
GRID_VS = """
#version 330
uniform mat4 u_vp;
uniform vec2 u_center;
uniform float u_size;
in vec2 in_q;
out vec2 v_w;
void main() {
    vec2 p = u_center + (in_q * 2.0 - 1.0) * u_size;
    v_w = p;
    gl_Position = u_vp * vec4(p, 0.0, 1.0);
}
"""
GRID_FS = """
#version 330
uniform float u_step;
uniform vec2 u_center;
uniform float u_fade;
in vec2 v_w;
out vec4 frag;
float gridline(vec2 c) {
    vec2 fw = max(fwidth(c), vec2(1e-6));
    vec2 g = abs(fract(c - 0.5) - 0.5) / fw;
    float l = 1.0 - min(min(g.x, g.y), 1.0);
    return l * (1.0 - smoothstep(0.15, 0.5, max(fw.x, fw.y)));
}
void main() {
    vec2 c = v_w / u_step;
    float a = max(gridline(c) * 0.10, gridline(c / 10.0) * 0.22);
    vec3 col = vec3(1.0);
    vec2 fw = max(fwidth(v_w), vec2(1e-6));
    float ax = 1.0 - min(abs(v_w.y) / fw.y / 1.2, 1.0);
    float ay = 1.0 - min(abs(v_w.x) / fw.x / 1.2, 1.0);
    if (ax > 0.0) { col = mix(col, vec3(0.9, 0.25, 0.32), ax); a = max(a, ax * 0.55); }
    if (ay > 0.0) { col = mix(col, vec3(0.5, 0.78, 0.1), ay); a = max(a, ay * 0.55); }
    a *= 1.0 - smoothstep(u_fade * 0.35, u_fade, length(v_w - u_center));
    if (a <= 0.002) discard;
    frag = vec4(col, a);
}
"""
BG_VS = """
#version 330
out vec2 v_uv;
void main() {
    vec2 p = vec2(gl_VertexID == 1 ? 3.0 : -1.0, gl_VertexID == 2 ? 3.0 : -1.0);
    v_uv = p * 0.5 + 0.5;
    gl_Position = vec4(p, 0.0, 1.0);
}
"""
BG_FS = """
#version 330
uniform vec3 u_top;
uniform vec3 u_bot;
in vec2 v_uv;
out vec4 frag;
void main() {
    vec3 c = mix(u_bot, u_top, v_uv.y);
    c += (fract(sin(dot(gl_FragCoord.xy, vec2(12.9898, 78.233))) * 43758.5453) - 0.5) / 255.0;
    frag = vec4(c, 1.0);
}
"""
UI_VS = """
#version 330
uniform vec4 u_rect;
uniform vec2 u_screen;
in vec2 in_q;
out vec2 v_p;
out vec2 v_uv;
void main() {
    vec2 p = u_rect.xy + in_q * u_rect.zw;
    v_p = p;
    v_uv = in_q;
    gl_Position = vec4(p.x / u_screen.x * 2.0 - 1.0, 1.0 - p.y / u_screen.y * 2.0, 0.0, 1.0);
}
"""
SHAPE_FS = """
#version 330
uniform int u_type;
uniform vec4 u_a;
uniform vec4 u_b;
uniform vec4 u_color;
in vec2 v_p;
out vec4 frag;
float sd_seg(vec2 p, vec2 a, vec2 b) {
    vec2 pa = p - a, ba = b - a;
    float h = clamp(dot(pa, ba) / max(dot(ba, ba), 1e-6), 0.0, 1.0);
    return length(pa - ba * h);
}
void main() {
    float d;
    if (u_type == 0) d = length(v_p - u_a.xy) - u_a.z;
    else if (u_type == 1) d = abs(length(v_p - u_a.xy) - u_a.z) - u_a.w * 0.5;
    else if (u_type == 2) d = sd_seg(v_p, u_a.xy, u_a.zw) - u_b.x;
    else {
        vec2 q = abs(v_p - u_a.xy) - u_a.zw + u_b.x;
        d = length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - u_b.x;
        if (u_b.y > 0.0) d = abs(d + u_b.y * 0.5) - u_b.y * 0.5;   // outline only
    }
    float a = clamp(0.5 - d, 0.0, 1.0);
    if (a <= 0.0) discard;
    frag = vec4(u_color.rgb, u_color.a * a);
}
"""
TEXT_FS = """
#version 330
uniform sampler2D u_tex;
uniform vec4 u_color;
in vec2 v_uv;
out vec4 frag;
void main() {
    float a = texture(u_tex, v_uv).r;
    frag = vec4(u_color.rgb, u_color.a * a);
}
"""
IMG_FS = """
#version 330
uniform sampler2D u_tex;
uniform int u_gray;
uniform int u_flip;
in vec2 v_uv;
out vec4 frag;
void main() {
    vec2 uv = v_uv;
    if (u_flip == 1) uv.x = 1.0 - uv.x;
    vec4 c = texture(u_tex, uv);
    if (u_gray == 1) c.rgb = vec3(dot(c.rgb, vec3(0.299, 0.587, 0.114)));
    frag = c;
}
"""


def _set(prog, name, value):
    try:
        u = prog[name]
    except KeyError:
        return
    if isinstance(value, np.ndarray):
        u.write(np.ascontiguousarray(value.T, dtype="f4").tobytes())  # row-major -> GL column-major
    else:
        u.value = value


# =================================================================== scene items
class Obj:
    """A 3D model in the scene."""
    def __init__(self, path, up, ctx, p_mesh, p_pick, v, n, f):
        self.path, self.name = path, os.path.basename(path)
        self.up = up or ("Z" if path.lower().endswith(".stl") else "Y")
        buf = np.empty((len(v), 6), "f4"); buf[:, :3] = v; buf[:, 3:] = n
        self.vbo = ctx.buffer(buf.tobytes())
        self.ibo = ctx.buffer(np.ascontiguousarray(f, dtype="u4").tobytes())
        self.vao = ctx.vertex_array(p_mesh, [(self.vbo, "3f 3f", "in_pos", "in_nrm")], self.ibo, index_element_size=4)
        self.pvao = ctx.vertex_array(p_pick, [(self.vbo, "3f 12x", "in_pos")], self.ibo, index_element_size=4)
        self.tris = len(f)
        self.lo, self.hi = v.min(axis=0).astype("f8"), v.max(axis=0).astype("f8")
        self.offset = np.zeros(3)

    def orient(self):
        return rot_x(90) if self.up == "Y" else np.eye(4)

    def matrix(self):
        return translate(self.offset) @ self.orient()

    def oriented_bounds(self):
        c = bbox_corners(self.lo, self.hi) @ self.orient()[:3, :3].T
        return c.min(axis=0), c.max(axis=0)

    def world_bounds(self):
        lo, hi = self.oriented_bounds()
        return lo + self.offset, hi + self.offset

    def sphere(self):
        lo, hi = self.world_bounds()
        return (lo + hi) / 2, max(float(np.linalg.norm(hi - lo)) / 2, 1e-6)

    def release(self):
        for o in (self.vao, self.pvao, self.vbo, self.ibo):
            o.release()


class Img:
    """An image on the 2D board. x, y = centre in board units; s = scale."""
    def __init__(self, path, ctx, w, h, data):
        self.path, self.name = path, os.path.basename(path)
        self.w, self.h = w, h
        self.tex = ctx.texture((w, h), 4, data)
        self.tex.build_mipmaps()
        self.tex.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
        try:
            self.tex.anisotropy = 8.0
        except Exception:
            pass
        self.x = self.y = 0.0
        self.s = 1.0
        self.flip = False

    def rect(self):
        w, h = self.w * self.s, self.h * self.s
        return self.x - w / 2, self.y - h / 2, w, h

    def release(self):
        self.tex.release()


# =================================================================== viewer
class Viewer:
    def __init__(self, argv_files):
        self.cfg = cfg = load_cfg()
        self.on_top = bool(cfg["on_top"])
        self.borderless = bool(cfg["borderless"])
        self.opacity = float(min(1.0, max(0.15, cfg["opacity"])))
        self.color_idx = int(cfg["color"]) % len(CLAYS)
        self.flat = bool(cfg["flat"])
        self.wire = bool(cfg["wire"])
        self.ortho = bool(cfg["ortho"])
        self.grid = bool(cfg["grid"])
        self.spin_speed = float(cfg["spin_speed"]) or 30.0
        self.light_yaw, self.light_pitch = (list(cfg.get("light") or [0, 0]) + [0, 0])[:2]
        self.view2d = bool(cfg["view2d"])
        self.gray2d = bool(cfg["gray2d"])
        self.max_rect = cfg.get("max_rect")
        self.autohide = bool(cfg.get("autohide", True))
        self.recent = [p for p in (cfg.get("recent") or []) if isinstance(p, str)]
        self.project_path = None
        self.project_dirty = False
        self.sil = False
        self.mirror = False
        self._amul = 1.0
        self.ui_a = 1.0
        self.left_t = 0.0
        self.menu_open = False
        self._gen = 0
        if os.name == "nt":
            try:  # own taskbar icon/group instead of python's
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"{APP}.RefViewer.1")
            except Exception:
                pass

        if not glfw.init():
            raise RuntimeError("Could not initialise GLFW.")
        glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, 3)
        glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, 3)
        glfw.window_hint(glfw.OPENGL_PROFILE, glfw.OPENGL_CORE_PROFILE)
        glfw.window_hint(glfw.OPENGL_FORWARD_COMPAT, 1)
        glfw.window_hint(glfw.SAMPLES, 4)
        glfw.window_hint(glfw.FLOATING, int(self.on_top))
        glfw.window_hint(glfw.DECORATED, int(not self.borderless))
        glfw.window_hint(glfw.VISIBLE, 0)
        w, h = max(200, int(cfg["w"])), max(160, int(cfg["h"]))
        self.win = glfw.create_window(w, h, APP, None, None)
        if not self.win:
            raise RuntimeError("Could not create an OpenGL 3.3 window. Please update your graphics driver.")
        if cfg["x"] is not None and self._pos_ok(int(cfg["x"]), int(cfg["y"]), w, h):
            glfw.set_window_pos(self.win, int(cfg["x"]), int(cfg["y"]))
        glfw.set_window_size_limits(self.win, 200, 160, glfw.DONT_CARE, glfw.DONT_CARE)
        glfw.make_context_current(self.win)
        glfw.swap_interval(1)

        self.ctx = ctx = moderngl.create_context()
        self.p_mesh = ctx.program(vertex_shader=MESH_VS, fragment_shader=MESH_FS)
        self.p_pick = ctx.program(vertex_shader=PICK_VS, fragment_shader=PICK_FS)
        self.p_grid = ctx.program(vertex_shader=GRID_VS, fragment_shader=GRID_FS)
        self.p_bg = ctx.program(vertex_shader=BG_VS, fragment_shader=BG_FS)
        self.p_shape = ctx.program(vertex_shader=UI_VS, fragment_shader=SHAPE_FS)
        self.p_text = ctx.program(vertex_shader=UI_VS, fragment_shader=TEXT_FS)
        self.p_img = ctx.program(vertex_shader=UI_VS, fragment_shader=IMG_FS)
        self.quad = ctx.buffer(np.array([0, 0, 1, 0, 0, 1, 1, 1], "f4").tobytes())
        self.vao_shape = ctx.vertex_array(self.p_shape, [(self.quad, "2f", "in_q")])
        self.vao_text = ctx.vertex_array(self.p_text, [(self.quad, "2f", "in_q")])
        self.vao_img = ctx.vertex_array(self.p_img, [(self.quad, "2f", "in_q")])
        self.vao_grid = ctx.vertex_array(self.p_grid, [(self.quad, "2f", "in_q")])
        self.vao_bg = ctx.vertex_array(self.p_bg, [])
        self.pick_fbo = None
        self.logo = None
        try:
            with Image.open(LOGO_PATH) as li:
                li = li.convert("RGBA")
                try:
                    glfw.set_window_icon(self.win, 1, [li])
                except Exception:
                    pass
                self.logo = ctx.texture(li.size, 4, li.tobytes())
                self.logo.build_mipmaps()
                self.logo.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
        except Exception:
            pass

        # 3D state
        self.objs, self.sel, self.isolate = [], None, False
        self.azim, self.elev = 0.0, 0.0
        self.target = np.zeros(3); self.dist = 4.0
        self.spin = False
        # 2D state
        self.imgs, self.isel = [], None
        self.cam2 = np.zeros(2); self.zoom2 = 1.0
        self.anim = None

        # background loading
        self._jobs = queue.Queue()
        self._results = []
        self._lock = threading.Lock()
        self._loading_names = []
        threading.Thread(target=self._worker, daemon=True).start()

        # ui state
        self._want_open = self._want_paste = False
        self._want_save = 0
        self._want_new = False
        self.help = False
        self.toast_msg, self.toast_t = "", 0.0
        self.hover = None
        self.drag = None
        self.drag_btn = None
        self.press = self.last = (0.0, 0.0)
        self.press_hit = None
        self.press_size = (w, h)
        self.press_data = None
        self.moved = False
        self.inside = False
        self._last_click = (0.0, 0.0, 0.0, -1)
        self.dirty = True
        self._fonts, self._texts = {}, {}
        self._cursors = {}
        self._cur_shape = None

        win = self.win
        glfw.set_mouse_button_callback(win, self.on_mouse)
        glfw.set_cursor_pos_callback(win, self.on_cursor)
        glfw.set_scroll_callback(win, self.on_scroll)
        glfw.set_key_callback(win, self.on_key)
        glfw.set_drop_callback(win, lambda _w, paths: self.add_paths(paths, at_cursor=True))
        glfw.set_cursor_enter_callback(win, self.on_enter)
        glfw.set_framebuffer_size_callback(win, lambda *_: self._mark())
        glfw.set_window_refresh_callback(win, lambda _w: self.render())
        glfw.set_window_content_scale_callback(win, lambda *_: self._mark())

        glfw.set_window_opacity(win, self.opacity)
        if self.max_rect and self.borderless:
            self._apply_max()
        else:
            self.max_rect = None
        glfw.show_window(win)

        # open files from the command line / file association, else restore last session
        projects = [a for a in argv_files if a.lower().endswith(PROJECT_EXT)]
        if projects:
            self.open_project(projects[0], ask=False)
        elif argv_files:
            self.add_paths(argv_files, mark=False)
        elif isinstance(cfg.get("session"), dict):
            self.apply_project(cfg["session"], None)
            pp = cfg.get("project_path")
            if pp and os.path.isfile(pp):
                self.project_path = pp
                self.project_dirty = bool(cfg.get("project_dirty"))
        elif not FROZEN and os.path.isdir(MODELS_DIR):
            self.add_paths([MODELS_DIR], quiet=True, mark=False)
        self.update_title()
        if not self.objs and not self.imgs and not self._loading_names:
            self.show_toast("Press H for shortcuts")

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _pos_ok(x, y, w, h):
        try:
            for m in glfw.get_monitors() or []:
                mx, my, mw, mh = glfw.get_monitor_workarea(m)
                if mx - w + 80 <= x <= mx + mw - 80 and my - 10 <= y <= my + mh - 60:
                    return True
        except Exception:
            return True
        return False

    def _mark(self):
        self.dirty = True

    @property
    def fb(self):
        w, h = glfw.get_framebuffer_size(self.win)
        return max(w, 1), max(h, 1)

    @property
    def scale(self):
        try:
            return max(0.75, glfw.get_window_content_scale(self.win)[0])
        except Exception:
            return 1.0

    def win_to_fb(self, wx, wy):
        ww, wh = glfw.get_window_size(self.win)
        fw, fh = self.fb
        return wx * fw / max(ww, 1), wy * fh / max(wh, 1)

    def fb_ratio(self):
        ww, _ = glfw.get_window_size(self.win)
        return self.fb[0] / max(ww, 1)

    def show_toast(self, msg):
        self.toast_msg, self.toast_t = msg, time.perf_counter()
        self.dirty = True

    def toast_active(self):
        return bool(self.toast_msg) and time.perf_counter() - self.toast_t < 2.0

    def set_cursor(self, shape):
        if shape == self._cur_shape:
            return
        self._cur_shape = shape
        if shape is None:
            glfw.set_cursor(self.win, None)
            return
        if shape not in self._cursors:
            const = {"hand": glfw.HAND_CURSOR, "move": getattr(glfw, "RESIZE_ALL_CURSOR", glfw.HAND_CURSOR),
                     "nwse": getattr(glfw, "RESIZE_NWSE_CURSOR", glfw.CROSSHAIR_CURSOR),
                     "nesw": getattr(glfw, "RESIZE_NESW_CURSOR", glfw.CROSSHAIR_CURSOR)}[shape]
            try:
                self._cursors[shape] = glfw.create_standard_cursor(const)
            except Exception:
                self._cursors[shape] = None
        glfw.set_cursor(self.win, self._cursors[shape])

    def animate(self, dur=0.3, **targets):
        vals = {}
        for k, v in targets.items():
            cur = getattr(self, k)
            if k == "azim":
                v = cur + angle_diff(v, cur)
            if isinstance(cur, np.ndarray):
                vals[k] = (cur.copy(), np.asarray(v, "f8"))
            else:
                vals[k] = (float(cur), float(v))
        self.anim = (time.perf_counter(), dur, vals)
        self.dirty = True

    def _step_anim(self, now):
        t0, dur, vals = self.anim
        t = min(1.0, (now - t0) / dur)
        e = 1 - (1 - t) ** 3
        for k, (a, b) in vals.items():
            if k in ("dist", "zoom2"):
                setattr(self, k, math.exp(math.log(a) + (math.log(b) - math.log(a)) * e))
            else:
                setattr(self, k, a + (b - a) * e)
        if t >= 1.0:
            self.anim = None
            self.azim %= 360.0
        self.dirty = True

    # ------------------------------------------------------------- loading
    def _worker(self):
        while True:
            kind, path, extra = self._jobs.get()
            try:
                data = load_mesh(path) if kind == "mesh" else load_image(path)
                res = (kind, True, path, data, extra)
            except Exception as e:
                res = (kind, False, path, str(e) or e.__class__.__name__, extra)
            with self._lock:
                self._results.append(res)
            glfw.post_empty_event()

    def _enqueue(self, kind, path, extra):
        extra.setdefault("gen", self._gen)
        self._loading_names.append(os.path.basename(path))
        self._jobs.put((kind, path, extra))
        self.dirty = True

    def _process_results(self):
        with self._lock:
            results, self._results = self._results, []
        for kind, ok, path, data, extra in results:
            if self._loading_names:
                self._loading_names.pop(0)
            self.dirty = True
            if extra.get("gen", self._gen) != self._gen:
                continue  # belongs to a board that was closed meanwhile
            batch = extra.get("batch") or {}
            batch["left"] = batch.get("left", 1) - 1
            if not ok:
                self.show_toast(f"Couldn't open {os.path.basename(path)}: {data}")
            elif kind == "mesh":
                self._add_obj(path, extra, data)
            else:
                self._add_img(path, extra, data, batch)
            if batch.get("left", 0) <= 0:
                if kind == "mesh" and batch.get("frame") and self.objs:
                    self.frame_all(animate=False)
                if kind == "image" and batch.get("fit") and self.imgs:
                    self.fit2d(animate=False)
        if results:
            self.update_title()

    def _add_obj(self, path, extra, data):
        v, n, f = data
        o = Obj(path, extra.get("up"), self.ctx, self.p_mesh, self.p_pick, v, n, f)
        first = not self.objs
        if extra.get("offset") is not None:
            o.offset = np.asarray(extra["offset"], "f8")
        else:
            self.place_obj(o)
        self.objs.append(o)
        batch = extra.get("batch") or {}
        if not extra.get("restore"):
            self.sel = o
            self.set_dirty()
        if first and not batch.get("keep_cam"):
            self.frame_all(animate=False)
        elif extra.get("focus") and not extra.get("restore"):
            self.focus_obj(o)

    def place_obj(self, o, after=None):
        lo, hi = o.oriented_bounds()
        others = [p for p in self.objs if p is not o] if after is None else after
        if not others:
            x = -(lo[0] + hi[0]) / 2
        else:
            mx = max(p.world_bounds()[1][0] for p in others)
            size = max(max(p.sphere()[1] for p in others), float(np.linalg.norm(hi - lo)) / 2)
            x = mx + size * 0.25 - lo[0]
        o.offset = np.array([x, -(lo[1] + hi[1]) / 2, -lo[2]])

    def relayout(self):
        placed = []
        for o in self.objs:
            self.place_obj(o, after=placed)
            placed.append(o)
        self.frame_all()
        self.set_dirty()

    def _add_img(self, path, extra, data, batch):
        w, h, raw = data
        im = Img(path, self.ctx, w, h, raw)
        place = extra.get("place")
        if place:
            im.x, im.y = float(place.get("x", 0)), float(place.get("y", 0))
            im.s = float(place.get("s", 1.0)) or 1.0
            im.flip = bool(place.get("flip", False))
        else:
            gap = 20.0
            if "x" not in batch:
                batch["x"], batch["y"] = self.cam2[0], self.cam2[1]
            im.x = batch["x"] + w / 2
            im.y = batch["y"]
            batch["x"] += w + gap
        self.imgs.append(im)
        if not extra.get("restore"):
            self.isel = im
            self.set_dirty()

    def add_paths(self, paths, quiet=False, at_cursor=False, mark=True):
        projects = [p for p in paths if p.lower().endswith(PROJECT_EXT) and os.path.isfile(p)]
        if projects:
            self.open_project(projects[0])
            return
        meshes, images, bad = [], [], set()
        for p in paths:
            p = os.path.abspath(p)
            items = []
            if os.path.isdir(p):
                for root, _dirs, names in os.walk(p):
                    items += [os.path.join(root, n) for n in sorted(names)]
            elif os.path.isfile(p):
                items = [p]
            for it in items:
                ext = os.path.splitext(it)[1].lower()
                if ext in MESH_EXTS:
                    meshes.append(it)
                elif ext in IMG_EXTS:
                    images.append(it)
                elif ext in UNSUPPORTED and not os.path.isdir(p):
                    bad.add(UNSUPPORTED[ext])
        if not meshes and not images:
            if bad:
                self.show_toast(f"{'/'.join(sorted(bad))} not supported - export as OBJ / GLB / PNG")
            elif not quiet:
                self.show_toast("No supported models or images found")
            return
        loaded = {o.path for o in self.objs}
        meshes = [m for m in meshes if m not in loaded]
        if meshes:
            batch = {"left": len(meshes), "frame": not self.objs}
            for i, m in enumerate(meshes):
                self._enqueue("mesh", m, {"batch": batch, "focus": i == len(meshes) - 1 and bool(self.objs),
                                          "restore": not mark})
        if images:
            batch = {"left": len(images), "fit": not self.imgs}
            if at_cursor and self.inside and self.view2d:
                x, y = self.screen_to_board(*self.win_to_fb(*glfw.get_cursor_pos(self.win)))
                batch["x"], batch["y"] = x, y
            for im in images:
                self._enqueue("image", im, {"batch": batch, "restore": not mark})
        if images and not meshes:
            self.set_view2d(True, toast=False)
            self.show_toast(f"Added {len(images)} image{'s' if len(images) > 1 else ''}")
        elif meshes and not images:
            self.set_view2d(False, toast=False)
        elif meshes and images:
            self.show_toast(f"Added {len(meshes)} model(s) and {len(images)} image(s) - Tab to switch")

    def open_dialog(self):
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True)
            pat = " ".join("*" + e for e in (PROJECT_EXT,) + MESH_EXTS + IMG_EXTS)
            paths = filedialog.askopenfilenames(
                parent=root, title=f"Open - {APP}",
                filetypes=[("Boards, models & images", pat), (f"{APP} boards", "*" + PROJECT_EXT),
                           ("3D models", " ".join("*" + e for e in MESH_EXTS)),
                           ("Images", " ".join("*" + e for e in IMG_EXTS)), ("All files", "*.*")])
            root.destroy()
        except Exception as e:
            self.show_toast(f"Open dialog failed: {e}")
            return
        if paths:
            self.add_paths(list(paths))

    def paste(self):
        try:
            from PIL import ImageGrab
            clip = ImageGrab.grabclipboard()
        except Exception as e:
            self.show_toast(f"Clipboard unavailable: {e}")
            return
        if isinstance(clip, list):
            self.add_paths([p for p in clip if isinstance(p, str)])
        elif isinstance(clip, Image.Image):
            os.makedirs(PASTE_DIR, exist_ok=True)
            path = os.path.join(PASTE_DIR, time.strftime("paste_%Y%m%d_%H%M%S") + f"_{int(time.time() * 1000) % 1000:03d}.png")
            clip.save(path)
            self.add_paths([path], at_cursor=True)
        else:
            self.show_toast("Clipboard has no image")

    def update_title(self):
        name = os.path.splitext(os.path.basename(self.project_path))[0] if self.project_path else "Untitled"
        glfw.set_window_title(self.win, f"{name}{'*' if self.project_dirty else ''} - {APP}")

    def set_dirty(self):
        if not self.project_dirty:
            self.project_dirty = True
            self.update_title()

    # ------------------------------------------------------------- boards (.maq)
    def project_dict(self, base_dir=None):
        def rel(p):
            if not base_dir:
                return None
            try:
                return os.path.relpath(p, base_dir)
            except ValueError:  # different drive
                return None
        return {
            "app": APP, "format": 1, "mode": "2d" if self.view2d else "3d",
            "scene": [{"path": o.path, "rel": rel(o.path), "up": o.up, "offset": [round(float(v), 6) for v in o.offset]}
                      for o in self.objs],
            "board": [{"path": im.path, "rel": rel(im.path), "x": round(im.x, 2), "y": round(im.y, 2),
                       "s": round(im.s, 5), "flip": im.flip} for im in self.imgs],
            "cam3d": {"azim": self.azim, "elev": self.elev, "target": [float(v) for v in self.target],
                      "dist": self.dist, "ortho": self.ortho},
            "cam2d": {"cam": [float(v) for v in self.cam2], "zoom": self.zoom2},
            "look": {"color": self.color_idx, "flat": self.flat, "wire": self.wire, "grid": self.grid,
                     "light": [self.light_yaw, self.light_pitch], "gray2d": self.gray2d},
        }

    def clear_all(self):
        self._gen += 1
        for o in self.objs:
            o.release()
        for im in self.imgs:
            im.release()
        self.objs, self.imgs = [], []
        self.sel = self.isel = None
        self.isolate = False
        self.anim = None
        self.dirty = True

    def apply_project(self, d, base_dir):
        self.clear_all()

        def resolve(e):
            p = e.get("path") or ""
            if p and os.path.isfile(p):
                return p
            r = e.get("rel")
            if base_dir and r:
                q = os.path.normpath(os.path.join(base_dir, r))
                if os.path.isfile(q):
                    return q
            return None

        look = d.get("look") or {}
        self.color_idx = int(look.get("color", self.color_idx)) % len(CLAYS)
        self.flat = bool(look.get("flat", self.flat))
        self.wire = bool(look.get("wire", self.wire))
        self.grid = bool(look.get("grid", self.grid))
        self.gray2d = bool(look.get("gray2d", self.gray2d))
        lt = look.get("light")
        if lt:
            self.light_yaw, self.light_pitch = float(lt[0]), float(lt[1])
        c3, c2 = d.get("cam3d"), d.get("cam2d")
        if c3:
            self.azim, self.elev = float(c3.get("azim", 0)), float(c3.get("elev", 0))
            self.target = np.asarray(c3.get("target", [0, 0, 0]), "f8")
            self.dist = float(c3.get("dist", 4.0)) or 4.0
            self.ortho = bool(c3.get("ortho", False))
        if c2:
            self.cam2 = np.asarray(c2.get("cam", [0, 0]), "f8")
            self.zoom2 = float(c2.get("zoom", 1.0)) or 1.0
        missing = 0
        scene = [e for e in d.get("scene") or [] if isinstance(e, dict)]
        board = [e for e in d.get("board") or [] if isinstance(e, dict)]
        mb = {"left": len(scene), "frame": not c3, "keep_cam": bool(c3)}
        for e in scene:
            path = resolve(e)
            if path:
                self._enqueue("mesh", path, {"up": e.get("up"), "offset": e.get("offset"), "restore": True, "batch": mb})
            else:
                missing += 1; mb["left"] -= 1
        ib = {"left": len(board), "fit": not c2}
        for e in board:
            path = resolve(e)
            if path:
                self._enqueue("image", path, {"place": e, "restore": True, "batch": ib})
            else:
                missing += 1; ib["left"] -= 1
        self.view2d = d.get("mode") == "2d"
        self.project_dirty = False
        if missing:
            self.show_toast(f"{missing} referenced file{'s' if missing > 1 else ''} not found")
        self.update_title()

    def ask_save_changes(self):
        """Returns True if it's OK to continue (saved or discarded)."""
        if not self.project_dirty or not (self.objs or self.imgs or self.project_path):
            return True
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True)
            name = os.path.basename(self.project_path) if self.project_path else "this board"
            ans = messagebox.askyesnocancel(APP, f"Save changes to {name}?", parent=root)
            root.destroy()
        except Exception:
            return True
        if ans is None:
            return False
        if ans:
            return self.save_project()
        return True

    def new_project(self):
        if not self.ask_save_changes():
            return
        self.clear_all()
        self.project_path, self.project_dirty = None, False
        self.azim = self.elev = 0.0
        self.cam2, self.zoom2 = np.zeros(2), 1.0
        self.update_title()
        self.show_toast("New board")

    def open_project(self, path, ask=True):
        if ask and not self.ask_save_changes():
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                d = json.load(fh)
        except Exception as e:
            self.show_toast(f"Couldn't open {os.path.basename(path)}: {e}")
            return
        self.apply_project(d, os.path.dirname(os.path.abspath(path)))
        self.project_path = os.path.abspath(path)
        self.project_dirty = False
        self.add_recent(self.project_path)
        self.update_title()
        self.show_toast(f"Opened {os.path.basename(path)}")

    def save_project(self, save_as=False):
        path = self.project_path
        if save_as or not path:
            try:
                import tkinter as tk
                from tkinter import filedialog
                root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True)
                path = filedialog.asksaveasfilename(
                    parent=root, title=f"Save board - {APP}", defaultextension=PROJECT_EXT,
                    initialfile=os.path.basename(path) if path else "Untitled" + PROJECT_EXT,
                    filetypes=[(f"{APP} board", "*" + PROJECT_EXT)])
                root.destroy()
            except Exception as e:
                self.show_toast(f"Save dialog failed: {e}")
                return False
            if not path:
                return False
        try:
            d = self.project_dict(os.path.dirname(os.path.abspath(path)))
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(d, fh, indent=1)
            os.replace(tmp, path)
        except Exception as e:
            self.show_toast(f"Couldn't save: {e}")
            return False
        self.project_path = os.path.abspath(path)
        self.project_dirty = False
        self.add_recent(self.project_path)
        self.update_title()
        self.show_toast(f"Saved {os.path.basename(path)}")
        return True

    def add_recent(self, path):
        self.recent = [path] + [p for p in self.recent if os.path.normcase(p) != os.path.normcase(path)]
        self.recent = self.recent[:8]

    def set_view2d(self, v, toast=True):
        if self.view2d != v:
            self.view2d = v
            self.anim = None
            if toast:
                self.show_toast("2D image board" if v else "3D models")
        self.update_title()
        self.dirty = True

    # ------------------------------------------------------------- 3D camera
    def visible_objs(self):
        if self.isolate and self.sel in self.objs:
            return [self.sel]
        return self.objs

    def scene_sphere(self):
        objs = self.visible_objs()
        if not objs:
            return np.zeros(3), 1.0
        bounds = [o.world_bounds() for o in objs]
        lo = np.min([b[0] for b in bounds], axis=0)
        hi = np.max([b[1] for b in bounds], axis=0)
        return (lo + hi) / 2, max(float(np.linalg.norm(hi - lo)) / 2, 1e-6)

    def view_rot(self):
        return rot_x(self.elev - 90.0) @ rot_z(-self.azim)

    def view_mat(self):
        return translate((0, 0, -self.dist)) @ self.view_rot() @ translate(-self.target)

    def proj_mat(self):
        fw, fh = self.fb
        asp = fw / fh
        t = math.tan(math.radians(FOV) / 2)
        c, r = self.scene_sphere()
        if self.ortho:
            hh = self.dist * t
            far = self.dist + float(np.linalg.norm(self.target - c)) + r * 20
            P = ortho(-hh * asp, hh * asp, -hh, hh, -far, far)
        else:
            cam = self.target + self.view_rot()[2, :3] * self.dist
            dc = float(np.linalg.norm(cam - c))
            near = max(dc - 1.3 * r, r * 0.002, self.dist * 0.01, 1e-6)
            P = perspective(FOV, asp, near, max(dc + 1.5 * r, near * 10))
        if self.mirror:
            P = np.diag([-1.0, 1, 1, 1]) @ P
        return P

    def frame_sphere(self, center, radius, animate=True):
        dist = radius / math.sin(math.radians(FOV) / 2) * 1.08
        if animate:
            self.animate(target=center, dist=dist)
        else:
            self.anim = None
            self.target, self.dist = np.asarray(center, "f8"), dist
        self.dirty = True

    def frame_all(self, animate=True):
        if self.objs:
            self.frame_sphere(*self.scene_sphere(), animate=animate)

    def focus_obj(self, o):
        self.sel = o
        self.frame_sphere(*o.sphere())
        self.update_title()

    def focus3d(self):
        if self.sel in self.objs:
            self.focus_obj(self.sel)
            self.show_toast(f"Focus: {self.sel.name}")
        elif self.objs:
            self.frame_all()

    def snap_axis(self, i, sg):
        az, el = AXIS_VIEWS[(i, sg)]
        if abs(self.elev - el) < 0.5 and (abs(el) >= 90 or abs(angle_diff(self.azim, az)) < 0.5):
            az, el = AXIS_VIEWS[(i, -sg)]
        self.animate(azim=az, elev=el)

    def orbit(self, daz, delv):
        self.anim = None
        self.azim = (self.azim + daz) % 360.0
        self.elev = min(90.0, max(-90.0, self.elev + delv))
        self.dirty = True

    def pan3d(self, dx, dy):
        fh = self.fb[1]
        wpp = 2 * self.dist * math.tan(math.radians(FOV) / 2) / fh
        if self.mirror:
            dx = -dx
        R = self.view_rot()
        self.anim = None
        self.target = self.target - R[0, :3] * dx * wpp + R[1, :3] * dy * wpp
        self.dirty = True

    def zoom3d(self, factor):
        _c, r = self.scene_sphere()
        self.dist = min(max(self.dist * factor, r * 1e-3), r * 1e3)
        self.dirty = True

    def pick(self, px, py):
        """GPU pick at framebuffer pixel. Returns (Obj or None, world point or None)."""
        objs = self.visible_objs()
        if not objs:
            return None, None
        fw, fh = self.fb
        ctx = self.ctx
        if self.pick_fbo is None or self.pick_fbo.size != (fw, fh):
            if self.pick_fbo is not None:
                for a in self.pick_fbo.color_attachments:
                    a.release()
                self.pick_fbo.depth_attachment.release()
                self.pick_fbo.release()
            self.pick_fbo = ctx.framebuffer(color_attachments=[ctx.texture((fw, fh), 4)],
                                            depth_attachment=ctx.depth_texture((fw, fh)))
        fbo = self.pick_fbo
        fbo.use()
        ctx.viewport = (0, 0, fw, fh)
        ctx.disable(moderngl.BLEND)
        ctx.enable(moderngl.DEPTH_TEST)
        fbo.clear(0, 0, 0, 0, depth=1.0)
        P, V = self.proj_mat(), self.view_mat()
        _set(self.p_pick, "u_proj", P)
        for i, o in enumerate(objs):
            oid = i + 1
            _set(self.p_pick, "u_mv", V @ o.matrix())
            _set(self.p_pick, "u_id", ((oid & 255) / 255.0, ((oid >> 8) & 255) / 255.0))
            o.pvao.render(moderngl.TRIANGLES)
        ix, iy = min(max(int(px), 0), fw - 1), min(max(int(fh - 1 - py), 0), fh - 1)
        col = fbo.read(viewport=(ix, iy, 1, 1), components=4)
        depth = float(np.frombuffer(fbo.read(viewport=(ix, iy, 1, 1), attachment=-1, dtype="f4"), "f4")[0])
        ctx.disable(moderngl.DEPTH_TEST)
        ctx.screen.use()
        oid = col[0] + col[1] * 256
        if oid == 0 or oid > len(objs):
            return None, None
        ndc = np.array([(ix + 0.5) / fw * 2 - 1, (iy + 0.5) / fh * 2 - 1, depth * 2 - 1, 1.0])
        wp = np.linalg.inv(P @ V) @ ndc
        return objs[oid - 1], wp[:3] / wp[3]

    def remove_selected(self):
        if self.view2d:
            if self.isel in self.imgs:
                self.imgs.remove(self.isel); self.isel.release()
                self.show_toast(f"Removed {self.isel.name}")
                self.isel = None
                self.set_dirty()
        elif self.sel in self.objs:
            self.objs.remove(self.sel); self.sel.release()
            self.show_toast(f"Removed {self.sel.name}")
            self.sel = None; self.isolate = False
            self.set_dirty()
        self.update_title()

    def cycle(self, d):
        if self.view2d:
            if self.imgs:
                i = (self.imgs.index(self.isel) + d) if self.isel in self.imgs else 0
                self.isel = self.imgs[i % len(self.imgs)]
                self.fit2d(only=self.isel)
        elif self.objs:
            i = (self.objs.index(self.sel) + d) if self.sel in self.objs else 0
            self.focus_obj(self.objs[i % len(self.objs)])
            self.show_toast(f"{self.objs.index(self.sel) + 1}/{len(self.objs)}   {self.sel.name}")
        self.update_title()

    # ------------------------------------------------------------- 2D camera
    def screen_to_board(self, px, py):
        fw, fh = self.fb
        return (px - fw / 2) / self.zoom2 + self.cam2[0], (py - fh / 2) / self.zoom2 + self.cam2[1]

    def board_to_screen(self, x, y):
        fw, fh = self.fb
        return (x - self.cam2[0]) * self.zoom2 + fw / 2, (y - self.cam2[1]) * self.zoom2 + fh / 2

    def zoom2d(self, factor, px=None, py=None):
        fw, fh = self.fb
        if px is None:
            px, py = fw / 2, fh / 2
        bx, by = self.screen_to_board(px, py)
        self.anim = None
        self.zoom2 = min(max(self.zoom2 * factor, 0.01), 50.0)
        ax, ay = self.screen_to_board(px, py)
        self.cam2 = self.cam2 + np.array([bx - ax, by - ay])
        self.dirty = True

    def fit2d(self, only=None, animate=True):
        items = [only] if only else self.imgs
        if not items:
            return
        rs = [im.rect() for im in items]
        x0 = min(r[0] for r in rs); y0 = min(r[1] for r in rs)
        x1 = max(r[0] + r[2] for r in rs); y1 = max(r[1] + r[3] for r in rs)
        s = self.scale
        fw, fh = self.fb
        side = 44 * s  # keep clear of the button column on the right
        z = min((fw - 40 * s - side) / max(x1 - x0, 1), (fh - self.bar_h() - 40 * s) / max(y1 - y0, 1))
        z = min(max(z, 0.01), 50.0)
        cam = np.array([(x0 + x1) / 2 + side / 2 / z, (y0 + y1) / 2 - self.bar_h() / 2 / z])
        if animate:
            self.animate(cam2=cam, zoom2=z)
        else:
            self.anim = None; self.cam2, self.zoom2 = cam, z
        self.dirty = True

    def arrange2d(self):
        if not self.imgs:
            return
        gap = 24.0
        hs = sorted(im.h * im.s for im in self.imgs)
        row_h = hs[len(hs) // 2]  # median height: normalise everything to one row height
        for im in self.imgs:
            im.s = row_h / im.h
        total = sum(im.w * im.s + gap for im in self.imgs)
        target_w = max(math.sqrt(total * (row_h + gap)) * 1.4, row_h)
        x = y = 0.0
        for im in self.imgs:
            w = im.w * im.s
            if x > 0 and x + w > target_w:
                x = 0.0; y += row_h + gap
            im.x, im.y = x + w / 2, y + row_h / 2
            x += w + gap
        self.fit2d()
        self.set_dirty()
        self.show_toast("Arranged")

    def img_at(self, bx, by):
        for im in reversed(self.imgs):
            x, y, w, h = im.rect()
            if x <= bx <= x + w and y <= by <= y + h:
                return im
        return None

    def handles(self):
        if not (self.view2d and self.isel in self.imgs):
            return []
        x, y, w, h = self.isel.rect()
        out = []
        for cx, cy in ((0, 0), (1, 0), (0, 1), (1, 1)):
            sx, sy = self.board_to_screen(x + cx * w, y + cy * h)
            out.append(((cx, cy), sx, sy))
        return out

    # ------------------------------------------------------------- window
    def toggle_max(self):
        if self.max_rect:
            x, y, w, h = self.max_rect
            self.max_rect = None
            glfw.set_window_pos(self.win, int(x), int(y))
            glfw.set_window_size(self.win, int(w), int(h))
        else:
            x, y = glfw.get_window_pos(self.win)
            w, h = glfw.get_window_size(self.win)
            self.max_rect = [x, y, w, h]
            self._apply_max()
        self.dirty = True

    def _apply_max(self):
        x, y, w, h = self.max_rect
        cx, cy = x + w / 2, y + h / 2
        best = None
        for m in glfw.get_monitors() or []:
            mx, my, mw, mh = glfw.get_monitor_workarea(m)
            if mx <= cx < mx + mw and my <= cy < my + mh:
                best = (mx, my, mw, mh)
        if best is None:
            mon = glfw.get_primary_monitor()
            best = glfw.get_monitor_workarea(mon) if mon else (0, 0, 1280, 800)
        mx, my, mw, mh = best
        glfw.set_window_pos(self.win, mx, my)
        glfw.set_window_size(self.win, mw, mh)

    def set_on_top(self, v):
        self.on_top = v
        glfw.set_window_attrib(self.win, glfw.FLOATING, int(v))
        self.show_toast("Always on top" if v else "Normal window")

    def set_borderless(self, v):
        self.borderless = v
        if not v and self.max_rect:
            self.toggle_max()
        glfw.set_window_attrib(self.win, glfw.DECORATED, int(not v))
        self.show_toast("Borderless" if v else "Window frame")

    # ------------------------------------------------------------- layout / hit test
    def bar_h(self):
        return 30 * self.scale

    def bar_items(self):
        s = self.scale
        fw, _ = self.fb
        bh = self.bar_h()
        sh = 20 * s
        items = {"menu": (4 * s, (bh - 24 * s) / 2, 30 * s, 24 * s),
                 "seg3d": (40 * s, (bh - sh) / 2, 34 * s, sh), "seg2d": (74 * s, (bh - sh) / 2, 34 * s, sh)}
        if self.borderless:
            bw = 40 * s
            items["close"] = (fw - bw, 0, bw, bh)
            items["max"] = (fw - 2 * bw, 0, bw, bh)
            items["min"] = (fw - 3 * bw, 0, bw, bh)
        return items

    def menu_entries(self):
        E = []
        def item(label, key="", act=None, check=None):
            E.append({"label": label, "key": key, "act": act, "check": check})
        item("New board", "Ctrl+N", "new")
        item("Open…", "Ctrl+O", "open")
        item("Save", "Ctrl+S", "save")
        item("Save as…", "Ctrl+Shift+S", "saveas")
        E.append(None)
        item("Add models / images…", "O", "add")
        item("Paste image", "Ctrl+V", "paste")
        rec = [p for p in self.recent if os.path.isfile(p)][:5]
        if rec:
            E.append(None)
            E.append({"label": "RECENT BOARDS", "header": True})
            for p in rec:
                item(os.path.splitext(os.path.basename(p))[0], "", ("recent", p))
        E.append(None)
        item("Always on top", "T", "on_top", self.on_top)
        item("Borderless window", "B", "borderless", self.borderless)
        item("Auto-hide UI", "", "autohide", self.autohide)
        if not self.view2d:
            item("Floor grid", "G", "grid", self.grid)
        E.append(None)
        item("Shortcuts", "H", "help")
        item(f"About {APP}", "", "about")
        item("Quit", "Ctrl+Q", "quit")
        return E

    def menu_layout(self):
        s = self.scale
        _fw, fh = self.fb
        entries = self.menu_entries()
        x0, y0 = 4 * s, self.bar_h() + 2 * s
        def build(k):
            size, ih, sep, pad = 12.5 * s * k, 26 * s * k, 9 * s * k, 6 * s * k
            lw = max(self.measure(e["label"], size, e.get("header"))[0] for e in entries if e)
            kw = max([self.measure(e["key"], size * 0.92)[0] for e in entries if e and e.get("key")] or [0])
            W = 30 * s * k + lw + 28 * s * k + kw + 12 * s * k
            rects, y = [], y0 + pad
            for e in entries:
                h = sep if e is None else ih
                rects.append((x0, y, W, h))
                y += h
            return rects, (x0, y0, W, y + pad - y0), size
        rects, panel, size = build(1.0)
        avail = fh - y0 - 6 * s
        if panel[3] > avail:
            rects, panel, size = build(max(0.55, avail / panel[3]))
        return entries, rects, panel, size

    def gizmo(self):
        s = self.scale
        fw, _ = self.fb
        R, br = 40 * s, 10.5 * s
        cx = fw - R - br - 12 * s
        cy = self.bar_h() + R + br + 8 * s
        V = self.view_rot()
        mx = -1.0 if self.mirror else 1.0
        items = []
        for i in range(3):
            col = V[:3, i]
            for sg in (1, -1):
                items.append((col[2] * sg, i, sg, cx + mx * col[0] * sg * R, cy - col[1] * sg * R))
        items.sort(key=lambda t: t[0])  # far -> near
        return cx, cy, R, br, items

    def buttons(self):
        s = self.scale
        fw, fh = self.fb
        r = 13 * s
        x = fw - 12 * s - r
        if self.view2d:
            y = self.bar_h() + 12 * s + r
        else:
            cx, cy, R, br, _ = self.gizmo()
            y = cy + R + br + 14 * s + r
        out = []
        for name, tip in (BUTTONS_2D if self.view2d else BUTTONS_3D):
            if y + r > fh - 22 * s:
                break
            out.append((name, tip, x, y, r))
            y += 2 * r + 6 * s
        return out

    def hit_test(self, x, y):
        s = self.scale
        fw, fh = self.fb
        if self.menu_open:
            entries, rects, (px, py, pw, ph), _size = self.menu_layout()
            if px <= x <= px + pw and py <= y <= py + ph:
                for i, (e, (rx, ry, rw, rh)) in enumerate(zip(entries, rects)):
                    if e and e.get("act") and ry <= y < ry + rh:
                        return ("menu", i)
                return ("menu_bg",)
        if self.inside:
            if y <= self.bar_h():
                for name, (bx, by, bw, bh) in self.bar_items().items():
                    if bx <= x <= bx + bw and by <= y <= by + bh:
                        return ("ui", name)
                if self.borderless:
                    return ("bar",)
            if self.borderless and not self.max_rect and x >= fw - 18 * s and y >= fh - 18 * s:
                return ("grip",)
        if not self.view2d:
            cx, cy, R, br, items = self.gizmo()
            for _dz, i, sg, bx, by in reversed(items):
                if math.hypot(x - bx, y - by) <= br + 1:
                    return ("bubble", i, sg)
            if math.hypot(x - cx, y - cy) <= R + br + 4 * s:
                return ("gizmo",)
        if self.inside:
            for name, _tip, bx, by, r in self.buttons():
                if math.hypot(x - bx, y - by) <= r + 2:
                    return ("btn", name)
        if self.view2d:
            for corner, hx, hy in self.handles():
                if abs(x - hx) <= 7 * s and abs(y - hy) <= 7 * s:
                    return ("handle", corner)
            im = self.img_at(*self.screen_to_board(x, y))
            if im:
                return ("img", im)
        return None

    # ------------------------------------------------------------- input
    def _mods(self):
        k = lambda a, b: glfw.get_key(self.win, a) == glfw.PRESS or glfw.get_key(self.win, b) == glfw.PRESS
        return (k(glfw.KEY_LEFT_SHIFT, glfw.KEY_RIGHT_SHIFT), k(glfw.KEY_LEFT_CONTROL, glfw.KEY_RIGHT_CONTROL),
                k(glfw.KEY_LEFT_ALT, glfw.KEY_RIGHT_ALT))

    def on_enter(self, _w, entered):
        self.inside = bool(entered)
        if not entered:
            self.left_t = time.perf_counter()
        if not entered and not self.drag:
            self.hover = None
            self.set_cursor(None)
        self.dirty = True

    def on_mouse(self, win, button, action, mods):
        wx, wy = glfw.get_cursor_pos(win)
        fx, fy = self.win_to_fb(wx, wy)
        hit = self.hit_test(fx, fy)
        shift, ctrl, alt = mods & glfw.MOD_SHIFT, mods & glfw.MOD_CONTROL, mods & glfw.MOD_ALT
        if action == glfw.PRESS:
            if self.drag:
                return
            if self.menu_open and (not hit or hit[0] not in ("menu", "menu_bg") and hit != ("ui", "menu")):
                self.menu_open = False      # click outside closes the menu and is swallowed
                self.dirty = True
                return
            now = time.perf_counter()
            lt, lx, ly, lb = self._last_click
            dbl = now - lt < DBL_T and math.hypot(wx - lx, wy - ly) < DBL_PX and lb == button
            self._last_click = (0.0 if dbl else now, wx, wy, button)
            self.press = self.last = (wx, wy)
            self.press_hit = hit
            self.moved = False
            kind = hit[0] if hit else None
            drag = None
            if button == glfw.MOUSE_BUTTON_LEFT:
                if kind in ("ui", "menu"):
                    drag = kind
                elif kind == "menu_bg":
                    drag = None
                elif kind == "bar":
                    if dbl:
                        self.toggle_max()
                    elif not self.max_rect:
                        drag = "move"
                elif alt and kind not in ("btn", "grip"):
                    # Blender "Emulate 3 Button Mouse" (pen / tablet friendly)
                    if self.view2d:
                        drag = "zoom" if ctrl else "pan"
                    else:
                        drag = "pan" if shift else "zoom" if ctrl else "orbit"
                elif kind == "grip":
                    drag = "resize"; self.press_size = glfw.get_window_size(win)
                elif kind == "btn":
                    drag = hit[1] if hit[1] in ("zoom", "pan", "light") else "btn"
                elif self.view2d:
                    drag = self._press2d(hit, dbl, shift, fx, fy)
                else:
                    drag = self._press3d(hit, dbl, shift, ctrl, fx, fy)
            elif button == glfw.MOUSE_BUTTON_MIDDLE:
                if alt and not self.view2d:
                    drag = "pivot"          # Blender: Alt+Middle click = center view to mouse
                elif self.view2d:
                    drag = "zoom" if ctrl else "pan"
                else:
                    drag = "pan" if shift else "zoom" if ctrl else "orbit"
            elif button == glfw.MOUSE_BUTTON_RIGHT:
                drag = "pan"
            if drag in ("orbit", "pan", "zoom", "bubble", "click3d", "light", "move_img", "scale"):
                self.anim = None
            self.drag, self.drag_btn = drag, button
        elif action == glfw.RELEASE and button == self.drag_btn:
            d = self.drag
            if d == "ui" and hit == self.press_hit:
                self.press_ui(hit[1])
            elif d == "menu" and hit == self.press_hit:
                self.menu_open = False
                entries = self.menu_entries()
                if 0 <= hit[1] < len(entries) and entries[hit[1]]:
                    self.menu_action(entries[hit[1]]["act"])
            elif d == "pivot":
                if self.moved:
                    pass
                else:
                    o, pt = self.pick(fx, fy)
                    if o is not None:
                        self.animate(target=pt)
            elif d in ("move_img", "scale") and self.moved:
                self.set_dirty()
            elif d == "bubble" and not self.moved and hit == self.press_hit:
                self.snap_axis(hit[1], hit[2])
            elif d == "btn" and hit == self.press_hit:
                self.press_button(hit[1])
            elif d == "click3d" and not self.moved:
                o, _p = self.pick(fx, fy)
                if o is not self.sel:
                    self.sel = o
                    self.update_title()
            self.drag = self.drag_btn = None
            self.hover = hit
            self._update_cursor(hit)
        self.dirty = True

    def _press3d(self, hit, dbl, shift, ctrl, fx, fy):
        kind = hit[0] if hit else None
        if kind == "bubble":
            return "bubble"
        if kind == "gizmo":
            return "orbit"
        if shift:
            return "pan"
        if ctrl:
            return "zoom"
        if glfw.get_key(self.win, glfw.KEY_L) == glfw.PRESS:
            return "light"
        if dbl:
            o, p = self.pick(fx, fy)
            if o is not None:
                self.sel = o
                self.animate(target=p)
                self.update_title()
            else:
                self.frame_all()
            return None
        return "click3d"

    def _press2d(self, hit, dbl, shift, fx, fy):
        kind = hit[0] if hit else None
        if kind == "handle" and self.isel:
            im = self.isel
            x, y, w, h = im.rect()
            cx, cy = hit[1]
            corner = np.array([x + cx * w, y + cy * h])
            anchor = np.array([x + (1 - cx) * w, y + (1 - cy) * h])
            self.press_data = (anchor, corner, im.s)
            return "scale"
        if kind == "img":
            im = hit[1]
            self.isel = im
            self.imgs.remove(im); self.imgs.append(im)  # bring to front
            self.update_title()
            if dbl:
                self.fit2d(only=im)
                return None
            return "move_img"
        if dbl:
            self.fit2d()
            return None
        if self.isel is not None:
            self.isel = None
            self.update_title()
        return "pan"

    def menu_action(self, act):
        if isinstance(act, tuple) and act[0] == "recent":
            self.open_project(act[1])
        elif act == "new":
            self.new_project()
        elif act == "open":
            self._want_open = True
        elif act == "save":
            self._want_save = 1
        elif act == "saveas":
            self._want_save = 2
        elif act == "add":
            self._want_open = True
        elif act == "paste":
            self._want_paste = True
        elif act == "on_top":
            self.set_on_top(not self.on_top)
        elif act == "borderless":
            self.set_borderless(not self.borderless)
        elif act == "autohide":
            self.autohide = not self.autohide
            self.show_toast("UI auto-hides when the cursor leaves" if self.autohide else "UI always visible")
        elif act == "grid":
            self.grid = not self.grid
        elif act == "help":
            self.help = True
        elif act == "about":
            self.show_toast(f"{APP} {VERSION}   ·   data: {DATA_DIR}")
        elif act == "quit":
            glfw.set_window_should_close(self.win, True)
        self.dirty = True

    def press_ui(self, name):
        if name == "menu":
            self.menu_open = not self.menu_open
            self.help = False
            return
        if name == "seg3d":
            self.set_view2d(False)
        elif name == "seg2d":
            self.set_view2d(True)
        elif name == "min":
            glfw.iconify_window(self.win)
        elif name == "max":
            self.toggle_max()
        elif name == "close":
            glfw.set_window_should_close(self.win, True)

    def press_button(self, name):
        if name == "focus":
            self.do_focus()
        elif name == "ortho":
            self.toggle_ortho()
        elif name == "spin":
            self.toggle_spin()
        elif name == "pin":
            self.set_on_top(not self.on_top)
        elif name == "help":
            self.help = not self.help
        elif name == "gray":
            self.gray2d = not self.gray2d
            self.show_toast("Grayscale on" if self.gray2d else "Grayscale off")
        elif name == "flip":
            self.flip2d()
        elif name == "arrange":
            self.arrange2d()

    def do_focus(self):
        if self.view2d:
            self.fit2d(only=self.isel if self.isel in self.imgs else None)
        else:
            self.focus3d()

    def flip2d(self):
        targets = [self.isel] if self.isel in self.imgs else self.imgs
        for im in targets:
            im.flip = not im.flip
        if targets:
            self.set_dirty()
            self.show_toast("Flipped" if len(targets) == 1 else "Flipped all images")

    def _update_cursor(self, hit):
        kind = hit[0] if hit else None
        if kind in ("ui", "btn", "bubble"):
            self.set_cursor("hand")
        elif kind == "grip":
            self.set_cursor("nwse")
        elif kind == "handle":
            self.set_cursor("nwse" if hit[1] in ((0, 0), (1, 1)) else "nesw")
        elif kind == "img":
            self.set_cursor("move")
        else:
            self.set_cursor(None)

    def on_cursor(self, win, wx, wy):
        if not self.drag:
            hit = self.hit_test(*self.win_to_fb(wx, wy))
            if hit != self.hover:
                self.hover = hit
                self._update_cursor(hit)
                self.dirty = True
            return
        dx, dy = wx - self.last[0], wy - self.last[1]
        self.last = (wx, wy)
        if math.hypot(wx - self.press[0], wy - self.press[1]) > DRAG_PX:
            self.moved = True
        d = self.drag
        if d == "pivot":
            if not self.moved:
                return
            self.drag = d = "orbit"
            dx, dy = wx - self.press[0], wy - self.press[1]
        if d in ("bubble", "click3d"):
            if not self.moved:
                return
            self.drag = d = "orbit"
            dx, dy = wx - self.press[0], wy - self.press[1]
        ratio = self.fb_ratio()
        if d == "orbit":
            self.orbit((dx if self.mirror else -dx) * 0.4, dy * 0.4)
        elif d == "light":
            self.light_yaw = (self.light_yaw + dx * 0.5) % 360.0
            self.light_pitch = min(85.0, max(-85.0, self.light_pitch + dy * 0.5))
            self.dirty = True
        elif d == "pan":
            if self.view2d:
                self.anim = None
                self.cam2 = self.cam2 - np.array([dx, dy]) * ratio / self.zoom2
                self.dirty = True
            else:
                self.pan3d(dx * ratio, dy * ratio)
        elif d == "zoom":
            if self.view2d:
                self.zoom2d(math.exp(-dy * 0.006))
            else:
                self.zoom3d(math.exp(dy * 0.006))
        elif d == "move_img" and self.isel:
            if not self.moved:
                return
            self.isel.x += dx * ratio / self.zoom2
            self.isel.y += dy * ratio / self.zoom2
            self.dirty = True
        elif d == "scale" and self.isel:
            anchor, corner, s0 = self.press_data
            p = np.array(self.screen_to_board(*self.win_to_fb(wx, wy)))
            k = float(np.linalg.norm(p - anchor) / max(np.linalg.norm(corner - anchor), 1e-6))
            im = self.isel
            im.s = max(s0 * k, 8.0 / max(im.w, im.h))
            sign = np.sign(corner - anchor)
            im.x = anchor[0] + sign[0] * im.w * im.s / 2
            im.y = anchor[1] + sign[1] * im.h * im.s / 2
            self.dirty = True
        elif d == "move":
            px, py = glfw.get_window_pos(win)
            glfw.set_window_pos(win, int(px + wx - self.press[0]), int(py + wy - self.press[1]))
            self.last = self.press
        elif d == "resize":
            nw = max(200, int(self.press_size[0] + wx - self.press[0]))
            nh = max(160, int(self.press_size[1] + wy - self.press[1]))
            glfw.set_window_size(win, nw, nh)
            self.dirty = True

    def on_scroll(self, win, _xo, yo):
        shift, ctrl, _alt = self._mods()
        if ctrl:
            self.opacity = min(1.0, max(0.15, self.opacity + yo * 0.05))
            glfw.set_window_opacity(self.win, self.opacity)
            self.show_toast(f"Opacity {round(self.opacity * 100)}%")
        elif self.view2d:
            fx, fy = self.win_to_fb(*glfw.get_cursor_pos(win))
            if shift and self.isel in self.imgs:
                self.isel.s = max(self.isel.s * (1.1 ** yo), 8.0 / max(self.isel.w, self.isel.h))
                self.set_dirty()
                self.dirty = True
            else:
                self.zoom2d(1.15 ** yo, fx, fy)
        else:
            self.anim = None
            self.zoom3d(0.88 ** yo)

    def on_key(self, win, key, _sc, action, mods):
        if action not in (glfw.PRESS, glfw.REPEAT):
            return
        ctrl, shift = mods & glfw.MOD_CONTROL, mods & glfw.MOD_SHIFT
        K = glfw
        # ---- common
        if self.menu_open and key == K.KEY_ESCAPE:
            self.menu_open = False
        elif ctrl and key in (K.KEY_Q, K.KEY_W):
            glfw.set_window_should_close(win, True)
        elif ctrl and key == K.KEY_S:
            self._want_save = 2 if shift else 1
        elif ctrl and key == K.KEY_O:
            self._want_open = True
        elif ctrl and key == K.KEY_N:
            self._want_new = True
        elif ctrl and key == K.KEY_V:
            self._want_paste = True
        elif ctrl and key == K.KEY_L:
            self.light_yaw = self.light_pitch = 0.0
            self.show_toast("Light reset")
        elif key == K.KEY_O:
            self._want_open = True
        elif key in (K.KEY_H, K.KEY_F1):
            self.help = not self.help
        elif key == K.KEY_ESCAPE:
            if self.help:
                self.help = False
            elif self.view2d:
                self.isel = None
            else:
                self.sel = None; self.isolate = False
        elif key == K.KEY_TAB:
            self.set_view2d(not self.view2d)
        elif key == K.KEY_T:
            self.set_on_top(not self.on_top)
        elif key == K.KEY_B:
            self.set_borderless(not self.borderless)
        elif key in (K.KEY_DELETE, K.KEY_BACKSPACE):
            self.remove_selected()
        elif key == K.KEY_F:
            self.do_focus()
        elif key == K.KEY_HOME:
            self.fit2d() if self.view2d else self.frame_all()
        elif key == K.KEY_PAGE_DOWN:
            self.cycle(1)
        elif key == K.KEY_PAGE_UP:
            self.cycle(-1)
        elif key in (K.KEY_KP_ADD, K.KEY_EQUAL):
            self.zoom2d(1.2) if self.view2d else self.zoom3d(0.85)
        elif key in (K.KEY_KP_SUBTRACT, K.KEY_MINUS):
            self.zoom2d(1 / 1.2) if self.view2d else self.zoom3d(1 / 0.85)
        elif self.view2d:
            self._key2d(key)
        else:
            self._key3d(key, ctrl, shift)
        self.dirty = True

    def _key2d(self, key):
        K = glfw
        if key == K.KEY_M:
            self.flip2d()
        elif key == K.KEY_K:
            self.press_button("gray")
        elif key == K.KEY_A:
            self.arrange2d()

    def _key3d(self, key, ctrl, shift):
        K = glfw
        if ctrl and key in (K.KEY_2, K.KEY_4, K.KEY_6, K.KEY_8, K.KEY_KP_2, K.KEY_KP_4, K.KEY_KP_6, K.KEY_KP_8):
            # Blender: Ctrl+Numpad 2/4/6/8 pans the view
            step = self.fb[1] * 0.1
            dx = {K.KEY_4: -1, K.KEY_KP_4: -1, K.KEY_6: 1, K.KEY_KP_6: 1}.get(key, 0) * step
            dy = {K.KEY_8: -1, K.KEY_KP_8: -1, K.KEY_2: 1, K.KEY_KP_2: 1}.get(key, 0) * step
            self.pan3d(-dx, -dy)
        elif key in (K.KEY_9, K.KEY_KP_9):
            self.animate(azim=self.azim + 180, elev=-self.elev)
        elif shift and key == K.KEY_C:
            self.frame_all()
        elif key in (K.KEY_1, K.KEY_KP_1):
            self.animate(azim=180 if ctrl else 0, elev=0)
        elif key in (K.KEY_3, K.KEY_KP_3):
            self.animate(azim=-90 if ctrl else 90, elev=0)
        elif key in (K.KEY_7, K.KEY_KP_7):
            self.animate(azim=0, elev=-90 if ctrl else 90)
        elif key in (K.KEY_5, K.KEY_KP_5):
            self.toggle_ortho()
        elif key in (K.KEY_4, K.KEY_KP_4):
            self.animate(azim=self.azim - 15, elev=self.elev)
        elif key in (K.KEY_6, K.KEY_KP_6):
            self.animate(azim=self.azim + 15, elev=self.elev)
        elif key in (K.KEY_8, K.KEY_KP_8):
            self.animate(azim=self.azim, elev=min(90, self.elev + 15))
        elif key in (K.KEY_2, K.KEY_KP_2):
            self.animate(azim=self.azim, elev=max(-90, self.elev - 15))
        elif key in (K.KEY_SLASH, K.KEY_KP_DIVIDE):
            if self.isolate:
                self.isolate = False
                self.show_toast("Show all")
                self.frame_all()
            elif self.sel in self.objs:
                self.isolate = True
                self.show_toast(f"Isolate: {self.sel.name}")
                self.focus_obj(self.sel)
            else:
                self.show_toast("Click a model to select it first")
        elif key == K.KEY_KP_DECIMAL:
            self.focus3d()
        elif key == K.KEY_R:
            self.animate(azim=0, elev=0)
            self.frame_all()
            self.anim[2]["azim"] = (self.azim, self.azim + angle_diff(0, self.azim))
            self.anim[2]["elev"] = (self.elev, 0.0)
        elif key == K.KEY_SPACE:
            if shift:
                self.spin_speed = -self.spin_speed
                self.spin = True
                self.show_toast(f"Spin {self.spin_speed:+.0f}°/s")
            else:
                self.toggle_spin()
        elif key in (K.KEY_LEFT_BRACKET, K.KEY_RIGHT_BRACKET):
            sign = 1 if self.spin_speed >= 0 else -1
            mag = abs(self.spin_speed) + (10 if key == K.KEY_RIGHT_BRACKET else -10)
            self.spin_speed = sign * min(180, max(5, mag))
            self.show_toast(f"Spin {self.spin_speed:+.0f}°/s")
        elif key == K.KEY_S:
            self.flat = not self.flat
            self.show_toast("Flat shading" if self.flat else "Smooth shading")
        elif key == K.KEY_K:
            self.sil = not self.sil
            self.show_toast("Silhouette" if self.sil else "Shaded")
        elif key == K.KEY_M:
            self.mirror = not self.mirror
            self.show_toast("Mirrored view" if self.mirror else "Normal view")
        elif key == K.KEY_W:
            self.wire = not self.wire
            self.show_toast("Wireframe on" if self.wire else "Wireframe off")
        elif key == K.KEY_C:
            self.color_idx = (self.color_idx + 1) % len(CLAYS)
            self.show_toast(CLAYS[self.color_idx][0])
        elif key == K.KEY_G:
            self.grid = not self.grid
            self.show_toast("Grid on" if self.grid else "Grid off")
        elif key == K.KEY_U:
            o = self.sel if self.sel in self.objs else (self.objs[0] if len(self.objs) == 1 else None)
            if o:
                cx = o.sphere()[0][0]
                o.up = "Z" if o.up == "Y" else "Y"
                lo, hi = o.oriented_bounds()
                o.offset = np.array([cx - (lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -lo[2]])
                self.focus_obj(o)
                self.set_dirty()
                self.show_toast(f"{o.name}: {o.up}-up")
            else:
                self.show_toast("Click a model to select it first")
        elif key == K.KEY_A:
            self.relayout()
            self.show_toast("Models lined up")

    def toggle_ortho(self):
        self.ortho = not self.ortho
        self.show_toast("Orthographic" if self.ortho else "Perspective")

    def toggle_spin(self):
        self.spin = not self.spin
        self.show_toast(f"Spin {self.spin_speed:+.0f}°/s" if self.spin else "Spin off")

    # ------------------------------------------------------------- 2D drawing primitives
    def _font(self, size, bold):
        key = (size, bold)
        if key not in self._fonts:
            names = (["segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf", "Arial Bold.ttf"] if bold
                     else ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf", "Arial.ttf"])
            font = None
            for n in names:
                try:
                    font = ImageFont.truetype(n, size); break
                except Exception:
                    pass
            if font is None:
                try:
                    font = ImageFont.load_default(size)
                except Exception:
                    font = ImageFont.load_default()
            self._fonts[key] = font
        return self._fonts[key]

    def measure(self, s, size, bold=False):
        size = max(6, int(round(size)))
        font = self._font(size, bold)
        try:
            asc, desc = font.getmetrics()
        except Exception:
            asc, desc = size, size // 4
        return font.getlength(s) + 2, asc + desc + 2

    def _text_tex(self, s, size, bold):
        key = (s, size, bold)
        t = self._texts.get(key)
        if t:
            return t
        if len(self._texts) > 500:
            for tex, *_ in self._texts.values():
                tex.release()
            self._texts.clear()
        w, h = self.measure(s, size, bold)
        w, h = max(1, int(math.ceil(w))), max(1, int(h))
        img = Image.new("L", (w, h), 0)
        ImageDraw.Draw(img).text((1, 1), s, font=self._font(size, bold), fill=255)
        tex = self.ctx.texture((w, h), 1, img.tobytes(), alignment=1)
        tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        t = (tex, w, h, img.getbbox() or (0, 0, w, h))
        self._texts[key] = t
        return t

    def text(self, s, x, y, size, color, bold=False, anchor="lt"):
        if not s:
            return
        tex, w, h, bb = self._text_tex(s, max(6, int(round(size))), bold)
        x0 = x if anchor[0] == "l" else x - w if anchor[0] == "r" else x - (bb[0] + bb[2]) / 2
        y0 = y if anchor[1] == "t" else y - h if anchor[1] == "b" else y - (bb[1] + bb[3]) / 2
        tex.use(0)
        p = self.p_text
        _set(p, "u_tex", 0)
        _set(p, "u_rect", (float(round(x0)), float(round(y0)), float(w), float(h)))
        _set(p, "u_screen", tuple(float(v) for v in self.fb))
        _set(p, "u_color", (float(color[0]), float(color[1]), float(color[2]), float(color[3]) * self._amul))
        self.vao_text.render(moderngl.TRIANGLE_STRIP)

    def _shape(self, typ, a, b, color, rect):
        p = self.p_shape
        _set(p, "u_type", typ)
        _set(p, "u_a", tuple(float(v) for v in a))
        _set(p, "u_b", tuple(float(v) for v in b))
        _set(p, "u_color", (float(color[0]), float(color[1]), float(color[2]), float(color[3]) * self._amul))
        _set(p, "u_rect", tuple(float(v) for v in rect))
        _set(p, "u_screen", tuple(float(v) for v in self.fb))
        self.vao_shape.render(moderngl.TRIANGLE_STRIP)

    def circle(self, x, y, r, c):
        self._shape(0, (x, y, r, 0), (0, 0, 0, 0), c, (x - r - 2, y - r - 2, 2 * r + 4, 2 * r + 4))

    def ring(self, x, y, r, w, c):
        R = r + w
        self._shape(1, (x, y, r, w), (0, 0, 0, 0), c, (x - R - 2, y - R - 2, 2 * R + 4, 2 * R + 4))

    def seg(self, x0, y0, x1, y1, w, c):
        r = w / 2
        self._shape(2, (x0, y0, x1, y1), (r, 0, 0, 0), c,
                    (min(x0, x1) - r - 2, min(y0, y1) - r - 2, abs(x1 - x0) + 2 * r + 4, abs(y1 - y0) + 2 * r + 4))

    def rrect(self, x, y, w, h, r, c, outline=0.0):
        self._shape(3, (x + w / 2, y + h / 2, w / 2, h / 2), (min(r, w / 2, h / 2), outline, 0, 0), c,
                    (x - 2, y - 2, w + 4, h + 4))

    def fit(self, s, size, maxw, bold=False):
        if self.measure(s, size, bold)[0] <= maxw:
            return s
        while len(s) > 4 and self.measure(s + "…", size, bold)[0] > maxw:
            s = s[:-1]
        return s + "…"

    # ------------------------------------------------------------- rendering
    def render(self):
        fw, fh = self.fb
        ctx = self.ctx
        ctx.screen.use()
        ctx.viewport = (0, 0, fw, fh)
        ctx.disable(moderngl.DEPTH_TEST | moderngl.BLEND | moderngl.CULL_FACE)
        ctx.clear(0.2, 0.2, 0.2, 1.0, depth=1.0)
        if self.view2d:
            _set(self.p_bg, "u_top", (0.19, 0.19, 0.20)); _set(self.p_bg, "u_bot", (0.15, 0.15, 0.16))
        else:
            _set(self.p_bg, "u_top", (0.235, 0.235, 0.235)); _set(self.p_bg, "u_bot", (0.165, 0.165, 0.165))
        self.vao_bg.render(moderngl.TRIANGLES, vertices=3)
        ctx.enable(moderngl.BLEND)
        ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA
        if self.view2d:
            self.render2d()
        else:
            self.render3d()
        ctx.disable(moderngl.DEPTH_TEST)
        ctx.enable(moderngl.BLEND)
        self.draw_overlay()
        glfw.swap_buffers(self.win)

    def render3d(self):
        ctx = self.ctx
        objs = self.visible_objs()
        if not objs:
            return
        P, V = self.proj_mat(), self.view_mat()
        c, r = self.scene_sphere()
        if self.grid and not self.sil:
            step = 10.0 ** math.floor(math.log10(max(r, 1e-6)))
            size = max(r * 30, self.dist * 30)
            g = self.p_grid
            _set(g, "u_vp", P @ V)
            _set(g, "u_center", (float(self.target[0]), float(self.target[1])))
            _set(g, "u_size", float(size))
            _set(g, "u_step", float(step))
            _set(g, "u_fade", float(max(r * 6, self.dist * 2.5)))
            self.vao_grid.render(moderngl.TRIANGLE_STRIP)
        ctx.disable(moderngl.BLEND)
        ctx.enable(moderngl.DEPTH_TEST)
        p = self.p_mesh
        lrot = (rot_y(self.light_yaw) @ rot_x(self.light_pitch))[:3, :3]
        _set(p, "u_proj", P)
        _set(p, "u_lrot", lrot)
        _set(p, "u_color", CLAYS[self.color_idx][1])
        _set(p, "u_flat", int(self.flat))
        _set(p, "u_ortho", int(self.ortho))
        _set(p, "u_sil", int(self.sil))
        _set(p, "u_mirror", int(self.mirror))
        show_sel = len(objs) > 1
        passes = [0, 1] if self.wire and not self.sil else [0]
        for wire in passes:
            if wire:
                ctx.enable(moderngl.BLEND)
                ctx.wireframe = True
            _set(p, "u_wire", wire)
            _set(p, "u_bias", 0.0004 if wire else 0.0)
            for o in objs:
                MV = V @ o.matrix()
                _set(p, "u_mv", MV)
                _set(p, "u_nm", MV[:3, :3])
                _set(p, "u_sel", 1.0 if (show_sel and o is self.sel) else 0.0)
                o.vao.render(moderngl.TRIANGLES)
            ctx.wireframe = False

    def render2d(self):
        s = self.scale
        fw, fh = self.fb
        p = self.p_img
        _set(p, "u_tex", 0)
        _set(p, "u_screen", (float(fw), float(fh)))
        _set(p, "u_gray", int(self.gray2d))
        for im in self.imgs:
            x, y, w, h = im.rect()
            sx, sy = self.board_to_screen(x, y)
            sw, sh = w * self.zoom2, h * self.zoom2
            if sx > fw or sy > fh or sx + sw < 0 or sy + sh < 0:
                continue
            im.tex.use(0)
            _set(p, "u_flip", int(im.flip))
            _set(p, "u_rect", (float(sx), float(sy), float(sw), float(sh)))
            self.vao_img.render(moderngl.TRIANGLE_STRIP)
        hov = self.hover[1] if self.hover and self.hover[0] == "img" else None
        if hov is not None and hov is not self.isel and self.inside:
            x, y, w, h = hov.rect()
            sx, sy = self.board_to_screen(x, y)
            self.rrect(sx, sy, w * self.zoom2, h * self.zoom2, 0, (1, 1, 1, 0.35), outline=1.2 * s)
        if self.isel in self.imgs:
            x, y, w, h = self.isel.rect()
            sx, sy = self.board_to_screen(x, y)
            self.rrect(sx - 1, sy - 1, w * self.zoom2 + 2, h * self.zoom2 + 2, 0, (*ACCENT, 1.0), outline=2 * s)
            for _c, hx, hy in self.handles():
                self.rrect(hx - 4.5 * s, hy - 4.5 * s, 9 * s, 9 * s, 1.5 * s, (1, 1, 1, 1))
                self.rrect(hx - 4.5 * s, hy - 4.5 * s, 9 * s, 9 * s, 1.5 * s, (*ACCENT, 1.0), outline=1.5 * s)

    def draw_overlay(self):
        s = self.scale
        fw, fh = self.fb
        now = time.perf_counter()
        empty = not (self.imgs if self.view2d else self.objs)
        ua = self.ui_a if self.autohide else 1.0
        dim = 0.3 if (self.drag in VIEW_DRAGS and not self.view2d) else 1.0

        self._amul = 1.0
        if self._loading_names:
            name = self._loading_names[0]
            more = len(self._loading_names) - 1
            label = "Loading  " + name + (f"   (+{more})" if more else "")
            if empty:
                cx, cy = fw / 2, fh / 2
                for k in range(8):
                    a = k / 8 * math.tau
                    ph = (now * 1.4 - k / 8) % 1.0
                    self.circle(cx + math.cos(a) * 14 * s, cy + math.sin(a) * 14 * s, 2.6 * s, (1, 1, 1, 0.15 + 0.7 * (1 - ph)))
                self.text(self.fit(label, 12 * s, fw - 30 * s), cx, cy + 34 * s, 12 * s, (1, 1, 1, 0.6), anchor="mm")
            else:
                size = 11.5 * s
                label = self.fit(label, size, fw * 0.6)
                tw, th = self.measure(label, size)
                x, y = 10 * s, fh - th - 22 * s
                self.rrect(x, y, tw + 34 * s, th + 10 * s, (th + 10 * s) / 2, (0.07, 0.07, 0.08, 0.8))
                ph = now * 4
                self.ring(x + 14 * s, y + (th + 10 * s) / 2, 5 * s, 1.6 * s, (1, 1, 1, 0.2))
                self.circle(x + 14 * s + math.cos(ph) * 5 * s, y + (th + 10 * s) / 2 + math.sin(ph) * 5 * s, 2 * s, (1, 1, 1, 0.9))
                self.text(label, x + 26 * s, y + (th + 10 * s) / 2, size, (1, 1, 1, 0.75), anchor="lm")
        elif empty:
            self.draw_empty()

        if ua > 0.01:
            self._amul = ua * dim
            if not empty and not self.help:
                self.draw_hud()
        if not self.view2d and ua > 0.01:
            self._amul = ua
            self.draw_gizmo()
        if ua > 0.01:
            self._amul = ua * dim
            self.draw_buttons()
            self.draw_bar()
            if self.borderless and not self.max_rect:
                self.draw_grip()
        self._amul = 1.0
        if self.menu_open:
            self.draw_menu()
        if self.help:
            self.draw_help()
        self.draw_toast(now)

    def draw_empty(self):
        s = self.scale
        fw, fh = self.fb
        cy = fh / 2 + 10 * s
        if self.logo is not None and fh > 260 * s:
            sz = 84 * s
            self.logo.use(0)
            p = self.p_img
            _set(p, "u_tex", 0); _set(p, "u_gray", 0); _set(p, "u_flip", 0)
            _set(p, "u_screen", (float(fw), float(fh)))
            _set(p, "u_rect", (float(fw / 2 - sz / 2), float(cy - sz - 34 * s), float(sz), float(sz)))
            self.vao_img.render(moderngl.TRIANGLE_STRIP)
        if self.view2d:
            t1, t2 = "Drop or paste (Ctrl+V) images here", "PNG · JPG · WEBP · BMP · GIF · TIFF"
        else:
            t1, t2 = "Drop a 3D model here", "OBJ · STL · PLY · GLB · GLTF"
        self.text(self.fit(t1, 16 * s, fw - 30 * s), fw / 2, cy, 16 * s, (1, 1, 1, 0.78), anchor="mm")
        self.text(self.fit(t2, 11.5 * s, fw - 30 * s), fw / 2, cy + 24 * s, 11.5 * s, (1, 1, 1, 0.38), anchor="mm")
        self.text(self.fit("Ctrl+O open   ·   H shortcuts", 11 * s, fw - 30 * s), fw / 2, cy + 44 * s,
                  11 * s, (1, 1, 1, 0.38), anchor="mm")
        other = self.objs if self.view2d else self.imgs
        if other:
            self.text(f"Tab = back to {'3D' if self.view2d else '2D'} ({len(other)})", fw / 2, cy + 68 * s,
                      11 * s, (*ACCENT, 0.95), anchor="mm")

    def draw_menu(self):
        s = self.scale
        entries, rects, (px, py, pw, ph), size = self.menu_layout()
        self.rrect(px + 2 * s, py + 3 * s, pw, ph, 8 * s, (0, 0, 0, 0.35))
        self.rrect(px, py, pw, ph, 8 * s, (0.13, 0.13, 0.145, 0.98))
        self.rrect(px, py, pw, ph, 8 * s, (1, 1, 1, 0.08), outline=1 * s)
        for i, (e, (x, y, w, h)) in enumerate(zip(entries, rects)):
            if e is None:
                self.seg(x + 10 * s, y + h / 2, x + w - 10 * s, y + h / 2, 1 * s, (1, 1, 1, 0.08))
                continue
            if e.get("header"):
                self.text(e["label"], x + 14 * s, y + h / 2 + 2 * s, size * 0.8, (0.4, 0.64, 1.0, 0.9), bold=True, anchor="lm")
                continue
            hov = self.hover == ("menu", i)
            if hov:
                self.rrect(x + 4 * s, y + 1, w - 8 * s, h - 2, 5 * s, (*ACCENT, 0.9))
            if e.get("check"):
                cx, cy = x + 16 * s, y + h / 2
                self.seg(cx - 4 * s, cy, cx - 1.2 * s, cy + 3 * s, 1.7 * s, (1, 1, 1, 0.95))
                self.seg(cx - 1.2 * s, cy + 3 * s, cx + 4.5 * s, cy - 3.5 * s, 1.7 * s, (1, 1, 1, 0.95))
            self.text(e["label"], x + 30 * s * (size / (12.5 * s)), y + h / 2, size, (1, 1, 1, 0.95 if hov else 0.85), anchor="lm")
            if e.get("key"):
                self.text(e["key"], x + w - 12 * s, y + h / 2, size * 0.92, (1, 1, 1, 0.75 if hov else 0.38), anchor="rm")

    def draw_hud(self):
        s = self.scale
        fw, _ = self.fb
        x, y = 12 * s, self.bar_h() + 8 * s
        maxw = fw - 24 * s - 120 * s
        if self.view2d:
            line1 = self.isel.name if self.isel in self.imgs else f"{len(self.imgs)} images"
            bits = [f"{round(self.zoom2 * 100)}%"]
            if self.isel in self.imgs:
                bits.insert(0, f"{self.isel.w}×{self.isel.h}")
            if self.gray2d:
                bits.append("GRAY")
        else:
            objs = self.visible_objs()
            if self.sel in self.objs:
                line1 = self.sel.name + (f"   ({self.objs.index(self.sel) + 1}/{len(self.objs)})" if len(self.objs) > 1 else "")
            else:
                line1 = f"{len(self.objs)} models" if len(self.objs) > 1 else self.objs[0].name
            tris = (self.sel.tris if self.sel in objs else sum(o.tris for o in objs))
            tri = f"{tris / 1e6:.2f}M" if tris >= 1e6 else f"{tris / 1e3:.0f}k" if tris >= 1e3 else str(tris)
            bits = [f"{tri} tris", "Ortho" if self.ortho else "Persp"]
            o = self.sel if self.sel in objs else (objs[0] if len(objs) == 1 else None)
            if o:
                bits.append(f"{o.up}-up")
            if self.isolate:
                bits.append("ISOLATED")
            if self.mirror:
                bits.append("MIRROR")
            if self.sil:
                bits.append("SILHOUETTE")
        self.text(self.fit(line1, 12.5 * s, maxw), x, y, 12.5 * s, (1, 1, 1, 0.62))
        self.text(self.fit("  ·  ".join(bits), 11 * s, maxw), x, y + 19 * s, 11 * s, (1, 1, 1, 0.36))

    def draw_bar(self):
        s = self.scale
        fw, _ = self.fb
        bh = self.bar_h()
        hv = self.hover[1] if self.hover and self.hover[0] == "ui" else None
        self.rrect(0, 0, fw, bh, 0, (0.0, 0.0, 0.0, 0.22))
        items = self.bar_items()
        # menu (hamburger)
        mx, my, mw, mh = items["menu"]
        if hv == "menu" or self.menu_open:
            self.rrect(mx, my, mw, mh, 5 * s, (1, 1, 1, 0.14 if not self.menu_open else 0.2))
        for k in (-1, 0, 1):
            yy = my + mh / 2 + k * 4.5 * s
            self.seg(mx + mw / 2 - 6 * s, yy, mx + mw / 2 + 6 * s, yy, 1.5 * s, (1, 1, 1, 0.85))
        # 3D | 2D segmented switch
        x3, y3, w3, h3 = items["seg3d"]
        x2, _y2, w2, _h2 = items["seg2d"]
        self.rrect(x3, y3, w3 + w2, h3, h3 / 2, (1, 1, 1, 0.08))
        ax, aw = (x2, w2) if self.view2d else (x3, w3)
        self.rrect(ax + 2 * s, y3 + 2 * s, aw - 4 * s, h3 - 4 * s, (h3 - 4 * s) / 2, (*ACCENT, 1.0))
        for name, (bx, by, bw, bhh), lbl in (("seg3d", items["seg3d"], "3D"), ("seg2d", items["seg2d"], "2D")):
            active = (name == "seg2d") == self.view2d
            a = 1.0 if active else (0.85 if hv == name else 0.5)
            self.text(lbl, bx + bw / 2, by + bhh / 2, 11 * s, (1, 1, 1, a), bold=True, anchor="mm")
        # board name
        name = os.path.splitext(os.path.basename(self.project_path))[0] if self.project_path else "Untitled"
        name += "  •" if self.project_dirty else ""
        right = (items["min"][0] if self.borderless else fw) - 10 * s
        left = x2 + w2 + 12 * s
        if right - left > 60 * s:
            self.text(self.fit(name, 11 * s, right - left), left, bh / 2, 11 * s, (1, 1, 1, 0.45), anchor="lm")
        if self.borderless:
            self.rrect(fw / 2 - 18 * s, 6 * s, 36 * s, 4 * s, 2 * s,
                       (1, 1, 1, 0.25 if self.hover == ("bar",) else 0.12))
            for name in ("min", "max", "close"):
                bx, by, bw, bhh = items[name]
                cx, cy = bx + bw / 2, by + bhh / 2
                hov = hv == name
                if hov:
                    self.rrect(bx, by, bw, bhh, 0, (0.86, 0.22, 0.22, 0.95) if name == "close" else (1, 1, 1, 0.12))
                ic = (1, 1, 1, 0.95 if hov else 0.6)
                d = 4.5 * s
                if name == "min":
                    self.seg(cx - d, cy, cx + d, cy, 1.4 * s, ic)
                elif name == "max":
                    if self.max_rect:
                        self.rrect(cx - d + 2 * s, cy - d - 1 * s, 2 * d - 2 * s, 2 * d - 2 * s, 1 * s, ic, outline=1.3 * s)
                        self.rrect(cx - d, cy - d + 1 * s, 2 * d - 2 * s, 2 * d - 2 * s, 1 * s, (0.13, 0.13, 0.13, 1))
                        self.rrect(cx - d, cy - d + 1 * s, 2 * d - 2 * s, 2 * d - 2 * s, 1 * s, ic, outline=1.3 * s)
                    else:
                        self.rrect(cx - d, cy - d, 2 * d, 2 * d, 1.2 * s, ic, outline=1.3 * s)
                else:
                    self.seg(cx - d, cy - d, cx + d, cy + d, 1.4 * s, ic)
                    self.seg(cx - d, cy + d, cx + d, cy - d, 1.4 * s, ic)

    def draw_grip(self):
        s = self.scale
        fw, fh = self.fb
        a = 0.6 if (self.hover == ("grip",) or self.drag == "resize") else 0.25
        for k in (4, 8, 12):
            self.seg(fw - 3 * s - k * s, fh - 3 * s, fw - 3 * s, fh - 3 * s - k * s, 1.3 * s, (1, 1, 1, a))

    def draw_gizmo(self):
        s = self.scale
        cx, cy, R, br, items = self.gizmo()
        hk = self.hover[0] if self.hover else None
        if hk in ("gizmo", "bubble") or (self.drag == "orbit" and self.press_hit and self.press_hit[0] in ("gizmo", "bubble")):
            self.circle(cx, cy, R + br + 4 * s, (1, 1, 1, 0.08))
        for _dz, i, sg, x, y in items:
            c = AXIS_COL[i]
            hov = self.hover == ("bubble", i, sg)
            if sg > 0:
                L = math.hypot(x - cx, y - cy)
                if L > br:
                    k = (L - br * 0.9) / L
                    self.seg(cx, cy, cx + (x - cx) * k, cy + (y - cy) * k, 2.2 * s, (*c, 1.0))
                fill = tuple(min(1.0, v + 0.25) for v in c) if hov else c
                self.circle(x, y, br, (*fill, 1.0))
                self.text(AXIS_LBL[i], x, y, 12.5 * s, (0.06, 0.06, 0.07, 0.95), bold=True, anchor="mm")
            else:
                self.circle(x, y, br, (c[0] * 0.38 + 0.08, c[1] * 0.38 + 0.08, c[2] * 0.38 + 0.08, 1.0))
                self.ring(x, y, br - 0.9 * s, 1.6 * s, (*c, 1.0))
                if hov:
                    self.text("-" + AXIS_LBL[i], x, y, 11 * s, (1, 1, 1, 0.95), bold=True, anchor="mm")

    def _icon(self, name, x, y, s, ic):
        w = 1.6 * s
        if name == "zoom":
            self.ring(x - 2 * s, y - 2 * s, 4.5 * s, w, ic)
            self.seg(x + 1.6 * s, y + 1.6 * s, x + 5.5 * s, y + 5.5 * s, 2.2 * s, ic)
        elif name == "pan":
            d = 6 * s
            self.seg(x - d, y, x + d, y, w, ic); self.seg(x, y - d, x, y + d, w, ic)
            for ax, ay in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                tx, ty = x + ax * d, y + ay * d
                px, py = -ay * 2.2 * s, ax * 2.2 * s
                bx, by = tx - ax * 2.4 * s, ty - ay * 2.4 * s
                self.seg(tx, ty, bx + px, by + py, w, ic); self.seg(tx, ty, bx - px, by - py, w, ic)
        elif name == "focus":
            self.ring(x, y, 4 * s, w, ic)
            self.circle(x, y, 1.4 * s, ic)
            for ax, ay in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                self.seg(x + ax * 6 * s, y + ay * 6 * s, x + ax * 8 * s, y + ay * 8 * s, w, ic)
        elif name == "light":
            self.circle(x, y, 3.2 * s, ic)
            for k in range(8):
                a = k / 8 * math.tau
                self.seg(x + math.cos(a) * 5.4 * s, y + math.sin(a) * 5.4 * s,
                         x + math.cos(a) * 7.4 * s, y + math.sin(a) * 7.4 * s, 1.4 * s, ic)
        elif name == "ortho":
            if self.ortho:
                a = 5 * s
                pts = [(x - a, y - a), (x + a, y - a), (x + a, y + a), (x - a, y + a)]
            else:
                pts = [(x - 3.5 * s, y - 5 * s), (x + 3.5 * s, y - 5 * s), (x + 6 * s, y + 5 * s), (x - 6 * s, y + 5 * s)]
            for k in range(4):
                self.seg(*pts[k], *pts[(k + 1) % 4], w, ic)
        elif name == "spin":
            self.ring(x, y, 5.5 * s, w, ic)
            self.circle(x + 5.5 * s * math.cos(-0.8), y + 5.5 * s * math.sin(-0.8), 2.4 * s, ic)
        elif name == "pin":
            self.circle(x, y - 2.5 * s, 3.8 * s, ic)
            self.seg(x - 5 * s, y + 0.5 * s, x + 5 * s, y + 0.5 * s, w, ic)
            self.seg(x, y + 1 * s, x, y + 7 * s, 1.4 * s, ic)
        elif name == "help":
            self.text("?", x, y, 13 * s, ic, bold=True, anchor="mm")
        elif name == "gray":
            self.ring(x, y, 5.5 * s, w, ic)
            for k in range(-4, 1):
                hh = math.sqrt(max(5.5 ** 2 - (k + 0.5) ** 2, 0)) * s
                self.seg(x + (k + 0.5) * s, y - hh, x + (k + 0.5) * s, y + hh, 1.2 * s, ic)
        elif name == "flip":
            self.seg(x, y - 7 * s, x, y + 7 * s, 1.2 * s, (*ic[:3], ic[3] * 0.6))
            self.seg(x - 2 * s, y - 5 * s, x - 7 * s, y + 5 * s, w, ic)
            self.seg(x - 7 * s, y + 5 * s, x - 2 * s, y + 5 * s, w, ic)
            self.seg(x - 2 * s, y + 5 * s, x - 2 * s, y - 5 * s, w, ic)
            self.seg(x + 2 * s, y - 5 * s, x + 7 * s, y + 5 * s, w, ic)
            self.seg(x + 7 * s, y + 5 * s, x + 2 * s, y + 5 * s, w, ic)
            self.seg(x + 2 * s, y + 5 * s, x + 2 * s, y - 5 * s, w, ic)
        elif name == "arrange":
            for gx in (-1, 1):
                for gy in (-1, 1):
                    self.rrect(x + gx * 3.3 * s - 2.6 * s, y + gy * 3.3 * s - 2.6 * s, 5.2 * s, 5.2 * s, 1 * s, ic)

    def draw_buttons(self):
        s = self.scale
        hover_tip = None
        for name, tip, x, y, r in self.buttons():
            hov = self.hover == ("btn", name)
            sel_img = self.isel in self.imgs
            active = ((name == "ortho" and self.ortho) or (name == "spin" and self.spin) or
                      (name == "pin" and self.on_top) or (name == "help" and self.help) or
                      (name == "gray" and self.gray2d) or (name == "flip" and sel_img and self.isel.flip) or
                      self.drag == name)
            bg = (*ACCENT, 0.9) if active else (1, 1, 1, 0.14) if hov else (0, 0, 0, 0.28)
            self.circle(x, y, r, bg)
            self._icon(name, x, y, s, (1, 1, 1, 0.9))
            if hov:
                hover_tip = (tip, x - r - 8 * s, y)
        if hover_tip and not self.drag:
            tip, tx, ty = hover_tip
            size = 11.5 * s
            tw, th = self.measure(tip, size)
            pw, ph = tw + 14 * s, th + 8 * s
            self.rrect(tx - pw, ty - ph / 2, pw, ph, 5 * s, (0.07, 0.07, 0.08, 0.92))
            self.text(tip, tx - pw / 2, ty, size, (1, 1, 1, 0.9), anchor="mm")

    def draw_help(self):
        s = self.scale
        fw, fh = self.fb
        sections = (HELP_2D if self.view2d else HELP_3D) + HELP_COMMON

        def layout(size, cols):
            lh = size * 1.7
            hdr = lh * 1.5
            heights = [hdr + lh * len(items) for _t, items in sections]
            if cols == 1:
                groups = [sections]
            else:
                best, split = None, 1
                for i in range(1, len(sections)):
                    m = max(sum(heights[:i]), sum(heights[i:]))
                    if best is None or m < best:
                        best, split = m, i
                groups = [sections[:split], sections[split:]]
            gap = size * 1.8
            colw = []
            for g in groups:
                kw = max(self.measure(k, size)[0] for _t, items in g for k, _d in items)
                dw = max(self.measure(d, size)[0] for _t, items in g for _k, d in items)
                colw.append((kw, dw))
            W = sum(kw + gap + dw for kw, dw in colw) + gap * 1.6 * (len(groups) - 1)
            H = max(sum(hdr + lh * len(items) for _t, items in g) for g in groups) + lh * 1.2
            return groups, colw, W, H, lh, hdr, gap

        base = 12.5 * s
        pad = 18 * s
        best = None
        for cols in (1, 2):
            groups, colw, W, H, *_ = layout(base, cols)
            k = min(1.0, (fh - 16 * s) / (H + 2 * pad), (fw - 16 * s) / (W + 2 * pad))
            if best is None or k > best[1] + 0.02:
                best = (cols, k)
        cols, k = best
        size = max(7.0, base * k)
        pad *= max(k, 0.5)
        groups, colw, W, H, lh, hdr, gap = layout(size, cols)
        pw, ph = W + 2 * pad, H + 2 * pad
        x0, y0 = (fw - pw) / 2, (fh - ph) / 2
        self.rrect(x0, y0, pw, ph, 10 * s, (0.10, 0.10, 0.11, 0.96))
        x = x0 + pad
        for g, (kw, dw) in zip(groups, colw):
            y = y0 + pad
            for title, items in g:
                self.text(title.upper(), x, y + lh * 0.35, size * 0.9, (0.40, 0.64, 1.0, 1.0), bold=True)
                y += hdr
                for key, desc in items:
                    self.text(key, x, y, size, (1, 1, 1, 0.5))
                    self.text(desc, x + kw + gap, y, size, (1, 1, 1, 0.9))
                    y += lh
            x += kw + gap + dw + gap * 1.6
        self.text("H or Esc to close", fw / 2, y0 + ph - pad - lh * 0.4, size * 0.9, (1, 1, 1, 0.35), anchor="mm")

    def draw_toast(self, now):
        if not self.toast_active():
            return
        s = self.scale
        fw, fh = self.fb
        age = now - self.toast_t
        a = 1.0 if age < 1.6 else max(0.0, 1 - (age - 1.6) / 0.4)
        size = 12.5 * s
        msg = self.fit(self.toast_msg, size, fw - 50 * s)
        tw, th = self.measure(msg, size)
        w, h = tw + 26 * s, th + 12 * s
        x, y = (fw - w) / 2, fh - h - 18 * s
        self.rrect(x, y, w, h, h / 2, (0.07, 0.07, 0.08, 0.88 * a))
        self.text(msg, fw / 2, y + h / 2, size, (1, 1, 1, 0.95 * a), anchor="mm")

    # ------------------------------------------------------------- main loop
    def ui_target(self, now):
        if not self.autohide or self.inside or self.menu_open or self.help or self.drag:
            return 1.0, None
        wait = 0.6 - (now - self.left_t)
        if wait > 0:
            return 1.0, wait          # keep visible a moment after the cursor leaves
        return 0.0, None

    def run(self):
        last = time.perf_counter()
        while True:
            if glfw.window_should_close(self.win):
                if self.confirm_close():
                    break
                glfw.set_window_should_close(self.win, False)
            now = time.perf_counter()
            spinning = self.spin and not self.view2d and bool(self.objs)
            target, wait = self.ui_target(now)
            fading = abs(self.ui_a - target) > 0.002 and self.autohide
            toast_age = now - self.toast_t if self.toast_msg else 99.0
            toast_fading = 1.6 <= toast_age < 2.0
            animating = spinning or self.anim is not None or bool(self._loading_names) or fading or toast_fading
            deadlines = [d for d in (wait, (1.6 - toast_age) if toast_age < 1.6 else None) if d is not None]
            iconified = glfw.get_window_attrib(self.win, glfw.ICONIFIED)
            if animating and not iconified:
                glfw.poll_events()                      # vsync in swap_buffers paces the loop
            elif animating or deadlines:
                glfw.wait_events_timeout(max(min(deadlines or [0.1]), 0.005))
            else:
                glfw.wait_events()                      # fully idle: 0% CPU / GPU
            now = time.perf_counter()
            dt = min(now - last, 0.1)
            last = now

            if self._want_open:
                self._want_open = False
                self.open_dialog()
            if self._want_paste:
                self._want_paste = False
                self.paste()
            if self._want_save:
                mode, self._want_save = self._want_save, 0
                self.save_project(save_as=(mode == 2))
            if self._want_new:
                self._want_new = False
                self.new_project()
            self._process_results()
            if self.anim is not None:
                self._step_anim(now)
            if spinning and self.drag != "orbit":
                self.azim = (self.azim + self.spin_speed * dt) % 360.0
                self.dirty = True
            target, _ = self.ui_target(now)
            if self.autohide and abs(self.ui_a - target) > 0.002:
                speed = dt / (0.12 if target > self.ui_a else 0.4)
                self.ui_a = min(target, self.ui_a + speed) if target > self.ui_a else max(target, self.ui_a - speed)
                self.dirty = True
            if animating:
                self.dirty = True
            if self.dirty:
                self.dirty = False
                self.render()
        self.shutdown()

    def confirm_close(self):
        # untitled boards are kept automatically as the session; saved boards ask first
        if self.project_path and self.project_dirty:
            return self.ask_save_changes()
        return True

    def shutdown(self):
        cfg = self.cfg
        try:
            if not glfw.get_window_attrib(self.win, glfw.ICONIFIED):
                if self.max_rect:
                    cfg["x"], cfg["y"], cfg["w"], cfg["h"] = self.max_rect
                else:
                    cfg["x"], cfg["y"] = glfw.get_window_pos(self.win)
                    cfg["w"], cfg["h"] = glfw.get_window_size(self.win)
        except Exception:
            pass
        cfg.update(on_top=self.on_top, borderless=self.borderless, opacity=round(self.opacity, 2),
                   color=self.color_idx, flat=self.flat, wire=self.wire, ortho=self.ortho, grid=self.grid,
                   spin_speed=self.spin_speed, light=[self.light_yaw, self.light_pitch],
                   view2d=self.view2d, gray2d=self.gray2d, max_rect=self.max_rect, autohide=self.autohide,
                   recent=self.recent, session=self.project_dict(None),
                   project_path=self.project_path, project_dirty=self.project_dirty)
        save_cfg(cfg)
        glfw.terminate()


def main():
    args = sys.argv[1:]
    shot = None
    if "--selftest" in args:   # load files, render one frame to a PNG, exit (used to verify builds)
        i = args.index("--selftest")
        shot = args[i + 1] if i + 1 < len(args) else os.path.join(DATA_DIR, "selftest.png")
        del args[i:i + 2]
    files = [a for a in args if not a.startswith("--")]
    app = Viewer(files)
    if shot:
        t0 = time.time()
        while (app._loading_names or app._results) and time.time() - t0 < 60:
            glfw.wait_events_timeout(0.05)
            app._process_results()
        app.anim = None
        app.toast_msg = ""
        app.render()
        fw, fh = app.fb
        Image.frombytes("RGB", (fw, fh), app.ctx.screen.read(viewport=(0, 0, fw, fh), components=3)).transpose(Image.FLIP_TOP_BOTTOM).save(shot)
        print(f"selftest ok: {len(app.objs)} models, {len(app.imgs)} images -> {shot}")
        glfw.terminate()
        return
    app.run()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        fatal(traceback.format_exc())
        sys.exit(1)
