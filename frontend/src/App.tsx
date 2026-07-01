import React, { useState } from 'react'
import axios from 'axios'

interface DetectionResult {
  screen_format: string
  detected_keyword: string | null
  match_source: string
  match_track: string | null
  priority: number | null
  confidence: number
  fired_ai: boolean
  ai_suggested_format: string | null
  ai_reasoning: string | null
}

function App() {
  const [amenity, setAmenity] = useState('')
  const [circuit, setCircuit] = useState('')
  const [result, setResult] = useState<DetectionResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleDetect = async () => {
    setLoading(true)
    setError(null)
    try {
      const response = await axios.post<DetectionResult>('/api/v1/detect/single', {
        amenity,
        circuit_name: circuit,
      })
      setResult(response.data)
    } catch (err) {
      setError('Detection failed. Is the backend running?')
    } finally {
      setLoading(false)
    }
  }

  return (
    <main style={{ fontFamily: 'system-ui, sans-serif', maxWidth: 640, margin: '3rem auto', padding: '0 1rem' }}>
      <h1 style={{ fontSize: '1.75rem', fontWeight: 700, marginBottom: '1.5rem' }}>
        Amenity Screen Format Detector
      </h1>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', marginBottom: '1rem' }}>
        <label>
          <span style={{ display: 'block', fontWeight: 600, marginBottom: '0.25rem' }}>
            Amenity string
          </span>
          <input
            value={amenity}
            onChange={e => setAmenity(e.target.value)}
            placeholder="e.g. IMAX | VIP 19+"
            style={{ width: '100%', padding: '0.5rem', border: '1px solid #ccc', borderRadius: 4, boxSizing: 'border-box' }}
          />
        </label>

        <label>
          <span style={{ display: 'block', fontWeight: 600, marginBottom: '0.25rem' }}>
            Circuit name (optional)
          </span>
          <input
            value={circuit}
            onChange={e => setCircuit(e.target.value)}
            placeholder="e.g. Cineplex Entertainment"
            style={{ width: '100%', padding: '0.5rem', border: '1px solid #ccc', borderRadius: 4, boxSizing: 'border-box' }}
          />
        </label>

        <button
          onClick={handleDetect}
          disabled={loading}
          style={{
            padding: '0.6rem 1.25rem',
            background: '#1d4ed8',
            color: '#fff',
            border: 'none',
            borderRadius: 4,
            cursor: loading ? 'not-allowed' : 'pointer',
            fontWeight: 600,
            alignSelf: 'flex-start',
          }}
        >
          {loading ? 'Detecting...' : 'Detect'}
        </button>
      </div>

      {error && (
        <p style={{ color: '#dc2626', marginBottom: '1rem' }}>{error}</p>
      )}

      {result && (
        <section
          aria-label="Detection result"
          style={{ background: '#f8fafc', border: '1px solid #e2e8f0', borderRadius: 8, padding: '1rem' }}
        >
          <h2 style={{ margin: '0 0 0.75rem', fontSize: '1.1rem' }}>Result</h2>
          <dl style={{ display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '0.35rem 1rem', margin: 0 }}>
            {Object.entries(result).map(([k, v]) => (
              <React.Fragment key={k}>
                <dt style={{ fontWeight: 600, color: '#475569' }}>{k}</dt>
                <dd style={{ margin: 0 }}>{String(v)}</dd>
              </React.Fragment>
            ))}
          </dl>
        </section>
      )}
    </main>
  )
}

export default App
