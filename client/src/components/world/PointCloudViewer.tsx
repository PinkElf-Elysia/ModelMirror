import { useEffect, useRef } from "react";
import * as THREE from "three";
import { PLYLoader } from "three/examples/jsm/loaders/PLYLoader.js";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";

interface PointCloudViewerProps {
  source: string;
  onError?: (message: string) => void;
}

/** Renders a plain point-cloud PLY file with Three.js. */
export function PointCloudViewer({ source, onError }: PointCloudViewerProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0e1524);

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
    controls.update();

    scene.add(new THREE.AmbientLight(0xffffff, 0.8));

    let disposed = false;
    let points: THREE.Points | null = null;

    let raf = 0;
    const render = () => {
      controls.update();
      renderer.render(scene, camera);
      raf = requestAnimationFrame(render);
    };
    raf = requestAnimationFrame(render);

    const loader = new PLYLoader();
    loader.load(
      source,
      (geometry) => {
        if (disposed) return;
        const material = new THREE.PointsMaterial({
          size: 0.01,
          vertexColors: Boolean(geometry.getAttribute("color")),
          color: geometry.getAttribute("color") ? undefined : 0x8fd3ff,
        });
        points = new THREE.Points(geometry, material);
        const box = new THREE.Box3().setFromObject(points);
        const size = box.getSize(new THREE.Vector3());
        const maxDim = Math.max(size.x, size.y, size.z) || 1;
        points.scale.setScalar(4 / maxDim);
        const center = box.getCenter(new THREE.Vector3());
        points.position.sub(center.multiplyScalar(4 / maxDim));
        scene.add(points);
      },
      undefined,
      (err) => {
        if (!disposed) {
          const message = err instanceof Error ? err.message : "未知错误";
          onError?.(`加载点云失败：${message}`);
        }
      },
    );

    return () => {
      disposed = true;
      cancelAnimationFrame(raf);
      controls.dispose();
      renderer.dispose();
      if (points) {
        points.geometry.dispose();
        (points.material as THREE.Material).dispose();
      }
      scene.traverse((obj) => {
        if (obj instanceof THREE.Mesh) {
          obj.geometry.dispose();
        }
      });
      if (renderer.domElement.parentElement === container) {
        container.removeChild(renderer.domElement);
      }
    };
  }, [source, onError]);

  return <div ref={containerRef} className="h-full w-full" />;
}
