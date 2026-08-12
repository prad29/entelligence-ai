import { useState, useEffect, useCallback } from 'react'
import api from '@/lib/api'

export interface IntlAmenity {
  id: number
  keyword: string
  screen_format: string
  tier: string
  status: string
  updated_at: string
}

interface ApiIntlAmenity {
  id: number
  amenity_keyword: string
  screen_format: string
  priority_tier: number
  status: string
  updated_at: string
}

interface PaginatedResponse {
  items: ApiIntlAmenity[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

function fromApi(a: ApiIntlAmenity): IntlAmenity {
  return {
    id: a.id,
    keyword: a.amenity_keyword,
    screen_format: a.screen_format,
    tier: `P${a.priority_tier}`,
    status: a.status,
    updated_at: a.updated_at,
  }
}

export interface IntlAmenityFilters {
  search?: string
  status?: string
  tier?: string
  page?: number
  pageSize?: number
}

export function useIntlAmenities(filters: IntlAmenityFilters = {}) {
  const [amenities, setAmenities] = useState<IntlAmenity[]>([])
  const [total, setTotal] = useState(0)
  const [totalPages, setTotalPages] = useState(1)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const page = filters.page ?? 1
  const pageSize = filters.pageSize ?? 50

  const fetchAmenities = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      if (filters.search) params.set('search', filters.search)
      if (filters.status) params.set('status', filters.status)
      if (filters.tier) params.set('tier', filters.tier)
      params.set('page', String(page))
      params.set('page_size', String(pageSize))

      const res = await api.get<PaginatedResponse>(`/api/v1/intl-amenities?${params.toString()}`)
      setAmenities(res.data.items.map(fromApi))
      setTotal(res.data.total)
      setTotalPages(res.data.total_pages)
      setError(null)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load amenities')
    } finally {
      setLoading(false)
    }
  }, [filters.search, filters.status, filters.tier, page, pageSize])

  useEffect(() => { void fetchAmenities() }, [fetchAmenities])

  const createAmenity = async (data: Omit<IntlAmenity, 'id' | 'updated_at'>) => {
    const res = await api.post<ApiIntlAmenity>('/api/v1/intl-amenities', {
      amenity_keyword: data.keyword,
      screen_format: data.screen_format,
      priority_tier: parseInt(data.tier.replace('P', '')),
    })
    const mapped = fromApi(res.data)
    setAmenities((prev) => [mapped, ...prev])
    return mapped
  }

  const updateAmenity = async (id: number, data: Partial<IntlAmenity>) => {
    const patch: Record<string, unknown> = {}
    if (data.keyword !== undefined) patch.amenity_keyword = data.keyword
    if (data.screen_format !== undefined) patch.screen_format = data.screen_format
    if (data.tier !== undefined) patch.priority_tier = parseInt(data.tier.replace('P', ''))
    if (data.status !== undefined) patch.status = data.status
    const res = await api.patch<ApiIntlAmenity>(`/api/v1/intl-amenities/${id}`, patch)
    const mapped = fromApi(res.data)
    setAmenities((prev) => prev.map((a) => (a.id === id ? mapped : a)))
    return mapped
  }

  const deleteAmenity = async (id: number) => {
    await api.delete(`/api/v1/intl-amenities/${id}`)
    setAmenities((prev) => prev.filter((a) => a.id !== id))
  }

  const approveAmenity = async (id: number) => {
    await api.post(`/api/v1/intl-amenities/${id}/approve`)
    setAmenities((prev) => prev.map((a) => (a.id === id ? { ...a, status: 'approved' } : a)))
  }

  const rejectAmenity = async (id: number, reason?: string) => {
    await api.post(`/api/v1/intl-amenities/${id}/reject`, { reason: reason ?? null })
    setAmenities((prev) => prev.map((a) => (a.id === id ? { ...a, status: 'rejected' } : a)))
  }

  return {
    amenities,
    total,
    totalPages,
    loading,
    error,
    createAmenity,
    updateAmenity,
    deleteAmenity,
    approveAmenity,
    rejectAmenity,
    refetch: fetchAmenities,
  }
}
