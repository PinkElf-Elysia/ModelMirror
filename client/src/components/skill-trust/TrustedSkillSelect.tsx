import type { InstalledSkillTrustFields } from "../../data/skillTrustIndex";

export interface TrustSelectableSkill extends InstalledSkillTrustFields {
  skill_id: string;
  name: string;
}

function blockedLabel(skill: TrustSelectableSkill) {
  if (skill.trust_activation_status === "ack_required") return "等待本机确认";
  if (skill.trust_state === "unverified_legacy") return "旧来源未核验";
  return "当前不可激活";
}

export default function TrustedSkillSelect({
  ariaLabel,
  disabled = false,
  onChange,
  placeholder = "不使用 Skill",
  skills,
  value,
}: {
  ariaLabel: string;
  disabled?: boolean;
  onChange: (skillId: string) => void;
  placeholder?: string;
  skills: TrustSelectableSkill[];
  value: string;
}) {
  const blockedCount = skills.filter((skill) => !skill.trust_activation_allowed).length;
  return (
    <div className="space-y-2">
      <select
        aria-label={ariaLabel}
        className="min-h-11 w-full rounded-lg border border-white/10 bg-ink-950/85 px-3 text-sm text-white outline-none focus:border-brand-300/45 disabled:cursor-not-allowed disabled:opacity-55"
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        value={value}
      >
        <option value="">{placeholder}</option>
        {skills.map((skill) => (
          <option
            className="bg-slate-950"
            disabled={!skill.trust_activation_allowed}
            key={skill.skill_id}
            value={skill.skill_id}
          >
            {skill.name}{skill.trust_activation_allowed ? "" : `（${blockedLabel(skill)}）`}
          </option>
        ))}
      </select>
      {blockedCount > 0 ? (
        <p className="text-[11px] leading-5 text-amber-100/80">
          {blockedCount} 个 Skill 因未确认、来源过期或不兼容而禁用。请到{" "}
          <a className="font-semibold underline underline-offset-4" href="/skills?tab=installed">
            已安装 Skill
          </a>{" "}
          查看原因或确认固定版本。
        </p>
      ) : null}
    </div>
  );
}
