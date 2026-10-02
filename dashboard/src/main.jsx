import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import './index.css'
import Layout from './Layout.jsx'
import Verification from './pages/Verification.jsx'
import Policy from './pages/Policy.jsx'
import AuditLog from './pages/AuditLog.jsx'

export default function App() {
  return (
    <BrowserRouter>
      <Layout>
        <Routes>
          <Route path="/" element={<Verification />} />
          <Route path="/policy" element={<Policy />} />
          <Route path="/audit" element={<AuditLog />} />
        </Routes>
      </Layout>
    </BrowserRouter>
  )
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)
