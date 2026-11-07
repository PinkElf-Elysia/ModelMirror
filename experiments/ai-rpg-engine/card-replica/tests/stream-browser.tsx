// Offline browser fixture: the driver intercepts all session APIs.
import {StrictMode, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {ChatView} from '../src/chat';
import '../src/styles.css';
function Fixture() {
  const [session, setSession] = useState('fixture');
  return <ChatView key={session} sessionId={session} exit={()=>setSession('other')} opened={setSession}/>;
}
createRoot(document.getElementById('root')!).render(<StrictMode><Fixture/></StrictMode>);
