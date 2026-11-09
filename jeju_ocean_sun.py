"""
제주도 DEM + 물결치는 바다 + 하늘을 가로지르는 태양  (Blender Python 스크립트)

사용법
  1) Blender 실행 → Scripting 탭 → 이 파일을 열고 [Run Script]
     (DEM_PATH 를 지정하지 않으면 제주도를 닮은 '가짜 지형'이 자동 생성됩니다)
  2) 터미널(배경 모드) 실행 예:
       blender -b -P jeju_ocean_sun.py -- --dem jeju.asc --save jeju.blend
       blender -b -P jeju_ocean_sun.py -- --dem jeju.asc --render-frame 120 --out frame120.png
       blender -b -P jeju_ocean_sun.py -- --dem jeju.asc --render-anim --out //render/frame_

지원하는 DEM 형식
  - .asc / .txt : ESRI ASCII Grid  (권장. 가장 안전)
  - .tif .tiff .png .exr ... : Blender 가 읽을 수 있는 이미지 (높이맵)
      * 부동소수점(32bit) 이미지  -> 값 = 미터(m)
      * 8/16bit 이미지           -> 0~1 값을 0~MAX_ELEV_M(m) 로 환산
  GeoTIFF 를 .asc 로 바꾸는 방법(GDAL):
       gdal_translate -of AAIGrid jeju_dem.tif jeju.asc
  DEM 은 SRTM / ASTER GDEM / 국토지리정보원 수치표고모델 등에서 받을 수 있습니다.
"""
import bpy
import sys
import math
import os
import numpy as np

# =====================================================================
# 설정
# =====================================================================
DEM_PATH = ""                 # 예: r"C:\data\jeju.asc"  (비우면 가짜 제주도 생성)
DEM_NODATA_BELOW = 0.5        # 이 값 이하(바다/무효값)는 바다로 취급 (m)
MAX_ELEV_M = 1947.0           # 한라산 높이 (8/16bit 이미지 환산용)
DEM_EXTENT_KM_X = 80.0        # DEM 동서 폭 (km). 지형 크기 환산에 사용
DEM_IS_LATLON = True          # 위경도(EPSG:4326) DEM 이면 True -> 동서 폭을 cos(위도)로 보정
LATITUDE = 33.4               # 제주도 위도

TERRAIN_WIDTH_BU = 160.0      # 지형 동서 폭 (Blender 단위)
VERT_EXAG = 3.0               # 높이 과장 배율
SEA_DEPTH_M = 120.0           # 바다 밑 깊이 (m)
GRID_MAX = 320                # 지형 격자 최대 해상도 (클수록 정밀/느림)

OCEAN_SIZE_BU = 700.0         # 바다 한 변 길이
OCEAN_TILE_BU = 90.0          # 파도 한 타일 크기
OCEAN_RESOLUTION = 14         # 파도 해상도
WAVE_SPEED = 0.55             # 파도 진행 속도 (클수록 빠름)

FRAME_START, FRAME_END, FPS = 1, 240, 24
SUN_AZIMUTH_START = 145.0     # 태양 방위각 시작 (0=북, 90=동, 180=남, 270=서) - 카메라가 남쪽을 보므로
SUN_AZIMUTH_END = 215.0       # 태양 방위각 끝                                   화면 안에서 이동하게 좁게 잡음
                              # (실제 일출~일몰처럼 쓰려면 95 ~ 265 로 바꾸세요)
SUN_MAX_ELEVATION = 24.0     # 정오 최고 고도 (도)
SUN_MIN_ELEVATION = 4.0       # 일출/일몰 고도 (도)
SUN_SIZE_DEG = 2.2            # 하늘에 보이는 태양 원반 크기 (도)

RENDER_RES = (1280, 720)
RENDER_SAMPLES = 64


# =====================================================================
# 명령행 인자 (blender ... -- --dem x.asc ...)
# =====================================================================
def parse_args():
    argv = sys.argv
    argv = argv[argv.index("--") + 1:] if "--" in argv else []
    opts = {"dem": DEM_PATH, "save": "", "render_frame": None, "render_anim": False,
            "out": "", "samples": RENDER_SAMPLES, "scale": 1.0}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--dem": opts["dem"] = argv[i + 1]; i += 1
        elif a == "--save": opts["save"] = argv[i + 1]; i += 1
        elif a == "--render-frame": opts["render_frame"] = int(argv[i + 1]); i += 1
        elif a == "--render-anim": opts["render_anim"] = True
        elif a == "--out": opts["out"] = argv[i + 1]; i += 1
        elif a == "--samples": opts["samples"] = int(argv[i + 1]); i += 1
        elif a == "--scale": opts["scale"] = float(argv[i + 1]); i += 1
        i += 1
    return opts


