import type { FetchProfiles } from './api'
import { useProfileSearch } from './useProfileSearch'

export function ProfileSearch({ query, fetchProfiles }: { query: string; fetchProfiles: FetchProfiles }) {
  const state = useProfileSearch(query, fetchProfiles)
  return <section aria-busy={state.loading}><p>{state.query}</p><ul>{state.results.map(item => <li key={item.id}>{item.label}</li>)}</ul>{state.error && <p role="alert">{state.error}</p>}</section>
}
