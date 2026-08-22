import { useEffect, useState } from 'react'
import type { FetchProfiles } from './api'
import type { SearchState } from './types'

export function useProfileSearch(query: string, fetchProfiles: FetchProfiles): SearchState {
  const [state, setState] = useState<SearchState>({ query: '', loading: false, results: [], error: null })
  useEffect(() => {
    const normalized = query.trim()
    if (!normalized) {
      setState({ query: normalized, loading: false, results: [], error: null })
      return
    }
    setState(previous => ({ ...previous, query: normalized, loading: true, error: null }))
    fetchProfiles(normalized).then(
      results => setState({ query: normalized, loading: false, results, error: null }),
      error => setState(previous => ({ ...previous, loading: false, error: String(error) })),
    )
  }, [query, fetchProfiles])
  return state
}
