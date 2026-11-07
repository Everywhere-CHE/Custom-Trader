/** 认证状态管理：登录/登出、Token 持久化。 */

import { create } from 'zustand'
import { http } from '../api/http'

interface AuthState {
  /** 登录用户名（null=未登录） */
  username: string | null
  /** 登录成功后设置 token，登出时清除 */
  login: (username: string, password: string) => Promise<void>
  /** 登出 */
  logout: () => void
  /** 修改密码 */
  changePassword: (oldPwd: string, newPwd: string) => Promise<void>
  /** 验证当前 token 是否有效 */
  checkAuth: () => Promise<boolean>
}

function getToken(): string | null {
  return localStorage.getItem('auth_token')
}

function setToken(token: string | null) {
  if (token) {
    localStorage.setItem('auth_token', token)
  } else {
    localStorage.removeItem('auth_token')
  }
}

export const useAuthStore = create<AuthState>(set => ({
  username: null,

  login: async (username, password) => {
    const res = await http.post<{ access_token: string; username: string }>(
      '/auth/login',
      { username, password },
    )
    setToken(res.data.access_token)
    set({ username: res.data.username })
  },

  logout: () => {
    setToken(null)
    set({ username: null })
  },

  changePassword: async (oldPwd, newPwd) => {
    await http.put('/auth/password', {
      old_password: oldPwd,
      new_password: newPwd,
    })
  },

  checkAuth: async () => {
    const token = getToken()
    if (!token) {
      set({ username: null })
      return false
    }
    try {
      const res = await http.post<{ username: string }>('/auth/me', {}, {
        headers: { Authorization: `Bearer ${token}` },
      })
      set({ username: res.data.username })
      return true
    } catch {
      setToken(null)
      set({ username: null })
      return false
    }
  },
}))

/** 获取当前 token 用于 API 请求头。 */
export function getAuthToken(): string | null {
  return getToken()
}