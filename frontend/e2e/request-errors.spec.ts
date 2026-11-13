import { expect, test, type Page } from '@playwright/test';
import { cors, grantedProfile, mockSignInAndMe, plantAlpha } from './support/fixtures';

async function openFailedRegister(page: Page) {
  await page.route('**/api/v1/**', route => route.fulfill({ status: 404, headers: cors, contentType: 'application/json', body: JSON.stringify({ detail: 'Unexpected test request' }) }));
  await mockSignInAndMe(page, { status: 200, body: { ...grantedProfile, modules: ['home', 'quality'], permissions: [], all_plants: true, unit_ids: [] } }, [plantAlpha]);
  await page.route('**/api/v1/quality/holds?*', route => route.fulfill({ status: 503, headers: { ...cors, 'X-Request-ID': 'req-shared-error-503' }, contentType: 'application/json', body: JSON.stringify({ detail: 'Quality records are temporarily unavailable' }) }));
  await page.goto('/app/quality/holds');
  await expect(page.getByRole('alert')).toContainText('Quality records are temporarily unavailable');
}

test('shared server errors expose one request ID and keyboard copying confirms the actual copied value', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await openFailedRegister(page);
  await expect(page.getByText('Request ID: req-shared-error-503', { exact: true })).toHaveCount(1);
  const copy = page.getByRole('button', { name: 'Copy request ID', exact: true });
  await expect(copy).toBeVisible();
  await copy.focus();
  await page.keyboard.press('Enter');
  await expect(copy).toHaveText('Copied');
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe('req-shared-error-503');
});

test('clipboard rejection preserves the server detail and offers honest manual-copy recovery', async ({ page }) => {
  await page.addInitScript(() => Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async () => { throw new Error('Clipboard denied'); } } }));
  await openFailedRegister(page);
  await expect(page.getByRole('button', { name: 'Copy request ID', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Copy request ID', exact: true }).click();
  await expect(page.getByText('Copy failed. Select the request ID and copy it manually.', { exact: true })).toBeVisible();
  await expect(page.getByText('Request ID: req-shared-error-503', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Copy request ID', exact: true })).not.toHaveText('Copied');
});
