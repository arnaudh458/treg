import { expect, test, type Page } from '@playwright/test'

async function signIn(page: Page) {
  await page.goto('/app?ref=frontend-test')
  await page.getByPlaceholder('you@work.com').fill(`browser-${Date.now()}@example.com`)
  await page.getByRole('button', { name: 'Email me a sign-in code' }).click()
  const code = await page.getByText(/dev code \d{6}/).innerText()
  await page.getByPlaceholder('6-digit code').fill(code.match(/\d{6}/)![0])
  await page.getByRole('dialog', { name: 'Sign in' }).getByRole('button', { name: 'Sign in', exact: true }).click()
  await page.getByPlaceholder('Team name, e.g. Superdesign').fill('Browser test team')
  await page.getByRole('button', { name: 'Create team →', exact: true }).click()
  await expect(page.getByText('Which agent are you using?', { exact: true })).toBeVisible()
  await page.getByRole('link', { name: 'Skip', exact: true }).click()
  await expect(page.getByRole('navigation', { name: 'Primary navigation' })).toBeVisible()
}

test('sign in, create team, switch pages, refresh and navigate back', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await signIn(page)
  const navigation = page.getByRole('navigation', { name: 'Primary navigation' })
  for (const name of ['Catalog', 'Your own tools', 'Activity', 'Team']) {
    await navigation.getByRole('button', { name, exact: true }).click()
    await expect(navigation.getByRole('button', { name, exact: true })).toHaveAttribute('aria-current', 'page')
  }
  await page.reload()
  await expect(navigation.getByRole('button', { name: 'Team', exact: true })).toHaveAttribute('aria-current', 'page')
  await page.goBack()
  await expect(navigation.getByRole('button', { name: 'Activity', exact: true })).toHaveAttribute('aria-current', 'page')
  await page.goForward()
  await expect(navigation.getByRole('button', { name: 'Team', exact: true })).toHaveAttribute('aria-current', 'page')
  await page.locator('.rd-account-menu summary').click()
  await page.locator('.rd-account-menu').getByRole('button', { name: 'Billing', exact: true }).click()
  await expect(page).toHaveURL(/#orgs$/)
  expect(errors).toEqual([])
})

test('signed-in users can visit the homepage and return to the dashboard', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await signIn(page)
  await page.getByRole('link', { name: 'treg home' }).click()
  await expect(page).toHaveURL('http://127.0.0.1:18791/')
  await expect(page.getByRole('heading', { level: 1 })).toContainText('OpenRouter for agent tools')
  await expect(page.getByRole('link', { name: 'Sign in', exact: true })).toHaveCount(0)
  await page.locator('.nav').getByRole('button', { name: 'Open dashboard' }).click()
  await expect(page.getByRole('navigation', { name: 'Primary navigation' })).toBeVisible()
})

test('onboarding controls and images work on mobile and dark theme', async ({ page }, testInfo) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await signIn(page)
  await page.getByRole('button', { name: 'Getting started', exact: true }).click()
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(page.locator('.rd-start')).toBeVisible()
  const trigger = page.locator('[aria-controls="rd-agent-options"]')
  await trigger.click()
  await page.locator('#rd-agent-options').getByRole('button', { name: 'Codex', exact: true }).click()
  await expect(trigger).toContainText('Codex')
  await page.getByRole('button', { name: 'Show key', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Hide key', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Hide key', exact: true }).click()
  await page.evaluate(() => Object.defineProperty(navigator, 'clipboard', {
    configurable: true, value: { writeText: () => Promise.reject(new Error('denied')) },
  }))
  await page.locator('.rd-setup-panel').first().getByRole('button', { name: 'Copy', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('Could not copy')
  await page.getByRole('button', { name: 'Dismiss', exact: true }).click()
  await page.locator('.rd-account-menu summary').click()
  await page.getByRole('button', { name: 'Dark appearance' }).click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await page.waitForFunction(() => [...document.querySelectorAll<HTMLImageElement>('.rd-try .try-ico')].every(img => img.complete && img.naturalWidth > 0))
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: testInfo.outputPath('mobile-dark.png'), fullPage: true })
  expect(errors).toEqual([])
})

test('public catalog and shared deep links remain available without a session', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('/catalog')
  await expect(page.locator('.pubnav')).toBeVisible()
  await expect(page.getByRole('navigation', { name: 'Primary navigation' })).toHaveCount(0)
  await page.getByRole('button', { name: 'Start free', exact: true }).click()
  await expect(page.getByRole('dialog', { name: 'Sign in' })).toBeVisible()
  await page.goto('/catalog/google')
  await expect(page.locator('.plat-head')).toBeVisible()
  await page.reload()
  await expect(page.locator('.plat-head')).toBeVisible()
  await page.goto('/app/tools/shared-example')
  await expect(page.getByRole('heading', { name: /shared-example/ })).toBeVisible()
  await expect(page.getByRole('dialog', { name: 'Sign in' })).toBeVisible()
  expect(errors).toEqual([])
})

test('session initialization never flashes the old signed-out landing page', async ({ page }) => {
  await signIn(page)
  let releaseSession!: () => void
  const sessionGate = new Promise<void>(resolve => { releaseSession = resolve })
  await page.route('**/auth/me', async route => { await sessionGate; await route.continue() })
  await page.reload()
  await expect(page.getByRole('status')).toHaveText('Loading treg…')
  await expect(page.getByText('Sign in to treg', { exact: true })).toHaveCount(0)
  releaseSession()
  await expect(page.getByRole('navigation', { name: 'Primary navigation' })).toBeVisible()
})

test('mainline team resources survive navigation and open the voice tools', async ({ page }) => {
  await signIn(page)
  await page.route('**/provider-resources?source=platform', route => route.fulfill({
    json: [{ id: 1, provider: 'fishaudio', kind: 'voice', upstream_id: 'test-private-voice', display_name: 'Test voice', status: 'active' }],
  }))
  await page.getByRole('navigation', { name: 'Primary navigation' }).getByRole('button', { name: 'Your own tools', exact: true }).click()
  await page.getByRole('button', { name: 'Team resources', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Team resources', exact: true })).toBeVisible()
  await expect(page.getByText('Test voice', { exact: true })).toBeVisible()
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Team resources', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Rename', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: 'Rename voice', exact: true })
  await expect(dialog.getByRole('textbox')).toHaveValue('Test voice')
  await expect(dialog.getByRole('textbox')).toBeFocused()
  await dialog.getByRole('button', { name: 'Cancel', exact: true }).click()
  await page.getByRole('button', { name: 'Use in TTS', exact: true }).click()
  // The disposable server has no provider credentials; verify the prepared request
  // through the API tab, which is available without enabling paid execution.
  const drawer = page.getByRole('dialog').filter({ hasText: 'Try “fishaudio.tts.s2-1-pro”' })
  await drawer.getByRole('button', { name: 'API', exact: true }).click()
  await expect(drawer.locator('pre')).toContainText('"reference_id": "test-private-voice"')
})
