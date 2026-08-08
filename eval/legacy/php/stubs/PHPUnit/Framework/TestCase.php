<?php

/**
 * Thin non-aborting stand-in for PHPUnit\Framework\TestCase.
 *
 * The legacy architecture tests use exactly three PHPUnit surfaces:
 *   - assertSame($expected, $actual, $message)
 *   - assertCount($expected, $actual, $message)
 *   - setUp() / parent::setUp()
 *
 * The only behavioural deviation from real PHPUnit is that a failing
 * assertion RECORDS the event and returns instead of throwing. Legacy rule
 * drivers call assertSame() inside foreach loops (HttpBoundaryRulesTest,
 * ModuleContractRulesTest, LayerBoundaryRulesTest, ModelOwnershipRulesTest),
 * so an aborting assertion would truncate the oracle at the first failing
 * iteration. No rule logic lives here.
 */

namespace PHPUnit\Framework;

use LimenOracle\Recorder;

abstract class TestCase
{
    protected function setUp(): void
    {
    }

    protected function tearDown(): void
    {
    }

    public function assertSame(mixed $expected, mixed $actual, string $message = ''): void
    {
        Recorder::record('assertSame', $expected, $actual, $message, $expected === $actual);
    }

    public function assertCount(int $expectedCount, mixed $haystack, string $message = ''): void
    {
        $actualCount = is_countable($haystack) ? count($haystack) : -1;

        Recorder::record(
            'assertCount',
            $expectedCount,
            $actualCount,
            $message,
            $expectedCount === $actualCount,
            ['haystack' => $haystack]
        );
    }

    public function assertTrue(mixed $condition, string $message = ''): void
    {
        Recorder::record('assertTrue', true, $condition, $message, $condition === true);
    }

    public function assertEquals(mixed $expected, mixed $actual, string $message = ''): void
    {
        Recorder::record('assertEquals', $expected, $actual, $message, $expected == $actual);
    }
}
