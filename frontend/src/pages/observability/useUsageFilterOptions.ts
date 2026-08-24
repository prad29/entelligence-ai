import { useMemo } from 'react'
import { useUsageBreakdown, type UsageFilterState } from '@/hooks/useUsage'
import { shortenModelId } from './labels'

/** `model_id` and `api_key_id` have no enumerating endpoint, so option lists
 *  are derived from `/breakdown`. Each list is fetched with the filter it
 *  populates removed, so picking model X doesn't shrink the model list to
 *  just X — but the *other* four filters (task type, caller, api key/model,
 *  market) still narrow it, which is the whole point. */
export function useUsageFilterOptions(filters: UsageFilterState) {
  const modelScope = useMemo(() => ({ ...filters, modelId: '' }), [filters])
  const apiKeyScope = useMemo(() => ({ ...filters, apiKeyId: '' }), [filters])

  const models = useUsageBreakdown('model_id', modelScope)
  const apiKeys = useUsageBreakdown('api_key_id', apiKeyScope)

  const modelOptions = useMemo(() => {
    const fetched = (models.data?.rows ?? [])
      .map((r) => r.model_id ?? '')
      .filter((v) => v !== '')
    // Guard a real Radix Select behaviour: if the currently selected model
    // fell out of the fetched list (e.g. the range moved past it), append
    // it so the trigger doesn't render a blank/placeholder value.
    if (filters.modelId && !fetched.includes(filters.modelId)) fetched.push(filters.modelId)
    return [
      { value: '', label: 'All models' },
      ...fetched.map((v) => ({ value: v, label: shortenModelId(v) })),
    ]
  }, [models.data, filters.modelId])

  const apiKeyOptions = useMemo(() => {
    const fetched = (apiKeys.data?.rows ?? [])
      .map((r) => r.api_key_id ?? '')
      .filter((v) => v !== '')
    if (filters.apiKeyId && !fetched.includes(filters.apiKeyId)) fetched.push(filters.apiKeyId)
    return [
      { value: '', label: 'All API keys' },
      ...fetched.map((v) => ({ value: v, label: v })),
    ]
  }, [apiKeys.data, filters.apiKeyId])

  return { modelOptions, apiKeyOptions, loading: models.loading || apiKeys.loading }
}
