// Benchmark scene (three.js / WebGL): 4000 instanced boxes, two lights with shadows, fog. Not a look; a load.
import * as THREE from "three";
let renderer, scene, camera, mesh;
export default {
  setup(mv) {
    renderer = new THREE.WebGLRenderer({canvas: mv.canvas(), antialias: true, preserveDrawingBuffer: true});
    renderer.setSize(mv.width, mv.height, false); renderer.shadowMap.enabled = true;
    scene = new THREE.Scene(); scene.fog = new THREE.Fog(0x050507, 10, 60); scene.background = new THREE.Color(0x050507);
    camera = new THREE.PerspectiveCamera(50, mv.width / mv.height, 0.1, 200);
    const geo = new THREE.BoxGeometry(0.4, 0.4, 0.4), mat = new THREE.MeshStandardMaterial({color: 0xd0c8b0, roughness: 0.4, metalness: 0.2});
    mesh = new THREE.InstancedMesh(geo, mat, 4000); mesh.castShadow = mesh.receiveShadow = true;
    const m = new THREE.Matrix4();
    for (let i = 0; i < 4000; i++) { m.makeTranslation((MV.rand(i) - 0.5) * 40, (MV.rand(i, "y") - 0.5) * 20, (MV.rand(i, "z") - 0.5) * 40); mesh.setMatrixAt(i, m); }
    scene.add(mesh);
    const l1 = new THREE.DirectionalLight(0xffffff, 2); l1.position.set(10, 20, 10); l1.castShadow = true; l1.shadow.mapSize.set(2048, 2048); scene.add(l1);
    scene.add(new THREE.PointLight(0xff6040, 40, 30)); scene.add(new THREE.AmbientLight(0x404050, 1));
  },
  render(f) {
    camera.position.set(Math.cos(f.t * 0.2) * 25, 6 + 3 * f.c.loud, Math.sin(f.t * 0.2) * 25); camera.lookAt(0, 0, 0);
    mesh.rotation.y = f.t * 0.1 + 0.3 * f.c.drums;
    renderer.render(scene, camera);
  },
};
