import { chromium } from 'playwright'
const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' })
const page = await browser.newPage({ viewport: { width: 1600, height: 1200 } })
await page.goto('http://127.0.0.1:5173/', { waitUntil: 'networkidle' })
const leave = page.getByRole('button', { name: /Switch/i })
if (await leave.count()) { await leave.click(); await page.waitForTimeout(1800) }
await page.waitForTimeout(800)
console.log('HASH ON MODE SELECT:', await page.evaluate(() => location.hash))
await page.getByRole('button', { name: 'Manage prop accounts', exact: true }).click()
await page.waitForTimeout(400)
const sections = await page.evaluate(() => {
  const el = document.querySelector('.front-door-actions')
  return el ? el.textContent : null
})
console.log('actions line:', sections)
await page.locator('.front-door-go').click()
for (const ms of [200, 500, 1000, 2000, 3000]) {
  await page.waitForTimeout(ms === 200 ? 200 : ms - 200)
  console.log(`  +${ms}ms hash=`, await page.evaluate(() => location.hash))
}
await browser.close()
