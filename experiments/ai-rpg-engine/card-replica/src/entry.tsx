import {createRoot} from 'react-dom/client';
import './styles.css';
import './platform-shell.css';

// Earth sessions bind the original entry bytes. Select the other cards before loading it.
if (/^\/rpg-app\/(xingjian\/|(?:ancient|cultivation|lord-of-mysteries|blue-fish)\/chat\/)/.test(location.pathname)) {
  void import('./xingjian').then(({Xingjian}) => {
    createRoot(document.getElementById('root')!).render(<Xingjian/>);
  });
} else if(new URLSearchParams(location.search).has('session')) {
  void import('./earth-session-host').then(({EarthSessionHost})=>createRoot(document.getElementById('root')!).render(<EarthSessionHost/>));
} else {
  void import('./main');
}
