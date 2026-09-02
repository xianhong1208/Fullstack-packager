import api from './client'
import type {
  User,
  UserListItem,
  Role,
  RoleCreate,
  RoleUpdate,
  PermissionCategory,
  LoginHistoryList,
} from './types'

export const userApi = {
  // ===== User List & Details =====

  getUsers: async (params?: {
    skip?: number
    limit?: number
    search?: string
    is_active?: boolean
    role_id?: number
  }): Promise<UserListItem[]> => {
    const response = await api.get<UserListItem[]>('/users', { params })
    return response.data
  },

  getUsersCount: async (params?: {
    search?: string
    is_active?: boolean
    role_id?: number
  }): Promise<{ count: number }> => {
    const response = await api.get<{ count: number }>('/users/count', { params })
    return response.data
  },

  getUser: async (userId: number): Promise<User> => {
    const response = await api.get<User>(`/users/${userId}`)
    return response.data
  },

  // ===== User Management =====

  updateUserRole: async (userId: number, roleId: number): Promise<{ message: string }> => {
    const response = await api.put<{ message: string }>(`/users/${userId}/role`, {
      role_id: roleId,
    })
    return response.data
  },

  updateUserStatus: async (userId: number, isActive: boolean): Promise<{ message: string }> => {
    const response = await api.put<{ message: string }>(`/users/${userId}/status`, {
      is_active: isActive,
    })
    return response.data
  },

  resetUserPassword: async (userId: number, newPassword: string): Promise<{ message: string }> => {
    const response = await api.post<{ message: string }>(`/users/${userId}/reset-password`, {
      new_password: newPassword,
    })
    return response.data
  },

  getUserLoginHistory: async (
    userId: number,
    limit = 50,
    offset = 0
  ): Promise<LoginHistoryList> => {
    const response = await api.get<LoginHistoryList>(`/users/${userId}/login-history`, {
      params: { limit, offset },
    })
    return response.data
  },

  // ===== Roles & Permissions =====

  getRoles: async (): Promise<Role[]> => {
    const response = await api.get<Role[]>('/users/roles/list')
    return response.data
  },

  getPermissions: async (): Promise<PermissionCategory[]> => {
    const response = await api.get<PermissionCategory[]>('/users/permissions/list')
    return response.data
  },

  // ===== Role Management (CRUD) =====

  getRole: async (roleId: number): Promise<Role> => {
    const response = await api.get<Role>(`/users/roles/${roleId}`)
    return response.data
  },

  createRole: async (data: RoleCreate): Promise<Role> => {
    const response = await api.post<Role>('/users/roles', data)
    return response.data
  },

  updateRole: async (roleId: number, data: RoleUpdate): Promise<Role> => {
    const response = await api.put<Role>(`/users/roles/${roleId}`, data)
    return response.data
  },

  deleteRole: async (roleId: number): Promise<{ message: string }> => {
    const response = await api.delete<{ message: string }>(`/users/roles/${roleId}`)
    return response.data
  },
}

export default userApi
