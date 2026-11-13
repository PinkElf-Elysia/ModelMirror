import CultivationStory from './CultivationStory';
import { Link } from 'react-router-dom';
import RpgShell from './RpgShell';

export default function CultivationCard() {
  return <RpgShell compact>
    <header className="rpg-embed-heading">
      <Link to="/rpg/cultivation">← 超级修仙</Link><h1>原有修行存档</h1>
    </header>
    <iframe title="三界修真档案构建系统" sandbox="allow-scripts" allow="clipboard-write"
      src="/rpg-app/cultivation/" className="w-full flex-1 border-0" style={{minHeight:'70dvh'}} />
    <CultivationStory archive=""/>
  </RpgShell>;
}
