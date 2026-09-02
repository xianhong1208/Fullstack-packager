import type { ReactNode } from 'react'
import { useAuth } from '../contexts/AuthContext'

interface PermissionGateProps {
  /** Required permission code(s) */
  permission?: string | string[]
  /** Require ALL permissions (AND) vs ANY permission (OR). Default: false (any) */
  requireAll?: boolean
  /** Fallback content when permission is denied */
  fallback?: ReactNode
  /** Children to render when permission is granted */
  children: ReactNode
}

/**
 * Permission-based conditional rendering component.
 *
 * @example
 * // Single permission
 * <PermissionGate permission="user:manage">
 *   <AdminPanel />
 * </PermissionGate>
 *
 * @example
 * // Multiple permissions (OR - any one is sufficient)
 * <PermissionGate permission={["task:view_own", "task:view_all"]}>
 *   <TaskList />
 * </PermissionGate>
 *
 * @example
 * // Multiple permissions (AND - all required)
 * <PermissionGate permission={["user:view", "user:manage"]} requireAll>
 *   <UserManagement />
 * </PermissionGate>
 *
 * @example
 * // With fallback
 * <PermissionGate permission="history:export" fallback={<p>You don't have export permission</p>}>
 *   <ExportButton />
 * </PermissionGate>
 */
export function PermissionGate({
  permission,
  requireAll = false,
  fallback = null,
  children,
}: PermissionGateProps) {
  const { hasPermission, hasAnyPermission } = useAuth()

  // If no permission specified, always render children
  if (!permission) {
    return <>{children}</>
  }

  const permissions = Array.isArray(permission) ? permission : [permission]

  let hasAccess: boolean
  if (requireAll) {
    // AND logic: user must have ALL permissions
    hasAccess = permissions.every((p) => hasPermission(p))
  } else {
    // OR logic: user must have ANY permission
    hasAccess = hasAnyPermission(...permissions)
  }

  if (!hasAccess) {
    return <>{fallback}</>
  }

  return <>{children}</>
}

/**
 * Hook for programmatic permission checking.
 * Useful when you need to check permissions outside JSX.
 */
export function usePermission() {
  const { hasPermission, hasAnyPermission, permissions } = useAuth()

  return {
    /** Check if user has a specific permission */
    can: hasPermission,
    /** Check if user has any of the specified permissions */
    canAny: hasAnyPermission,
    /** Check if user has all of the specified permissions */
    canAll: (...codes: string[]) => codes.every((code) => hasPermission(code)),
    /** All user permissions as a Set */
    permissions,
  }
}

export default PermissionGate
