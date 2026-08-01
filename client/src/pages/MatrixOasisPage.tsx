import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import final1600 from "../assets/matrix-oasis/matrix-oasis-final-1600.webp";
import final900 from "../assets/matrix-oasis/matrix-oasis-final-900.webp";
import gateway1600 from "../assets/matrix-oasis/matrix-oasis-gateway-1600.webp";
import gateway900 from "../assets/matrix-oasis/matrix-oasis-gateway-900.webp";
import "./MatrixOasisPage.css";

type MatrixOasisPhase = "idle" | "playing" | "settled";

const FULL_SEQUENCE_MS = 3200;
const REDUCED_SEQUENCE_MS = 250;

function useReducedMotion() {
  const [reduced, setReduced] = useState(() =>
    typeof window === "undefined"
      ? false
      : window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  );

  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduced(query.matches);
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);

  return reduced;
}

export default function MatrixOasisPage() {
  const [phase, setPhase] = useState<MatrixOasisPhase>("idle");
  const [runId, setRunId] = useState(0);
  const reducedMotion = useReducedMotion();

  useEffect(() => {
    document.title = "矩阵绿洲 - 模镜 ModelMirror";
  }, []);

  useEffect(() => {
    if (phase !== "playing") return;

    const timer = window.setTimeout(
      () => setPhase("settled"),
      reducedMotion ? REDUCED_SEQUENCE_MS : FULL_SEQUENCE_MS,
    );
    return () => window.clearTimeout(timer);
  }, [phase, reducedMotion, runId]);

  const enterOasis = () => {
    setRunId((current) => current + 1);
    setPhase("playing");
  };

  return (
    <main className={`matrix-oasis matrix-oasis--${phase}`}>
      <Link className="matrix-oasis__brand" to="/studio">
        <img alt="" height="32" src="/logo.png" width="32" />
        <span>ModelMirror</span>
      </Link>

      <section
        aria-label="矩阵绿洲，人类知识与人工智能共同生成的开放世界预告"
        className="matrix-oasis__scene"
        key={runId}
      >
        <picture
          aria-hidden={phase !== "idle"}
          className="matrix-oasis__image matrix-oasis__gateway"
        >
          <source media="(max-width: 720px)" srcSet={gateway900} />
          <img
            alt={phase === "idle" ? "蓝紫色数字城市中央展开一条发光的矩阵门廊" : ""}
            decoding="async"
            height="900"
            src={gateway1600}
            width="1600"
          />
        </picture>

        <picture
          aria-hidden={phase !== "settled"}
          className="matrix-oasis__image matrix-oasis__destination"
        >
          <source media="(max-width: 720px)" srcSet={final900} />
          <img
            alt={
              phase === "settled"
                ? "机器人与计算机先驱在宏大的数字殿堂中隔空伸手相触"
                : ""
            }
            decoding="async"
            height="900"
            src={final1600}
            width="1600"
          />
        </picture>

        <div aria-hidden="true" className="matrix-oasis__shards">
          {["left", "upper", "lower", "right"].map((position) => (
            <span
              className={`matrix-oasis__shard matrix-oasis__shard--${position}`}
              key={position}
              style={{ backgroundImage: `url(${final1600})` }}
            />
          ))}
        </div>

        <div aria-hidden="true" className="matrix-oasis__tunnel">
          {[0, 1, 2, 3, 4, 5].map((index) => (
            <span className="matrix-oasis__gate" key={index} />
          ))}
        </div>
        <div aria-hidden="true" className="matrix-oasis__floor" />
        <div aria-hidden="true" className="matrix-oasis__bloom" />
        <div aria-hidden="true" className="matrix-oasis__vignette" />

        {phase === "idle" ? (
          <div className="matrix-oasis__intro">
            <p>世界仍在生成</p>
            <h1>矩阵绿洲</h1>
            <button className="matrix-oasis__primary" onClick={enterOasis} type="button">
              进入矩阵绿洲
              <span aria-hidden="true">→</span>
            </button>
          </div>
        ) : null}

        {phase === "playing" ? (
          <button
            className="matrix-oasis__skip"
            onClick={() => setPhase("settled")}
            type="button"
          >
            跳过
          </button>
        ) : null}

        {phase === "settled" ? (
          <div className="matrix-oasis__ending">
            <div className="matrix-oasis__ending-title">
              <h1>矩阵绿洲</h1>
              <span>即将开放</span>
            </div>
            <div className="matrix-oasis__actions">
              <button onClick={enterOasis} type="button">再次进入</button>
              <Link to="/studio">返回工作台</Link>
            </div>
          </div>
        ) : null}

        <p aria-live="polite" className="sr-only">
          {phase === "idle"
            ? "矩阵绿洲预告已准备"
            : phase === "playing"
              ? "正在进入矩阵绿洲"
              : "矩阵绿洲即将开放"}
        </p>
      </section>
    </main>
  );
}
