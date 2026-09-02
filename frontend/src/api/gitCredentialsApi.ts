import api from './client'

export type GitProvider = 'gitlab' | 'github' | 'generic'

// A stored Git credential as returned by the API. The token itself is never
// returned — only a masked hint (last 4 chars).
export interface GitCredential {
  id: number
  provider: GitProvider
  host: string
  label: string | null
  token_hint: string | null
  created_at: string
  updated_at: string
  last_used_at: string | null
}

export interface GitCredentialCreate {
  provider: GitProvider
  host?: string | null
  label?: string | null
  token: string
}

export interface GitCredentialUpdate {
  label?: string | null
  token?: string | null
}

const BASE = '/settings/git-credentials'

export const gitCredentialsApi = {
  list: async (): Promise<GitCredential[]> => {
    const { data } = await api.get<GitCredential[]>(BASE)
    return data
  },
  create: async (payload: GitCredentialCreate): Promise<GitCredential> => {
    const { data } = await api.post<GitCredential>(BASE, payload)
    return data
  },
  update: async (id: number, payload: GitCredentialUpdate): Promise<GitCredential> => {
    const { data } = await api.put<GitCredential>(`${BASE}/${id}`, payload)
    return data
  },
  remove: async (id: number): Promise<void> => {
    await api.delete(`${BASE}/${id}`)
  },
}

export default gitCredentialsApi
