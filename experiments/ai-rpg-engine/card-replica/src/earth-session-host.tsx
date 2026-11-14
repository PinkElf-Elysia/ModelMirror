import {useState} from 'react';
import {ChatView} from './chat';
import {validSession} from './presentation';
export function EarthSessionHost(){
 const [id,setId]=useState(()=>new URLSearchParams(location.search).get('session')||'');
 function opened(next:string){const url=new URL(location.href);url.searchParams.set('session',next);history.replaceState(null,'',url);setId(next);}
 function exit(){const url=new URL(location.href);url.searchParams.delete('session');location.assign(url);}
 return (validSession(id)||/^shop-(?:start|[a-f0-9]{40})$/.test(id))?<ChatView key={id} sessionId={id} opened={opened} exit={exit}/>:<main className="platform-empty"><h1>会话地址无效</h1><p>请从角色页重新选择已保存的故事。</p><button onClick={exit}>返回角色页</button></main>;
}