# =====================================================================
# DEM 읽기
# =====================================================================
def load_asc(path):
    """ESRI ASCII Grid -> (미터 단위 2D 배열[북쪽이 0행])"""
    header = {}
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for _ in range(6):
            pos = f.tell()
            line = f.readline()
            parts = line.split()
            if len(parts) == 2 and parts[0].lower() in (
                    "ncols", "nrows", "xllcorner", "yllcorner", "xllcenter", "yllcenter",
                    "cellsize", "nodata_value"):
                header[parts[0].lower()] = float(parts[1])
            else:
                f.seek(pos)
                break
        data = np.loadtxt(f, dtype=np.float32)
    nodata = header.get("nodata_value", -9999.0)
    data = np.where(np.isclose(data, nodata), np.nan, data)
    return data


def load_image_dem(path):
    img = bpy.data.images.load(path, check_existing=False)
    w, h = img.size
    px = np.empty(w * h * img.channels, dtype=np.float32)
    img.pixels.foreach_get(px)
    px = px.reshape(h, w, img.channels)[:, :, 0]
    px = np.flipud(px)                       # Blender 는 좌하단 원점 -> 북쪽을 0행으로
    if not img.is_float:
        px = px * MAX_ELEV_M                 # 0~1 -> m
    bpy.data.images.remove(img)
    return px.astype(np.float32)


def fake_jeju(cols=600):
    """DEM 이 없을 때 쓰는 제주도 닮은 지형 (동서로 긴 타원 + 한라산 + 368개 오름 느낌)"""
    rng = np.random.default_rng(7)
    rows = cols // 2
    x = np.linspace(-1, 1, cols)[None, :]
    y = np.linspace(1, -1, rows)[:, None] * 0.5
    # 섬 윤곽: 동서 0.82 / 남북 0.36 의 불규칙 타원
    ang = np.arctan2(y, x)
    wob = 1 + 0.06 * np.sin(3 * ang + 1) + 0.04 * np.sin(5 * ang + 2) + 0.03 * np.sin(9 * ang)
    d = np.sqrt((x / 0.82) ** 2 + (y / 0.36) ** 2) / wob
    land = np.clip(1.0 - d, 0, 1)
    base = (land ** 0.6) * 140.0                                  # 완만한 해안 저지대
    # 한라산 (중앙에서 약간 서쪽 위쪽 제외)
    r2 = ((x + 0.02) ** 2 / 0.20 ** 2) + ((y - 0.0) ** 2 / 0.13 ** 2)
    halla = 1800.0 * np.exp(-r2 * 1.6) * (land > 0)
    # 산 정상 분화구
    crater = 90.0 * np.exp(-(((x + 0.02) ** 2 + (y) ** 2) / 0.004))
    # 오름 (작은 기생화산)
    oreum = np.zeros_like(base)
    for _ in range(90):
        ox = rng.uniform(-0.7, 0.7); oy = rng.uniform(-0.17, 0.17)
        if (ox / 0.8) ** 2 + (oy / 0.34) ** 2 < 0.85:
            oreum += rng.uniform(40, 110) * np.exp(-(((x - ox) ** 2 + (y - oy) ** 2) / rng.uniform(0.0004, 0.0012)))
    h = (base + halla - crater + oreum) * (land > 0)
    h = np.where(land > 0.02, h + 3.0, np.nan)                    # 바다는 NaN
    return h.astype(np.float32)


def get_dem(path):
    if path:
        ext = os.path.splitext(path)[1].lower()
        print("[DEM] loading:", path)
        if ext in (".asc", ".txt"):
            return load_asc(path)
        return load_image_dem(path)
    print("[DEM] 파일이 없어 가짜 제주도 지형을 생성합니다.")
    return fake_jeju()


# =====================================================================
# 지형 메쉬
# =====================================================================
def box_blur(a, passes=3):
    for _ in range(passes):
        p = np.pad(a, 1, mode="edge")
        a = sum(p[i:i + a.shape[0], j:j + a.shape[1]] for i in range(3) for j in range(3)) / 9.0
    return a


