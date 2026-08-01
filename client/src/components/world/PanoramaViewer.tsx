import { useEffect, useRef } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";

interface PanoramaViewerProps {
  source: string;
  onError?: (message: string) => void;
}

/** Renders an equirectangular panorama PNG on the inside of a sphere. */
export function PanoramaViewer({ source, onError }: PanoramaViewerProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0e1524);

    const camera = new THREE.PerspectiveCamera(
      75,
      container.clientWidth / container.clientHeight,
      0.1,
      1000,
    );
    camera.position.set(0, 0, 0.1);

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(container.clientWidth, container.clientHeight);
    container.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableZoom = true;
    controls.enablePan = false;
    controls.minDistance = 0.1;
    controls.maxDistance = 0.4;
    controls.rotateSpeed = 0.6;

    let disposed = false;
    let raf = 0;
    const render = () => {
      controls.update();
      renderer.render(scene, camera);
      raf = requestAnimationFrame(render);
    };
    raf = requestAnimationFrame(render);

    const textureLoader = new THREE.TextureLoader();
    textureLoader.load(
      source,
      (texture) => {
        if (disposed) return;
        texture.colorSpace = THREE.SRGBColorSpace;
        const geometry = new THREE.SphereGeometry(100, 64, 32);
        // Flip so the panorama maps correctly to the sphere interior.
        const material = new THREE.MeshBasicMaterial({
          map: texture,
          side: THREE.BackSide,
        });
        const mesh = new THREE.Mesh(geometry, material);
        mesh.scale.set(-1, 1, 1);
        scene.add(mesh);
      },
      undefined,
      (err) => {
        if (!disposed) {
          const message = err instanceof Error ? err.message : "未知错误";
          onError?.(`加载全景图失败：${message}`);
        }
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
          else if (mat instanceof THREE.Material) {
            mat.dispose();
            const withMap = mat as THREE.Material & { map?: THREE.Texture };
            if (withMap.map) withMap.map.dispose();
          }
        }
      });
      if (renderer.domElement.parentElement === container) {
        container.removeChild(renderer.domElement);
      }
    };
  }, [source, onError]);

  return <div ref={containerRef} className="h-full w-full" />;
}
