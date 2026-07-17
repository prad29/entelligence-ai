const STORAGE_PREFIX = 'batch-job:'

function storageKey(namespace: string): string {
  return `${STORAGE_PREFIX}${namespace}`
}

export function saveActiveJob(namespace: string, jobId: string): void {
  try {
    localStorage.setItem(storageKey(namespace), jobId)
  } catch {
    // localStorage unavailable (private browsing, quota) — persistence is
    // best-effort only, never block the upload flow on it.
  }
}

export function loadActiveJob(namespace: string): string | null {
  try {
    return localStorage.getItem(storageKey(namespace))
  } catch {
    return null
  }
}

export function clearActiveJob(namespace: string): void {
  try {
    localStorage.removeItem(storageKey(namespace))
  } catch {
    // best-effort, see saveActiveJob
  }
}
