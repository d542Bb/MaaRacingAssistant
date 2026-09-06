import React from 'react';
import { createRoot } from 'react-dom/client';
import '@xyflow/react/dist/style.css';
import '@semi-css';
import './styles.css';
import App from './App';

document.body.setAttribute('theme-mode', 'dark');
createRoot(document.getElementById('root')).render(<App />);