def build_terrain(dem_m):
    rows, cols = dem_m.shape
    stride = int(math.ceil(max(rows, cols) / GRID_MAX))
    if stride > 1:
        dem_m = dem_m[::stride, ::stride]
        rows, cols = dem_m.shape

    # 바다/무효값 -> 바다 밑으로, 해안은 부드럽게
    is_sea = np.isnan(dem_m) | (dem_m <= DEM_NODATA_BELOW)
    depth = np.where(is_sea, -SEA_DEPTH_M, dem_m)
    depth = box_blur(depth, passes=3)

    extent_km = DEM_EXTENT_KM_X * (math.cos(math.radians(LATITUDE)) if DEM_IS_LATLON else 1.0)
    m_per_bu = extent_km * 1000.0 / TERRAIN_WIDTH_BU
    height_bu = depth / m_per_bu * VERT_EXAG
    tw = TERRAIN_WIDTH_BU
    th = tw * rows / cols

    xs = np.linspace(-tw / 2, tw / 2, cols)
    ys = np.linspace(th / 2, -th / 2, rows)
    gx, gy = np.meshgrid(xs, ys)
    verts = np.stack([gx, gy, height_bu], axis=-1).reshape(-1, 3)
    idx = np.arange(rows * cols).reshape(rows, cols)
    quads = np.stack([idx[:-1, :-1], idx[:-1, 1:], idx[1:, 1:], idx[1:, :-1]], axis=-1).reshape(-1, 4)

    mesh = bpy.data.meshes.new("JejuTerrain")
    mesh.from_pydata(verts.tolist(), [], quads.tolist())
    mesh.update()
    obj = bpy.data.objects.new("JejuTerrain", mesh)
    bpy.context.scene.collection.objects.link(obj)
    try:
        mesh.shade_smooth()
    except Exception:
        for p in mesh.polygons:
            p.use_smooth = True

    # --- 재질: 높이에 따른 색 (바다 밑 모래 -> 해변 -> 초원 -> 숲 -> 바위) ---
    hmax = max(float(height_bu.max()), 0.1)
    mat = bpy.data.materials.new("TerrainMat")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    texco = nt.nodes.new("ShaderNodeTexCoord")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    mr = nt.nodes.new("ShaderNodeMapRange")
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    noise = nt.nodes.new("ShaderNodeTexNoise")
    mix = nt.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    nt.links.new(texco.outputs["Object"], sep.inputs["Vector"])
    nt.links.new(sep.outputs["Z"], mr.inputs["Value"])
    mr.inputs["From Min"].default_value = -4.0
    mr.inputs["From Max"].default_value = hmax
    nt.links.new(mr.outputs["Result"], ramp.inputs["Fac"])
    stops = [(0.00, (0.55, 0.50, 0.38, 1)),   # 바다 밑 모래
             (0.20, (0.72, 0.66, 0.50, 1)),   # 해변
             (0.24, (0.22, 0.42, 0.14, 1)),   # 초원
             (0.50, (0.10, 0.26, 0.10, 1)),   # 숲
             (0.80, (0.30, 0.26, 0.22, 1)),   # 바위
             (1.00, (0.85, 0.85, 0.85, 1))]   # 정상
    ramp.color_ramp.elements[0].position = stops[0][0]
    ramp.color_ramp.elements[0].color = stops[0][1]
    ramp.color_ramp.elements[1].position = stops[1][0]
    ramp.color_ramp.elements[1].color = stops[1][1]
    for pos, col in stops[2:]:
        e = ramp.color_ramp.elements.new(pos)
        e.color = col
    noise.inputs["Scale"].default_value = 0.8
    nt.links.new(texco.outputs["Object"], noise.inputs["Vector"])
    mix.inputs["Factor"].default_value = 0.25
    nt.links.new(ramp.outputs["Color"], mix.inputs["A"])
    nt.links.new(noise.outputs["Color"], mix.inputs["B"])
    nt.links.new(mix.outputs["Result"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 0.9
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    obj.data.materials.append(mat)
    return obj, hmax


# =====================================================================
# 바다 (Ocean 모디파이어 + 물 재질)
# =====================================================================
def setp(node, names, value):
    """Blender 버전마다 달라지는 Principled 입력 이름을 안전하게 설정"""
    for n in names if isinstance(names, (list, tuple)) else [names]:
        if n in node.inputs:
            node.inputs[n].default_value = value
            return True
    return False


def build_ocean():
    bpy.ops.mesh.primitive_plane_add(size=OCEAN_SIZE_BU)
    ocean = bpy.context.active_object
    ocean.name = "Ocean"
    mod = ocean.modifiers.new("Ocean", "OCEAN")
    mod.geometry_mode = "GENERATE"
    mod.spatial_size = int(OCEAN_TILE_BU)
    mod.repeat_x = mod.repeat_y = max(1, int(round(OCEAN_SIZE_BU / OCEAN_TILE_BU)))
    mod.resolution = OCEAN_RESOLUTION
    mod.wave_scale = 1.1
    mod.choppiness = 1.6
    mod.wind_velocity = 14.0
    mod.wave_alignment = 0.35
    mod.wave_direction = math.radians(20)
    mod.damping = 0.4
    mod.random_seed = 3
    mod.use_normals = True
    mod.use_foam = True
    mod.foam_coverage = 0.15
    mod.time = 0.0
    mod.keyframe_insert("time", frame=FRAME_START)
    mod.time = (FRAME_END - FRAME_START) / FPS * WAVE_SPEED * 6.0
    mod.keyframe_insert("time", frame=FRAME_END)

    # 생성된 격자의 중심이 원점에 오도록 이동
    dg = bpy.context.evaluated_depsgraph_get()
    ev = ocean.evaluated_get(dg)
    vs = np.empty(len(ev.data.vertices) * 3, dtype=np.float32)
    ev.data.vertices.foreach_get("co", vs)
    vs = vs.reshape(-1, 3)
    cx, cy = (vs[:, 0].min() + vs[:, 0].max()) / 2, (vs[:, 1].min() + vs[:, 1].max()) / 2
    ocean.location = (-cx, -cy, 0.0)
    print("[Ocean] vertices:", len(vs), " extent:", np.ptp(vs[:, 0]), "x", np.ptp(vs[:, 1]))

    # --- 물 재질: 태양 반사가 잘 보이도록 매끈한 유리형 ---
    mat = bpy.data.materials.new("WaterMat")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Base Color"].default_value = (0.02, 0.22, 0.32, 1)
    bsdf.inputs["Roughness"].default_value = 0.03
    bsdf.inputs["IOR"].default_value = 1.333
    setp(bsdf, ["Transmission Weight", "Transmission"], 0.85)
    setp(bsdf, ["Specular IOR Level"], 0.7)

    # 잔물결 범프 (시간에 따라 흐르는 4D 노이즈)
    texco = nt.nodes.new("ShaderNodeTexCoord")
    noise = nt.nodes.new("ShaderNodeTexNoise")
    noise.noise_dimensions = "4D"
    noise.inputs["Scale"].default_value = 1.4
    noise.inputs["Detail"].default_value = 6.0
    noise.inputs["Roughness"].default_value = 0.6
    nt.links.new(texco.outputs["Object"], noise.inputs["Vector"])
    noise.inputs["W"].default_value = 0.0
    noise.inputs["W"].keyframe_insert("default_value", frame=FRAME_START)
    noise.inputs["W"].default_value = (FRAME_END - FRAME_START) / FPS * 1.5
    noise.inputs["W"].keyframe_insert("default_value", frame=FRAME_END)
    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.25
    bump.inputs["Distance"].default_value = 0.2
    nt.links.new(noise.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])

    # 파도 거품 (Ocean 모디파이어가 만든 'foam' 색상 속성)
    foam = nt.nodes.new("ShaderNodeVertexColor")
    foam.layer_name = mod.foam_layer_name or "foam"
    foam_bsdf = nt.nodes.new("ShaderNodeBsdfDiffuse")
    foam_bsdf.inputs["Color"].default_value = (0.95, 0.97, 1.0, 1)
    mixsh = nt.nodes.new("ShaderNodeMixShader")
    sepf = nt.nodes.new("ShaderNodeSeparateColor")
    nt.links.new(foam.outputs["Color"], sepf.inputs["Color"])
    nt.links.new(sepf.outputs["Red"], mixsh.inputs["Fac"])
    nt.links.new(bsdf.outputs["BSDF"], mixsh.inputs[1])
    nt.links.new(foam_bsdf.outputs["BSDF"], mixsh.inputs[2])
    nt.links.new(mixsh.outputs["Shader"], out.inputs["Surface"])
    ocean.data.materials.append(mat)
    try:
        ocean.data.shade_smooth()
    except Exception:
        pass
    return ocean


# =====================================================================
# 하늘 + 태양 (Nishita 하늘 텍스처, 태양이 하루 동안 이동)
# =====================================================================
def build_sky():
    world = bpy.data.worlds.new("Sky")
    bpy.context.scene.world = world
    try:
        world.use_nodes = True
    except Exception:
        pass
    nt = world.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputWorld")
    bg = nt.nodes.new("ShaderNodeBackground")
    sky = nt.nodes.new("ShaderNodeTexSky")
    try:
        sky.sky_type = "NISHITA"                 # Blender 2.9 ~ 4.x
    except TypeError:
        sky.sky_type = "MULTIPLE_SCATTERING"     # Blender 5.x (이름이 바뀜)
    # 버전마다 속성 이름/존재 여부가 달라서, 없는 속성은 건너뜁니다.
    for attr, val in (("sun_disc", True), ("sun_size", math.radians(SUN_SIZE_DEG)),
                      ("sun_intensity", 1.0), ("air_density", 1.0), ("dust_density", 1.2)):
        if hasattr(sky, attr):
            setattr(sky, attr, val)
    nt.links.new(sky.outputs["Color"], bg.inputs["Color"])
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])

    steps = 24
    for i in range(steps + 1):
        t = i / steps
        frame = FRAME_START + (FRAME_END - FRAME_START) * t
        az = SUN_AZIMUTH_START + (SUN_AZIMUTH_END - SUN_AZIMUTH_START) * t
        el = SUN_MIN_ELEVATION + (SUN_MAX_ELEVATION - SUN_MIN_ELEVATION) * math.sin(math.pi * t)
        sky.sun_rotation = math.radians(az)
        sky.sun_elevation = math.radians(el)
        sky.keyframe_insert("sun_rotation", frame=frame)
        sky.keyframe_insert("sun_elevation", frame=frame)
    return sky


