import { useState, useEffect } from 'react'
import { ShieldCheck, ArrowRight, Zap, Eye, ShieldAlert, FileText, Server, Settings, Activity } from 'lucide-react'
import './Landing.css'

const STATS = [
  { value: '~100%', label: 'Deterministic precision', icon: Zap },
  { value: '~1ms', label: 'Per-chunk latency', icon: Activity },
  { value: '0', label: 'Bytes of PII to cloud', icon: ShieldCheck },
  { value: '4', label: 'File formats supported', icon: FileText },
]

const STEPS = [
  { label: 'Intercept', icon: Zap, desc: 'Browser extension catches every prompt & file before it reaches the LLM' },
  { label: 'Detect', icon: Eye, desc: 'Tiered engine: Luhn for cards, prefix for API keys, NER for names' },
  { label: 'Mask', icon: ShieldCheck, desc: 'Real values replaced with realistic fakes. LLM sees surrogates only.' },
  { label: 'Scan', icon: ShieldAlert, desc: 'Independent residual scanner re-checks. Leaks? Request blocked.' },
  { label: 'Restore', icon: ArrowRight, desc: 'Vault swaps fakes back to reals in the LLM response' },
]

const FEATURES = [
  { icon: Eye, title: 'Tiered Detection Engine', desc: 'Deterministic checks (Luhn, mod-97, prefix matching) for structured PII. Compact GLiNER2-PII NER model for names, organizations, and locations. Sentence-boundary chunking for long documents.', tag: 'deterministic + semantic' },
  { icon: ShieldCheck, title: 'Faker Substitution', desc: 'Names become realistic fake names. Emails become plausible fake emails. Descriptive anchors were rejected after empirical eval showed 76% re-identifiability vs 14% chance baseline.', tag: 'empirically validated' },
  { icon: Server, title: 'Per-Conversation Vault', desc: 'Every conversation gets its own fake↔real mapping with TTL. Cross-conversation consistency is a fingerprint, not a feature. Fresh surrogates per conversation prevent correlation.', tag: 'bijective + TTL' },
  { icon: ShieldAlert, title: 'Fail-Closed Policy', desc: 'If the backend is down or the residual scanner finds leaks, the request is blocked — not sent unprotected. A logged escape hatch lets users override with a deliberate click.', tag: 'fail-closed + escape hatch' },
  { icon: FileText, title: 'Multimodal File Support', desc: 'Upload PDFs, Word docs, Excel spreadsheets, or text files. Text is extracted, PII is masked, and the file is reconstructed in-place — preserving layout, fonts, images, and formulas.', tag: 'PDF · Word · Excel · Text' },
  { icon: Settings, title: 'Control Plane', desc: 'A cloud-based control plane manages policy distribution and audit ingestion — metadata only. Entity types, counts, and timing. Never prompt content, masked or unmasked.', tag: 'metadata only' },
]

const PLATFORM = [
  {
    tag: 'Data Plane', tagClass: 'data', title: 'Local Protection Layer',
    features: [
      'Chrome extension (Manifest V3) with chrome.debugger network-layer interception',
      'Tiered detection: deterministic + GLiNER2-PII via ONNX',
      'Sentence-boundary chunking for long documents',
      'Independent residual scanner (fail-closed last gate)',
      'Per-conversation bijective vault with TTL',
      'Multimodal: PDF, Word, Excel, Text — in-place reconstruction',
      'Response demasking (backend endpoint; extension integration planned)',
      'Per-install token authentication',
    ],
  },
  {
    tag: 'Control Plane', tagClass: 'control', title: 'Governance & Visibility',
    features: [
      'Policy CRUD: entity-type → action mappings (faker/redact/keep)',
      'Versioned policies with export for local backend pull',
      'Audit ingestion: metadata only — never text',
      'Dashboard with real-time charts (Recharts)',
      'Verification stats: entities masked, leak rate, fail-closed events',
      'Filterable audit log with GDPR right-to-erasure',
    ],
  },
]

const FAQ = [
  { q: 'Is this just DLP with an AI sticker on it?', a: 'No. Pattern-matching DLP cannot tell a draft email from a deal memo because prompts are unstructured. We use a tiered approach — deterministic checks for structured PII (cards, keys, JWTs) and a compact NER model for names. The deterministic tier catches what DLP catches; the semantic tier catches what DLP cannot.' },
  { q: 'Does my PII ever leave my machine?', a: 'No. The detection engine, masker, vault, and residual scanner all run locally in a FastAPI backend on 127.0.0.1. Only masked surrogates are sent to the LLM. The cloud control plane — if present — receives metadata only (entity types, counts, timing). Never prompt content.' },
  { q: 'What happens if the backend is down?', a: 'The request is blocked. Fail-closed. A privacy tool that silently passes through raw PII on failure has negative value. A logged escape hatch lets users click "Send anyway, unprotected" if they choose, and that frequency is tracked as a reliability metric.' },
  { q: 'Can it handle files like PDFs and Word docs?', a: 'Yes. The multimodal pipeline extracts text from PDF, Word, Excel, and text files. PII is detected and masked, then the file is reconstructed in-place — preserving layout, fonts, images, and formatting. The masked file is what gets uploaded to ChatGPT.' },
  { q: 'What about long documents?', a: 'Text is split into overlapping chunks at sentence boundaries before semantic inference. This handles GLiNER\'s 512-token context window without losing entities at boundaries. The deterministic tier has no length limit.' },
  { q: 'How do you avoid this becoming employee surveillance?', a: 'The system is designed to govern data, not monitor people. It flags sensitive data in context, not employee identity. The audit log records entity types and counts — not who typed what. The vault stores mappings, not behavioral profiles.' },
]

