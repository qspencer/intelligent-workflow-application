import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router';

import { App } from './components/App';
import './styles.css';

const root = document.getElementById('root');
if (!root) throw new Error('#root element not found');

createRoot(root).render(
  <BrowserRouter>
    <App />
  </BrowserRouter>,
);
