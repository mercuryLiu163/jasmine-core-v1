import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import { App } from './App'
import { SessionProvider } from './app/session'
import './styles.css'

const container = document.getElementById('root')
if (container === null) throw new Error('index.html is missing #root')

createRoot(container).render(
  <StrictMode>
    <SessionProvider>
      <App />
    </SessionProvider>
  </StrictMode>,
)
