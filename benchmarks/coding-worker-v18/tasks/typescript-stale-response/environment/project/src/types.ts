export type Profile = { id: string; label: string }
export type SearchState = {
  query: string
  loading: boolean
  results: Profile[]
  error: string | null
}
