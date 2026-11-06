import { Link } from 'react-router-dom';
import RpgShell from './RpgShell';

export default function AncientCardPage() {
  return <RpgShell compact>
    <header className="rpg-embed-heading">
      <Link to="/rpg/ancient">← 古代人生</Link><h1>命书预览</h1>
    </header>
    <iframe title="命运卷轴角色配置" sandbox="allow-scripts" allow="clipboard-write"
      src="/rpg-app/ancient/" className="min-h-[65dvh] w-full flex-1 border-0" />
  </RpgShell>;
}
