import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

// React 19 dev mode workaround: performance.measure() throws DataCloneError
// when component props/state are too large to be structured-cloned.
// This wraps measure() to suppress that specific error.
if (import.meta.env.DEV) {
  const originalMeasure = performance.measure.bind(performance);
  performance.measure = (...args: Parameters<typeof performance.measure>) => {
    try {
      return originalMeasure(...args);
    } catch (e) {
      if (e instanceof DOMException && e.name === 'DataCloneError') {
        // Suppress DataCloneError from React 19 profiling
        return undefined as unknown as PerformanceMeasure;
      }
      throw e;
    }
  };
}

createRoot(document.getElementById('root')!).render(
  <App />,
)
