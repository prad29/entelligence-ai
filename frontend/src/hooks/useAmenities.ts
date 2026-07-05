import { useState, useEffect, useCallback } from 'react'
import api from '@/lib/api'

export interface Amenity {
  id: number
  keyword: string
  screen_format: string
  tier: string
  circuit: string | null
  status: string
  updated_at: string
}

interface ApiAmenity {
  id: number
  amenity_keyword: string
  screen_format: string
  priority_tier: number
  circuit_name: string | null
  status: string
  updated_at: string
}

function fromApi(a: ApiAmenity): Amenity {
  return {
    id: a.id,
    keyword: a.amenity_keyword,
    screen_format: a.screen_format,
    tier: `P${a.priority_tier}`,
    circuit: a.circuit_name,
    status: a.status,
    updated_at: a.updated_at,
  }
}

export interface AmenityFilters {
  search?: string
  status?: string
  tier?: string
}

export function useAmenities(filters: AmenityFilters = {}) {
  const [amenities, setAmenities] = useState<Amenity[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetchAmenities = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      if (filters.search) params.set('search', filters.search)
      if (filters.status) params.set('status', filters.status)
      if (filters.tier) params.set('tier', filters.tier)

      const res = await api.get<ApiAmenity[]>(`/api/v1/amenities?${params.toString()}`)
      setAmenities(res.data.map(fromApi))
      setError(null)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load amenities')
    } finally {
      setLoading(false)
    }
  }, [filters.search, filters.status, filters.tier])

  useEffect(() => { void fetchAmenities() }, [fetchAmenities])

  const createAmenity = async (data: Omit<Amenity, 'id' | 'updated_at'>) => {
    const res = await api.post<ApiAmenity>('/api/v1/amenities', {
      amenity_keyword: data.keyword,
      screen_format: data.screen_format,
      priority_tier: parseInt(data.tier.replace('P', '')),
      circuit_name: data.circuit ?? null,
      status: data.status,
    })
    const mapped = fromApi(res.data)
    setAmenities((prev) => [mapped, ...prev])
    return mapped
  }

  const updateAmenity = async (id: number, data: Partial<Amenity>) => {
    const patch: Record<string, unknown> = {}
    if (data.keyword !== undefined) patch.amenity_keyword = data.keyword
    if (data.screen_format !== undefined) patch.screen_format = data.screen_format
    if (data.tier !== undefined) patch.priority_tier = parseInt(data.tier.replace('P', ''))
    if (data.circuit !== undefined) patch.circuit_name = data.circuit ?? null
    if (data.status !== undefined) patch.status = data.status
    const res = await api.patch<ApiAmenity>(`/api/v1/amenities/${id}`, patch)
    const mapped = fromApi(res.data)
    setAmenities((prev) => prev.map((a) => (a.id === id ? mapped : a)))
    return mapped
  }

  const deleteAmenity = async (id: number) => {
    await api.delete(`/api/v1/amenities/${id}`)
    setAmenities((prev) => prev.filter((a) => a.id !== id))
  }

  return {
    amenities,
    loading,
    error,
    createAmenity,
    updateAmenity,
    deleteAmenity,
    refetch: fetchAmenities,
  }
}