# =====================================================================
# 카메라 / 렌더 설정
# =====================================================================
def build_camera(hmax):
    cam_data = bpy.data.cameras.new("Cam")
    cam_data.lens = 24
    cam = bpy.data.objects.new("Camera", cam_data)
    bpy.context.scene.collection.objects.link(cam)
    cam.location = (0.0, 105.0, 14.0)            # 섬 북쪽 바다 위, 남쪽(태양 방향)을 바라봄
    target = bpy.data.objects.new("CamTarget", None)
    target.location = (0.0, -20.0, 22.0)         # 살짝 위를 보게 해서 하늘의 태양도 화면에 담김
    bpy.context.scene.collection.objects.link(target)
    con = cam.constraints.new("TRACK_TO")
    con.target = target
    con.track_axis = "TRACK_NEGATIVE_Z"
    con.up_axis = "UP_Y"
    bpy.context.scene.camera = cam
    return cam


def setup_render(opts):
    sc = bpy.context.scene
    sc.frame_start, sc.frame_end = FRAME_START, FRAME_END
    sc.render.fps = FPS
    sc.render.resolution_x = int(RENDER_RES[0] * opts["scale"])
    sc.render.resolution_y = int(RENDER_RES[1] * opts["scale"])
    try:
        sc.render.engine = "CYCLES"
        sc.cycles.samples = opts["samples"]
        sc.cycles.use_denoising = True
        sc.cycles.device = "CPU"
        sc.cycles.max_bounces = 8
        sc.cycles.transmission_bounces = 8
    except Exception as e:
        print("[Render] Cycles 설정 실패:", e)
    sc.view_settings.exposure = -1.5             # 태양이 화면에 있어도 하늘이 하얗게 날아가지 않도록
    sc.view_settings.view_transform = "AgX" if "AgX" in [
        i.identifier for i in sc.view_settings.bl_rna.properties["view_transform"].enum_items] else "Filmic"


