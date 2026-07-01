import { useState, useEffect, useCallback } from 'react'
import api from '@/lib/api'

export interface Amenity {
  id: string
  keyword: string
  screen_format: string
  tier: 'P1' | 'P2' | 'P3' | 'P4' | 'P5' | 'P6'
  circuit: string | null
  status: 'active' | 'pending' | 'inactive'
  updated_at: string
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

      const res = await api.get<Amenity[]>(`/api/v1/amenities?${params.toString()}`)
      setAmenities(res.data)
      setError(null)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load amenities')
    } finally {
      setLoading(false)
    }
  }, [filters.search, filters.status, filters.tier])

  useEffect(() => { void fetchAmenities() }, [fetchAmenities])

  const createAmenity = async (data: Omit<Amenity, 'id' | 'updated_at'>) => {
    const res = await api.post<Amenity>('/api/v1/amenities', data)
    setAmenities((prev) => [res.data, ...prev])
    return res.data
  }

  const updateAmenity = async (id: string, data: Partial<Amenity>) => {
    const res = await api.patch<Amenity>(`/api/v1/amenities/${id}`, data)
    setAmenities((prev) => prev.map((a) => (a.id === id ? res.data : a)))
    return res.data
  }

  const deleteAmenity = async (id: string) => {
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
