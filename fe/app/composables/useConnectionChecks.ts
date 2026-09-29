export interface RouteInfo {
  name: string
  method: string
  path: string
  description: string
}

export type CheckStatus = 'pending' | 'ok' | 'error'

export interface CheckRow extends RouteInfo {
  status: CheckStatus
  httpStatus: number | null
  latencyMs: number | null
  detail: string
}

/**
 * Loads the backend's diagnostic routes from `GET /testing` and calls each one.
 * Runs in the browser, so the API base is the host-visible URL, not the Docker one.
 */
export function useConnectionChecks() {
  const apiBase = useRuntimeConfig().public.apiBase
  const rows = ref<CheckRow[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function runCheck(row: CheckRow): Promise<void> {
    row.status = 'pending'
    const start = performance.now()
    try {
      const res = await fetch(`${apiBase}${row.path}`)
      const body = await res.json()
      row.httpStatus = res.status
      row.latencyMs = Math.round(performance.now() - start)
      row.status = res.ok && body.ok !== false ? 'ok' : 'error'
      row.detail = body.detail ?? JSON.stringify(body)
    } catch (e) {
      row.httpStatus = null
      row.latencyMs = null
      row.status = 'error'
      row.detail = `Request failed: ${(e as Error).message}`
    }
  }

  async function runAll(): Promise<void> {
    loading.value = true
    error.value = null
    try {
      const res = await fetch(`${apiBase}/testing`)
      if (!res.ok) throw new Error(`GET /testing returned HTTP ${res.status}`)
      const routes: RouteInfo[] = await res.json()
      rows.value = routes.map((route) => ({
        ...route,
        status: 'pending',
        httpStatus: null,
        latencyMs: null,
        detail: '',
      }))
      await Promise.all(rows.value.map(runCheck))
    } catch (e) {
      rows.value = []
      error.value = `Could not reach the API at ${apiBase}: ${(e as Error).message}`
    } finally {
      loading.value = false
    }
  }

  return { apiBase, rows, loading, error, runAll, runCheck }
}
