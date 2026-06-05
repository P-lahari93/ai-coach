import axios from 'axios'

const BASE_URL = import.meta.env.VITE_API_URL || '/api/v1'

export const api = axios.create({
  baseURL: BASE_URL,
  headers: { 'Content-Type': 'application/json' },
})

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

api.interceptors.response.use(
  (r) => r,
  async (error) => {
    if (error.response?.status === 401) {
      const refreshToken = localStorage.getItem('refresh_token')
      if (refreshToken) {
        try {
          const { data } = await axios.post(`${BASE_URL}/auth/refresh`, { refresh_token: refreshToken })
          localStorage.setItem('access_token', data.access_token)
          localStorage.setItem('refresh_token', data.refresh_token)
          error.config.headers.Authorization = `Bearer ${data.access_token}`
          return api(error.config)
        } catch {
          localStorage.removeItem('access_token')
          localStorage.removeItem('refresh_token')
          window.location.href = '/login'
        }
      }
    }
    return Promise.reject(error)
  }
)

export const authApi = {
  login: (email: string, password: string) => api.post('/auth/login', { email, password }),
  register: (email: string, password: string, full_name: string) => api.post('/auth/register', { email, password, full_name }),
  logout: (refresh_token: string) => api.post('/auth/logout', { refresh_token }),
  me: () => api.get('/auth/me'),
}

export const modulesApi = {
  list: (params?: Record<string, unknown>) => api.get('/modules/', { params }),
  get: (id: string) => api.get(`/modules/${id}`),
}

export const sessionsApi = {
  createCoaching: (module_id: string) => api.post('/sessions/coaching', { module_id }),
  listCoaching: (params?: Record<string, unknown>) => api.get('/sessions/coaching', { params }),
  getCoaching: (id: string) => api.get(`/sessions/coaching/${id}`),
  completeCoaching: (id: string, intake_data: Record<string, string>) => api.post(`/sessions/coaching/${id}/complete`, { intake_data }),
  abandonCoaching: (id: string) => api.post(`/sessions/coaching/${id}/abandon`),
  createRoleplay: (module_id: string, persona_id?: string, scenario_prompt?: string) => api.post('/sessions/roleplay', { module_id, persona_id, scenario_prompt }),
  listRoleplay: (params?: Record<string, unknown>) => api.get('/sessions/roleplay', { params }),
  getRoleplay: (id: string) => api.get(`/sessions/roleplay/${id}`),
  submitTurn: (id: string, content: string) => api.post(`/sessions/roleplay/${id}/turn`, { content }),
  completeRoleplay: (id: string) => api.post(`/sessions/roleplay/${id}/complete`),
}

export const feedbackApi = {
  get: (id: string) => api.get(`/feedback/${id}`),
  rate: (id: string, rating: number, notes?: string) => api.post(`/feedback/${id}/rate`, { rating, notes }),
}

export const progressApi = {
  list: () => api.get('/progress/'),
  getModule: (module_id: string) => api.get(`/progress/module/${module_id}`),
  leaderboard: (module_id: string) => api.get(`/progress/leaderboard/${module_id}`),
}

export const notificationsApi = {
  list: (params?: Record<string, unknown>) => api.get('/progress/notifications', { params }),
  unreadCount: () => api.get('/progress/notifications/unread-count'),
  markRead: (id: string) => api.patch(`/progress/notifications/${id}`, { is_read: true }),
  markAllRead: () => api.post('/progress/notifications/mark-all-read'),
}

export const knowledgeApi = {
  list: () => api.get('/knowledge/'),
  get: (id: string) => api.get(`/knowledge/${id}`),
  create: (name: string, description?: string) => api.post('/knowledge/', { name, description }),
  delete: (id: string) => api.delete(`/knowledge/${id}`),
  listSources: (kb_id: string) => api.get(`/knowledge/${kb_id}/sources`),
  addText: (kb_id: string, title: string, content: string) => api.post(`/knowledge/${kb_id}/sources/text`, { title, content }),
  deleteSource: (kb_id: string, source_id: string) => api.delete(`/knowledge/${kb_id}/sources/${source_id}`),
}
