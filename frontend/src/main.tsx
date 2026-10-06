import React from 'react'
import ReactDOM from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import App from './App'
import { ErrorBoundary } from './components/ErrorBoundary'
import { AuthGate } from './components/AuthGate'
import './styles/index.css'

// el motor anterior is configured via vite-plugin-motor anterior - no manual config needed

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
})

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        {/* F6: sin sesión (con OIDC) no se monta la app: ni su WS ni sus peticiones */}
        <AuthGate>
          <App />
        </AuthGate>
      </QueryClientProvider>
    </ErrorBoundary>
  </React.StrictMode>,
)