# =====================================================================
# 메인
# =====================================================================
def main():
    opts = parse_args()
    # 키프레임 보간을 '선형'으로 (파도/태양이 일정한 속도로 움직이게)
    try:
        bpy.context.preferences.edit.keyframe_new_interpolation_type = "LINEAR"
    except Exception:
        pass

    bpy.ops.wm.read_factory_settings(use_empty=True)
    try:
        bpy.context.preferences.edit.keyframe_new_interpolation_type = "LINEAR"
    except Exception:
        pass

    dem = get_dem(opts["dem"])
    terrain, hmax = build_terrain(dem)
    build_ocean()
    build_sky()
    build_camera(hmax)
    setup_render(opts)
    bpy.context.scene.frame_set(FRAME_START)

    if opts["save"]:
        bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(opts["save"]))
        print("[Save]", opts["save"])
    if opts["render_frame"] is not None:
        bpy.context.scene.frame_set(opts["render_frame"])
        bpy.context.scene.render.filepath = os.path.abspath(opts["out"] or "jeju_frame.png")
        bpy.ops.render.render(write_still=True)
        print("[Render] saved", bpy.context.scene.render.filepath)
    elif opts["render_anim"]:
        bpy.context.scene.render.filepath = opts["out"] or "//render/frame_"
        bpy.ops.render.render(animation=True)


main()
