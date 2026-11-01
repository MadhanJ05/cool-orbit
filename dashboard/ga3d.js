// Gallium in 3D: a physically based three.js scene of the alpha-gallium block that dashboard/context.py exports
// (built by coolorbit/sim/alloys.py, the same code our simulations start from). three.js r160 (MIT) is vendored under
// vendor/three/ because the server's CSP allows only our own scripts. app.js draws the card and an empty stage
// (#ga3d-stage); this module fills it and sets data-ready="1". If WebGL or this module fails, app.js falls back to
// its small canvas viewer. Nothing here invents data: positions, pairs and the cell come from the export.
import * as THREE from "./vendor/three/three.module.min.js";
import { OrbitControls } from "./vendor/three/addons/OrbitControls.js";
import { RoomEnvironment } from "./vendor/three/addons/RoomEnvironment.js";

const stage = document.getElementById("ga3d-stage");
const data = window.COOL_ORBIT_DATA;
const g = data && data.context && data.context.gallium;

function webgl() {
  try {
    const c = document.createElement("canvas");
    return Boolean(c.getContext("webgl2") || c.getContext("webgl"));
  } catch (e) {
    return false;
  }
}

function glowTexture() {
  const c = document.createElement("canvas");
  c.width = c.height = 128;
  const x = c.getContext("2d");
  const grad = x.createRadialGradient(64, 64, 0, 64, 64, 64);
  grad.addColorStop(0, "rgba(120, 255, 200, 0.9)");
  grad.addColorStop(0.25, "rgba(52, 211, 153, 0.45)");
  grad.addColorStop(1, "rgba(52, 211, 153, 0)");
  x.fillStyle = grad;
  x.fillRect(0, 0, 128, 128);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

function build() {
  if (!stage || !g || !Array.isArray(g.atoms) || !g.atoms.length || !webgl()) return;
  const still = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "high-performance" });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.setClearColor(0x000000, 0);

  const scene = new THREE.Scene();
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(new RoomEnvironment(renderer), 0.04).texture;

  // centre the block on the origin
  const P = g.atoms.map((a) => new THREE.Vector3(a.x, a.y, a.z));
  const real = P.filter((_, i) => !g.atoms[i].ghost);
  const centre = real.reduce((s, v) => s.add(v), new THREE.Vector3()).multiplyScalar(1 / real.length);
  P.forEach((v) => v.sub(centre));
  const radius = Math.max(...P.map((v) => v.length())) || 10;
  scene.fog = new THREE.Fog(0x0a0a0b, radius * 1.6, radius * 4.2);

  // liquid-metal atoms: polished metal with a clear coat; partners from neighbouring cells are faint glass
  const sphere = new THREE.SphereGeometry(0.62, 48, 32);
  const metal = new THREE.MeshPhysicalMaterial({ color: 0xdfe8f5, metalness: 1, roughness: 0.18, clearcoat: 0.7,
    clearcoatRoughness: 0.12, envMapIntensity: 1.35 });
  const ghostMat = new THREE.MeshPhysicalMaterial({ color: 0x7f96bd, metalness: 0.6, roughness: 0.35, transparent: true,
    opacity: 0.28, depthWrite: false, envMapIntensity: 0.8 });
  const ri = [], gi = [];
  g.atoms.forEach((a, i) => (a.ghost ? gi : ri).push(i));
  const m4 = new THREE.Matrix4();
  const atomsMesh = new THREE.InstancedMesh(sphere, metal, ri.length);
  ri.forEach((idx, k) => atomsMesh.setMatrixAt(k, m4.makeTranslation(P[idx].x, P[idx].y, P[idx].z)));
  const ghostsMesh = new THREE.InstancedMesh(sphere, ghostMat, Math.max(1, gi.length));
  gi.forEach((idx, k) => ghostsMesh.setMatrixAt(k, m4.compose(P[idx], new THREE.Quaternion(), new THREE.Vector3(0.85, 0.85, 0.85))));
  ghostsMesh.count = gi.length;
  const group = new THREE.Group();
  group.add(atomsMesh, ghostsMesh);

  // the gallium pairs: glowing green bonds with a soft halo at each pair's centre
  const bondMat = new THREE.MeshStandardMaterial({ color: 0x1f9e74, emissive: 0x34d399, emissiveIntensity: 1.4,
    metalness: 0.2, roughness: 0.35 });
  const bondGeo = new THREE.CylinderGeometry(0.17, 0.17, 1, 20, 1);
  const up = new THREE.Vector3(0, 1, 0);
  const glow = new THREE.SpriteMaterial({ map: glowTexture(), color: 0xffffff, transparent: true, depthWrite: false,
    blending: THREE.AdditiveBlending, opacity: 0.85 });
  const halos = [];
  for (const b of g.bonds || []) {
    const A = P[b.a], B = P[b.b];
    if (!A || !B) continue;
    const dir = new THREE.Vector3().subVectors(B, A);
    const len = dir.length();
    const mesh = new THREE.Mesh(bondGeo, bondMat);
    mesh.scale.set(1, len, 1);
    mesh.position.copy(A).addScaledVector(dir, 0.5);
    mesh.quaternion.setFromUnitVectors(up, dir.clone().normalize());
    group.add(mesh);
    const halo = new THREE.Sprite(glow);
    halo.position.copy(mesh.position);
    const ghostPair = g.atoms[b.a].ghost || g.atoms[b.b].ghost;
    halo.scale.setScalar(ghostPair ? 1.2 : 1.9);
    halo.material = ghostPair ? glow.clone() : glow;
    if (ghostPair) halo.material.opacity = 0.35;
    halos.push(halo);
    group.add(halo);
  }

  // the block's cell frame, faint and glowing
  const rp = (g.cell && g.cell.reps) || {};
  const L = new THREE.Vector3(g.cell.a * (rp.a || 1), g.cell.b * (rp.b || 1), g.cell.c * (rp.c || 1));
  const frame = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(L.x, L.y, L.z)),
    new THREE.LineBasicMaterial({ color: 0xb8bec9, transparent: true, opacity: 0.32 }));
  frame.position.copy(L.clone().multiplyScalar(0.5).sub(centre));
  group.add(frame);
  scene.add(group);

  // lights: the environment does most of it; a key light and a cool rim light add highlights on the metal
  const key = new THREE.DirectionalLight(0xffffff, 2.2);
  key.position.set(1, 1.4, 1.2).multiplyScalar(radius * 2);
  const rim = new THREE.DirectionalLight(0xd8e2ff, 1.4);
  rim.position.set(-1.2, 0.4, -1).multiplyScalar(radius * 2);
  scene.add(key, rim, new THREE.AmbientLight(0x26262b, 0.6));

  const camera = new THREE.PerspectiveCamera(32, 1, 0.1, radius * 20);
  camera.position.set(radius * 1.6, radius * 1.05, radius * 2.3);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.06;
  controls.enablePan = false;
  controls.minDistance = radius * 1.4;
  controls.maxDistance = radius * 5;
  controls.autoRotate = !still;
  controls.autoRotateSpeed = 0.55; // slow and calm (about 110 s per turn)

  stage.replaceChildren(renderer.domElement);
  renderer.domElement.setAttribute("aria-hidden", "true");
  function fit() {
    const w = stage.clientWidth || 480;
    const h = stage.clientHeight || Math.round(w * 0.78);
    renderer.setSize(w, h, false);
    renderer.domElement.style.width = "100%";
    renderer.domElement.style.height = "100%";
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
  fit();
  new ResizeObserver(fit).observe(stage);

  let visible = true;
  new IntersectionObserver((entries) => { visible = entries.some((e) => e.isIntersecting); }).observe(stage);
  const clock = new THREE.Clock();
  function frameLoop() {
    requestAnimationFrame(frameLoop);
    if (!visible || document.hidden) return;
    const t = clock.getElapsedTime();
    if (!still) bondMat.emissiveIntensity = 1.25 + 0.3 * Math.sin(t * 0.9); // the pairs breathe, slowly
    controls.update();
    renderer.render(scene, camera);
  }
  frameLoop();
  stage.dataset.ready = "1";
}

try {
  build();
} catch (e) {
  if (stage) stage.dataset.failed = "1"; // app.js falls back to its canvas viewer
}
