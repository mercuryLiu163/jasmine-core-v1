/**
 * Token entry (P6-A01).
 *
 * The token is typed into a `type="password"` field with a proper label, is
 * validated by *using* it (a real GET), and is held in memory only. The page
 * states the exact scopes a P6-01 read-only token needs and never mints,
 * bootstraps or guesses one. Nothing here writes to storage or the URL.
 */

import { useId, useState } from 'react'

import { CoreClient } from '../api/client'
import { makeEndpoints } from '../api/endpoints'
import { useSession, REQUIRED_READ_SCOPES } from '../app/session'
import { CoreApiError, describeError } from '../api/errors'
import { ErrorPanel } from './States'

export function SignIn(): JSX.Element {
  const { signIn } = useSession()
  const [value, setValue] = useState('')
  const [error, setError] = useState<CoreApiError | null>(null)
  const [checking, setChecking] = useState(false)
  const inputId = useId()
  const helpId = useId()

  const submit = async (event: React.FormEvent): Promise<void> => {
    event.preventDefault()
    const candidateToken = value.trim()
    if (!candidateToken || checking) return
    setChecking(true)
    setError(null)
    // The token is accepted only after a real authenticated read succeeds, and
    // the session is not opened until it does. A successful parse of the box is
    // not evidence that the token works; a token Core refuses never becomes the
    // session token, so no later view can run with unvalidated credentials.
    try {
      const candidate = makeEndpoints(new CoreClient({ getToken: () => candidateToken }))
      await candidate.health()
      signIn(candidateToken)
      setValue('')
    } catch (caught) {
      setError(
        caught instanceof CoreApiError
          ? caught
          : new CoreApiError({
              kind: 'unknown',
              status: null,
              code: 'ui_signin_failed',
              message: String(caught),
            }),
      )
    } finally {
      setChecking(false)
    }
  }

  return (
    <main id="main" className="signin">
      <h1>Jasmine Core Dashboard(P6-01 只读)</h1>
      <p>
        页面通过同源代理读取本机 Core。token 只保存在内存中:刷新或点击“退出”即清除,
        不写入 localStorage/sessionStorage/IndexedDB,不进入 URL、日志或打包产物。
      </p>
      <form onSubmit={(event) => void submit(event)} aria-describedby={helpId}>
        <label htmlFor={inputId}>Core API token</label>
        <input
          id={inputId}
          type="password"
          autoComplete="off"
          spellCheck={false}
          value={value}
          onChange={(event) => setValue(event.target.value)}
        />
        <button type="submit" disabled={checking || value.trim().length === 0}>
          {checking ? '校验中…' : '使用此 token'}
        </button>
      </form>
      <p id={helpId} className="muted">
        只读 token 需要的 scope:{' '}
        {REQUIRED_READ_SCOPES.map((scope) => (
          <code key={scope}>{scope}</code>
        ))}
        。缺少任一 scope 时,对应视图会明确显示 forbidden,而不会改用更高权限的凭据。
      </p>
      <p className="muted">
        本页不会代你铸造 key、不会使用 admin bootstrap,也不会调用 fingerprint 扫描。
      </p>
      {error ? <ErrorPanel error={error} what="健康检查" onRetry={() => setError(null)} /> : null}
    </main>
  )
}

export function describeSignInFailure(error: CoreApiError): string {
  return describeError(error)
}
