import {createRoot} from 'react-dom/client';
import './styles.css';

// Earth sessions bind the original entry bytes. Select the other cards before loading it.
if (/^\/rpg-app\/(xingjian\/|(?:ancient|cultivation|lord-of-mysteries)\/chat\/)/.test(location.pathname)) {
  void import('./xingjian').then(({Xingjian}) => {
    createRoot(document.getElementById('root')!).render(<Xingjian/>);
  });
} else {
  void import('./main');
}
