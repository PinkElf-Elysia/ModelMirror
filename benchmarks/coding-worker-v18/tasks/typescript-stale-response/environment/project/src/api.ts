import type { Profile } from './types'

export type FetchProfiles = (query: string, signal?: AbortSignal) => Promise<Profile[]>
