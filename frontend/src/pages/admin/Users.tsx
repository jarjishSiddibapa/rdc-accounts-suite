import { useEffect, useMemo, useState } from 'react'
import { Archive, Building2, Filter, KeyRound, Plus, Power, RotateCcw, ShieldCheck, UserRoundPen } from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { Button } from '@/components/Button'
import { Modal } from '@/components/Modal'
import { Pagination } from '@/components/Pagination'
import { PasswordInput } from '@/components/PasswordInput'
import { PasswordStrengthMeter } from '@/components/PasswordStrengthMeter'
import { SearchBox } from '@/components/SearchBox'
import { LoadingNotice } from '@/components/LoadingNotice'
import { useDebouncedValue } from '@/hooks/useDebouncedValue'
import { formatIndianDate, formatIndianNumber } from '@/lib/regional'
import { generateStrongPassword, scorePasswordStrength } from '@/lib/passwordStrength'
import { cn } from '@/utils/cn'
import { ApiError, del, get, post, put } from '@/lib/api'
import { getUserDisplayName, getUserFullName } from '@/lib/user'

interface AdminUser {
  id: number
  email: string
  first_name: string | null
  last_name: string | null
  role: 'admin' | 'user'
  created_at: string
  is_active: boolean
  is_deleted: boolean
  allowed_apps: string[] | null
}

interface AppInfo {
  key: string
  label: string
  company: 'RDC' | 'Ultrafine'
  collaborator: string | null
}

type CompanyFilter = 'all' | AppInfo['company']
type UserRoleFilter = 'all' | AdminUser['role']
type UserScopeFilter = 'current' | 'active' | 'inactive' | 'archived' | 'all'

