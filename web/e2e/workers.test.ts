import { describe, expect, it } from 'vitest';

import { playwrightWorkers } from './workers';

describe('playwrightWorkers', () => {
	it("keeps Playwright's own default locally until #906 is fixed", () => {
		expect(playwrightWorkers({})).toBeUndefined();
	});

	it('takes a positive PW_WORKERS', () => {
		expect(playwrightWorkers({ PW_WORKERS: '3' })).toBe(3);
		expect(playwrightWorkers({ PW_WORKERS: ' 1 ' })).toBe(1);
	});

	it.each(['0', '-2', 'lots', '2.5', ''])('ignores PW_WORKERS=%j', (value) => {
		expect(playwrightWorkers({ PW_WORKERS: value })).toBeUndefined();
	});

	it('keeps one worker in CI, whatever PW_WORKERS says', () => {
		expect(playwrightWorkers({ CI: 'true', PW_WORKERS: '4' })).toBe(1);
	});
});