export default function Landing() {
  const [scrolled, setScrolled] = useState(false)
  const [activeStep, setActiveStep] = useState(0)

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 40)
    window.addEventListener('scroll', onScroll)
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  useEffect(() => {
    const interval = setInterval(() => setActiveStep(s => (s + 1) % STEPS.length), 3000)
    return () => clearInterval(interval)
  }, [])

  return (
    <div className="landing-page">
      {/* NAV */}
      <nav className={`landing-nav ${scrolled ? 'scrolled' : ''}`}>
        <div className="nav-brand">
          <div className="brand-mark"><ShieldCheck size={20} color="#4f8cff" /></div>
          <span>Doppel</span>
        </div>
        <div className="nav-links">
          <a href="#how">How It Works</a>
          <a href="#features">Features</a>
          <a href="#platform">Platform</a>
          <a href="#faq">FAQ</a>
          <a href="/app" className="nav-dashboard">Dashboard →</a>
        </div>
      </nav>

      {/* HERO */}
      <section className="hero">
        <div className="hero-content">
          <div className="hero-badge">
            <span className="badge-dot" />
            PII never leaves your machine
          </div>
          <h1>
            Looks like your data.<br />
            Acts like your data.<br />
            <span className="never">Never is your data.</span>
          </h1>
          <p className="hero-desc">
            Doppel intercepts prompts and file uploads before they reach the LLM,
            masks sensitive data locally, and restores real values in the response.
          </p>
          <div className="hero-buttons">
            <a href="#platform" className="btn-primary">Explore Platform <ArrowRight size={16} /></a>
            <a href="#how" className="btn-secondary">How It Works</a>
          </div>
        </div>
      </section>

      {/* STATS */}
      <div className="stats-row">
        {STATS.map((s, i) => {
          const Icon = s.icon
          return (
            <div key={i} className="stat-item">
              <Icon size={20} color="#4f8cff" />
              <div className="stat-val">{s.value}</div>
              <div className="stat-lbl">{s.label}</div>
            </div>
          )
        })}
      </div>

      {/* HOW IT WORKS */}
      <section className="section" id="how">
        <h2 className="sec-title">How It Works</h2>
        <p className="sec-sub">Five steps. All local. Before anything reaches the LLM.</p>
        <div className="pipeline">
          {STEPS.map((step, i) => {
            const Icon = step.icon
            return (
              <div key={i} className={`pipe-node ${activeStep === i ? 'active' : ''}`}>
                <div className="pipe-icon"><Icon size={22} /></div>
                <div className="pipe-label">{step.label}</div>
                <div className="pipe-desc">{step.desc}</div>
                {i < STEPS.length - 1 && <div className="pipe-arrow">→</div>}
              </div>
            )
          })}
        </div>
      </section>

      {/* FEATURES */}
      <section className="section alt" id="features">
        <h2 className="sec-title">Features</h2>
        <p className="sec-sub">Every architectural decision is designed to keep PII on your device.</p>
        <div className="feature-grid">
          {FEATURES.map((f, i) => {
            const Icon = f.icon
            return (
              <div key={i} className="feat-card">
                <div className="feat-icon"><Icon size={22} color="#4f8cff" /></div>
                <h3>{f.title}</h3>
                <p>{f.desc}</p>
                <span className="feat-tag">{f.tag}</span>
              </div>
            )
          })}
        </div>
      </section>

      {/* EXPLORE PLATFORM */}
      <section className="section" id="platform">
        <h2 className="sec-title">Explore the Platform</h2>
        <p className="sec-sub">Two planes working together — data protection on the device, governance at scale.</p>
        <div className="platform-grid">
          {PLATFORM.map((card, i) => (
            <div key={i} className="plat-card">
              <span className={`plat-tag ${card.tagClass}`}>{card.tag}</span>
              <h3>{card.title}</h3>
              <ul>
                {card.features.map((feat, j) => <li key={j}>{feat}</li>)}
              </ul>
            </div>
          ))}
        </div>
      </section>

      {/* FAQ */}
      <section className="section alt" id="faq">
        <h2 className="sec-title">FAQ</h2>
        <div className="faq-list">
          {FAQ.map((item, i) => (
            <div key={i} className="faq-item">
              <div className="faq-q">{item.q}</div>
              <div className="faq-a">{item.a}</div>
            </div>
          ))}
        </div>
      </section>

      {/* FOOTER */}
      <footer className="landing-footer">
        <div className="footer-brand">
          <div className="brand-mark"><ShieldCheck size={20} color="#4f8cff" /></div>
          <span>Doppel</span>
        </div>
        <p>Looks like your data. Acts like your data. Never is your data.</p>
        <div className="footer-links">
          <a href="#how">How It Works</a>
          <a href="#features">Features</a>
          <a href="#platform">Platform</a>
          <a href="/app">Dashboard</a>
        </div>
        <div className="footer-copy">Graduation Project · 5-person team · 4-week sprint · 2025</div>
      </footer>
    </div>
  )
}