export default function Users() {
  const [users, setUsers] = useState<AdminUser[]>([])
  const [userTotal, setUserTotal] = useState(0)
  const [userPage, setUserPage] = useState(1)
  const [userPageSize, setUserPageSize] = useState(25)
  const [userSearch, setUserSearch] = useState('')
  const [userRole, setUserRole] = useState<UserRoleFilter>('all')
  const [userScope, setUserScope] = useState<UserScopeFilter>('current')
  const [apps, setApps] = useState<AppInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const debouncedUserSearch = useDebouncedValue(userSearch)

  const [addOpen, setAddOpen] = useState(false)
  const [newFirstName, setNewFirstName] = useState('')
  const [newLastName, setNewLastName] = useState('')
  const [newEmail, setNewEmail] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [newRole, setNewRole] = useState<'admin' | 'user'>('user')
  const [saving, setSaving] = useState(false)

  const [editTarget, setEditTarget] = useState<AdminUser | null>(null)
  const [editFirstName, setEditFirstName] = useState('')
  const [editLastName, setEditLastName] = useState('')
  const [editEmailValue, setEditEmailValue] = useState('')

  const [resetTarget, setResetTarget] = useState<AdminUser | null>(null)
  const [resetPassword, setResetPassword] = useState('')

  const [permsTarget, setPermsTarget] = useState<AdminUser | null>(null)
  const [permsSelected, setPermsSelected] = useState<Set<string>>(new Set())
  const [permsSearch, setPermsSearch] = useState('')
  const [permsCompany, setPermsCompany] = useState<CompanyFilter>('all')

  const [catalogOpen, setCatalogOpen] = useState(false)
  const [catalogSearch, setCatalogSearch] = useState('')
  const [catalogCompany, setCatalogCompany] = useState<CompanyFilter>('all')
  const [companySavingKey, setCompanySavingKey] = useState<string | null>(null)
  const [collaboratorSavingKey, setCollaboratorSavingKey] = useState<string | null>(null)

  const [deleteTarget, setDeleteTarget] = useState<AdminUser | null>(null)
  const [busy, setBusy] = useState(false)

  async function loadUsers() {
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams({
        paginated: 'true',
        limit: String(userPageSize),
        offset: String((userPage - 1) * userPageSize),
      })
      if (debouncedUserSearch.trim()) params.set('search', debouncedUserSearch.trim())
      if (userRole !== 'all') params.set('role', userRole)
      if (userScope === 'all') params.set('include_archived', 'true')
      else if (userScope !== 'current') params.set('status', userScope)

      const userData = await get<{ total: number; items: AdminUser[] }>(
        `/admin/users?${params.toString()}`,
      )
      setUsers(userData.items)
      setUserTotal(userData.total)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to load users.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadUsers()
  }, [debouncedUserSearch, userPage, userPageSize, userRole, userScope])

  useEffect(() => {
    void get<AppInfo[]>('/admin/apps')
      .then(setApps)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Failed to load applications.'))
  }, [])

  async function handleAdd() {
    if (!scorePasswordStrength(newPassword).isAcceptable) {
      setError('Choose a stronger password. Use at least 10 characters with uppercase, lowercase, a number, and a symbol.')
      return
    }
    setSaving(true)
    try {
      await post('/admin/users', {
        first_name: newFirstName,
        last_name: newLastName,
        email: newEmail,
        password: newPassword,
        role: newRole,
      })
      setAddOpen(false)
      setNewFirstName('')
      setNewLastName('')
      setNewEmail('')
      setNewPassword('')
      setNewRole('user')
      await loadUsers()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to create user.')
    } finally {
      setSaving(false)
    }
  }

  async function handleEditProfile() {
    if (!editTarget) return
    setBusy(true)
    try {
      await put(`/admin/users/${editTarget.id}/profile`, {
        first_name: editFirstName,
        last_name: editLastName,
        email: editEmailValue,
      })
      setEditTarget(null)
      await loadUsers()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to update user details.')
    } finally {
      setBusy(false)
    }
  }

  async function handleResetPassword() {
    if (!resetTarget) return
    if (!scorePasswordStrength(resetPassword).isAcceptable) {
      setError('Choose a stronger password. Use at least 10 characters with uppercase, lowercase, a number, and a symbol.')
      return
    }
    setBusy(true)
    try {
      await post(`/admin/users/${resetTarget.id}/reset-password`, { password: resetPassword })
      setResetTarget(null)
      setResetPassword('')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to reset password.')
    } finally {
      setBusy(false)
    }
  }

  function openPerms(u: AdminUser) {
    setPermsTarget(u)
    setPermsSelected(new Set(u.allowed_apps ?? []))
    setPermsSearch('')
    setPermsCompany('all')
  }

  async function handleSavePerms() {
    if (!permsTarget) return
    setBusy(true)
    try {
      await put(`/admin/users/${permsTarget.id}/permissions`, {
        allowed_apps: Array.from(permsSelected),
      })
      setPermsTarget(null)
      await loadUsers()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to update permissions.')
    } finally {
      setBusy(false)
    }
  }

  async function handleCompanyChange(appKey: string, company: AppInfo['company']) {
    setCompanySavingKey(appKey)
    setError(null)
    try {
      const updated = await put<AppInfo>(`/admin/apps/${appKey}/company`, { company })
      setApps((current) => current.map((app) => (app.key === updated.key ? updated : app)))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to update application company.')
    } finally {
      setCompanySavingKey(null)
    }
  }

  async function handleCollaboratorChange(appKey: string, collaborator: string) {
    const trimmed = collaborator.trim()
    const current = apps.find((app) => app.key === appKey)
    if (current && (current.collaborator ?? '') === trimmed) return
    setCollaboratorSavingKey(appKey)
    setError(null)
    try {
      const updated = await put<AppInfo>(`/admin/apps/${appKey}/collaborator`, {
        collaborator: trimmed || null,
      })
      setApps((currentApps) => currentApps.map((app) => (app.key === updated.key ? updated : app)))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to update application collaborator.')
    } finally {
      setCollaboratorSavingKey(null)
    }
  }

  async function handleDelete() {
    if (!deleteTarget) return
    setBusy(true)
    try {
      await del(`/admin/users/${deleteTarget.id}`)
      setDeleteTarget(null)
      await loadUsers()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to archive user.')
    } finally {
      setBusy(false)
    }
  }

  const [togglingId, setTogglingId] = useState<number | null>(null)

  async function handleToggleActive(u: AdminUser) {
    setTogglingId(u.id)
    setError(null)
    try {
      await put(`/admin/users/${u.id}/active`, { is_active: !u.is_active })
      await loadUsers()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to update user status.')
    } finally {
      setTogglingId(null)
    }
  }

  async function handleRestore(u: AdminUser) {
    setTogglingId(u.id)
    setError(null)
    try {
      await post(`/admin/users/${u.id}/restore`)
      await loadUsers()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to restore user.')
    } finally {
      setTogglingId(null)
    }
  }

  const userPageCount = Math.max(1, Math.ceil(userTotal / userPageSize))

  const visiblePermissionApps = useMemo(() => {
    const query = permsSearch.trim().toLocaleLowerCase()
    return apps.filter((app) => {
      const matchesCompany = permsCompany === 'all' || app.company === permsCompany
      const matchesQuery = !query || `${app.label} ${app.key} ${app.company}`.toLocaleLowerCase().includes(query)
      return matchesCompany && matchesQuery
    })
  }, [apps, permsCompany, permsSearch])

  const visibleCatalogApps = useMemo(() => {
    const query = catalogSearch.trim().toLocaleLowerCase()
    return apps.filter((app) => {
      const matchesCompany = catalogCompany === 'all' || app.company === catalogCompany
      const matchesQuery = !query || `${app.label} ${app.key} ${app.company}`.toLocaleLowerCase().includes(query)
      return matchesCompany && matchesQuery
    })
  }, [apps, catalogCompany, catalogSearch])

  const allVisibleSelected =
    visiblePermissionApps.length > 0 && visiblePermissionApps.every((app) => permsSelected.has(app.key))

  return (
    <AppShell title="Users">
      <div className="flex flex-col gap-8">
        <div className="flex flex-col gap-4">
          <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-center">
            <div>
              <p className="text-sm font-semibold text-accent">Access control</p>
              <p className="mt-1 text-sm text-ink-dim">Manage who can sign in and which applications they can use.</p>
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <Button
                variant="secondary"
                icon={<Building2 className="h-4 w-4" />}
                onClick={() => setCatalogOpen(true)}
              >
                Application catalogue
              </Button>
              <Button icon={<Plus className="h-4 w-4" />} onClick={() => setAddOpen(true)}>
                Add user
              </Button>
            </div>
          </div>

          {error && (
            <p className="status-banner border-red-500/25 bg-red-500/8 text-red-500">
              {error}
            </p>
          )}

          <div className="subpanel flex flex-col gap-3 p-3 lg:flex-row lg:items-center">
            <div className="flex items-center gap-2 text-sm font-semibold text-ink-dim lg:pr-2">
              <Filter className="h-4 w-4 text-accent" />
              Find users
            </div>
            <SearchBox
              value={userSearch}
              onChange={(value) => {
                setUserSearch(value)
                setUserPage(1)
              }}
              placeholder="Search name, email, or user ID"
              aria-label="Search users by name, email, or user ID"
              className="w-full lg:max-w-md lg:flex-1"
            />
            <label className="flex min-w-40 flex-col gap-1 text-xs font-medium text-ink-faint">
              Role
              <select
                value={userRole}
                onChange={(event) => {
                  setUserRole(event.target.value as UserRoleFilter)
                  setUserPage(1)
                }}
                className="field-control text-sm text-ink"
              >
                <option value="all">All roles</option>
                <option value="admin">Administrators</option>
                <option value="user">Users</option>
              </select>
            </label>
            <label className="flex min-w-48 flex-col gap-1 text-xs font-medium text-ink-faint">
              Account status
              <select
                value={userScope}
                onChange={(event) => {
                  setUserScope(event.target.value as UserScopeFilter)
                  setUserPage(1)
                }}
                className="field-control text-sm text-ink"
              >
                <option value="current">All current users</option>
                <option value="active">Active only</option>
                <option value="inactive">Inactive only</option>
                <option value="archived">Archived only</option>
                <option value="all">Current and archived</option>
              </select>
            </label>
            <span className="whitespace-nowrap text-xs font-medium text-ink-faint">
              {formatIndianNumber(userTotal)} found
            </span>
          </div>

          <div className="table-shell">
            <table className="w-full min-w-[900px] border-collapse text-sm">
              <thead>
                <tr className="border-b border-border bg-bg-soft/45 text-left">
                  <th className="px-4 py-3 font-medium text-ink-dim">ID</th>
                  <th className="px-4 py-3 font-medium text-ink-dim">Name</th>
                  <th className="px-4 py-3 font-medium text-ink-dim">Email</th>
                  <th className="px-4 py-3 font-medium text-ink-dim">Role</th>
                  <th className="px-4 py-3 font-medium text-ink-dim">Status</th>
                  <th className="px-4 py-3 font-medium text-ink-dim">Apps</th>
                  <th className="px-4 py-3 font-medium text-ink-dim">Created</th>
                  <th className="px-4 py-3 text-right font-medium text-ink-dim">Actions</th>
                </tr>
              </thead>
              <tbody>
                {loading && (
                  <tr>
                    <td colSpan={8} className="px-4 py-8 text-center text-ink-faint">
                      <LoadingNotice className="py-1" />
                    </td>
                  </tr>
                )}
                {!loading && users.length === 0 && (
                  <tr>
                    <td colSpan={8} className="px-4 py-8 text-center text-ink-faint">
                      No users found.
                    </td>
                  </tr>
                )}
                {!loading && users.map((u) => (
                  <tr key={u.id} className="border-b border-border/80 transition-colors last:border-b-0 hover:bg-bg-soft/55">
                    <td className="px-4 py-3 text-ink-dim">{u.id}</td>
                    <td className="px-4 py-3 font-medium text-ink">
                      {getUserFullName(u) || <span className="font-normal text-ink-faint">Not set</span>}
                    </td>
                    <td className="px-4 py-3 text-ink">{u.email}</td>
                    <td className="px-4 py-3 text-ink capitalize">{u.role}</td>
                    <td className="px-4 py-3">
                      <span
                        className={cn(
                          'inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium',
                          u.is_deleted
                            ? 'bg-red-500/10 text-red-500'
                            : u.is_active
                            ? 'bg-emerald-500/10 text-emerald-600'
                            : 'bg-ink/10 text-ink-faint',
                        )}
                      >
                        {u.is_deleted ? 'Archived' : u.is_active ? 'Active' : 'Inactive'}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-ink-dim">
                      {u.role === 'admin' ? (
                        <span className="text-ink-faint">All (admin)</span>
                      ) : (
                        `${formatIndianNumber(u.allowed_apps?.length ?? 0)} of ${formatIndianNumber(apps.length)}`
                      )}
                    </td>
                    <td className="px-4 py-3 text-ink-dim">
                      {formatIndianDate(u.created_at)}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center justify-end gap-2">
                        {u.is_deleted ? (
                          <button
                            onClick={() => void handleRestore(u)}
                            disabled={togglingId === u.id}
                            aria-label="Restore user"
                            title="Restore as inactive"
                            className="grid h-11 w-11 place-items-center rounded-full text-ink-dim transition hover:bg-bg-soft hover:text-emerald-500 disabled:opacity-50 sm:h-8 sm:w-8"
                          >
                            <RotateCcw className="h-4 w-4" />
                          </button>
                        ) : (
                          <>
                          <button
                            onClick={() => {
                              setEditTarget(u)
                              setEditFirstName(u.first_name ?? '')
                              setEditLastName(u.last_name ?? '')
                              setEditEmailValue(u.email)
                            }}
                            aria-label="Edit user details"
                            title="Edit user details"
                            className="grid h-11 w-11 place-items-center rounded-full text-ink-dim transition hover:bg-bg-soft hover:text-accent sm:h-8 sm:w-8"
                          >
                            <UserRoundPen className="h-4 w-4" />
                          </button>
                          <button
                            onClick={() => setResetTarget(u)}
                            aria-label="Reset password"
                            className="grid h-11 w-11 place-items-center rounded-full text-ink-dim transition hover:bg-bg-soft hover:text-accent sm:h-8 sm:w-8"
                          >
                            <KeyRound className="h-4 w-4" />
                          </button>
                          {u.role !== 'admin' && (
                            <button
                              onClick={() => openPerms(u)}
                              aria-label="Manage app access"
                              className="grid h-11 w-11 place-items-center rounded-full text-ink-dim transition hover:bg-bg-soft hover:text-accent sm:h-8 sm:w-8"
                            >
                              <ShieldCheck className="h-4 w-4" />
                            </button>
                          )}
                          <button
                            onClick={() => void handleToggleActive(u)}
                            disabled={togglingId === u.id}
                            aria-label={u.is_active ? 'Deactivate user' : 'Activate user'}
                            title={u.is_active ? 'Deactivate' : 'Activate'}
                            className={cn(
                              'grid h-11 w-11 place-items-center rounded-full transition hover:bg-bg-soft disabled:opacity-50 sm:h-8 sm:w-8',
                              u.is_active
                                ? 'text-ink-dim hover:text-amber-500'
                                : 'text-ink-dim hover:text-emerald-500',
                            )}
                          >
                            <Power className="h-4 w-4" />
                          </button>
                          <button
                            onClick={() => setDeleteTarget(u)}
                            aria-label="Archive user"
                            title="Archive account"
                            className="grid h-11 w-11 place-items-center rounded-full text-ink-dim transition hover:bg-bg-soft hover:text-red-500 sm:h-8 sm:w-8"
                          >
                            <Archive className="h-4 w-4" />
                          </button>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination
            page={userPage}
            pageCount={userPageCount}
            pageSize={userPageSize}
            totalItems={userTotal}
            itemLabel="users"
            onPageChange={setUserPage}
            onPageSizeChange={(size) => {
              setUserPageSize(size)
              setUserPage(1)
            }}
            pageSizeOptions={[10, 25, 50, 100]}
          />
        </div>
      </div>

      <Modal open={addOpen} onClose={() => setAddOpen(false)} title="Add user">
        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault()
            void handleAdd()
          }}
        >
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <label className="flex flex-col gap-1.5 text-sm">
              <span className="font-medium text-ink-dim">First name <span className="font-normal text-ink-faint">(optional)</span></span>
              <input
                type="text"
                maxLength={100}
                autoComplete="given-name"
                value={newFirstName}
                onChange={(e) => setNewFirstName(e.target.value)}
                className="field-control"
              />
            </label>
            <label className="flex flex-col gap-1.5 text-sm">
              <span className="font-medium text-ink-dim">Last name <span className="font-normal text-ink-faint">(optional)</span></span>
              <input
                type="text"
                maxLength={100}
                autoComplete="family-name"
                value={newLastName}
                onChange={(e) => setNewLastName(e.target.value)}
                className="field-control"
              />
            </label>
          </div>
          <label className="flex flex-col gap-1.5 text-sm">
            <span className="font-medium text-ink-dim">Email</span>
            <input
              required
              type="email"
              value={newEmail}
              onChange={(e) => setNewEmail(e.target.value)}
              placeholder="name@rdc.in"
              className="field-control"
            />
          </label>
          <label className="flex flex-col gap-1.5 text-sm">
            <div className="flex items-center justify-between gap-2">
              <span className="font-medium text-ink-dim">Password</span>
              <button
                type="button"
                onClick={() => setNewPassword(generateStrongPassword())}
                className="text-xs font-medium text-accent hover:underline"
              >
                Generate strong password
              </button>
            </div>
            <PasswordInput
              required
              minLength={10}
              autoComplete="new-password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
            />
            <PasswordStrengthMeter password={newPassword} />
          </label>
          <label className="flex flex-col gap-1.5 text-sm">
            <span className="font-medium text-ink-dim">Role</span>
            <select
              value={newRole}
              onChange={(e) => setNewRole(e.target.value as 'admin' | 'user')}
              className="field-control"
            >
              <option value="user">User</option>
              <option value="admin">Admin</option>
            </select>
          </label>
          <div className="mt-2 flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
            <Button type="button" variant="secondary" onClick={() => setAddOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" loading={saving} disabled={!scorePasswordStrength(newPassword).isAcceptable}>
              Create
            </Button>
          </div>
        </form>
      </Modal>

      <Modal
        open={editTarget !== null}
        onClose={() => setEditTarget(null)}
        title={`Edit user details${editTarget ? ` | ${getUserDisplayName(editTarget)}` : ''}`}
      >
        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault()
            void handleEditProfile()
          }}
        >
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <label className="flex flex-col gap-1.5 text-sm">
              <span className="font-medium text-ink-dim">First name <span className="font-normal text-ink-faint">(optional)</span></span>
              <input
                type="text"
                maxLength={100}
                autoComplete="given-name"
                value={editFirstName}
                onChange={(e) => setEditFirstName(e.target.value)}
                className="field-control"
              />
            </label>
            <label className="flex flex-col gap-1.5 text-sm">
              <span className="font-medium text-ink-dim">Last name <span className="font-normal text-ink-faint">(optional)</span></span>
              <input
                type="text"
                maxLength={100}
                autoComplete="family-name"
                value={editLastName}
                onChange={(e) => setEditLastName(e.target.value)}
                className="field-control"
              />
            </label>
          </div>
          <label className="flex flex-col gap-1.5 text-sm">
            <span className="font-medium text-ink-dim">Email</span>
            <input
              type="email"
              required
              value={editEmailValue}
              onChange={(e) => setEditEmailValue(e.target.value)}
              className="field-control"
            />
          </label>
          <div className="mt-2 flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
            <Button type="button" variant="secondary" onClick={() => setEditTarget(null)}>
              Cancel
            </Button>
            <Button type="submit" loading={busy}>
              Save
            </Button>
          </div>
        </form>
      </Modal>

      <Modal
        open={resetTarget !== null}
        onClose={() => setResetTarget(null)}
        title={`Reset password for ${resetTarget ? getUserDisplayName(resetTarget) : ''}`}
      >
        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault()
            void handleResetPassword()
          }}
        >
          <label className="flex flex-col gap-1.5 text-sm">
            <div className="flex items-center justify-between gap-2">
              <span className="font-medium text-ink-dim">New password</span>
              <button
                type="button"
                onClick={() => setResetPassword(generateStrongPassword())}
                className="text-xs font-medium text-accent hover:underline"
              >
                Generate strong password
              </button>
            </div>
            <PasswordInput
              required
              minLength={10}
              autoComplete="new-password"
              value={resetPassword}
              onChange={(e) => setResetPassword(e.target.value)}
            />
            <PasswordStrengthMeter password={resetPassword} />
          </label>
          <div className="mt-2 flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
            <Button type="button" variant="secondary" onClick={() => setResetTarget(null)}>
              Cancel
            </Button>
            <Button type="submit" loading={busy} disabled={!scorePasswordStrength(resetPassword).isAcceptable}>
              Reset
            </Button>
          </div>
        </form>
      </Modal>

      <Modal
        open={permsTarget !== null}
        onClose={() => setPermsTarget(null)}
        title={`App access for ${permsTarget ? getUserDisplayName(permsTarget) : ''}`}
        className="max-w-2xl"
      >
        <div className="flex flex-col gap-4">
          <div className="rounded-2xl border border-accent/20 bg-accent/5 p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <p className="text-sm font-semibold text-ink">Explicit access only</p>
                <p className="mt-1 text-xs leading-5 text-ink-dim">
                  New users start with no applications. Select only the tools this user is allowed to open.
                </p>
              </div>
              <span className="rounded-full bg-accent/10 px-3 py-1 text-xs font-semibold text-accent">
                {formatIndianNumber(permsSelected.size)} selected
              </span>
            </div>
          </div>

          <div className="grid gap-3 sm:grid-cols-[1fr_11rem]">
            <SearchBox
              value={permsSearch}
              onChange={setPermsSearch}
              placeholder="Search applications…"
              aria-label="Search applications"
            />
            <select
              value={permsCompany}
              onChange={(event) => setPermsCompany(event.target.value as CompanyFilter)}
              aria-label="Filter applications by company"
              className="field-control"
            >
              <option value="all">All companies</option>
              <option value="RDC">RDC</option>
              <option value="Ultrafine">Ultrafine</option>
            </select>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
            <span className="text-ink-faint">
              Showing {formatIndianNumber(visiblePermissionApps.length)} of {formatIndianNumber(apps.length)} applications
            </span>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => {
                  const next = new Set(permsSelected)
                  visiblePermissionApps.forEach((app) => next.add(app.key))
                  setPermsSelected(next)
                }}
                disabled={visiblePermissionApps.length === 0 || allVisibleSelected}
                className="font-semibold text-accent transition hover:text-accent-2 disabled:cursor-not-allowed disabled:opacity-40"
              >
                Select visible
              </button>
              <span className="text-border">•</span>
              <button
                type="button"
                onClick={() => setPermsSelected(new Set())}
                disabled={permsSelected.size === 0}
                className="font-semibold text-ink-dim transition hover:text-red-500 disabled:cursor-not-allowed disabled:opacity-40"
              >
                Clear all
              </button>
            </div>
          </div>

          <div className="max-h-[42vh] overflow-y-auto rounded-2xl border border-border bg-surface/45 p-2">
            {visiblePermissionApps.length === 0 ? (
              <div className="grid min-h-32 place-items-center px-4 text-center text-sm text-ink-faint">
                No applications match this search and filter.
              </div>
            ) : (
              <div className="grid gap-1.5">
                {visiblePermissionApps.map((app) => {
                  const selected = permsSelected.has(app.key)
                  return (
                    <label
                      key={app.key}
                      className={cn(
                        'flex cursor-pointer items-center gap-3 rounded-xl border px-3.5 py-3 transition',
                        selected
                          ? 'border-accent/35 bg-accent/8 shadow-[inset_3px_0_0_var(--color-accent)]'
                          : 'border-transparent hover:border-border hover:bg-bg-soft/70',
                      )}
                    >
                      <input
                        type="checkbox"
                        checked={selected}
                        onChange={(event) => {
                          const next = new Set(permsSelected)
                          if (event.target.checked) next.add(app.key)
                          else next.delete(app.key)
                          setPermsSelected(next)
                        }}
                        className="h-4 w-4 shrink-0 rounded border-border accent-accent"
                      />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-medium text-ink">{app.label}</span>
                        <span className="mt-0.5 block truncate text-xs text-ink-faint">{app.key}</span>
                      </span>
                      <span
                        className={cn(
                          'shrink-0 rounded-full px-2.5 py-1 text-[10px] font-bold tracking-wide uppercase',
                          'bg-accent/10 text-accent',
                        )}
                      >
                        {app.company}
                      </span>
                    </label>
                  )
                })}
              </div>
            )}
          </div>

          <div className="mt-2 flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
            <Button type="button" variant="secondary" onClick={() => setPermsTarget(null)}>
              Cancel
            </Button>
            <Button loading={busy} onClick={() => void handleSavePerms()}>
              Save
            </Button>
          </div>
        </div>
      </Modal>

      <Modal
        open={catalogOpen}
        onClose={() => setCatalogOpen(false)}
        title="Application catalogue"
        className="max-w-3xl"
      >
        <div className="flex flex-col gap-4">
          <div className="rounded-2xl border border-border bg-bg-soft/55 p-4">
            <div className="flex items-start gap-3">
              <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-accent/10 text-accent">
                <Building2 className="h-5 w-5" />
              </span>
              <div>
                <p className="text-sm font-semibold text-ink">Company ownership &amp; footer credit</p>
                <p className="mt-1 text-xs leading-5 text-ink-dim">
                  Classify each application as RDC or Ultrafine, and credit who it was built with. Its footer
                  always reads &ldquo;Made by Jarjish&rdquo;, plus that name if set. Changes save immediately and are
                  available only to administrators.
                </p>
              </div>
            </div>
          </div>

          <div className="grid gap-3 sm:grid-cols-[1fr_11rem]">
            <SearchBox
              value={catalogSearch}
              onChange={setCatalogSearch}
              placeholder="Search by name or key…"
              aria-label="Search application catalogue"
            />
            <select
              value={catalogCompany}
              onChange={(event) => setCatalogCompany(event.target.value as CompanyFilter)}
              aria-label="Filter catalogue by company"
              className="field-control"
            >
              <option value="all">All companies</option>
              <option value="RDC">RDC</option>
              <option value="Ultrafine">Ultrafine</option>
            </select>
          </div>

          <p className="text-xs text-ink-faint">
            Showing {formatIndianNumber(visibleCatalogApps.length)} of {formatIndianNumber(apps.length)} applications
          </p>

          <div className="max-h-[48vh] overflow-y-auto rounded-2xl border border-border bg-surface/45">
            {visibleCatalogApps.length === 0 ? (
              <div className="grid min-h-36 place-items-center px-4 text-center text-sm text-ink-faint">
                No applications match this search and filter.
              </div>
            ) : (
              <div className="divide-y divide-border/80">
                {visibleCatalogApps.map((app) => (
                  <div key={app.key} className="flex flex-col gap-3 px-4 py-3.5 sm:flex-row sm:items-center">
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-semibold text-ink">{app.label}</p>
                      <p className="mt-0.5 truncate text-xs text-ink-faint">{app.key}</p>
                    </div>
                    <label className="flex shrink-0 items-center gap-2 text-xs text-ink-dim">
                      <span>Made with</span>
                      <input
                        key={`${app.key}-${app.collaborator ?? ''}`}
                        type="text"
                        defaultValue={app.collaborator ?? ''}
                        onBlur={(event) => void handleCollaboratorChange(app.key, event.target.value)}
                        disabled={collaboratorSavingKey === app.key}
                        placeholder="Collaborator name"
                        aria-label={`Collaborator credited alongside Jarjish for ${app.label}`}
                        className="field-control min-w-40 py-2 text-sm"
                      />
                    </label>
                    <label className="flex shrink-0 items-center gap-2 text-xs text-ink-dim">
                      <span>Company</span>
                      <select
                        value={app.company}
                        onChange={(event) => void handleCompanyChange(app.key, event.target.value as AppInfo['company'])}
                        disabled={companySavingKey === app.key}
                        aria-label={`Company for ${app.label}`}
                        className="field-control min-w-36 py-2 text-sm"
                      >
                        <option value="RDC">RDC</option>
                        <option value="Ultrafine">Ultrafine</option>
                      </select>
                    </label>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="flex justify-end">
            <Button variant="secondary" onClick={() => setCatalogOpen(false)}>
              Done
            </Button>
          </div>
        </div>
      </Modal>

      <Modal open={deleteTarget !== null} onClose={() => setDeleteTarget(null)} title="Archive account">
        <p className="mb-6 text-sm text-ink-dim">
          Remove <strong>{deleteTarget ? getUserDisplayName(deleteTarget) : ''}</strong>? They'll no longer be able to sign in and
          will disappear from this list. Nothing is ever hard-deleted, so their account and
          history are preserved. If you just want to temporarily block sign-in, use the
          Deactivate toggle instead.
        </p>
        <div className="flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
          <Button variant="secondary" onClick={() => setDeleteTarget(null)}>
            Cancel
          </Button>
          <Button variant="danger" loading={busy} onClick={() => void handleDelete()}>
            Archive
          </Button>
        </div>
      </Modal>
    </AppShell>
  )
}
