import { useState, useEffect, useCallback } from 'react'
import api from '@/lib/api'

export interface MovieFormat {
  id: number
  keyword: string
  format: '70MM' | '35MM' | '3D' | '2D' | string
  tier: string
  status: string
  updated_at: string
}

interface ApiMovieFormat {
  id: number
  keyword: string
  format: string
  priority_tier: number
  status: string
  updated_at: string
}

interface PaginatedResponse {
  items: ApiMovieFormat[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

function fromApi(m: ApiMovieFormat): MovieFormat {
  return {
    id: m.id,
    keyword: m.keyword,
    format: m.format,
    tier: `P${m.priority_tier}`,
    status: m.status,
    updated_at: m.updated_at,
  }
}

export interface MovieFormatFilters {
  search?: string
  status?: string
  tier?: string
  format?: string
  page?: number
  pageSize?: number
}

export function useMovieFormats(filters: MovieFormatFilters = {}) {
  const [formats, setFormats] = useState<MovieFormat[]>([])
  const [total, setTotal] = useState(0)
  const [totalPages, setTotalPages] = useState(1)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const page = filters.page ?? 1
  const pageSize = filters.pageSize ?? 50

  const fetchFormats = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      if (filters.search) params.set('search', filters.search)
      if (filters.status) params.set('status', filters.status)
      if (filters.tier) params.set('tier', filters.tier)
      if (filters.format) params.set('format', filters.format)
      params.set('page', String(page))
      params.set('page_size', String(pageSize))

      const res = await api.get<PaginatedResponse>(`/api/v1/movie-formats?${params.toString()}`)
      setFormats(res.data.items.map(fromApi))
      setTotal(res.data.total)
      setTotalPages(res.data.total_pages)
      setError(null)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load movie formats')
    } finally {
      setLoading(false)
    }
  }, [filters.search, filters.status, filters.tier, filters.format, page, pageSize])

  useEffect(() => { void fetchFormats() }, [fetchFormats])

  const createFormat = async (data: Omit<MovieFormat, 'id' | 'updated_at'>) => {
    const res = await api.post<ApiMovieFormat>('/api/v1/movie-formats', {
      keyword: data.keyword,
      format: data.format,
      priority_tier: parseInt(data.tier.replace('P', '')),
      status: data.status,
    })
    const mapped = fromApi(res.data)
    setFormats((prev) => [mapped, ...prev])
    return mapped
  }

  const updateFormat = async (id: number, data: Partial<MovieFormat>) => {
    const patch: Record<string, unknown> = {}
    if (data.keyword !== undefined) patch.keyword = data.keyword
    if (data.format !== undefined) patch.format = data.format
    if (data.tier !== undefined) patch.priority_tier = parseInt(data.tier.replace('P', ''))
    if (data.status !== undefined) patch.status = data.status
    const res = await api.patch<ApiMovieFormat>(`/api/v1/movie-formats/${id}`, patch)
    const mapped = fromApi(res.data)
    setFormats((prev) => prev.map((f) => (f.id === id ? mapped : f)))
    return mapped
  }

  const deleteFormat = async (id: number) => {
    await api.delete(`/api/v1/movie-formats/${id}`)
    setFormats((prev) => prev.filter((f) => f.id !== id))
  }

  return {
    formats,
    total,
    totalPages,
    loading,
    error,
    createFormat,
    updateFormat,
    deleteFormat,
    refetch: fetchFormats,
  }
}
