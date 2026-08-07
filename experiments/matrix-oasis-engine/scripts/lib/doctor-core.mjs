const TOOL_REQUIREMENTS = {
  node: {
    requiredForRound: true,
    requirement: "24.x",
    supported: (version) => major(version) === 24,
    remediation: "Install Node.js 24.x outside the repository and retry.",
  },
  npm: {
    requiredForRound: true,
    requirement: "11.x",
    supported: (version) => major(version) === 11,
    remediation: "Install npm 11.x with the active Node.js 24.x toolchain.",
  },
  git: {
    requiredForRound: true,
    requirement: "available",
    supported: (version) => Boolean(version),
    remediation: "Install Git and make the executable available on PATH.",
  },
  godot: {
    requiredForRound: false,
    requirement: "4.6.x",
    supported: (version) => /^4\.6(?:\.|$)/.test(version ?? ""),
    remediation: "For a later round, provide Godot 4.6.x through GODOT_BIN or PATH outside the repository.",
  },
};

function major(version) {
  const match = /^(\d+)/.exec(version ?? "");
  return match ? Number(match[1]) : null;
}

function checkTool(id, detectedVersion, strictGodot) {
  const definition = TOOL_REQUIREMENTS[id];
  const isDetected = Boolean(detectedVersion);
  const isSupported = isDetected && definition.supported(detectedVersion);
  const isStrictOptional = id === "godot" && strictGodot;

  let status = "ready";
  if (!isSupported) {
    status = definition.requiredForRound || isStrictOptional ? "blocked" : "warning";
  }

  return {
    id,
    requiredForRound: definition.requiredForRound,
    status,
    requirement: definition.requirement,
    detectedVersion: detectedVersion ?? null,
    remediation: isSupported ? "None." : definition.remediation,
  };
}

export function buildDoctorReport(detectedVersions, options = {}) {
  const strictGodot = options.strictGodot === true;
  const checks = ["node", "npm", "git", "godot"].map((id) =>
    checkTool(id, detectedVersions[id] ?? null, strictGodot),
  );

  const hasBlocker = checks.some((check) => check.status === "blocked");
  const hasWarning = checks.some((check) => check.status === "warning");

  return {
    overallStatus: hasBlocker
      ? "blocked"
      : hasWarning
        ? "ready_with_warnings"
        : "ready",
    checks,
  };
}

export function doctorExitCode(report) {
  return report.overallStatus === "blocked" ? 1 : 0;
}

export function extractVersion(output) {
  const match = /(\d+\.\d+(?:\.\d+){0,2})/.exec(output ?? "");
  return match?.[1] ?? null;
}
