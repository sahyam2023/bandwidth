// src/main.tsx (Should look like this)
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom'; // <-- Ensure this import
import App from './App.tsx';
import './index.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter> {/* <-- Ensure App is wrapped */}
      <App />
    </BrowserRouter>
  </StrictMode>
);