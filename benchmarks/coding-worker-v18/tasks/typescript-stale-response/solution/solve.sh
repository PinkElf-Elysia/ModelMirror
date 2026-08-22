#!/bin/sh
set -eu
cat > /workspace/src/types.ts <<'TS'
export type Profile = { id: string; label: string }
export type SearchState = { query: string; loading: boolean; results: Profile[]; error: string | null }
export type RequestIdentity = { generation: number; query: string }
TS
cat > /workspace/src/useProfileSearch.tsx <<'TS'
import { useEffect, useRef, useState } from 'react'
import type { FetchProfiles } from './api'
import type { RequestIdentity, SearchState } from './types'

export function useProfileSearch(query: string, fetchProfiles: FetchProfiles): SearchState {
  const [state, setState] = useState<SearchState>({ query: '', loading: false, results: [], error: null })
  const generation = useRef(0)
  useEffect(() => {
    const identity: RequestIdentity = { generation: ++generation.current, query: query.trim() }
    let mounted = true
    if (!identity.query) {
      setState({ query: identity.query, loading: false, results: [], error: null })
      return () => { mounted = false }
    }
    setState(previous => ({ ...previous, query: identity.query, loading: true, error: null }))
    fetchProfiles(identity.query).then(
      results => {
        if (mounted && generation.current === identity.generation) setState({ query: identity.query, loading: false, results, error: null })
      },
      error => {
        if (mounted && generation.current === identity.generation) setState(previous => ({ ...previous, loading: false, error: String(error) }))
      },
    )
    return () => { mounted = false }
  }, [query, fetchProfiles])
  return state
}
TS

