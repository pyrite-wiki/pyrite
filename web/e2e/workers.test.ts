import { describe, expect, it } from 'vitest';

import { DEFAULT_LOCAL_WORKERS, playwrightWorkers } from './workers';

describe('playwrightWorkers (#168)', () => {
	it('is bounded locally by default, not half the cores', () => {
		expect(playwrightWorkers({})).toBe(DEFAULT_LOCAL_WORKERS);
		expect(DEFAULT_LOCAL_WORKERS).toBe(2);
	});

	it('takes a positive PW_WORKERS', () => {
		expect(playwrightWorkers({ PW_WORKERS: '3' })).toBe(3);
		expect(playwrightWorkers({ PW_WORKERS: ' 1 ' })).toBe(1);
	});

	it.each(['0', '-2', 'lots', '2.5', ''])('falls back to the default for PW_WORKERS=%j', (value) => {
		expect(playwrightWorkers({ PW_WORKERS: value })).toBe(DEFAULT_LOCAL_WORKERS);
	});

	it('keeps one worker in CI, whatever PW_WORKERS says', () => {
		expect(playwrightWorkers({ CI: 'true', PW_WORKERS: '4' })).toBe(1);
	});
});
