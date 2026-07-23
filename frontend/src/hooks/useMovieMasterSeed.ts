import { useState } from 'react'

interface SeedResult {
  previously_seeded: number
  inserted: number
  updated: number
  skipped: number
  skipped_undefined_country?: number
  total_in_file: number
}

interface SeedState {
  loading: boolean
  result: SeedResult | null
  error: string | null
}

export type MovieMasterMarket = 'domestic' | 'international'

const SEED_ENDPOINT: Record<MovieMasterMarket, string> = {
  domestic: '/api/v1/movie-title-match/master/seed',
  international: '/api/v1/movie-title-match/master/intl/seed',
}

const COUNT_ENDPOINT: Record<MovieMasterMarket, string> = {
  domestic: '/api/v1/movie-title-match/master/count',
  international: '/api/v1/movie-title-match/master/intl/count',
}

export function useMovieMasterSeed(market: MovieMasterMarket = 'domestic') {
  const [state, setState] = useState<SeedState>({ loading: false, result: null, error: null })

  async function uploadFile(file: File) {
    setState({ loading: true, result: null, error: null })
    try {
      const form = new FormData()
      form.append('file', file)
      const res = await fetch(SEED_ENDPOINT[market], {
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
    const res = await fetch(COUNT_ENDPOINT[market])
    if (!res.ok) return 0
    const data = await res.json()
    return data.count ?? 0
  }

  function reset() {
    setState({ loading: false, result: null, error: null })
  }

  return { ...state, uploadFile, fetchCount, reset }
}
