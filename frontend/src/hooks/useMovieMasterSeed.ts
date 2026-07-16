import { useState } from 'react'

interface SeedResult {
  previously_seeded: number
  inserted: number
  updated: number
  skipped: number
  total_in_file: number
}

interface SeedState {
  loading: boolean
  result: SeedResult | null
  error: string | null
}

export function useMovieMasterSeed() {
  const [state, setState] = useState<SeedState>({ loading: false, result: null, error: null })

  async function uploadFile(file: File) {
    setState({ loading: true, result: null, error: null })
    try {
      const form = new FormData()
      form.append('file', file)
      const res = await fetch('/api/v1/movie-title-match/master/seed', {
        method: 'POST',
        body: form,
      })
      if (!res.ok) {
        const text = await res.text()
        throw new Error(text || `Server error ${res.status}`)
      }
      const data: SeedResult = await res.json()
      setState({ loading: false, result: data, error: null })
    } catch (err: unknown) {
      setState({ loading: false, result: null, error: err instanceof Error ? err.message : 'Upload failed' })
    }
  }

  async function fetchCount(): Promise<number> {
    const res = await fetch('/api/v1/movie-title-match/master/count')
    if (!res.ok) return 0
    const data = await res.json()
    return data.count ?? 0
  }

  function reset() {
    setState({ loading: false, result: null, error: null })
  }

  return { ...state, uploadFile, fetchCount, reset }
}
