import { useEffect, useRef } from "react";
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";

interface MeshViewerProps {
  source: string;
  format: "glb" | "gltf";
  onError?: (message: string) => void;
}

/** Loads a GLB/glTF mesh with Three.js and lets the user orbit/zoom. */
export function MeshViewer({ source, format, onError }: MeshViewerProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0e1524);
    let disposed = false;

    const camera = new THREE.PerspectiveCamera(
      50,
      container.clientWidth / container.clientHeight,
      0.1,
      5000,
    );
    camera.position.set(1.5, 1.2, 2);

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(container.clientWidth, container.clientHeight);
    container.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.target.set(0, 0, 0);
    controls.update();

    // Lighting
    scene.add(new THREE.AmbientLight(0xffffff, 0.6));
    const dir = new THREE.DirectionalLight(0xffffff, 1.2);
    dir.position.set(3, 5, 4);
    scene.add(dir);

    // Grid helper for spatial reference
    scene.add(new THREE.GridHelper(10, 20, 0x334155, 0x1e293b));

    let raf = 0;
    const render = () => {
      controls.update();
      renderer.render(scene, camera);
      raf = requestAnimationFrame(render);
    };
    raf = requestAnimationFrame(render);

    const loader = new GLTFLoader();

    loader.load(
      source,
      (gltf) => {
        if (disposed) return;
        const box = new THREE.Box3().setFromObject(gltf.scene);
        const size = box.getSize(new THREE.Vector3());
        const center = box.getCenter(new THREE.Vector3());
        const maxDim = Math.max(size.x, size.y, size.z) || 1;
        const scale = 4 / maxDim;
        gltf.scene.scale.setScalar(scale);
        gltf.scene.position.sub(center.multiplyScalar(scale));
        scene.add(gltf.scene);
        camera.near = 0.01;
        camera.far = maxDim * 10 + 100;
        camera.updateProjectionMatrix();
      },
      undefined,
      (err) => {
        if (!disposed) onError?.(`加载 3D 文件失败：${format.toUpperCase()} 解析错误`);
      },
    );

    return () => {
      disposed = true;
      cancelAnimationFrame(raf);
      controls.dispose();
      renderer.dispose();
      scene.traverse((obj) => {
        if (obj instanceof THREE.Mesh) {
          obj.geometry.dispose();
          const mat = obj.material;
          if (Array.isArray(mat)) mat.forEach((m) => m.dispose());
          else if (mat) mat.dispose();
        }
      });
      if (renderer.domElement.parentElement === container) {
        container.removeChild(renderer.domElement);
      }
    };
  }, [source, format, onError]);

  return <div ref={containerRef} className="h-full w-full" />;
}
